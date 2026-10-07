"""konsol#305 story 2.4: working-day deadlines, pure (no frappe, no konsol).

Decision #305-2.4-1 (Deepak Pai, 6 Oct 2026): a group-wide working-day offset
per step (TB, IC, journals, sign-off) from period end; a declared working
week (no Mon–Fri default) and a declared holiday list; effective-dated by
``valid_from``; undeclared reads "No due date declared"; overdue is shown
only, never blocks. Rejected: #305-2.4-2 per-entity overrides; #305-2.4-3
calendar days.

Engineering calls (plan-w5b.md §3):
- C-D2: the governing rule is the one with the latest ``valid_from`` on or
  before the period's ``end_date``; none means every step is undeclared.
- C-D3: the due date is the Nth working day AFTER ``end_date``; a working day
  is a ticked weekday that is not a declared holiday. An offset of 0 or blank
  is undeclared (an unset Int reads 0). The count stops after 3,660 calendar
  days with an error, never a guess.
- C-D4: "overdue" is ``past`` AND the step still open; the caller decides it.
  For IC and journals the "open" rule is ``ic_open`` / ``journals_open``
  (#305-Q5-1, konsol#305 D57b).

Rules are dicts carrying the "Close Deadline Rule" child fields; dates are
``datetime.date``.
"""
from datetime import timedelta

STEPS = ("tb", "ic", "journals", "signoff")
OFFSET_FIELD = {
    "tb": "tb_due_days",
    "ic": "ic_due_days",
    "journals": "journals_due_days",
    "signoff": "signoff_due_days",
}
# The Int field labels on "Close Deadline Rule" (plan-w5b.md §4b).
STEP_LABEL = {
    "tb": "Trial Balances",
    "ic": "Intercompany",
    "journals": "Journals",
    "signoff": "Sign-off",
}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
UNDECLARED = "No due date declared"

MAX_DAYS = 3660
TEN_YEARS_ERROR = "Due date could not be computed: no working day in ten years"


def governing_rule(rules, end_date):
    """The rule with the latest ``valid_from <= end_date``, else None (C-D2).

    Two rules that start on the same day are ambiguous: raise, never pick one
    (D52 refuses that save).
    """
    best = None
    for rule in rules:
        start = rule["valid_from"]
        if start > end_date:
            continue
        if best is not None and start == best["valid_from"]:
            raise ValueError("Two deadline rules start on %s: keep one." % start.isoformat())
        if best is None or start > best["valid_from"]:
            best = rule
    return best


def due_date(end_date, offset, working_days, holidays):
    """The ``offset``-th day after ``end_date`` that is a working day (C-D3).

    ``working_days`` is a set of weekday indexes (Monday 0), ``holidays`` a
    set of dates. Raises ValueError past 3,660 calendar days.
    """
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 1:
        raise ValueError("A due-date offset must be a whole number of working days, at least 1: got %r" % (offset,))
    holidays = set(holidays)
    found = 0
    day = end_date
    for _ in range(MAX_DAYS):
        day = day + timedelta(days=1)
        if day.weekday() in working_days and day not in holidays:
            found += 1
            if found == offset:
                return day
    raise ValueError(TEN_YEARS_ERROR)


def _undeclared():
    return {"due": None, "past": False, "text": UNDECLARED}


def period_deadlines(rules, holidays, end_date, today):
    """``{step: {"due": date|None, "past": bool, "text": str}}`` for one period."""
    rule = governing_rule(rules, end_date)
    if rule is None:
        return {step: _undeclared() for step in STEPS}
    working_days = {i for i, wd in enumerate(WEEKDAYS) if rule.get(wd)}
    holidays = set(holidays)
    out = {}
    for step in STEPS:
        offset = rule.get(OFFSET_FIELD[step])
        if offset in (None, "", 0):
            out[step] = _undeclared()
            continue
        due = due_date(end_date, offset, working_days, holidays)
        out[step] = {"due": due, "past": today > due, "text": "Due %s" % due.isoformat()}
    return out


# --- D57b: when the IC and journals steps are still open (#305-Q5-1) ---------
# Decision #305-Q5-1 (Deepak Pai, 7 Oct 2026): the IC step is done when no
# over-tolerance pair is open; the journals step is done when no Consolidation
# Journal of the period is a draft or pending approval (docstatus 0). Overdue
# shows for both. Rejected: Q5-2 (show the due date only). The grid decides
# ``ic_overdue`` / ``journals_overdue`` = past AND open (C-D4) from these two
# helpers only; no other surface re-derives them.

#: ic_model states in which there is no reconciled pair to be open: no
#: Published Intercompany Account (nothing to pair), or Close Settings declares
#: no intercompany in this group. Their own setup line shows elsewhere.
IC_NOTHING_TO_OPEN = ("not_configured", "not_applicable")
#: ic_model states in which the warehouse could not be read.
IC_UNREADABLE = ("error", "not_built")


def ic_open(state, counts):
    """True when at least one intercompany pair is over tolerance (#305-Q5-1).

    ``state`` and ``counts`` are what ``ic_api.signoff_summary`` returns
    (``ic_model.signoff_line``). An unreadable warehouse raises ValueError,
    never False; so does a "checked" read without a readable
    ``over_tolerance`` count, or an unknown state.
    """
    if state in IC_NOTHING_TO_OPEN:
        return False
    if state in IC_UNREADABLE:
        raise ValueError("Intercompany could not be read (%s), so whether a pair is still "
                         "over tolerance is unknown" % state)
    if state != "checked":
        raise ValueError("unknown intercompany state %r" % (state,))
    count = (counts or {}).get("over_tolerance")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("Intercompany was checked but its over-tolerance count is not "
                         "readable: %r" % (count,))
    return count > 0


def journals_open(open_count):
    """True when at least one Consolidation Journal of the period is a draft
    or pending approval (docstatus 0) (#305-Q5-1). ``open_count`` must be a
    non-negative int; anything else raises ValueError, never False."""
    if isinstance(open_count, bool) or not isinstance(open_count, int) or open_count < 0:
        raise ValueError("The count of draft or pending journals is not readable: %r"
                         % (open_count,))
    return open_count > 0


def _repeated(values):
    """Each value that appears more than once, once, in first-seen order."""
    seen, repeated = set(), []
    for value in values:
        if value in seen and value not in repeated:
            repeated.append(value)
        seen.add(value)
    return repeated


def rule_problems(rules, holidays):
    """The sentences that refuse a Close Settings save (D52; C-D1, C-D3).

    ``rules`` are "Close Deadline Rule" child dicts, ``holidays`` are "Close
    Holiday" child dicts (``holiday_date``). Empty tables are allowed: no rule
    means no due dates, and nothing is invented. A blank or 0 offset is
    undeclared, not a problem. The blank ``change_reason`` is refused by
    ``reqd`` on the child doctype (C-D7), not here.
    """
    problems = []
    for start in _repeated([rule["valid_from"] for rule in rules]):
        problems.append("Two deadline rules start on %s: keep one." % start)
    for rule in rules:
        start = rule["valid_from"]
        if not any(rule.get(wd) for wd in WEEKDAYS):
            problems.append(
                "The rule from %s declares no working day: tick the days your team works." % start
            )
        for step in STEPS:
            offset = rule.get(OFFSET_FIELD[step])
            if isinstance(offset, (int, float)) and not isinstance(offset, bool) and offset < 0:
                problems.append("The rule from %s has a negative %s offset." % (start, STEP_LABEL[step]))
    for day in _repeated([holiday["holiday_date"] for holiday in holidays]):
        problems.append("%s is listed twice as a holiday." % day)
    return problems
