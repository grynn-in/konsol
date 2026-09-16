"""TDD (konsol#189-20) — launch_options reads the declared fiscal calendar.

``konsol.orchestrator.api.launch_options`` used to source ``fiscal_years``
from a best-effort ClickHouse query against ``epm_gold.gold_trial_balance``
and ``fiscal_periods`` from the year-agnostic ``Fiscal Period`` template — a
fixed fourteen rows (OPN, P1..P12, CLS) every year reused (see
test_period_status.py's design note). Both are wrong once fiscal years and
periods are declared documents (``EPM Fiscal Year`` / ``EPM Fiscal Year
Period``, konsol#189): a year's own periods can differ from the template (a
13-period year), and a site with no declared year must not invent one.

This installs a stub ``frappe`` — ``launch_options`` does ``import frappe``
lazily inside its body, so the stub must stay installed in ``sys.modules``
for the duration of the call, the same pattern test_fiscal_year_warehouse.py
uses for ``fiscal_calendar.fiscal_period_rows()``.

The control_api readers (konsol#189-39) follow the same rule: a period that is
not a declared EPM Fiscal Year row is reported as undeclared, never assumed
Open, and the snapshot's option list and the readiness check read the
declared rows. control_api.py is loaded by path against stub frappe / konsol
modules, installed for the whole `with` block and restored on the way out; a
load error becomes an AssertionError so the host runner can't count it as a
skip.
"""
import ast
import importlib.util
import os
import sys
import types
from contextlib import contextmanager
from datetime import date, datetime

from konsol.orchestrator import api


class _Row(dict):
    """A frappe.get_all row: dict with attribute access, like frappe._dict."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class _FakeFrappe(types.ModuleType):
    """Stand-in for frappe: get_all, plus the only_for gate launch_options
    opens with (konsol#166) — recorded as ``("only_for", roles)`` in ``calls``
    so the role check is exercised here, not stubbed away."""

    def __init__(self, years, periods_by_year, definitions=(), groups=()):
        super().__init__("frappe")
        self._years = years
        self._periods_by_year = periods_by_year
        self._definitions = definitions
        self._groups = groups
        self.calls = []

    def only_for(self, roles, message=False):
        roles = [roles] if isinstance(roles, str) else list(roles)
        self.calls.append(("only_for", tuple(roles)))

    def get_all(self, doctype, fields=None, filters=None, order_by=None, **kwargs):
        self.calls.append(doctype)
        if doctype == "Pipeline":
            return [_Row(name=n) for n in self._definitions]
        if doctype == "EPM Fiscal Year":
            desc = bool(order_by and "desc" in order_by.lower())
            rows = [_Row(fiscal_year=y) for y in self._years]
            rows.sort(key=lambda r: r["fiscal_year"], reverse=desc)
            return rows
        if doctype == "EPM Fiscal Year Period":
            parent = (filters or {}).get("parent")
            rows = [_Row(r) for r in self._periods_by_year.get(parent, [])]
            rows.sort(key=lambda r: r["fiscal_period"])
            return rows
        if doctype == "Consolidation Group":
            return [_Row(g) for g in self._groups]
        raise AssertionError(f"unexpected frappe.get_all({doctype!r})")


def _install(fake):
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = fake
    return saved


def _restore(saved):
    if saved is None:
        sys.modules.pop("frappe", None)
    else:
        sys.modules["frappe"] = saved


#: A 13-period year — proof fiscal_periods comes from the year's own rows,
#: not the legacy 14-row (OPN, P1..P12, CLS) template.
_PERIODS_2026 = [
    {
        "fiscal_period": i,
        "period_code": f"P{i:02d}",
        "period_label": f"Period {i}",
        "period_type": "Regular",
    }
    for i in range(1, 14)
]


def test_launch_options_from_fiscal_year():
    fake = _FakeFrappe(
        years=[2025, 2026],
        periods_by_year={"2026": _PERIODS_2026, "2025": []},
        definitions=["Close"],
    )
    saved = _install(fake)
    warehouse_calls = []
    try:
        from konsol import clickhouse

        orig_execute = clickhouse.execute
        clickhouse.execute = lambda *a, **k: warehouse_calls.append((a, k))
        try:
            out = api.launch_options()
        finally:
            clickhouse.execute = orig_execute
    finally:
        _restore(saved)

    # Years come from EPM Fiscal Year, newest first — no invented range.
    assert out["fiscal_years"] == ["2026", "2025"]
    assert "EPM Fiscal Year" in fake.calls

    # No warehouse / ClickHouse read anywhere in the call.
    assert warehouse_calls == [], "launch_options must not read the warehouse"

    # The current (newest) year's own 13 rows, not the fixed 14-period template.
    periods = out["fiscal_periods"]
    assert len(periods) == 13
    assert [p["value"] for p in periods] == [str(i) for i in range(1, 14)]
    for p in periods:
        assert {"value", "label", "type"} <= set(p), p
    assert periods[0]["label"] == "Period 1"
    assert periods[0]["type"] == "Regular"

    # Unrelated surfaces (definitions, scopes via Consolidation Group) still wired.
    assert out["definitions"] == ["Close"]
    assert "Consolidation Group" in fake.calls

    # konsol#166: it reads with get_all, which ignores permissions, so the call
    # is gated to the launch roles — the stub records that check.
    gates = [c for c in fake.calls if isinstance(c, tuple) and c[0] == "only_for"]
    assert gates, f"launch_options did not call frappe.only_for: {fake.calls}"
    assert set(gates[0][1]) == {"EPM Admin", "EPM Analyst", "System Manager"}, (
        f"launch_options is not gated to the launch roles: {gates}")


# ---- control_api readers (konsol#189-39) -------------------------------

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CA_PATH = os.path.join(APP_DIR, "control_api.py")


class _ValidationError(Exception):
    pass


class _PeriodNotDeclared(_ValidationError):
    pass


class _Dict(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None


def _getdate(value=None):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


class _DB:
    def __init__(self, period_fields=None):
        self.period_fields = period_fields
        self.get_value_calls = []

    def get_value(self, doctype, filters=None, fieldname=None, *args, **kwargs):
        self.get_value_calls.append((doctype, filters, fieldname))
        if doctype == "EPM Fiscal Year Period":
            return _Dict(self.period_fields) if self.period_fields else None
        return None

    def exists(self, doctype, filters=None):
        return False

    def count(self, doctype, filters=None):
        return 0


def _row(year, period, code, ptype, start, end, label=None, status="Open"):
    return {
        "fiscal_year": year,
        "fiscal_period": period,
        "period_code": code,
        "period_label": label or code,
        "period_type": ptype,
        "start_date": date.fromisoformat(start),
        "end_date": date.fromisoformat(end),
        "quarter": "",
        "status": status,
    }


@contextmanager
def _control_api(rows=(), period_rows=None, period_fields=None, today="2026-09-14"):
    """`rows` is what fiscal_calendar.fiscal_period_rows() returns;
    `period_rows` maps (str year, int period) -> period_status.period_row()."""
    calls = {"set_status": [], "check_epm_admin": 0}

    frappe = types.ModuleType("frappe")
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.ValidationError = _ValidationError
    frappe._ = lambda s: s
    frappe._dict = _Dict
    frappe.db = _DB(period_fields)

    def throw(msg, exc=_ValidationError, *args, **kwargs):
        raise exc(msg)

    frappe.throw = throw
    frappe.get_meta = lambda doctype: _Dict(issingle=False)
    frappe.get_all = lambda *a, **k: []
    utils = types.ModuleType("frappe.utils")
    utils.today = lambda: today
    utils.getdate = _getdate
    utils.add_to_date = utils.get_datetime = utils.now_datetime = lambda *a, **k: None
    frappe.utils = utils

    ps = types.ModuleType("konsol.period_status")
    ps.OPEN, ps.CLOSED, ps.LOCKED = "Open", "Closed", "Locked"
    ps.PeriodNotDeclared = _PeriodNotDeclared

    def period_row(fiscal_year, fiscal_period):
        row = (period_rows or {}).get((str(fiscal_year), int(fiscal_period)))
        if row is None:
            raise _PeriodNotDeclared(f"FY{fiscal_year} has no period {fiscal_period}.")
        return dict(row)

    def set_status(fiscal_year, fiscal_period, status, start_date=None, end_date=None,
                   reason=None, note=None):
        calls["set_status"].append({
            "fiscal_year": fiscal_year, "fiscal_period": fiscal_period, "status": status,
            "start_date": start_date, "end_date": end_date, "reason": reason, "note": note,
        })
        return _Dict(
            name="row-9", fiscal_year=str(fiscal_year), fiscal_period=int(fiscal_period),
            period_code="P9", start_date=date(2026, 9, 1), end_date=date(2026, 9, 30),
            status=status, closed_by=None, closed_on=None,
        )

    ps.period_row = period_row
    ps.get_status = lambda fy, fp: period_row(fy, fp)["status"]
    ps.assert_declared = lambda fy, fp: period_row(fy, fp) and None
    ps.set_status = set_status

    def _current_period(rows_arg, now):
        """Same rule as the real fiscal_calendar.current_period (konsol#189
        review nit 6): the declared Regular row covering ``now``, or None."""
        for row in rows_arg:
            if row.get("period_type") != "Regular":
                continue
            start, end = row.get("start_date"), row.get("end_date")
            if start and end and start <= now <= end:
                return (row.get("fiscal_year"), row.get("fiscal_period"))
        return None

    fc = types.ModuleType("konsol.fiscal_calendar")
    fc.fiscal_period_rows = lambda: [dict(r) for r in rows]
    fc.current_period = _current_period

    def check_epm_admin():
        calls["check_epm_admin"] += 1

    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    konsol.period_status = ps
    konsol.fiscal_calendar = fc
    budget_sheet = types.ModuleType("konsol.epm.doctype.budget_sheet.budget_sheet")
    budget_sheet.LAYER_ROLES = {}
    schema_lifecycle = types.ModuleType("konsol.schema_lifecycle")
    schema_lifecycle.check_epm_admin = check_epm_admin

    stubs = {
        "frappe": frappe,
        "frappe.utils": utils,
        "konsol": konsol,
        "konsol.period_status": ps,
        "konsol.fiscal_calendar": fc,
        "konsol.epm.doctype.budget_sheet.budget_sheet": budget_sheet,
        "konsol.schema_lifecycle": schema_lifecycle,
    }
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("_control_api_under_test", CA_PATH)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 — never let this read as a skip
            raise AssertionError(f"control_api.py failed to load on stubs: {exc!r}") from exc
        module._test_calls = calls
        yield module
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


# Declared rows: FY2026 with a thirteenth Regular period and the non-Regular
# periods a year may carry, plus one row of FY2025.
_ROWS = [
    _row(2025, 1, "P1", "Regular", "2025-01-01", "2025-01-31", "Jan"),
    _row(2026, 0, "OPN", "Opening", "2026-01-01", "2026-01-01"),
    _row(2026, 1, "P1", "Regular", "2026-01-01", "2026-01-31", "Jan"),
    _row(2026, 9, "P9", "Regular", "2026-09-01", "2026-09-30", "Sep"),
    _row(2026, 13, "P13", "Regular", "2026-12-01", "2026-12-31", "Dec"),
    _row(2026, 14, "ADJ", "Adjustment", "2026-12-31", "2026-12-31"),
    _row(2026, 15, "CLS", "Closing", "2026-12-31", "2026-12-31"),
]


def test_period_block_undeclared_is_explicit():
    with _control_api(rows=_ROWS) as ca:
        block = ca._period_block("2026", "7")
    assert block["declared"] is False, block
    assert block["fiscal_year"] == "2026"
    assert block["fiscal_period"] == "7"
    assert block.get("status") is None, "an undeclared period has no status, not Open"
    assert block.get("start_date") is None and block.get("end_date") is None

    declared = {("2026", 9): {
        "fiscal_year": 2026, "fiscal_period": 9, "code": "P9", "type": "Regular",
        "start_date": date(2026, 9, 1), "end_date": date(2026, 9, 30),
        "row_status": "Closed", "year_status": "Open", "status": "Closed",
    }}
    fields = {"closed_by": "fc@example.com", "closed_on": datetime(2026, 9, 3, 10, 0)}
    with _control_api(rows=_ROWS, period_rows=declared, period_fields=fields) as ca:
        block = ca._period_block("2026", "9")
        doctypes = [c[0] for c in ca.frappe.db.get_value_calls]
    assert block["declared"] is True
    assert block["status"] == "Closed"
    assert block["closed_by"] == "fc@example.com"
    assert block["closed_on"].startswith("2026-09-03")
    assert "Period Status" not in doctypes, "close stamps live on the EPM Fiscal Year row"

    with _control_api() as ca:
        empty = ca._period_block("2026", None)
    assert empty["fiscal_period"] is None and empty["status"] is None


def test_set_period_status_passes_reason():
    with _control_api() as ca:
        out = ca.set_period_status("2026", "9", "Open", reason="late accrual", note="see JE-42")
        calls = ca._test_calls
    assert calls["check_epm_admin"] == 1
    (call,) = calls["set_status"]
    assert call["reason"] == "late accrual"
    assert call["note"] == "see JE-42"
    assert call["status"] == "Open"
    assert set(out) == {"name", "fiscal_year", "fiscal_period", "status", "closed_by", "closed_on"}
    assert out["status"] == "Open"

    with _control_api() as ca:
        ca.set_period_status("2026", "9", "Closed")
        (call,) = ca._test_calls["set_status"]
    assert call["reason"] is None and call["note"] is None


def _fy_check(ca, process_id):
    rows = [r for r in ca._prerequisites(process_id, "2026", True)
            if r["due"] == "Fiscal Year covers today"]
    assert len(rows) == 1, f"{process_id}: expected one 'Fiscal Year covers today' check"
    return rows[0]


def test_readiness_needs_fiscal_year():
    with _control_api(rows=_ROWS, today="2026-09-14") as ca:
        for pid in ("budgeting", "forecasting", "consolidation"):
            assert _fy_check(ca, pid)["status"] == "configured"

    # Declared rows exist, but none contains today: the check fails.
    with _control_api(rows=_ROWS, today="2027-02-10") as ca:
        row = _fy_check(ca, "budgeting")
    assert row["status"] == "missing"
    assert row["doctype"] == "EPM Fiscal Year"
    assert row["actionable"] is True

    with _control_api(rows=[], today="2026-09-14") as ca:
        assert _fy_check(ca, "consolidation")["status"] == "missing"


def test_period_options_regular_rows():
    with _control_api(rows=_ROWS) as ca:
        options = ca._period_options("2026")
    assert options == ["FY2026 · Jan", "FY2026 · Sep", "FY2026 · Dec"], options

    with _control_api(rows=_ROWS) as ca:
        assert ca._period_options("2027") == [], "no declared rows, no invented option"


def test_current_fiscal_year_is_declared():
    """konsol#189-42: _current_fiscal_year() reads the declared calendar — the
    fiscal_year of the Regular period covering today — instead of inventing
    the calendar year. FY2026 runs Apr 2026 -> Mar 2027, so a date in Feb 2027
    is still "2026"."""
    fy2026_rows = [
        _row(2026, 1, "P01", "Regular", "2026-04-01", "2026-04-30", "Apr"),
        _row(2026, 11, "P11", "Regular", "2027-02-01", "2027-02-28", "Feb"),
        _row(2026, 12, "P12", "Regular", "2027-03-01", "2027-03-31", "Mar"),
    ]
    with _control_api(rows=fy2026_rows, today="2027-02-10") as ca:
        assert ca._current_fiscal_year() == "2026"

    # No declared row covers today (calendar year would be 2027; nothing is
    # declared that far): None, not an invented year.
    with _control_api(rows=_ROWS, today="2027-02-10") as ca:
        assert ca._current_fiscal_year() is None
        snapshot = ca.get_snapshot(fiscal_period="9")

    assert snapshot["fiscal_year"] is None, "no year-level part may invent a year"
    assert snapshot["period"]["declared"] is False


# ---- start_process must not silently default a fiscal year (konsol#189-45) --

def _install_start_run(launched):
    """Stub konsol.orchestrator.api.start_run — the consolidation launcher —
    so start_process's `from konsol.orchestrator.api import start_run` resolves
    without touching the real orchestrator, and records whether it ran."""
    pkg = types.ModuleType("konsol.orchestrator")
    pkg.__path__ = []
    mod = types.ModuleType("konsol.orchestrator.api")

    def start_run(definition, params=None):
        launched.append((definition, params))
        return "PR-1"

    mod.start_run = start_run
    names = ("konsol.orchestrator", "konsol.orchestrator.api")
    saved = {name: sys.modules.get(name) for name in names}
    sys.modules["konsol.orchestrator"] = pkg
    sys.modules["konsol.orchestrator.api"] = mod
    return saved


def _restore_modules(saved):
    for name, mod in saved.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


def test_start_process_needs_a_declared_year():
    launched = []

    # No fiscal_year argument, and no declared EPM Fiscal Year covers today:
    # start_process must refuse rather than launch across every year.
    with _control_api(rows=_ROWS, today="2027-02-10") as ca:
        saved = _install_start_run(launched)
        try:
            try:
                ca.start_process("consolidation")
                raise AssertionError("expected frappe.throw for an undeclared year")
            except _ValidationError as exc:
                assert "No Fiscal Year is declared" in str(exc), exc
        finally:
            _restore_modules(saved)
    assert launched == [], "nothing may be launched when no fiscal year is declared"

    # A declared EPM Fiscal Year row covers today: start_process launches with it.
    with _control_api(rows=_ROWS, today="2026-09-14") as ca:
        saved = _install_start_run(launched)
        try:
            out = ca.start_process("consolidation")
        finally:
            _restore_modules(saved)
    assert out == {"ok": True, "run_kind": "pipeline", "name": "PR-1"}
    assert launched == [("Group Close", {"fiscal_year": 2026})]


# ---- write endpoints must be POST-only (project rule: a GET rolls back at --
# the end, so any @frappe.whitelist() method that writes must be
# methods=["POST"], and its callers must POST) -----------------------------

def _assert_control_api_post_only(name):
    with open(CA_PATH) as f:
        tree = ast.parse(f.read())
    func = next((n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == name), None)
    assert func is not None, f"control_api.py has no {name} function"
    found = False
    for dec in func.decorator_list:
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "whitelist"
                and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "frappe"):
            for kw in dec.keywords:
                if kw.arg == "methods":
                    found = ast.literal_eval(kw.value) == ["POST"]
    assert found, f"{name} is not @frappe.whitelist(methods=['POST'])"


def test_write_endpoints_post_only():
    """set_period_status and start_process both write (they close/lock/reopen
    a period, or launch a run) — a GET call would run the write and then roll
    back at the end of the request while still reporting success. Both must
    be POST-only."""
    _assert_control_api_post_only("set_period_status")
    _assert_control_api_post_only("start_process")
