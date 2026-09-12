"""Cancel only while the period is open, and nothing changes after submit (#136).

The convention (decided 12 Sep 2026) applied to the doctypes #136 listed.
Trial Balance Submission's ClickHouse landing and claim stay its submit
(idempotent by batch_id); its cancel is gated like the rest."""
import ast
import datetime
import glob
import os
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATED = {
    "ConsolidationAdjustment": "assert_open",
    "ICBalance": "assert_open",
    "AllocationRun": "assert_open",
    "HistoricalEquityRate": "assert_open_between",
    "OwnershipPeriod": "assert_open_between",
    "TrialBalanceSubmission": "assert_open",
}


def _classes():
    for path in glob.glob(os.path.join(APP_DIR, "*", "doctype", "*", "*.py")):
        if path.endswith("__init__.py"):
            continue
        with open(path) as f:
            tree = ast.parse(f.read())
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            yield os.path.relpath(path, APP_DIR), cls


def _method(cls, name):
    return next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name), None)


def test_every_listed_doctype_gates_its_cancel_on_the_period():
    seen = set()
    for rel, cls in _classes():
        if cls.name not in GATED:
            continue
        seen.add(cls.name)
        fn = _method(cls, "before_cancel")
        assert fn is not None, f"{rel}: {cls.name} has no before_cancel"
        calls = [getattr(c.func, "id", None) or getattr(c.func, "attr", None)
                 for c in ast.walk(fn) if isinstance(c, ast.Call)]
        assert GATED[cls.name] in calls, (cls.name, calls)
        gate_line = next(c.lineno for c in ast.walk(fn) if isinstance(c, ast.Call)
                         and (getattr(c.func, "id", None) or getattr(c.func, "attr", None)) == GATED[cls.name])
        early = [n for n in ast.walk(fn) if isinstance(n, ast.Assign) and n.lineno < gate_line
                 and any(isinstance(t, ast.Attribute) and ast.unparse(t.value) == "self" for t in n.targets)]
        assert not early, f"{cls.name}: nothing may change before the gate"
    assert seen == set(GATED), set(GATED) - seen


def test_no_controller_db_sets_itself_on_submit_or_cancel():
    """db_set in on_submit/on_cancel changes a submitted/cancelled doc: set
    the field in before_submit/before_cancel (Budget Cycle did; #136)."""
    offenders = []
    for rel, cls in _classes():
        for hook in ("on_submit", "on_cancel"):
            fn = _method(cls, hook)
            if fn is None:
                continue
            for c in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
                f = c.func
                if isinstance(f, ast.Attribute) and f.attr == "db_set" and isinstance(f.value, ast.Name) and f.value.id == "self":
                    offenders.append(f"{rel}:{cls.name}.{hook}")
    assert not offenders, offenders


def _period_status_fn(name):
    with open(os.path.join(APP_DIR, "period_status.py")) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    fn.body = [s for s in fn.body if not isinstance(s, (ast.Import, ast.ImportFrom))]
    return fn


def test_the_first_period_a_date_affects_follows_the_warehouse():
    """The warehouse applies a record from each period start (the 1st) on or
    after its date: the 1st affects its own month, the 15th the next one."""
    ns = {"datetime": datetime,
          "getdate": lambda v: v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v))}
    exec(compile(ast.Module(body=[_period_status_fn("first_period_affected")], type_ignores=[]), "period_status.py", "exec"), ns)
    f = ns["first_period_affected"]
    assert f("2024-03-01") == datetime.date(2024, 3, 1)
    assert f("2024-03-15") == datetime.date(2024, 4, 1)
    assert f("2024-12-31") == datetime.date(2025, 1, 1)
    assert f(None) is None


def test_the_range_gate_checks_every_period_in_the_range():
    """One query for any Closed/Locked period from the first affected period
    to the end (inclusive, or exclusive for the next rate), open-ended with no end."""
    with open(os.path.join(APP_DIR, "period_status.py")) as f:
        src = f.read()
    assert "STR_TO_DATE(CONCAT(fiscal_year, '-', LPAD(fiscal_period, 2, '0'), '-01')" in src
    body = ast.unparse(_period_status_fn("assert_open_between"))
    assert "status IN %(settled)s" in body and ">= %(first)s" in body
    assert "'<' if end_exclusive else '<='" in body


def test_budget_cycle_locks_before_the_transition_and_pushes_after_the_commit():
    """The D365 push/withdraw calls are untouched (the write-back is being
    redesigned); they only run after the commit now, with the sheet sync."""
    for rel, cls in _classes():
        if cls.name != "BudgetCycle":
            continue
        src = {name: ast.unparse(_method(cls, name)) for name in ("before_submit", "on_submit", "before_cancel", "on_cancel", "_push_sheets")}
        assert "self.status = 'Locked'" in src["before_submit"]
        assert "self.status = 'Open'" in src["before_cancel"]
        for hook in ("on_submit", "on_cancel"):
            assert "after_commit_once(" in src[hook] and "_sync_to_clickhouse" not in src[hook], hook
        assert "enqueue_push_budget_sheet" in src["_push_sheets"] and "withdraw_budget_sheet" in src["_push_sheets"]
        return
    raise AssertionError("BudgetCycle not found")

