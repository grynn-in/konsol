"""Fiscal status precedence, pure: konsol/fiscal_status_model.py.

Loaded by path; the module imports nothing from frappe or konsol.
"""
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("fsm_under_test", os.path.join(APP_DIR, "fiscal_status_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def test_the_year_wins():
    O, C, L = M.OPEN, M.CLOSED, M.LOCKED
    cases = {
        (O, O): O, (O, C): C, (O, L): L,
        (C, O): C, (C, C): C, (C, L): L,
        (L, O): L, (L, C): L, (L, L): L,
    }
    for (year_status, row_status), want in cases.items():
        assert M.effective_status(year_status, row_status) == want, (year_status, row_status)


def test_transitions_by_role():
    O, C, L = M.OPEN, M.CLOSED, M.LOCKED
    ADMIN = {"EPM Admin"}
    SYS_MGR = {"System Manager"}
    ANALYST = {"EPM Analyst"}

    # (current, new) -> which of ADMIN/SYS_MGR/ANALYST are allowed
    matrix = {
        (O, O): {"admin", "sys_mgr", "analyst"},
        (C, C): {"admin", "sys_mgr", "analyst"},
        (L, L): {"admin", "sys_mgr", "analyst"},
        (O, C): {"admin", "sys_mgr"},
        (O, L): {"admin", "sys_mgr"},
        (C, L): {"admin", "sys_mgr"},
        (C, O): {"admin", "sys_mgr"},
        (L, O): {"sys_mgr"},
        (L, C): {"sys_mgr"},
    }
    roles_by_name = {"admin": ADMIN, "sys_mgr": SYS_MGR, "analyst": ANALYST}

    for (current, new), allowed_names in matrix.items():
        for role_name, roles in roles_by_name.items():
            reason = M.transition_problem(current, new, roles)
            if role_name in allowed_names:
                assert reason is None, (current, new, role_name, reason)
            else:
                assert reason is not None, (current, new, role_name)
                assert current in reason or new in reason, (current, new, role_name, reason)
                if role_name != "analyst":
                    needed = "System Manager" if allowed_names == {"sys_mgr"} else "EPM Admin"
                    assert needed in reason or "System Manager" in reason, reason

    reason = M.transition_problem(L, O, ADMIN)
    assert "System Manager" in reason
    assert "Reopening" in reason
    assert "Locked" in reason

    # a role not listed at all behaves like EPM Analyst: never allowed,
    # except the same -> same no-op
    UNLISTED = {"Some Other Role"}
    assert M.transition_problem(O, O, UNLISTED) is None
    for current, new in matrix:
        if current == new:
            continue
        assert M.transition_problem(current, new, UNLISTED) is not None

    # no roles at all: never allowed except the no-op
    assert M.transition_problem(C, C, set()) is None
    assert M.transition_problem(O, C, set()) is not None


def test_transition_problem_rejects_unknown_status():
    try:
        M.transition_problem("Bogus", M.OPEN, {"System Manager"})
    except ValueError as e:
        assert "Bogus" in str(e)
    else:
        raise AssertionError("not refused")

    try:
        M.transition_problem(M.OPEN, "Bogus", {"System Manager"})
    except ValueError as e:
        assert "Bogus" in str(e)
    else:
        raise AssertionError("not refused")


def test_row_not_looser_than_year():
    O, C, L = M.OPEN, M.CLOSED, M.LOCKED

    problems = M.row_problems(C, [{"code": "P1", "status": O}], {"P1"})
    assert len(problems) == 1
    assert "P1" in problems[0]

    problems = M.row_problems(L, [{"code": "P2", "status": C}], {"P2"})
    assert len(problems) == 1
    assert "P2" in problems[0]

    problems = M.row_problems(O, [{"code": "P3", "status": C}], {"P3"})
    assert problems == []


def test_no_new_row_in_closed_year():
    O, C, L = M.OPEN, M.CLOSED, M.LOCKED

    problems = M.row_problems(C, [{"code": "NEW", "status": C}], {"P1"})
    assert len(problems) == 1
    assert "NEW" in problems[0]
    assert C in problems[0]

    problems = M.row_problems(L, [{"code": "NEW", "status": L}], {"P1"})
    assert len(problems) == 1
    assert "NEW" in problems[0]
    assert L in problems[0]

    problems = M.row_problems(O, [{"code": "NEW", "status": O}], {"P1"})
    assert problems == []

    # a brand-new fiscal year has no previously saved rows: no new-row check
    problems = M.row_problems(C, [{"code": "NEW", "status": C}], None)
    assert problems == []


def test_blank_status_refused():
    """konsol#189 PR #191 review, finding 4: Frappe keeps a blank "" status
    on a REST save (update_if_missing only fills None); row_problems must
    report it as a normal problem naming the row code, not let it through
    or raise an uncaught ValueError."""
    O = M.OPEN

    # a blank status on a row is refused, naming the row's code
    problems = M.row_problems(O, [{"code": "P04", "status": ""}], {"P04"})
    assert problems
    assert "P04" in problems[0]

    # a blank status on the year is refused too
    problems = M.row_problems("", [{"code": "P04", "status": O}], {"P04"})
    assert problems

    # case matters: "open" (lowercase) is not one of Open/Closed/Locked
    problems = M.row_problems("open", [{"code": "P04", "status": "open"}], {"P04"})
    assert problems
    assert any("P04" in p for p in problems)


def test_unknown_status_raises():
    try:
        M.effective_status("Bogus", M.OPEN)
    except ValueError as e:
        assert "Bogus" in str(e)
    else:
        raise AssertionError("not refused")

    try:
        M.effective_status(M.OPEN, None)
    except ValueError as e:
        assert "None" in str(e)
    else:
        raise AssertionError("not refused")

    try:
        M.effective_status(M.OPEN, "")
    except ValueError:
        pass
    else:
        raise AssertionError("not refused")
