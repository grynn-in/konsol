"""Which approvals change the numbers, and which periods (pure):
konsol/close/data_change_model.py (konsol#305 S41).

- stories: 9.2; #305-W4-4 option 4c (widen R2b); #305-R2b / R2b-3; W4-E13.
- AMENDED 4 Oct 2026 (Deepak "all ★", #305 issuecomment-5978983396), which
  overrides the row as originally written:
  - ``NUMBER_DRIVING`` is every ``close_policy_model.APPROVAL_DOCTYPES``
    doctype (all 7, IC Balance included) — not six of seven. A submit
    (approval), a cancel, and a Consolidation Journal's Reverse (which this
    codebase already records as that doctype's ``on_cancel``, see
    ``cancel_event.py``'s docstring: "submit is the approval, cancel the
    reversal") all count; S42 wires the hook that calls this on both events.
  - The affected periods are the changed period PLUS every later period
    (a balance sheet is cumulative, #305-W4-7): this module returns that
    full, structural list. It does not know which of those periods have a
    *signed* run — that is live, frappe-bound state — so the caller
    (S42 -> ``signoff_gate.record_data_change``, which already guards a
    non-Regular or pre-first-close period by marking nothing) decides which
    of the returned periods are actually signed and need "Re-sign Needed".

Imports nothing from frappe or konsol, so it stays host-testable with no
site. Loaded by path in konsol/tests/test_close_data_change_model.py,
mirroring konsol/tests/test_close_journal_model.py.
"""

#: The doctypes whose submit (approval) or cancel changes the numbers a
#: period's statements and checks read (#305-W4-4, AMENDED 4 Oct: every
#: close_policy_model.APPROVAL_DOCTYPES doctype, IC Balance included). Kept
#: as its own tuple, not an import of close_policy_model, so this module
#: stays frappe/konsol-free (test_close_data_change_model.py pins it equal
#: to close_policy_model.APPROVAL_DOCTYPES, loaded separately by path).
NUMBER_DRIVING = (
    "Consolidation Journal",
    "Business Combination",
    "Business Disposal",
    "Group Exchange Rate",
    "Ownership Period",
    "Historical Equity Rate",
    "IC Balance",
)

#: change_text's recognised actions (approval_api.approve / a cancel).
_ACTIONS = ("approved", "cancelled")


def changed_periods(doctype, period, period_rows, reverse=None):
    """The sorted, de-duplicated list of ``(fiscal_year, fiscal_period)``
    keys whose data changed when one ``doctype`` document was submitted or
    cancelled in ``period`` (``close_event.period_of(doc)``'s key).

    A doctype outside ``NUMBER_DRIVING`` returns ``[]``.

    Otherwise the result is ``period`` itself, plus ``reverse`` (a
    ``(reverse_fiscal_year, reverse_fiscal_period)`` pair, when both are
    non-zero — a blank Int reads as 0, journal_model.reversal_pair_problem's
    convention) when given, plus every period in ``period_rows`` (dicts
    carrying at least ``fiscal_year`` and ``fiscal_period``, the keys
    ``fiscal_calendar.fiscal_period_rows()`` returns) that is later than
    ``period`` — a change to one period's data moves every cumulative
    balance sheet after it (#305-W4-7), so it is "affected" too, whatever
    its period_type or signoff state. ``reverse`` is folded in explicitly
    because a reversal always posts into a period after ``period`` (the
    journal's own validate() refuses otherwise), so it would already be
    "later" if it is declared in ``period_rows`` — but it is included even
    when ``period_rows`` is sparse and does not carry it.

    This module does not filter by period_type or by whether a period has
    a signed run: that needs live state this pure module never sees. The
    caller applies it (S42, through signoff_gate.record_data_change, which
    already marks nothing for a non-Regular or pre-first-close period).
    """
    if doctype not in NUMBER_DRIVING:
        return []
    fiscal_year, fiscal_period = period
    primary_keys = {(int(fiscal_year), int(fiscal_period))}
    if reverse:
        reverse_year, reverse_period = reverse
        if reverse_year and reverse_period:
            primary_keys.add((int(reverse_year), int(reverse_period)))
    earliest = min(primary_keys)
    later_keys = {
        (int(row["fiscal_year"]), int(row["fiscal_period"]))
        for row in period_rows
        if (int(row["fiscal_year"]), int(row["fiscal_period"])) > earliest
    }
    return sorted(primary_keys | later_keys)


def change_text(doctype, name, action):
    """"<doctype> <name> <action>" (``record_data_change`` appends " at
    <time> by <user>", signoff_gate.py:375-380). ``action`` must be
    "approved" or "cancelled"; anything else raises ValueError (fail
    closed: never guess a verb for a state this module does not know)."""
    if action not in _ACTIONS:
        raise ValueError(
            f"'{action}' is not a recognised data-change action "
            f"(expected one of {_ACTIONS})."
        )
    return f"{doctype} {name} {action}"
