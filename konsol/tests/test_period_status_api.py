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
    `periods` maps (fiscal_year, fiscal_period) -> the period row's columns;
    `settings` maps an EPM Settings fieldname -> its value (unset reads 0)."""

    def __init__(self, years=None, periods=None, settings=None):
        self.years = years or {}
        self.periods = periods or {}
        self.settings = settings or {}
        self.calls = []
        self.single_reads = []

    def get_single_value(self, doctype, fieldname, *args, **kwargs):
        self.single_reads.append((doctype, fieldname))
        if doctype != "EPM Settings":
            raise AssertionError(f"unexpected single doctype: {doctype}")
        return self.settings.get(fieldname, 0)

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


class _DoesNotExistError(_ValidationError):
    pass


class _Dict(dict):
    """Stand-in for frappe._dict: a dict whose keys read as attributes."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None


def _getdate(value):
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _stub_frappe(db, year_docs=None):
    """`year_docs` maps fiscal_year (str) -> a _FakeYear that get_doc returns;
    any other year raises DoesNotExistError, as frappe.get_doc does."""
    frappe = types.ModuleType("frappe")
    frappe.db = db
    frappe.ValidationError = _ValidationError
    frappe.DoesNotExistError = _DoesNotExistError
    frappe._ = lambda s: s
    frappe._dict = _Dict
    frappe.get_doc_calls = []

    def throw(msg, exc=_ValidationError, *args, **kwargs):
        raise exc(msg)

    def get_doc(doctype, name=None, *args, **kwargs):
        frappe.get_doc_calls.append((doctype, name))
        if doctype != "EPM Fiscal Year":
            raise AssertionError(f"unexpected get_doc: {doctype}")
        doc = (year_docs or {}).get(str(name))
        if doc is None:
            raise _DoesNotExistError(f"EPM Fiscal Year {name} not found")
        return doc

    frappe.throw = throw
    frappe.get_doc = get_doc
    utils = types.ModuleType("frappe.utils")
    utils.getdate = _getdate
    frappe.utils = utils
    return frappe


@contextmanager
def _load(db, year_docs=None):
    saved = {k: sys.modules.get(k) for k in ("frappe", "frappe.utils")}
    stub = _stub_frappe(db, year_docs)
    sys.modules["frappe"] = stub
    sys.modules["frappe.utils"] = stub.utils
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


def _period(status="Open", code="P14", period_type="Adjustment"):
    return {
        "period_code": code,
        "period_type": period_type,
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


def test_regular_always_postable():
    db = _DB(years={"2025": "Open"},
             periods={("2025", 3): _period(code="P03", period_type="Regular")})
    with _load(db) as ps:
        ps.assert_postable(2025, 3)  # no settings ticked: must not raise
        assert "Regular" in ps.postable_types()


def test_closing_postable_only_when_ticked():
    periods = {("2025", 13): _period(code="CLS", period_type="Closing")}
    db = _DB(years={"2025": "Open"}, periods=periods)
    with _load(db) as ps:
        exc = _refusal(ps.assert_postable, 2025, 13)
        assert isinstance(exc, _ValidationError)
        assert not isinstance(exc, ps.PeriodNotDeclared)
        assert str(exc) == (
            "CLS (Closing) does not take trial balances on this site. "
            "Tick it in EPM Settings \u2192 Close to allow it.")
    db = _DB(years={"2025": "Open"}, periods=periods, settings={"tb_accepts_closing": 1})
    with _load(db) as ps:
        ps.assert_postable(2025, 13)  # ticked: must not raise
    assert ("EPM Settings", "tb_accepts_closing") in db.single_reads


def test_undeclared_not_postable():
    with _load(_DB()) as ps:
        assert isinstance(_refusal(ps.assert_postable, 2031, 1), ps.PeriodNotDeclared)
    db = _DB(years={"2025": "Open"}, periods={("2025", 1): _period(code="P01")})
    with _load(db) as ps:
        assert isinstance(_refusal(ps.assert_postable, 2025, 14), ps.PeriodNotDeclared)


def test_postable_types_reads_settings():
    with _load(_DB()) as ps:
        assert ps.postable_types() == {"Regular"}
    db = _DB(settings={"tb_accepts_opening": 1, "tb_accepts_adjustment": 1})
    with _load(db) as ps:
        assert ps.postable_types() == {"Regular", "Opening", "Adjustment"}
    assert set(db.single_reads) == {
        ("EPM Settings", "tb_accepts_opening"),
        ("EPM Settings", "tb_accepts_closing"),
        ("EPM Settings", "tb_accepts_adjustment"),
    }
    db = _DB(settings={"tb_accepts_closing": 1})
    with _load(db) as ps:
        assert ps.postable_types() == {"Regular", "Closing"}


# ---- set_status delegates to the EPM Fiscal Year actions --------------------

USER = "closer@example.com"
CLOSED_ON = "2025-04-02 10:00:00"


class _FakeYear:
    """An EPM Fiscal Year doc: its period rows, and close/lock/reopen actions
    that record each call and move the row as the real actions do."""

    def __init__(self, fiscal_year="2025", status="Open"):
        self.name = str(fiscal_year)
        self.fiscal_year = int(fiscal_year)
        self.status = status
        self.periods = [
            types.SimpleNamespace(
                name="row-p03", fiscal_period=3, period_code="P03",
                period_type="Regular", start_date=date(2025, 3, 1),
                end_date=date(2025, 3, 31), status="Open",
                closed_by=None, closed_on=None),
        ]
        self.calls = []

    def _row(self, fiscal_period):
        return next(r for r in self.periods if r.fiscal_period == int(fiscal_period))

    def _move(self, fiscal_period, status):
        row = self._row(fiscal_period)
        row.status = status
        row.closed_by, row.closed_on = (None, None) if status == "Open" else (USER, CLOSED_ON)
        return {"fiscal_period": row.fiscal_period, "period_code": row.period_code, "status": status}

    def close_period(self, fiscal_period, note=None):
        self.calls.append(("close_period", fiscal_period, note))
        return self._move(fiscal_period, "Closed")

    def lock_period(self, fiscal_period, note=None):
        self.calls.append(("lock_period", fiscal_period, note))
        return self._move(fiscal_period, "Locked")

    def reopen_period(self, fiscal_period, reason):
        self.calls.append(("reopen_period", fiscal_period, reason))
        return self._move(fiscal_period, "Open")


#: What callers read off set_status's result: control_api.set_period_status
#: (name, fiscal_year, fiscal_period, status, closed_by, closed_on) and
#: test_fiscal_year_bench (period_code, status, closed_by, closed_on).
CALLER_ATTRS = ("name", "fiscal_year", "fiscal_period", "status", "closed_by", "closed_on")


def test_set_status_delegates():
    year = _FakeYear()
    with _load(_DB(), {"2025": year}) as ps:
        import frappe

        doc = ps.set_status(2025, "3", ps.CLOSED, note="books signed")
        assert year.calls == [("close_period", 3, "books signed")]
        assert frappe.get_doc_calls == [("EPM Fiscal Year", "2025")]
        for attr in CALLER_ATTRS:
            assert hasattr(doc, attr), attr
        assert doc.status == "Closed"
        assert doc.closed_by == USER
        assert doc.closed_on == CLOSED_ON
        assert doc.fiscal_period == 3
        assert str(doc.fiscal_year) == "2025"
        assert doc.name

        # Reopen needs a reason; without one it is refused before any action.
        exc = _refusal(ps.set_status, 2025, 3, ps.OPEN)
        assert isinstance(exc, _ValidationError)
        assert "reason" in str(exc)
        assert year.calls == [("close_period", 3, "books signed")]

        doc = ps.set_status(2025, 3, ps.OPEN, reason="late journal")
        assert year.calls[-1] == ("reopen_period", 3, "late journal")
        assert doc.status == "Open"
        assert doc.closed_by is None
        assert doc.closed_on is None

        # Equal dates are fine, as strings or dates.
        doc = ps.set_status(2025, 3, ps.LOCKED, "2025-03-01", date(2025, 3, 31))
        assert year.calls[-1] == ("lock_period", 3, None)
        assert doc.status == "Locked"
        assert doc.closed_by == USER
        assert len(year.calls) == 3


def test_set_status_refuses_other_dates():
    year = _FakeYear()
    with _load(_DB(), {"2025": year}) as ps:
        expected = ("P03 of FY2025 is declared 2025-03-01..2025-03-31; set_status "
                    "can't change a period's dates — edit the EPM Fiscal Year")
        exc = _refusal(ps.set_status, 2025, 3, ps.CLOSED, "2025-03-02", None)
        assert isinstance(exc, _ValidationError)
        assert str(exc) == expected
        exc = _refusal(ps.set_status, 2025, 3, ps.CLOSED, None, date(2025, 4, 30))
        assert str(exc) == expected
        assert year.calls == []


def test_set_status_undeclared_year_refused():
    with _load(_DB(), {"2025": _FakeYear()}) as ps:
        exc = _refusal(ps.set_status, 2031, 3, ps.CLOSED)
        assert isinstance(exc, ps.PeriodNotDeclared)
        assert "FY2031 is not declared" in str(exc)
        exc = _refusal(ps.set_status, 2025, 14, ps.CLOSED)
        assert isinstance(exc, ps.PeriodNotDeclared)
        assert "FY2025 has no period 14" in str(exc)
