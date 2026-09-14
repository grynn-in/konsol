"""Cancel only while the period is open, and nothing changes after submit (#136).

The convention (decided 12 Sep 2026) applied to the doctypes #136 listed.
Trial Balance Submission's ClickHouse landing and claim stay its submit
(idempotent by batch_id); its cancel is gated like the rest."""
import ast
import datetime
import glob
import importlib.util
import os
import re
import sqlite3
import sys
import types
from contextlib import contextmanager

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATED = {
    "ConsolidationAdjustment": "assert_open",
    "ICBalance": "assert_open",
    "AllocationRun": "assert_open",
    "HistoricalEquityRate": "assert_open_between",
    "OwnershipPeriod": "assert_open_between",
    "TrialBalanceSubmission": "assert_open",
    "GroupExchangeRate": "assert_open",
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


class _ValidationError(Exception):
    pass


class _SqliteDB:
    """frappe.db over an in-memory SQLite holding the two EPM Fiscal Year
    tables, so the date-range queries really run against declared rows.
    MariaDB placeholders become SQLite ones; every query is recorded."""

    def __init__(self, years, periods):
        self.calls = []
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE `tabEPM Fiscal Year` (name TEXT, fiscal_year INTEGER, status TEXT)")
        self.conn.execute(
            "CREATE TABLE `tabEPM Fiscal Year Period` (parent TEXT, parentfield TEXT, "
            "fiscal_period INTEGER, period_code TEXT, period_type TEXT, "
            "start_date TEXT, end_date TEXT, status TEXT)")
        for year, status in years.items():
            self.conn.execute("INSERT INTO `tabEPM Fiscal Year` VALUES (?, ?, ?)", (str(year), year, status))
        for year, period, code, start, end, status in periods:
            self.conn.execute(
                "INSERT INTO `tabEPM Fiscal Year Period` VALUES (?, 'periods', ?, ?, 'Posting', ?, ?, ?)",
                (str(year), period, code, start, end, status))

    @staticmethod
    def _param(v):
        return v.isoformat() if isinstance(v, datetime.date) else v

    def sql(self, query, values=None, as_dict=False, **kwargs):
        self.calls.append(query)
        q = re.sub(r"%\((\w+)\)s", r":\1", query).replace("%s", "?").replace("%%", "%")
        q = q.replace("LOCK IN SHARE MODE", "")
        if isinstance(values, dict):
            params = {k: self._param(v) for k, v in values.items()}
        else:
            params = [self._param(v) for v in (values or ())]
        out = []
        for row in self.conn.execute(q, params).fetchall():
            d = {k: (datetime.date.fromisoformat(row[k]) if k.endswith("_date") and row[k] else row[k])
                 for k in row.keys()}
            out.append(d if as_dict else tuple(d.values()))
        return out


def _getdate(v):
    if isinstance(v, datetime.datetime):
        return v.date()
    return v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v))


@contextmanager
def _period_status(db):
    """period_status.py loaded by path against a stub frappe that stays
    installed for the whole block (the functions import frappe.utils at call
    time); sys.modules is restored afterwards. A load error is an
    AssertionError, never a "needs frappe" skip."""
    frappe = types.ModuleType("frappe")
    frappe.db = db
    frappe.ValidationError = _ValidationError
    frappe._ = lambda s: s

    def throw(msg, exc=_ValidationError, *a, **kw):
        raise exc(msg)

    frappe.throw = throw
    utils = types.ModuleType("frappe.utils")
    utils.getdate = _getdate
    frappe.utils = utils
    saved = {k: sys.modules.get(k) for k in ("frappe", "frappe.utils")}
    sys.modules["frappe"], sys.modules["frappe.utils"] = frappe, utils
    try:
        spec = importlib.util.spec_from_file_location(
            "period_status_under_test", os.path.join(APP_DIR, "period_status.py"))
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except ImportError as exc:
            raise AssertionError(f"period_status.py must load against a stub frappe: {exc!r}") from exc
        yield mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _declared(year_2025="Closed", p12="Open"):
    """FY2024 Open with November, December and a Dec-31 adjustment period;
    FY2025 (Closed by default) whose rows are themselves Open."""
    return _SqliteDB(
        years={2024: "Open", 2025: year_2025},
        periods=[
            (2024, 11, "P11", "2024-11-01", "2024-11-30", "Open"),
            (2024, 12, "P12", "2024-12-01", "2024-12-31", p12),
            (2024, 13, "P13", "2024-12-31", "2024-12-31", "Open"),
            (2025, 1, "P01", "2025-01-01", "2025-01-31", "Open"),
            (2025, 2, "P02", "2025-02-01", "2025-02-28", "Open"),
        ],
    )


def _refused(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except _ValidationError as exc:
        return str(exc)
    raise AssertionError(f"{fn.__name__}{args} {kwargs} was not refused")


def test_first_period_affected_is_next_declared_start():
    """The first declared period starting on or after the date, across years;
    None past the last declared period. No month arithmetic: mid-December
    lands on the declared Dec-31 adjustment period, not 1 January."""
    db = _declared()
    with _period_status(db) as ps:
        f = ps.first_period_affected
        assert f("2024-11-01") == datetime.date(2024, 11, 1)
        assert f("2024-11-15") == datetime.date(2024, 12, 1)
        assert f(datetime.date(2024, 12, 15)) == datetime.date(2024, 12, 31)
        assert f("2025-01-01") == datetime.date(2025, 1, 1)
        assert f("2025-02-02") is None
        assert f(None) is None
    assert db.calls and all("STR_TO_DATE" not in q for q in db.calls), db.calls
    assert any("`tabEPM Fiscal Year Period`" in q for q in db.calls), db.calls


def test_range_gate_uses_rows_not_month_arithmetic():
    """Every declared period starting in the range must be effectively Open
    (the stricter of year and row): a Closed year blocks its Open rows, an
    exclusive end leaves out the period starting on it, no end means every
    later period."""
    db = _declared()
    with _period_status(db) as ps:
        gate = ps.assert_open_between
        gate("2024-11-01", "2024-12-31", action="cancel a rate")
        gate("2024-11-01", "2025-01-01", action="cancel a rate", end_exclusive=True)
        assert _refused(gate, "2024-11-01", "2025-01-01", action="cancel a rate") == (
            "Cannot cancel a rate: it changes fiscal period P01 of FY2025, which is closed.")
        assert _refused(gate, "2024-12-15", None, action="cancel an ownership period") == (
            "Cannot cancel an ownership period: it changes fiscal period P01 of FY2025, which is closed.")
        gate("2025-03-01", None, action="cancel a rate")  # past the last declared period
    assert db.calls and all("STR_TO_DATE" not in q for q in db.calls), db.calls
    assert any("`tabEPM Fiscal Year`" in q and "`tabEPM Fiscal Year Period`" in q for q in db.calls), db.calls

    # A Locked row starting in the range is named with its own status; a
    # range starting after its first day leaves it alone.
    with _period_status(_declared(year_2025="Open", p12="Locked")) as ps:
        assert _refused(ps.assert_open_between, "2024-12-01", "2024-12-20", action="cancel") == (
            "Cannot cancel: it changes fiscal period P12 of FY2024, which is locked.")
        ps.assert_open_between("2024-12-15", "2024-12-20", action="cancel")
        ps.assert_open_between("2025-01-01", None, action="cancel")


def test_mid_period_start_is_not_blocked_by_its_own_period():
    """A period's membership is decided by its first day: a change effective
    15 March does not change March, so a Closed March must not block it;
    only the periods starting on or after the date (April on) must be Open."""
    months = [(m, datetime.date(2025, m, 1)) for m in range(1, 13)]
    db = _SqliteDB(
        years={2025: "Open"},
        periods=[
            (2025, m, f"P{m:02d}", start.isoformat(),
             ((datetime.date(2025, m + 1, 1) if m < 12 else datetime.date(2026, 1, 1))
              - datetime.timedelta(days=1)).isoformat(),
             "Closed" if m <= 3 else "Open")
            for m, start in months
        ],
    )
    with _period_status(db) as ps:
        gate = ps.assert_open_between
        gate("2025-03-15", None, action="cancel an ownership period")
        gate("2025-03-15", "2025-06-30", action="cancel an ownership period")
        assert _refused(gate, "2025-03-01", None, action="cancel an ownership period") == (
            "Cannot cancel an ownership period: it changes fiscal period P03 of FY2025, which is closed.")


def _ownership_period_module(period_status, next_start=None):
    """konsol.consolidation.doctype.ownership_period.ownership_period, loaded
    by path against a minimal frappe (only frappe.db.get_value, for the next
    ownership period's effective_date) and the already-loaded (sqlite-backed)
    ``period_status`` module, so before_cancel's own combination of
    ``next_start`` and ``first_period_affected`` runs against real declared
    rows, not a mock of period_status."""
    frappe = types.ModuleType("frappe")
    frappe.db = types.SimpleNamespace(get_value=lambda *a, **k: next_start)
    document_mod = types.ModuleType("frappe.model.document")

    class Document:
        def __init__(self, **fields):
            self.__dict__.update(fields)

    document_mod.Document = Document
    clickhouse = types.ModuleType("konsol.clickhouse")
    clickhouse.sync_doctype_after_commit = lambda *a, **k: None
    mods = {
        "frappe": frappe,
        "frappe.model": types.ModuleType("frappe.model"),
        "frappe.model.document": document_mod,
        "konsol": types.ModuleType("konsol"),
        "konsol.clickhouse": clickhouse,
        "konsol.period_status": period_status,
    }
    path = os.path.join(APP_DIR, "consolidation", "doctype", "ownership_period", "ownership_period.py")
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("ownership_period_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return module


def test_cancel_past_last_declared_period():
    """konsol#191 review finding 5: first_period_affected returns None past
    the last declared period (on main it always returned a date). Cancelling
    an Ownership Period whose next sibling starts after the last declared
    period must not raise TypeError from comparing a date with that None:
    with no later declared period, the ownership's own end stays the bound,
    and the gate still runs on whichever declared periods are in range."""
    db = _SqliteDB(
        years={2024: "Open", 2025: "Open"},
        periods=[
            (2024, 11, "P11", "2024-11-01", "2024-11-30", "Open"),
            (2024, 12, "P12", "2024-12-01", "2024-12-31", "Open"),
            (2025, 1, "P01", "2025-01-01", "2025-01-31", "Closed"),
            (2025, 2, "P02", "2025-02-01", "2025-02-28", "Open"),
        ],
    )
    with _period_status(db) as ps:
        # The next Ownership Period for the same group/entity starts after
        # FY2025 P02, the last declared period: first_period_affected(next_start)
        # is None.
        next_start = datetime.date(2026, 1, 1)

        # No declared period between effective_date and end_date is closed:
        # accepted, with no TypeError even though end_date is past the last
        # declared period.
        module = _ownership_period_module(ps, next_start=next_start)
        module.OwnershipPeriod(
            consolidation_group="CG1", data_area_id="E1", name="OP-1",
            effective_date=datetime.date(2025, 2, 1),
            end_date=datetime.date(2026, 6, 30)).before_cancel()

        # FY2025 P01 is Closed and in range: the gate still refuses, rather
        # than crashing.
        module = _ownership_period_module(ps, next_start=next_start)
        doc = module.OwnershipPeriod(
            consolidation_group="CG1", data_area_id="E1", name="OP-2",
            effective_date=datetime.date(2024, 11, 1),
            end_date=datetime.date(2026, 6, 30))
        assert _refused(doc.before_cancel) == (
            "Cannot cancel an ownership period: it changes fiscal period P01 of FY2025, which is closed.")


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

