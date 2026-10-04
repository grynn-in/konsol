"""Statement commentary: save rules, the stale-edit check, the Close Event
detail and the per-group missing-commentary count. Pure (konsol#305 M42;
stories 8.3, 9.1; #305-W4-5 5b, W4-6 6a; D2-4; W4-E14, W4-E15, W4-E16).

Imports nothing from frappe or konsol; the caller (the Statement Commentary
controller, M43) passes every input:

- ``period``: a ``fiscal_calendar.fiscal_period_rows()`` row — its
  ``status`` is already the EFFECTIVE status (fiscal_status_model). Commentary
  is refused once the period is Closed or Locked (#305-W4-5 5b); Open, signed
  off or not, always passes.
- ``heading_row``: ``frappe.db.get_value("Main Account", heading,
  ["is_group", "status", "account_name"], as_dict=True)``, or None when the
  code does not exist. Commentary attaches only to a Published heading
  (Main Account with Is Group), never a leaf, a Draft/Inactive row, or a
  code that is not in the chart at all (D2-4, W4-E15).
- ``group_is_root``: whether ``group`` is a root Consolidation Group (no
  ``data_area_id``); commentary is a group-level record, never entity-scoped.

``save_problems`` never throws; it returns every problem it finds (zero or
more), for the caller to join and throw (mirrors close_event_model's
``event_problems``). No policy default: a missing or wrong input is a named
gap, never guessed past.
"""

#: The key that makes one commentary record unique: one row per
#: (consolidation group, fiscal year, fiscal period, heading).
KEY_FIELDS = ("consolidation_group", "fiscal_year", "fiscal_period", "heading")

PUBLISHED = "Published"
OPEN = "Open"

_PERIOD_CLOSED_SENTENCE = (
    "FY{fiscal_year} P{fiscal_period:02d} is {status}: commentary is refused once a period "
    "is closed (#305-W4-5). Reopen the period to change its commentary."
)
_NOT_A_HEADING_SENTENCE = (
    "{heading} is not a statement heading of the group chart: commentary attaches to a "
    "heading (Main Account with Is Group), D2-4."
)
_NOT_A_GROUP_SENTENCE = "{group} is not a consolidation group."
_STALE_SENTENCE = "{by} changed this commentary at {at}: reload it and edit again."


def save_problems(period, heading, heading_row, group, group_is_root):
    """Every reason saving this commentary is refused, as sentences; an
    empty list means the save is allowed.

    - ``period`` not Open (effective status) → the period is Closed/Locked.
    - ``heading_row`` None, a leaf (``is_group`` falsy), or not Published →
      not a statement heading.
    - ``group_is_root`` False → not a consolidation group.
    """
    problems = []

    status = period["status"]
    if status != OPEN:
        problems.append(_PERIOD_CLOSED_SENTENCE.format(
            fiscal_year=period["fiscal_year"], fiscal_period=period["fiscal_period"], status=status,
        ))

    if (heading_row is None
            or not heading_row.get("is_group")
            or heading_row.get("status") != PUBLISHED):
        problems.append(_NOT_A_HEADING_SENTENCE.format(heading=heading))

    if not group_is_root:
        problems.append(_NOT_A_GROUP_SENTENCE.format(group=group))

    return problems


def stale_problem(sent_modified, current_modified, by, at):
    """None when the save may proceed (a first save, or the editor's sent
    token matches the document's current one); otherwise the sentence
    naming who changed it and when. A sent token for a document that does
    not exist, or a missing token for one that does, is stale too — only
    "both None" (first save) and "equal" (unchanged) are safe."""
    if sent_modified is None and current_modified is None:
        return None
    if sent_modified == current_modified:
        return None
    return _STALE_SENTENCE.format(by=by, at=at)


def event_detail(consolidation_group, heading, heading_name, text):
    """The ``commentary_saved`` Close Event detail (M41). A blank save
    clears the commentary and is recorded as ``text: ""`` (W4-E14) — never
    omitted or replaced by a guess."""
    return {
        "consolidation_group": consolidation_group,
        "heading": heading,
        "heading_name": heading_name,
        "text": text or "",
    }


def missing_commentary(headings, rows, groups):
    """Per root group in ``groups``, how many of ``headings`` (Published,
    is_group) have non-blank commentary, and the names of the ones that
    don't, in ``lft`` (statement) order. Informational only (W4-E16; 8.4's
    threshold is a later row).

    - ``headings``: ``[{heading, heading_name, lft}]``.
    - ``rows``: ``[{consolidation_group, heading, text}]``; a blank
      (or missing) ``text`` counts as missing commentary.
    """
    ordered = sorted(headings, key=lambda h: h["lft"])
    texts = {}
    for row in rows:
        texts[(row["consolidation_group"], row["heading"])] = (row.get("text") or "").strip()

    result = []
    for group in groups:
        missing = []
        with_commentary = 0
        for heading in ordered:
            text = texts.get((group, heading["heading"]), "")
            if text:
                with_commentary += 1
            else:
                missing.append(heading["heading_name"])
        result.append({
            "consolidation_group": group,
            "headings": len(ordered),
            "with_commentary": with_commentary,
            "missing": missing,
        })
    return result
