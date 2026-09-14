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
"""
import sys
import types

from konsol.orchestrator import api


class _Row(dict):
    """A frappe.get_all row: dict with attribute access, like frappe._dict."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class _FakeFrappe(types.ModuleType):
    """Stand-in for frappe: only get_all, which is all launch_options needs."""

    def __init__(self, years, periods_by_year, definitions=(), groups=()):
        super().__init__("frappe")
        self._years = years
        self._periods_by_year = periods_by_year
        self._definitions = definitions
        self._groups = groups
        self.calls = []

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
