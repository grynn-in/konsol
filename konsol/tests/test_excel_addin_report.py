"""Applying a compiled report to the workbook: report.js (konsol#548 v1 T13).

Decided by Deepak Pai, 27 Nov 2026: a re-apply updates only a sheet the
add-in recognises as its own report; anything else gets a new sheet, and a
sheet applied before v1 is not adopted (Q3).

The add-in's mark on a report sheet has two parts, and both must agree:

- a sheet-scoped named item ``konsol_report`` over the report's area, with
  the comment ``konsol-report:<report_id>``;
- a document-settings entry ``konsolReports[report_id]`` holding the
  template, its version, the parameters, the area, the server, the sheet's
  worksheet id and when it was applied.

A copied sheet carries the name but not the worksheet id, so it forks: a new
report id and a new entry. An update clears only the named area, refuses to
grow over non-empty cells, and refuses a report applied from another server.

report.js runs under node in ``excel_addin_report_harness.mjs`` with a fake
Office and Excel; nothing here runs in Excel.
"""
import copy
import json
import os
import shutil
import subprocess

TESTS = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(TESTS, "excel_addin_report_harness.mjs")
GOLDEN = os.path.join(TESTS, "fixtures", "report_golden")
PANE = "https://pane.example"
OTHER = "https://other.example"


def _golden(name):
    with open(os.path.join(GOLDEN, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


#: Today's cell maps from build_cell_map: 1 account paints A1:M4, 2 accounts A1:M5.
ONE = _golden("pnl_monthly__1acct__actuals__2024")
TWO = _golden("pnl_monthly__2acct__actuals__2024")


def _with_report(cell_map, area, version="1", params=None, formats=None):
    m = copy.deepcopy(cell_map)
    m["report"] = {"template_id": m["template_id"], "template_version": version,
                   "params": params or {"entity": m["entity"], "year": m["year"]}, "area": area}
    if formats is not None:
        m["formats"] = formats
    return m


def _run(**scenario):
    node = shutil.which("node")
    # Not a skip: a host without node would report green while testing nothing.
    assert node, "node is required to run the Excel add-in tests"
    proc = subprocess.run([node, HARNESS, json.dumps(scenario)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert not out["unloadedReads"], ("read before load+sync", out["unloadedReads"])
    return out


def _sheet(out, name):
    for s in out["sheets"]:
        if s["name"] == name:
            return s
    raise AssertionError(f"no sheet {name!r} in {[s['name'] for s in out['sheets']]}")


def _own_sheet(name="P&L", ws_id="{ws-1}", report_id="r-abc", area="A1:M4", cells=None):
    return {"name": name, "id": ws_id, "cells": cells or {"A1": "Monthly P&L", "B3": {"f": "=K.EPM(1)"}},
            "names": [{"name": "konsol_report", "area": area, "comment": "konsol-report:" + report_id}]}


def _entry(ws_id="{ws-1}", area="A1:M4", server=PANE, template_id="pnl_monthly"):
    return {"template_id": template_id, "template_version": "1", "params": {"entity": "ZZ1", "year": 2024},
            "area": area, "server": server, "worksheet_id": ws_id, "applied_at": "2026-11-01T00:00:00.000Z"}


SNAPSHOTS = {"TB": {"template_id": "trial_balance_long", "sheet_name": "TB", "server": PANE}}


def _messages(out):
    return " | ".join(m for _, m in out["logs"])


# -- a fresh workbook --------------------------------------------------------

def test_fresh_workbook_writes_a_new_sheet_with_both_marker_parts():
    out = _run(action="apply", cellMap=ONE, options={"target": "active"},
               sheets=[{"name": "Sheet1", "id": "{ws-1}"}], active="Sheet1",
               settings={"konsolSnapshots": SNAPSHOTS})
    assert out["error"] is None, out["error"]
    r = out["result"]
    assert r["action"] == "new", r
    assert r["sheet"] == "P&L" and r["area"] == "A1:M4", r
    sheet = _sheet(out, "P&L")
    assert sheet["cells"]["A1"] == "Monthly P&L"
    assert sheet["cells"]["B3"] == '=K.EPM("ZZ1", 2024, 1, "4000")'
    (name,) = sheet["names"]
    assert name["name"] == "konsol_report" and name["area"] == "A1:M4", name
    assert name["comment"].startswith("konsol-report:"), name
    report_id = name["comment"][len("konsol-report:"):]
    assert report_id
    entry = out["settings"]["konsolReports"][report_id]
    assert entry["template_id"] == "pnl_monthly"
    assert entry["area"] == "A1:M4"
    assert entry["server"] == PANE
    assert entry["worksheet_id"] == sheet["id"]
    assert entry["applied_at"]
    assert "params" in entry and "template_version" in entry
    # Never touches the snapshots key.
    assert out["settings"]["konsolSnapshots"] == SNAPSHOTS
    assert out["pins"] >= 1 and out["saves"] >= 1
    assert out["active"] == "P&L"
    assert _sheet(out, "Sheet1")["cells"] == {}


def test_report_key_supplies_template_version_params_and_area():
    m = _with_report(ONE, "A1:M4", version="7", params={"entity": "ZZ1", "year": 2024, "scenario": "actuals"})
    out = _run(action="apply", cellMap=m, sheets=[{"name": "Sheet1"}], active="Sheet1")
    (entry,) = out["settings"]["konsolReports"].values()
    assert entry["template_version"] == "7"
    assert entry["params"] == {"entity": "ZZ1", "year": 2024, "scenario": "actuals"}


def test_formats_are_written():
    m = _with_report(ONE, "A1:M4", formats=[{"range": "B3:M4", "number_format": "#,##0;(#,##0)"},
                                           {"range": "A4", "bold": True}])
    out = _run(action="apply", cellMap=m, sheets=[{"name": "Sheet1"}], active="Sheet1")
    f = _sheet(out, "P&L")["formats"]
    assert f["B3"]["nf"] == "#,##0;(#,##0)" and f["M4"]["nf"] == "#,##0;(#,##0)", f
    assert f["A4"]["b"] is True, f


# -- update of the add-in's own sheet ----------------------------------------

def test_own_sheet_updates_and_clears_only_the_area():
    cells = {"A1": "Monthly P&L", "D1": "stale inside the area", "O10": "mine", "N1": "also mine"}
    out = _run(action="apply", cellMap=ONE, options={"target": "active"},
               sheets=[_own_sheet(cells=cells)], active="P&L",
               settings={"konsolReports": {"r-abc": _entry()}, "konsolSnapshots": SNAPSHOTS})
    assert out["error"] is None, out["error"]
    r = out["result"]
    assert r["action"] == "update" and r["sheet"] == "P&L" and r["area"] == "A1:M4", r
    assert [s["name"] for s in out["sheets"]] == ["P&L"], "no new sheet"
    sheet = _sheet(out, "P&L")
    assert "D1" not in sheet["cells"], "the old content inside the area is cleared"
    assert sheet["cells"]["O10"] == "mine" and sheet["cells"]["N1"] == "also mine"
    assert sheet["cells"]["B3"] == '=K.EPM("ZZ1", 2024, 1, "4000")'
    (name,) = sheet["names"]
    assert name["comment"] == "konsol-report:r-abc", "the report id is stable"
    entry = out["settings"]["konsolReports"]["r-abc"]
    assert entry["applied_at"] != "2026-11-01T00:00:00.000Z"
    assert entry["worksheet_id"] == "{ws-1}"
    assert out["settings"]["konsolSnapshots"] == SNAPSHOTS


def test_user_cells_outside_the_area_are_never_written():
    cells = {"N1": "x", "A5": "below", "Z100": 3}
    out = _run(action="apply", cellMap=ONE, sheets=[_own_sheet(cells=cells)], active="P&L",
               settings={"konsolReports": {"r-abc": _entry()}})
    assert out["result"]["action"] == "update"
    written = {a for s, a in out["writes"] if s == "P&L"}
    assert not written & {"N1", "A5", "Z100"}, written
    sheet = _sheet(out, "P&L")
    assert sheet["cells"]["N1"] == "x" and sheet["cells"]["A5"] == "below" and sheet["cells"]["Z100"] == 3


def test_renamed_own_sheet_is_still_recognised():
    out = _run(action="apply", cellMap=ONE, sheets=[_own_sheet(name="My P&L")], active="My P&L",
               settings={"konsolReports": {"r-abc": _entry()}})
    r = out["result"]
    assert r["action"] == "update" and r["sheet"] == "My P&L", r
    assert [s["name"] for s in out["sheets"]] == ["My P&L"]


def test_update_that_grows_into_empty_cells_moves_the_name():
    out = _run(action="apply", cellMap=TWO, sheets=[_own_sheet()], active="P&L",
               settings={"konsolReports": {"r-abc": _entry()}})
    r = out["result"]
    assert r["action"] == "update" and r["area"] == "A1:M5", r
    (name,) = _sheet(out, "P&L")["names"]
    assert name["area"] == "A1:M5" and name["comment"] == "konsol-report:r-abc"
    assert out["settings"]["konsolReports"]["r-abc"]["area"] == "A1:M5"


def test_update_that_shrinks_clears_the_old_area():
    cells = {"A5": "old total", "B5": {"f": "=SUM(B3:B4)"}}
    out = _run(action="apply", cellMap=ONE, sheets=[_own_sheet(area="A1:M5", cells=cells)], active="P&L",
               settings={"konsolReports": {"r-abc": _entry(area="A1:M5")}})
    assert out["result"]["action"] == "update"
    sheet = _sheet(out, "P&L")
    assert "A5" not in sheet["cells"] and "B5" not in sheet["cells"]
    assert sheet["names"][0]["area"] == "A1:M4"


def test_overlap_is_refused_naming_the_cells():
    cells = {"A1": "Monthly P&L", "B5": "mine", "M5": 4}
    settings = {"konsolReports": {"r-abc": _entry()}}
    out = _run(action="apply", cellMap=TWO, sheets=[_own_sheet(cells=cells)], active="P&L",
               settings=settings)
    r = out["result"]
    assert r["action"] == "refused", r
    assert "B5" in r["message"] and "M5" in r["message"], r["message"]
    assert "new sheet" in r["message"], r["message"]
    assert out["writes"] == [], "nothing is written or cleared"
    assert out["settings"] == settings
    assert _sheet(out, "P&L")["names"][0]["area"] == "A1:M4"


def test_template_mismatch_writes_a_new_sheet():
    m = copy.deepcopy(ONE)
    m["template_id"] = "pnl_actual_budget_monthly"
    out = _run(action="apply", cellMap=m, sheets=[_own_sheet()], active="P&L",
               settings={"konsolReports": {"r-abc": _entry()}})
    r = out["result"]
    assert r["action"] == "new" and r["sheet"] == "P&L (2)", r
    assert _sheet(out, "P&L")["cells"]["A1"] == "Monthly P&L"


def test_target_new_never_updates():
    out = _run(action="apply", cellMap=ONE, options={"target": "new"}, sheets=[_own_sheet()], active="P&L",
               settings={"konsolReports": {"r-abc": _entry()}})
    assert out["result"]["action"] == "new" and out["result"]["sheet"] == "P&L (2)"
    assert [a for s, a in out["writes"] if s == "P&L"] == []


# -- sheets that are not the add-in's own ------------------------------------

def test_sheet_named_like_the_report_without_marker_gets_a_new_sheet():
    cells = {"A1": "my own P&L", "C7": 42}
    out = _run(action="apply", cellMap=ONE, sheets=[{"name": "P&L", "id": "{ws-1}", "cells": cells}],
               active="P&L")
    r = out["result"]
    assert r["action"] == "new" and r["sheet"] == "P&L (2)", r
    assert _sheet(out, "P&L")["cells"] == cells, "a sheet without the marker is never cleared"
    assert [a for s, a in out["writes"] if s == "P&L"] == []


def test_the_next_free_suffix_is_used():
    out = _run(action="apply", cellMap=ONE,
               sheets=[{"name": "P&L"}, {"name": "p&l (2)"}], active="P&L")
    assert out["result"]["sheet"] == "P&L (3)", out["result"]


def test_pre_v1_sheet_is_not_adopted():
    # A sheet the old pane painted: right cells, no marker, maybe a snapshot entry.
    out = _run(action="apply", cellMap=ONE, sheets=[{"name": "P&L", "cells": {"A1": "Monthly P&L"}}],
               active="P&L", settings={"konsolSnapshots": SNAPSHOTS})
    assert out["result"]["action"] == "new" and out["result"]["sheet"] == "P&L (2)"


def test_name_without_settings_entry_is_not_own():
    out = _run(action="inspect", sheets=[_own_sheet()], active="P&L", settings={})
    r = out["result"]
    assert r["own"] is False and r["report_id"] == "r-abc", r
    assert r["reason"]
    out = _run(action="apply", cellMap=ONE, sheets=[_own_sheet()], active="P&L", settings={})
    assert out["result"]["action"] == "new"


def test_settings_entry_without_name_is_not_own():
    out = _run(action="inspect", sheets=[{"name": "P&L", "id": "{ws-1}"}], active="P&L",
               settings={"konsolReports": {"r-abc": _entry()}})
    assert out["result"]["own"] is False


def test_workbook_scoped_comment_text_is_not_enough():
    sheet = _own_sheet()
    sheet["names"][0]["comment"] = "something else"
    out = _run(action="inspect", sheets=[sheet], active="P&L",
               settings={"konsolReports": {"r-abc": _entry()}})
    assert out["result"]["own"] is False, out["result"]


# -- a copied sheet forks ----------------------------------------------------

def test_copied_sheet_forks_a_new_report():
    original = _own_sheet(name="P&L", ws_id="{ws-1}")
    copy_ = _own_sheet(name="P&L (2)", ws_id="{ws-2}")
    settings = {"konsolReports": {"r-abc": _entry(ws_id="{ws-1}")}}
    seen = _run(action="inspect", sheets=[original, copy_], active="P&L (2)", settings=settings)["result"]
    assert seen["own"] is True and seen.get("copied") is True, seen

    out = _run(action="apply", cellMap=ONE, sheets=[original, copy_], active="P&L (2)", settings=settings)
    r = out["result"]
    assert r["action"] == "update" and r["sheet"] == "P&L (2)", r
    reports = out["settings"]["konsolReports"]
    assert reports["r-abc"] == settings["konsolReports"]["r-abc"], "the original's entry is untouched"
    (new_id,) = [k for k in reports if k != "r-abc"]
    assert reports[new_id]["worksheet_id"] == "{ws-2}"
    assert _sheet(out, "P&L (2)")["names"][0]["comment"] == "konsol-report:" + new_id
    assert _sheet(out, "P&L")["names"][0]["comment"] == "konsol-report:r-abc"
    assert [a for s, a in out["writes"] if s == "P&L"] == []


def test_inspect_own_sheet():
    r = _run(action="inspect", sheets=[_own_sheet()], active="P&L",
             settings={"konsolReports": {"r-abc": _entry()}})["result"]
    assert r["own"] is True and r["report_id"] == "r-abc" and r["entry"]["area"] == "A1:M4", r
    assert not r.get("copied")


def test_inspect_fresh_sheet():
    r = _run(action="inspect", sheets=[{"name": "Sheet1"}], active="Sheet1")["result"]
    assert r["own"] is False and r["report_id"] is None and r["entry"] is None and r["reason"], r


# -- refusals ------------------------------------------------------------------

def test_other_server_is_refused():
    settings = {"konsolReports": {"r-abc": _entry(server=OTHER)}}
    out = _run(action="apply", cellMap=ONE, sheets=[_own_sheet()], active="P&L", settings=settings)
    r = out["result"]
    assert r["action"] == "refused", r
    assert OTHER in r["message"] and PANE in r["message"], r["message"]
    assert "Switch the pane's Server back" in r["message"], r["message"]
    assert out["writes"] == [] and out["settings"] == settings
    assert len(out["sheets"]) == 1


def test_missing_excel_api_1_7_is_refused():
    out = _run(action="apply", cellMap=ONE, sheets=[{"name": "Sheet1"}], active="Sheet1", api17=False)
    r = out["result"]
    assert r["action"] == "refused" and "1.7" in r["message"], r
    assert out["writes"] == [] and out["pins"] == 0 and len(out["sheets"]) == 1


def test_cells_outside_the_declared_area_are_refused():
    m = _with_report(ONE, "A1:M3")  # B4:M4 lies outside
    out = _run(action="apply", cellMap=m, sheets=[{"name": "Sheet1"}], active="Sheet1")
    r = out["result"]
    assert r["action"] == "refused" and "A1:M3" in r["message"], r
    assert out["writes"] == [] and len(out["sheets"]) == 1


def test_outcome_is_logged_through_the_pane_hook():
    out = _run(action="apply", cellMap=ONE, sheets=[{"name": "Sheet1"}], active="Sheet1")
    assert "P&L" in _messages(out), out["logs"]
    out = _run(action="apply", cellMap=ONE, sheets=[{"name": "Sheet1"}], active="Sheet1", api17=False)
    assert any(level == "err" for level, _ in out["logs"]), out["logs"]


def test_without_a_log_hook_apply_still_works():
    out = _run(action="apply", cellMap=ONE, sheets=[{"name": "Sheet1"}], active="Sheet1", noLogHook=True)
    assert out["error"] is None and out["result"]["action"] == "new"


def test_unsaved_settings_are_reported():
    out = _run(action="apply", cellMap=ONE, sheets=[{"name": "Sheet1"}], active="Sheet1", saveFails=True)
    r = out["result"]
    assert r["action"] == "new" and "could not save" in r["message"], r
