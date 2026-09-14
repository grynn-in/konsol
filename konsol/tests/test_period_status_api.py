"""period_status.py on EPM Fiscal Year rows (konsol#189): declared periods
only, nothing defaults to Open.

period_status.py is loaded by path against a stub frappe whose db.sql returns
canned rows and records every query. The stub stays installed for the whole
`with` block (the module calls frappe at call time), and sys.modules is
restored on the way out so later test files aren't affected. A load error is
turned into an AssertionError: the host runner would otherwise count a
"needs frappe" ImportError as a skip.
"""
import importlib.util
import os
import sys
import types
from contextlib import contextmanager
from datetime import date

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PS_PATH = os.path.join(APP_DIR, "period_status.py")


class _ValidationError(Exception):
    pass


class _DB:
    """Stand-in for frappe.db. `years` maps fiscal_year -> year status;
    `periods` maps (fiscal_year, fiscal_period) -> the period row's columns."""

    def __init__(self, years=None, periods=None):
        self.years = years or {}
        self.periods = periods or {}
        self.calls = []

    def sql(self, query, values=None, *args, **kwargs):
        self.calls.append((query, values, kwargs))
        vals = values if isinstance(values, (list, tuple)) else [values]
        if "`tabEPM Fiscal Year Period`" in query:
            year, period = str(vals[0]), int(vals[1])
            row = self.periods.get((year, period))
            return [dict(row)] if row else []
        if "`tabEPM Fiscal Year`" in query:
            year = str(vals[0])
            if year not in self.years:
                return []
            return [{"name": year, "status": self.years[year]}]
        raise AssertionError(f"unexpected query: {query}")


def _stub_frappe(db):
    frappe = types.ModuleType("frappe")
    frappe.db = db
    frappe.ValidationError = _ValidationError
    frappe._ = lambda s: s

    def throw(msg, exc=_ValidationError, *args, **kwargs):
        raise exc(msg)

    frappe.throw = throw
    return frappe


@contextmanager
def _load(db):
    saved = {k: sys.modules.get(k) for k in ("frappe",)}
    sys.modules["frappe"] = _stub_frappe(db)
    try:
        spec = importlib.util.spec_from_file_location("period_status_under_test", PS_PATH)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except ImportError as exc:
            raise AssertionError(
                f"period_status.py must load against a stub frappe: {exc!r}") from exc
        yield mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _period(status="Open", code="P14"):
    return {
        "period_code": code,
        "period_type": "Adjustment",
        "start_date": date(2025, 12, 31),
        "end_date": date(2025, 12, 31),
        "status": status,
    }


def _refusal(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the exception type is checked by the caller
        return exc
    raise AssertionError(f"{fn.__name__}{args} was not refused")


def test_missing_year_refused_named():
    with _load(_DB()) as ps:
        exc = _refusal(ps.get_status, 2031, 1)
        assert isinstance(exc, ps.PeriodNotDeclared)
        assert isinstance(exc, _ValidationError)
        assert "FY2031 is not declared" in str(exc)
        assert "EPM Fiscal Year" in str(exc)
        for fn in (ps.period_row, ps.is_open, ps.assert_declared, ps.assert_open, ps.period_dates):
            assert isinstance(_refusal(fn, 2031, 1), ps.PeriodNotDeclared), fn.__name__


def test_missing_period_refused_named():
    db = _DB(years={"2025": "Open"}, periods={("2025", 1): _period(code="P01")})
    with _load(db) as ps:
        exc = _refusal(ps.get_status, 2025, 14)
        assert isinstance(exc, ps.PeriodNotDeclared)
        assert "FY2025 has no period 14" in str(exc)
        exc = _refusal(ps.assert_open, 2025, 14, action="post")
        assert isinstance(exc, ps.PeriodNotDeclared)


def test_year_closed_wins():
    db = _DB(years={"2025": "Closed"}, periods={("2025", 14): _period("Open")})
    with _load(db) as ps:
        assert ps.get_status(2025, 14) == ps.CLOSED
        assert ps.is_open(2025, 14) is False
        row = ps.period_row(2025, 14)
        assert row["row_status"] == "Open"
        assert row["year_status"] == "Closed"
        assert row["status"] == "Closed"
        ps.assert_declared(2025, 14)  # declared, just not open
        exc = _refusal(ps.assert_open, 2025, 14, action="post this journal")
        assert not isinstance(exc, ps.PeriodNotDeclared)
        assert isinstance(exc, _ValidationError)
        assert str(exc) == "Cannot post this journal: fiscal period 14 of FY2025 is closed."


def test_open_passes():
    db = _DB(years={"2025": "Open"}, periods={("2025", 14): _period("Open")})
    with _load(db) as ps:
        assert ps.OPEN == "Open"
        assert ps.get_status(2025, 14) == ps.OPEN
        assert ps.is_open("2025", "14") is True
        ps.assert_declared(2025, 14)
        ps.assert_open(2025, 14, action="run")  # must not raise
        assert ps.period_dates(2025, 14) == (date(2025, 12, 31), date(2025, 12, 31))
        row = ps.period_row(2025, 14)
        assert row["code"] == "P14"
        assert row["type"] == "Adjustment"
        assert row["start_date"] == date(2025, 12, 31)
        assert row["end_date"] == date(2025, 12, 31)


def test_year_read_is_locking():
    db = _DB(years={"2025": "Open"}, periods={("2025", 14): _period("Open")})
    with _load(db) as ps:
        ps.period_row(2025, 14)
    year_sql = [q for q, _, _ in db.calls if "`tabEPM Fiscal Year`" in q]
    assert year_sql, "period_row must read the fiscal year"
    assert all("LOCK IN SHARE MODE" in q for q in year_sql), year_sql
    assert all("fiscal_year" in q for q in year_sql), year_sql
