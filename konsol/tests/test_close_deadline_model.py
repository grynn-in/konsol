"""konsol#305 story 2.4 (#305-2.4-1): deadline_model part 1, pure.

``konsol/close/deadline_model.py`` is pure: loaded by path, no frappe or konsol
import. Every surface (TB list, Grid, My work) reads a period's due dates from
it (D55 is the one frappe-bound reader).

Decision pinned here: #305-2.4-1 (Deepak Pai, 6 Oct 2026) — a group-wide
working-day offset per step (TB, IC, journals, sign-off) from period end; a
declared working week (no Mon–Fri default) and a declared holiday list;
effective-dated by ``valid_from``; undeclared reads "No due date declared";
overdue is shown only, never blocks. Rejected: #305-2.4-2 per-entity
overrides; #305-2.4-3 calendar days.

Engineering calls: C-D2 (governing rule), C-D3 (Nth working day after
end_date; 0/blank = undeclared; ten-year stop), C-D4 (overdue = past AND step
open, decided by the caller, never here).
"""
import ast
import importlib.util
import os
from datetime import date, timedelta

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PY = os.path.join(APP_DIR, "close", "deadline_model.py")
_spec = importlib.util.spec_from_file_location("deadline_model_under_test", MODEL_PY)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

MON_FRI = {0, 1, 2, 3, 4}
SUN_THU = {6, 0, 1, 2, 3}


def _rule(valid_from, days, tb=None, ic=None, journals=None, signoff=None):
    r = {"valid_from": valid_from, "change_reason": "test"}
    for i, wd in enumerate(M.WEEKDAYS):
        r[wd] = 1 if i in days else 0
    r["tb_due_days"] = tb
    r["ic_due_days"] = ic
    r["journals_due_days"] = journals
    r["signoff_due_days"] = signoff
    return r


def test_module_imports_no_frappe():
    with open(MODEL_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in ("frappe", "konsol"), alias.name
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("frappe", "konsol"), node.module


def test_constants():
    assert M.STEPS == ("tb", "ic", "journals", "signoff")
    assert M.OFFSET_FIELD == {
        "tb": "tb_due_days",
        "ic": "ic_due_days",
        "journals": "journals_due_days",
        "signoff": "signoff_due_days",
    }
    assert M.WEEKDAYS == (
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    )
    assert M.UNDECLARED == "No due date declared"
    # The Int field labels of "Close Deadline Rule" (plan-w5b.md §4b); D52's
    # sentences and D53's doctype both use them.
    assert M.STEP_LABEL == {
        "tb": "Trial Balances",
        "ic": "Intercompany",
        "journals": "Journals",
        "signoff": "Sign-off",
    }


# ---- due_date (C-D3) --------------------------------------------------------

def test_due_date_mon_fri_no_holiday():
    assert date(2025, 7, 31).weekday() == 3  # Thursday
    assert M.due_date(date(2025, 7, 31), 5, MON_FRI, set()) == date(2025, 8, 7)


def test_due_date_skips_a_holiday():
    assert M.due_date(date(2025, 7, 31), 5, MON_FRI, {date(2025, 8, 4)}) == date(2025, 8, 8)


def test_due_date_follows_the_declared_week_not_mon_fri():
    # Sun–Thu: Fri 1 Aug and Sat 2 Aug are not working days.
    assert M.due_date(date(2025, 7, 31), 1, MON_FRI, set()) == date(2025, 8, 1)
    assert M.due_date(date(2025, 7, 31), 1, SUN_THU, set()) == date(2025, 8, 3)
    assert M.due_date(date(2025, 7, 31), 6, MON_FRI, set()) == date(2025, 8, 8)
    assert M.due_date(date(2025, 7, 31), 6, SUN_THU, set()) == date(2025, 8, 10)


def test_due_date_counts_after_end_date_not_on_it():
    # end_date itself is a working day (Thu) but is never counted.
    assert M.due_date(date(2025, 7, 31), 1, {3}, set()) == date(2025, 8, 7)


def test_due_date_ten_year_stop_raises():
    mondays = set()
    d = date(2025, 8, 4)
    for _ in range(600):
        mondays.add(d)
        d += timedelta(days=7)
    try:
        M.due_date(date(2025, 7, 31), 1, {0}, mondays)
    except ValueError as e:
        assert str(e) == "Due date could not be computed: no working day in ten years"
    else:
        raise AssertionError("expected the ten-year ValueError")


def test_due_date_no_working_day_raises_the_ten_year_error():
    try:
        M.due_date(date(2025, 7, 31), 1, set(), set())
    except ValueError as e:
        assert str(e) == "Due date could not be computed: no working day in ten years"
    else:
        raise AssertionError("expected the ten-year ValueError")


def test_due_date_refuses_a_non_positive_offset():
    for bad in (0, -1, None):
        try:
            M.due_date(date(2025, 7, 31), bad, MON_FRI, set())
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for offset %r" % (bad,))


# ---- governing_rule (C-D2) --------------------------------------------------

def test_governing_rule_latest_valid_from_on_or_before_end():
    first = _rule(date(2025, 1, 1), MON_FRI, tb=5)
    second = _rule(date(2025, 7, 1), MON_FRI, tb=3)
    rules = [second, first]
    assert M.governing_rule(rules, date(2025, 6, 30)) is first
    assert M.governing_rule(rules, date(2025, 7, 31)) is second
    # on the day itself counts
    assert M.governing_rule(rules, date(2025, 7, 1)) is second


def test_governing_rule_none_before_the_first_rule():
    rules = [_rule(date(2025, 1, 1), MON_FRI, tb=5)]
    assert M.governing_rule(rules, date(2024, 12, 31)) is None
    assert M.governing_rule([], date(2025, 7, 31)) is None


def test_governing_rule_two_rules_same_start_raise():
    rules = [_rule(date(2025, 1, 1), MON_FRI, tb=5), _rule(date(2025, 1, 1), SUN_THU, tb=3)]
    try:
        M.governing_rule(rules, date(2025, 7, 31))
    except ValueError as e:
        assert "2025-01-01" in str(e)
    else:
        raise AssertionError("expected ValueError: two rules start on the same day")


# ---- period_deadlines -------------------------------------------------------

def test_period_deadlines_no_rule_all_undeclared():
    out = M.period_deadlines([], set(), date(2025, 7, 31), date(2030, 1, 1))
    assert set(out) == set(M.STEPS)
    for step in M.STEPS:
        assert out[step] == {"due": None, "past": False, "text": M.UNDECLARED}


def test_period_deadlines_rule_starts_later_all_undeclared():
    rules = [_rule(date(2025, 8, 1), MON_FRI, tb=5, ic=5, journals=5, signoff=5)]
    out = M.period_deadlines(rules, set(), date(2025, 7, 31), date(2030, 1, 1))
    for step in M.STEPS:
        assert out[step] == {"due": None, "past": False, "text": M.UNDECLARED}


def test_period_deadlines_zero_or_blank_offset_is_undeclared():
    rules = [_rule(date(2025, 1, 1), MON_FRI, tb=5, ic=0, journals=None, signoff="")]
    out = M.period_deadlines(rules, set(), date(2025, 7, 31), date(2025, 8, 1))
    assert out["tb"] == {"due": date(2025, 8, 7), "past": False, "text": "Due 2025-08-07"}
    for step in ("ic", "journals", "signoff"):
        assert out[step] == {"due": None, "past": False, "text": M.UNDECLARED}


def test_period_deadlines_each_step_uses_its_own_offset():
    rules = [_rule(date(2025, 1, 1), MON_FRI, tb=1, ic=2, journals=3, signoff=5)]
    out = M.period_deadlines(rules, {date(2025, 8, 4)}, date(2025, 7, 31), date(2025, 7, 31))
    assert out["tb"]["due"] == date(2025, 8, 1)
    assert out["ic"]["due"] == date(2025, 8, 5)
    assert out["journals"]["due"] == date(2025, 8, 6)
    assert out["signoff"]["due"] == date(2025, 8, 8)


def test_period_deadlines_uses_the_governing_rule():
    first = _rule(date(2025, 1, 1), MON_FRI, tb=5)
    second = _rule(date(2025, 7, 1), SUN_THU, tb=1)
    out_jun = M.period_deadlines([first, second], set(), date(2025, 6, 30), date(2025, 6, 30))
    out_jul = M.period_deadlines([first, second], set(), date(2025, 7, 31), date(2025, 7, 31))
    assert out_jun["tb"]["due"] == date(2025, 7, 7)
    assert out_jul["tb"]["due"] == date(2025, 8, 3)


def test_period_deadlines_past_is_strictly_after_due():
    rules = [_rule(date(2025, 1, 1), MON_FRI, tb=5)]
    on_due = M.period_deadlines(rules, set(), date(2025, 7, 31), date(2025, 8, 7))
    assert on_due["tb"] == {"due": date(2025, 8, 7), "past": False, "text": "Due 2025-08-07"}
    day_after = M.period_deadlines(rules, set(), date(2025, 7, 31), date(2025, 8, 8))
    assert day_after["tb"] == {"due": date(2025, 8, 7), "past": True, "text": "Due 2025-08-07"}


def test_period_deadlines_ten_year_error_propagates():
    mondays = set()
    d = date(2025, 8, 4)
    for _ in range(600):
        mondays.add(d)
        d += timedelta(days=7)
    rules = [_rule(date(2025, 1, 1), {0}, tb=1)]
    try:
        M.period_deadlines(rules, mondays, date(2025, 7, 31), date(2025, 8, 1))
    except ValueError as e:
        assert str(e) == "Due date could not be computed: no working day in ten years"
    else:
        raise AssertionError("expected the ten-year ValueError")


def test_period_deadlines_has_no_overdue_key():
    # C-D4: "overdue" (past AND step open) is the caller's decision.
    rules = [_rule(date(2025, 1, 1), MON_FRI, tb=5)]
    out = M.period_deadlines(rules, set(), date(2025, 7, 31), date(2026, 1, 1))
    for step in M.STEPS:
        assert set(out[step]) == {"due", "past", "text"}
