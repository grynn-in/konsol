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
- F3 (review S17): the SPA owns the date wording (close-ui dueDate.js), so a
  dated step's ``text`` is None; only an undeclared step carries a sentence
  (``UNDECLARED``). No surface shows a server-formatted date.

#509 (option D, chosen under Deepak Pai's standing rule of 5 Nov 2026):
holidays are declared per calendar year in a "Close Holiday Calendar". A year
nobody declared must not count as working days, so when the count reaches a
day in an undeclared year the step has no due date and reads
``HOLIDAYS_UNDECLARED`` ("Holidays not declared for 2027: add a Close Holiday Calendar for 2027."). A year declared
with no holidays counts every ticked weekday. Rejected: A keep the flat list,
B infer a year from one date, C a years table beside the list.

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
HOLIDAYS_UNDECLARED = "Holidays not declared for {year}: add a Close Holiday Calendar for {year}."
MIN_YEAR, MAX_YEAR = 1970, 2149  # ClickHouse Date range, as the rest of konsol

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


class UndeclaredYear(ValueError):
    """The count reached a day in a year with no Close Holiday Calendar (#509)."""

    def __init__(self, year):
        super().__init__(HOLIDAYS_UNDECLARED.format(year=year))
        self.year = year


def due_date(end_date, offset, working_days, holidays, declared_years):
    """The ``offset``-th day after ``end_date`` that is a working day (C-D3).

    ``working_days`` is a set of weekday indexes (Monday 0), ``holidays`` a
    set of dates, ``declared_years`` the years that have a calendar. Raises
    ``UndeclaredYear`` (a ValueError) when the count reaches a day in a year
    not declared, and ValueError past 3,660 calendar days.
    """
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 1:
        raise ValueError("A due-date offset must be a whole number of working days, at least 1: got %r" % (offset,))
    holidays = set(holidays)
    declared_years = set(declared_years)
    found = 0
    day = end_date
    for _ in range(MAX_DAYS):
        day = day + timedelta(days=1)
        if day.year not in declared_years:
            raise UndeclaredYear(day.year)
        if day.weekday() in working_days and day not in holidays:
            found += 1
            if found == offset:
                return day
    raise ValueError(TEN_YEARS_ERROR)


def _undeclared():
    return {"due": None, "past": False, "text": UNDECLARED}


def period_deadlines(rules, holidays, end_date, today, declared_years):
    """``{step: {"due": date|None, "past": bool, "text": str|None}}`` for one
    period. ``text`` is ``UNDECLARED`` for an undeclared rule or offset, and
    ``HOLIDAYS_UNDECLARED`` when the count reaches a year with no calendar
    (#509); it is None for a dated step (F3). ``declared_years`` has no
    default: a caller that forgets it fails, it does not guess."""
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
        try:
            due = due_date(end_date, offset, working_days, holidays, declared_years)
        except UndeclaredYear as err:
            out[step] = {"due": None, "past": False, "text": str(err)}
            continue
        out[step] = {"due": due, "past": today > due, "text": None}
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


def rule_problems(rules):
    """The sentences that refuse a Close Settings save (D52; C-D1, C-D3).

    ``rules`` are "Close Deadline Rule" child dicts. An empty table is
    allowed: no rule means no due dates, and nothing is invented. A blank or 0 offset is
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
    return problems


def calendar_problems(year, no_holidays, dates):
    """The sentences that refuse a Close Holiday Calendar save (#509).

    ``dates`` are the ``datetime.date`` of its holiday rows. A year is
    declared either with its holidays or with ``no_holidays`` ticked, never
    both and never neither.
    """
    if isinstance(year, bool) or not isinstance(year, int) or not MIN_YEAR <= year <= MAX_YEAR:
        return ["The year must be between %d and %d: got %s." % (MIN_YEAR, MAX_YEAR, year)]
    problems = []
    if any(day is None for day in dates):
        # Frappe runs validate before the child's reqd check (review of #512).
        problems.append("A holiday row has no date: fill it in or delete the row.")
        return problems
    if no_holidays and dates:
        n = len(dates)
        problems.append("No Holidays This Year is ticked but %d holiday%s: remove %s or untick."
                        % (n, " is listed" if n == 1 else "s are listed", "it" if n == 1 else "them"))
    if not no_holidays and not dates:
        problems.append("List the holidays for %d, or tick No Holidays This Year." % year)
    for day in dates:
        if day.year != year:
            problems.append("%s is not in %d." % (day.isoformat(), year))
    for day in _repeated(dates):
        problems.append("%s is listed twice." % day.isoformat())
    return problems
