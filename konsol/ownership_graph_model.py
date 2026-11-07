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
