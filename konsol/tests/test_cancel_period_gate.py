"""Cancel only while the period is open, and nothing changes after submit (#136).

The convention (decided 12 Sep 2026) applied to the doctypes #136 listed.
Trial Balance Submission is the documented exception: its ClickHouse landing
and claim ARE its submit (idempotent by batch_id), and it already gates on
assert_open in validate."""
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
    "HistoricalEquityRate": "assert_open_on",
    "OwnershipPeriod": "assert_open_on",
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
        assert calls.index(GATED[cls.name]) == 0, f"{cls.name}: the gate comes before anything else"
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


def test_assert_open_on_uses_the_warehouse_date_mapping():
    """build_date_from_year_period: period P of year Y is the month starting
    Y-P-01, so a date falls in (its year, its month)."""
    with open(os.path.join(APP_DIR, "period_status.py")) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "assert_open_on")
    fn.body = [s for s in fn.body if not isinstance(s, ast.ImportFrom)]
    asked = []
    ns = {"assert_open": lambda y, p, action="run": asked.append((y, p, action)),
          "getdate": lambda v: None if v in (None, "") else datetime.date.fromisoformat(str(v))}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "period_status.py", "exec"), ns)
    ns["assert_open_on"]("2024-12-31", action="cancel x")
    ns["assert_open_on"]("2025-01-01")
    ns["assert_open_on"](None)
    assert asked == [(2024, 12, "cancel x"), (2025, 1, "run")]


def test_budget_cycle_locks_before_the_transition_and_pushes_after_the_commit():
    for rel, cls in _classes():
        if cls.name != "BudgetCycle":
            continue
        src = {name: ast.unparse(_method(cls, name)) for name in ("before_submit", "on_submit", "before_cancel", "on_cancel")}
        assert "self.status = 'Locked'" in src["before_submit"]
        assert "self.status = 'Open'" in src["before_cancel"]
        for hook in ("on_submit", "on_cancel"):
            assert "after_commit_once(" in src[hook] and "_sync_to_clickhouse" not in src[hook], hook
        return
    raise AssertionError("BudgetCycle not found")
