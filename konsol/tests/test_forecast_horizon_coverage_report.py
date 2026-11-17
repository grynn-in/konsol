"""Budget S6 KA9 (konsol#348 QB10 b, S6-Q9 a): the Forecast Horizon Coverage
Desk report.

One row per forecast round (scenario, as-of date) with its state and the
sentence ``horizon_coverage_model.rounds`` writes. The report reads Budget
Cycle through ``frappe.get_list`` (permissions apply), forecast type, not
cancelled, ordered explicitly (v16 sorts by creation otherwise), and the
declared calendar through ``fiscal_calendar.fiscal_period_rows``. Read-only:
no warehouse read, nothing written. Host-run under a stub frappe.
"""
import importlib.util
import json
import os
import sys
import types
from datetime import date

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(APP_DIR, "epm", "report", "forecast_horizon_coverage")
REPORT_PY = os.path.join(REPORT_DIR, "forecast_horizon_coverage.py")
REPORT_JSON = os.path.join(REPORT_DIR, "forecast_horizon_coverage.json")
REPORT_JS = os.path.join(REPORT_DIR, "forecast_horizon_coverage.js")
SIDEBAR = os.path.join(APP_DIR, "epm", "sidebar", "planning", "planning.json")
WORKSPACE = os.path.join(APP_DIR, "epm", "workspace", "planning", "planning.json")

NAME = "Forecast Horizon Coverage"


def _periods(fy):
    rows = []
    for p in range(1, 13):
        start = date(fy, p, 1)
        end = date(fy + (p == 12), (p % 12) + 1, 1).toordinal() - 1
        rows.append({"fiscal_year": fy, "fiscal_period": p, "period_type": "Regular",
                     "start_date": start, "end_date": date.fromordinal(end)})
    return rows


CYCLES = [
    # 12 periods from FY2026 P10: P11-P12 of 2026 and P1-P10 of 2027, both years approved: ok
    {"name": "ZZ-FC-26", "scenario_id": "ZZ_FC", "scenario_type": "forecast", "fiscal_year": 2026,
     "as_of_date": date(2026, 10, 31), "horizon_periods": 12, "docstatus": 1},
    {"name": "ZZ-FC-27", "scenario_id": "ZZ_FC", "scenario_type": "forecast", "fiscal_year": 2027,
     "as_of_date": date(2026, 10, 31), "horizon_periods": 12, "docstatus": 1},
    # 6 periods from FY2026 P9; the FY2027 Cycle is a draft: uncovered
    {"name": "ZZ-FC2-26", "scenario_id": "ZZ_FC2", "scenario_type": "forecast", "fiscal_year": 2026,
     "as_of_date": date(2026, 9, 30), "horizon_periods": 6, "docstatus": 1},
    {"name": "ZZ-FC2-27", "scenario_id": "ZZ_FC2", "scenario_type": "forecast", "fiscal_year": 2027,
     "as_of_date": date(2026, 9, 30), "horizon_periods": 6, "docstatus": 0},
]


class _Frappe:
    def __init__(self, cycles=CYCLES, periods=None):
        self.cycles = cycles
        self.periods = periods if periods is not None else _periods(2026) + _periods(2027)
        self.get_list_calls = []
        self.writes = []

    def module(self):
        site = self
        frappe = types.ModuleType("frappe")
        frappe._dict = _Dict

        def get_list(doctype, filters=None, fields=None, order_by=None, limit_page_length=None, **kw):
            site.get_list_calls.append({"doctype": doctype, "filters": filters, "fields": fields,
                                        "order_by": order_by, "limit_page_length": limit_page_length})
            rows = [dict(c) for c in site.cycles]
            sid = (filters or {}).get("scenario_id")
            if sid:
                rows = [r for r in rows if r["scenario_id"] == sid]
            return rows

        def get_all(*a, **k):
            raise AssertionError("the report reads Budget Cycle with get_list (permissions apply)")

        def forbidden(*a, **k):
            site.writes.append(a)
            raise AssertionError("the report writes nothing")

        frappe.get_list = get_list
        frappe.get_all = get_all
        frappe.db = types.SimpleNamespace(set_value=forbidden, sql=forbidden, commit=forbidden)
        frappe.throw = lambda msg, *a, **k: (_ for _ in ()).throw(Exception(msg))
        frappe.whitelist = lambda *a, **k: (lambda f: f)
        return frappe

    def run(self, filters=None):
        frappe = self.module()
        fiscal_calendar = types.ModuleType("konsol.fiscal_calendar")
        fiscal_calendar.fiscal_period_rows = lambda: [dict(r) for r in self.periods]
        saved = {k: sys.modules.get(k) for k in ("frappe", "konsol.fiscal_calendar")}
        sys.modules["frappe"] = frappe
        sys.modules["konsol.fiscal_calendar"] = fiscal_calendar
        import konsol
        saved_attr = getattr(konsol, "fiscal_calendar", None)
        konsol.fiscal_calendar = fiscal_calendar
        try:
            spec = importlib.util.spec_from_file_location("forecast_horizon_coverage_t", REPORT_PY)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m.execute(filters)
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
            if saved_attr is None:
                delattr(konsol, "fiscal_calendar")
            else:
                konsol.fiscal_calendar = saved_attr


class _Dict(dict):
    def __init__(self, d=None, **kw):
        super().__init__(d or {}, **kw)

    def __getattr__(self, k):
        return self.get(k)


def _result(filters=None, site=None):
    site = site or _Frappe()
    out = site.run(filters)
    columns, data = out[0], out[1]
    message = out[2] if len(out) > 2 else None
    return site, columns, data, message


def test_one_row_per_round_with_state_and_sentence():
    site, columns, data, _ = _result()
    assert [(r["scenario_id"], r["as_of_date"], r["state"]) for r in data] == [
        ("ZZ_FC", "2026-10-31", "ok"),
        ("ZZ_FC2", "2026-09-30", "uncovered"),
    ]
    assert "ZZ-FC2-27 is a draft" in data[1]["sentence"]
    assert "FY2027 P1-P3" in data[1]["sentence"]
    assert data[0]["as_of_period"] == "FY2026 P10"
    assert data[0]["periods"] == "FY2026 P11-P12, FY2027 P1-P10"
    assert data[0]["cycles"] == "ZZ-FC-26, ZZ-FC-27"
    assert {c["fieldname"] for c in columns} >= {
        "scenario_id", "as_of_date", "as_of_period", "horizon_periods", "periods", "cycles", "state", "sentence"}
    assert site.writes == []


def test_reads_forecast_cycles_with_permissions_and_an_explicit_order():
    site, _, _, _ = _result()
    (call,) = site.get_list_calls
    assert call["doctype"] == "Budget Cycle"
    assert call["filters"]["scenario_type"] == "forecast"
    assert call["filters"]["docstatus"] == ["<", 2]
    assert call["order_by"]
    assert call["limit_page_length"] == 0
    assert {"name", "scenario_id", "scenario_type", "fiscal_year", "as_of_date", "horizon_periods",
            "docstatus"} <= set(call["fields"])


def test_scenario_filter_narrows_the_read():
    site, _, data, _ = _result({"scenario_id": "ZZ_FC2"})
    assert site.get_list_calls[0]["filters"]["scenario_id"] == "ZZ_FC2"
    assert [r["scenario_id"] for r in data] == ["ZZ_FC2"]


def test_an_undeclared_as_of_is_its_own_state():
    cycles = [{"name": "ZZ-FC-30", "scenario_id": "ZZ_FC", "scenario_type": "forecast", "fiscal_year": 2030,
               "as_of_date": date(2030, 3, 31), "horizon_periods": 3, "docstatus": 1}]
    _, _, data, _ = _result(site=_Frappe(cycles=cycles))
    assert data[0]["state"] == "undeclared_as_of"
    assert data[0]["as_of_period"] == ""


def test_no_round_says_so():
    _, _, data, message = _result(site=_Frappe(cycles=[]))
    assert data == []
    assert message and "No forecast round" in message


def test_report_json():
    with open(REPORT_JSON, encoding="utf-8") as fh:
        r = json.load(fh)
    assert r["doctype"] == "Report" and r["name"] == r["report_name"] == NAME
    assert r["report_type"] == "Script Report" and r["is_standard"] == "Yes"
    assert r["module"] == "EPM"
    assert r["ref_doctype"] == "Budget Cycle"
    assert {x["role"] for x in r["roles"]} == {"EPM Admin", "EPM Analyst", "EPM User"}


def test_report_js_registers_the_scenario_filter():
    with open(REPORT_JS, encoding="utf-8") as fh:
        js = fh.read()
    assert f'frappe.query_reports["{NAME}"]' in js
    assert '"scenario_id"' in js and '"Scenario"' in js
    assert "frappe.call" not in js and "frappe.xcall" not in js


def test_linked_from_the_forecast_section_of_the_sidebar_and_workspace():
    with open(SIDEBAR, encoding="utf-8") as fh:
        sidebar = json.load(fh)
    links = [(i.get("link_type"), i.get("link_to")) for i in sidebar["items"]]
    assert ("Report", NAME) in links
    assert links.index(("Report", NAME)) == links.index(("DocType", "Current Forecast")) + 1
    with open(WORKSPACE, encoding="utf-8") as fh:
        ws = json.load(fh)
    assert any(s["type"] == "Report" and s["link_to"] == NAME and s["label"] == NAME for s in ws["shortcuts"])
    assert NAME in ws["content"]
