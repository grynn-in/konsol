"""The ownership graph's edge rule — pure, no frappe, no ClickHouse.

konsol#117 option A (Deepak Pai, 7 Nov 2026): ownership is a graph whose
links are Ownership Period rows (owner node -> owned node) and whose
fallback, for a node no link names, is the tree parent. Two readers walk
that graph: ``ConsolidationGroup.build_rows`` enumerates every path for the
warehouse closure, and ``OwnershipPeriod._validate_no_cycle`` refuses a link
that would close a ring. They used to build the edges each in their own
way, with different subtleties (PR #459 re-review, finding A); this module
is the one place the rule lives, so the ring the reconcile would raise on is
exactly the ring validate refuses.

The rule (``ownership_edges``):

* A node is owned by the owner of every link that names it, when the link's
  ``owner_node`` is set AND is a node (a row written before the owner field
  existed, or whose owner node is gone, is not a link: finding B). A link
  naming a node that does not exist is skipped (validate refuses it; a stale
  row must not stop every other node's closure).
* A node no such link names is owned by its tree parent, when that parent is
  a node. A root is owned by nobody.
* Two periods on one (owner, owned) edge are one edge: the graph is undated,
  dbt resolves each link's dates.
* ``tentative`` links (the validator's: drafts, which ``build_rows`` does not
  read) add their edge without standing in for the tree parent of the node
  they name, so a ring through either the draft or the tree edge is refused
  before the draft is submitted, and nothing a draft could still lose by
  being deleted hides a ring that exists today.

Rows are what ``frappe.get_all`` returns (dicts with attribute read) or any
object with those attributes; a node row has ``name``,
``consolidation_group``, ``data_area_id``, ``parent_consolidation_group``;
a link row has ``consolidation_group``, ``data_area_id`` (the owned node)
and ``owner_node`` (the owner's node name).
"""


def _get(row, key):
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def node_key(group, entity):
    """The (group, entity) key of a node; a blank entity, however spelled
    (None, ''), is the group's own node."""
    return (group, entity or "")


def ownership_edges(nodes, links=(), tentative=()):
    """Owner node name -> the owned node names, for every node in ``nodes``
    (a node that owns nothing maps to ``[]``). Owned nodes are listed in
    ``nodes`` order, so ``build_rows`` enumerates paths as it always did;
    tentative edges follow."""
    by_name = {_get(n, "name"): n for n in nodes}
    by_key = {node_key(_get(n, "consolidation_group"), _get(n, "data_area_id")): _get(n, "name")
              for n in nodes}

    def edge(link):
        owner = _get(link, "owner_node")
        owned = by_key.get(node_key(_get(link, "consolidation_group"), _get(link, "data_area_id")))
        if owner and owned and owner in by_name:
            return owner, owned
        return None

    owners = {}
    for link in links:
        found = edge(link)
        if found:
            owners.setdefault(found[1], set()).add(found[0])
    children = {name: [] for name in by_name}
    for n in nodes:
        name, parent = _get(n, "name"), _get(n, "parent_consolidation_group")
        for owner in sorted(owners.get(name) or ([parent] if parent in by_name else [])):
            children[owner].append(name)
    for link in tentative:
        found = edge(link)
        if found and found[1] not in children[found[0]]:
            children[found[0]].append(found[1])
    return children


def path_between(edges, start, target):
    """Depth-first over ``edges`` (as ``ownership_edges`` returns them): the
    node names after ``start`` on a path that reaches ``target`` (``target``
    last), or None when none does. A visited set keeps a graph that already
    holds a ring (through a patch or an import) from looping."""
    visited, stack = set(), [(start, [])]
    while stack:
        node, trail = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        for nxt in edges.get(node, ()):
            if nxt == target:
                return trail + [nxt]
            if nxt not in visited:
                stack.append((nxt, trail + [nxt]))
    return None


#: The methods through which an entity is consolidated or equity-accounted:
#: the warehouse's "live" (ownership_exits.sql, ``is_live``).
LIVE_METHODS = ("full", "proportional", "equity")


def end_is_exit(link, others, disposals):
    """Whether ending ``link`` takes its node out of the group: the rule the
    warehouse posts an exit journal on (konsolidat#335 option A, decided by
    Deepak Pai 14 Nov 2026; macros/ownership_exits.sql), so konsol asks for
    the exit account exactly when the warehouse will need it (review F6).

    An end is an exit when all three hold:

    * the link was live (its method is in ``LIVE_METHODS``): a link with
      method ``none`` consolidated nothing, so nothing is derecognised;
    * on the day after its end no period in ``others`` holds the node through
      a live method (effective on or before that day, open-ended or ending on
      or after it): an owner transfer, a contiguous period, another owner's
      link keep the node, whatever ``supersedes`` says; a successor with
      method ``none`` (deemed loss of control) does not;
    * no full sale ended it: the link is ``is_disposal`` AND a row of
      ``disposals`` is dated on its end with no retained interest (the
      warehouse's ``ended_by_disposal``).

    ``link`` has ``end_date``, ``consolidation_method``, ``is_disposal``;
    ``others`` are the node's OTHER submitted periods (``effective_date``,
    ``end_date``, ``consolidation_method``); ``disposals`` the submitted
    Business Disposals of the node's entity in its group (``disposal_date``,
    ``retained_interest_pct``). Dates are ``datetime.date`` (the caller
    normalises); a blank end is open-ended and never an exit.
    """
    end = _get(link, "end_date")
    if end is None or _get(link, "consolidation_method") not in LIVE_METHODS:
        return False
    for other in others:
        start, other_end = _get(other, "effective_date"), _get(other, "end_date")
        if (_get(other, "consolidation_method") in LIVE_METHODS and start is not None
                and (start - end).days <= 1 and (other_end is None or other_end > end)):
            return False
    if int(_get(link, "is_disposal") or 0):
        for sale in disposals:
            if _get(sale, "disposal_date") == end and float(_get(sale, "retained_interest_pct") or 0) <= 0:
                return False
    return True


def reentry_after_exit(period, others, disposals, acquisitions):
    """Whether ``period`` brings back a node that left the group, with no
    acquisition behind it (konsolidat#335 review F4, X14). A re-entry is an
    acquisition (IFRS 3): the warehouse derecognised the node when it left
    (an exit journal, or a full Business Disposal) and its retranslation
    window restarted, so nothing carries its balances back and a period that
    merely starts again is silently wrong. It needs a submitted Business
    Combination dated on its start. A re-entry after a full Business Disposal
    is refused on the same footing: the node was sold, buying it again is an
    acquisition too.

    ``period`` has ``effective_date`` and ``consolidation_method``; ``others``
    the node's OTHER submitted periods, ``disposals`` its submitted Business
    Disposals (as ``end_is_exit`` reads them), ``acquisitions`` the
    acquisition dates of its submitted Business Combinations.

    Returns None when nothing is wrong, else ``(left, is_exit)``: the day the
    latest earlier live period ended and whether that end was an exit (False:
    a full disposal). Nothing is wrong when ``period`` is not live, when no
    live period of the node ended before it, when a live period holds the
    node the day before it starts (a contiguous successor, another owner's
    link), or when a Business Combination is dated on its start."""
    start = _get(period, "effective_date")
    if start is None or _get(period, "consolidation_method") not in LIVE_METHODS:
        return None
    earlier = []
    for other in others:
        if _get(other, "consolidation_method") not in LIVE_METHODS or _get(other, "effective_date") is None:
            continue
        end = _get(other, "end_date")
        # held the day before: started before ``start``, not ended before it
        # went by (dates only; this module imports nothing)
        if _get(other, "effective_date") < start and (end is None or end >= start or (start - end).days == 1):
            return None
        if end is not None and end < start:
            earlier.append(other)
    if not earlier or start in acquisitions:
        return None
    last = max(earlier, key=lambda o: _get(o, "end_date"))
    rest = [o for o in others if o is not last]
    return _get(last, "end_date"), end_is_exit(last, rest, disposals)


def exit_and_return_in_one_period(period, others, disposals, period_of):
    """Whether ``period`` starts again in the same declared period in which
    an earlier link of its node ended in an exit (konsolidat#335 review F1,
    X18). The rules above are by day; the warehouse reads ownership at each
    declared period's end (macros/ownership_resolution.sql), so a link that
    ends 10 Mar and a live link that starts 20 Mar (one Regular period) leave
    the node held when March closes: no exit journal. Asking for the exit
    account, then for a Business Combination, would post a second acquisition
    on balances that were never derecognised. A Business Combination
    therefore does not excuse it.

    ``period_of(date)`` is the code of the declared Regular period holding
    ``date`` (None when none does: such a date is not judged). The other
    arguments are those of ``reentry_after_exit``; only an end that
    ``end_is_exit`` calls an exit counts (a full disposal and a contiguous
    successor do not).

    Returns None when nothing is wrong, else ``(end, period_code)``."""
    found = reentry_after_exit(period, others, disposals, ())
    if found is None:
        return None
    left, is_exit = found
    if not is_exit:
        return None
    code = period_of(left)
    if code is None or code != period_of(_get(period, "effective_date")):
        return None
    return left, code
