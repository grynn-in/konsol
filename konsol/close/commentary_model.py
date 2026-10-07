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

import importlib.util as _importlib_util
import os as _os


def _load_period_name():
    """konsol/close/period_name.py loaded by path (konsol#305 review-w5): the
    one "FY2025 P07" format, reachable even under the host tests' stub
    ``konsol.close`` package."""
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "period_name.py")
    spec = _importlib_util.spec_from_file_location("konsol_close_period_name", path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.period_name


period_name = _load_period_name()

#: The key that makes one commentary record unique: one row per
#: (consolidation group, fiscal year, fiscal period, heading).
KEY_FIELDS = ("consolidation_group", "fiscal_year", "fiscal_period", "heading")

PUBLISHED = "Published"
OPEN = "Open"

_PERIOD_CLOSED_SENTENCE = (
    "{period} is {status}: commentary is refused once a period "
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
            period=period_name(period["fiscal_year"], period["fiscal_period"]), status=status,
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
    don't, in ``lft`` (statement) order. Informational only (W4-E16); the
    headings 8.4's threshold requires are ``requirement``'s, below.

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


# --- konsol#305-W5-2 (story 8.4): commentary required above the threshold --
#
# Deepak, 6 Oct 2026, option 8.4-1: a statement heading whose variance
# against the comparison column (the previous Regular period, W4-E5 /
# #305-W4-7 7a) is above the declared Close Settings threshold and has no
# commentary makes the close Amber; signing needs a typed acknowledgement
# (the #265 / #305-W3-8 path, applied in signoff_model). Rejected: 8.4-2 a
# Red block; 8.4-3 every heading required. An undeclared threshold is a
# setup gap (close_policy_model.commentary_threshold), never defaulted.

#: The get_statement states a group's statement can be in. ``ok`` and
#: ``no_chart`` can be checked (no chart: no heading to require); the others
#: mean the statement could not be read, so the requirement is unknown.
STATEMENT_STATES = ("ok", "no_chart", "not_built", "error", "setup_gap")
_CHECKABLE = ("ok", "no_chart")

_EITHER = "Either is exceeded"
_BOTH = "Both are exceeded"


def _percent(variance, comparison):
    """|variance| as a percentage of |comparison|, 2 dp; None on a zero base
    (no percentage exists; ``_above`` treats any movement there as above)."""
    if not comparison:
        return None
    return round(abs(variance) / abs(comparison) * 100, 2)


def _above(threshold, variance, comparison):
    """Whether ``variance`` is strictly above ``threshold`` (close_policy_model
    .commentary_threshold's ``threshold`` dict). An undeclared part never
    triggers; with both declared the declared ``combine`` rule decides."""
    amount, percent, combine = threshold["amount"], threshold["percent"], threshold["combine"]
    amount_hit = amount is not None and abs(variance) > amount
    if percent is None:
        percent_hit = False
    elif not comparison:
        percent_hit = abs(variance) > 0
    else:
        percent_hit = abs(variance) / abs(comparison) * 100 > percent
    if amount is not None and percent is not None:
        if combine == _BOTH:
            return amount_hit and percent_hit
        if combine == _EITHER:
            return amount_hit or percent_hit
        raise ValueError("commentary threshold rule %r is not declared" % (combine,))
    return amount_hit or percent_hit


def group_requirement(threshold, statement, texts):
    """One group's headings above ``threshold`` and the ones of those with no
    commentary (``required``), in statement order (Profit and Loss, then the
    Balance Sheet; ``heading_order`` within each).

    - ``threshold``: ``close_policy_model.commentary_threshold(...)["threshold"]``.
    - ``statement``: ``statement_model.statement`` output.
    - ``texts``: ``{heading: text}`` for this group and period; blank text is
      no commentary.

    No comparison (``periods["comparison_note"]`` set: no earlier period, or
    no warehouse rows for it) is ``not_comparable``, with the statement's own
    note as ``message``: no variance exists, so nothing is above the
    threshold, and the note says why.
    """
    note = statement["periods"].get("comparison_note")
    if note:
        return {"state": "not_comparable", "message": note, "over_threshold": 0, "required": []}
    over, required = 0, []
    for section in statement["sections"]:
        for line in section["lines"]:
            if line.get("kind") != "heading":
                continue
            variance, comparison = line["variance"], line["comparison"]
            if variance is None or comparison is None:
                raise ValueError("heading %s has no variance although the comparison loaded"
                                 % line["heading"])
            if not _above(threshold, variance, comparison):
                continue
            over += 1
            if (texts.get(line["heading"]) or "").strip():
                continue
            required.append({
                "heading": line["heading"],
                "heading_name": line["heading_name"],
                "section": section["section"],
                "current": line["current"],
                "comparison": comparison,
                "variance": variance,
                "percent": _percent(variance, comparison),
            })
    return {"state": "checked", "message": None, "over_threshold": over, "required": required}


def requirement(declared, groups):
    """The sign-off's commentary-threshold line.

    - ``declared``: ``close_policy_model.commentary_threshold(...)``. A gap
      means ``state "undeclared"`` with the gap's message; ``groups`` is not
      read (the caller need not read any statement).
    - ``groups``: per root group ``{"consolidation_group", "state", "message",
      "statement", "texts"}``; ``state`` is a ``STATEMENT_STATES`` value
      (get_statement's), anything else raises ValueError.

    Returns ``{"state", "threshold", "message", "groups", "required_missing"}``.
    ``state`` is ``undeclared``, ``checked`` or ``unknown`` (some group's
    statement could not be read: ``required_missing`` is None and
    ``message`` names each such group, never counted as 0).
    """
    if declared["gap"] is not None:
        return {"state": "undeclared", "threshold": None, "message": declared["gap"]["message"],
                "groups": [], "required_missing": None}
    threshold = declared["threshold"]
    out, unread = [], []
    for group in groups or ():
        state = group["state"]
        if state not in STATEMENT_STATES:
            raise ValueError("unknown statement state %r for %s; expected one of %s"
                             % (state, group["consolidation_group"], ", ".join(STATEMENT_STATES)))
        if state == "ok":
            entry = group_requirement(threshold, group["statement"], group["texts"] or {})
        elif state == "no_chart":
            entry = {"state": "checked", "message": group["message"], "over_threshold": 0,
                     "required": []}
        else:
            entry = {"state": state, "message": group["message"], "over_threshold": None,
                     "required": []}
            unread.append("%s (%s)" % (group["consolidation_group"], group["message"]))
        out.append(dict({"consolidation_group": group["consolidation_group"]}, **entry))
    if unread:
        return {"state": "unknown", "threshold": threshold,
                "message": "The commentary threshold cannot be checked: the statement of %s "
                           "could not be read." % ", ".join(unread),
                "groups": out, "required_missing": None}
    return {"state": "checked", "threshold": threshold, "message": None, "groups": out,
            "required_missing": sum(len(g["required"]) for g in out)}
