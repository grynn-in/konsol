"""Refresh sheet / Refresh workbook ribbon buttons (decided by Deepak Pai, 7 Nov 2026).

Two buttons on the add-in's Home-ribbon group, not in the task pane:

- **Refresh sheet** re-runs the active sheet: its =K.* formulas recalculate
  (``worksheet.calculate(true)``), and a snapshot sheet is snapshotted again
  with its saved parameters.
- **Refresh workbook** re-runs every saved snapshot and recalculates the whole
  workbook (``application.calculate(full)``).

Every refreshed sheet gets a stamp "Values as of <YYYY-MM-DD HH:MM> UTC" in
row 1: in the cell that already holds a stamp, else in the first empty cell
right of row 1's last used cell (G1 on the Monthly P&L, J1 on the TB
snapshot), so it never overwrites report data. Not signed in: nothing is
recalculated or stamped, and the message says sign in.

Three layers are tested here: the manifests (both ribbon groups carry both
buttons, each wired to a function the runtime associates), the page (it loads
ribbon.js and saves a snapshot's parameters), and ribbon.js itself, run under
node by ``excel_addin_ribbon_harness.mjs`` with stub Office and Excel objects.
Nothing here runs in Excel; see docs/excel-addin.md for what needs a live check.
"""
import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

from konsol import report_compiler

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDIN = os.path.join(APP_DIR, "public", "excel-addin")
HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "excel_addin_ribbon_harness.mjs")

NS = {
    "o": "http://schemas.microsoft.com/office/appforoffice/1.1",
    "v": "http://schemas.microsoft.com/office/taskpaneappversionoverrides",
    "bt": "http://schemas.microsoft.com/office/officeappbasictypes/1.0",
}
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
COMMANDS = {"refreshSheet", "refreshWorkbook"}
STAMP = re.compile(r"^Values as of \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC$")


def _read(name):
    with open(os.path.join(ADDIN, name), encoding="utf-8") as f:
        return f.read()


# -- the manifests -----------------------------------------------------------

def _ribbon_groups(manifest):
    root = ET.parse(os.path.join(ADDIN, manifest)).getroot()
    groups = []
    for ep in root.iter("{%s}ExtensionPoint" % NS["v"]):
        if ep.get(XSI_TYPE) == "PrimaryCommandSurface":
            groups.extend(ep.iter("{%s}Group" % NS["v"]))
    return root, groups


def _execute_functions(group):
    names = []
    for control in group.findall("v:Control", NS):
        action = control.find("v:Action", NS)
        if action is not None and action.get(XSI_TYPE) == "ExecuteFunction":
            names.append(action.find("v:FunctionName", NS).text.strip())
    return names


def test_both_ribbon_groups_carry_both_refresh_buttons():
    for manifest in ("manifest.xml", "manifest.demo.xml"):
        _, groups = _ribbon_groups(manifest)
        assert len(groups) == 2, (manifest, "expected a desktop and a web group")
        for group in groups:
            assert sorted(_execute_functions(group)) == sorted(COMMANDS), (manifest, group.get("id"))


def test_every_ribbon_function_is_associated_by_the_runtime():
    src = _read("ribbon.js")
    associated = set(re.findall(r'Office\.actions\.associate\(\s*"([^"]+)"', src))
    for manifest in ("manifest.xml", "manifest.demo.xml"):
        _, groups = _ribbon_groups(manifest)
        named = {n for g in groups for n in _execute_functions(g)}
        assert named <= associated, (manifest, named - associated)


def test_control_ids_are_unique_and_every_resid_resolves():
    for manifest in ("manifest.xml", "manifest.demo.xml"):
        root, _ = _ribbon_groups(manifest)
        ids = [c.get("id") for c in root.iter("{%s}Control" % NS["v"])]
        assert len(ids) == len(set(ids)), (manifest, ids)
        declared = {e.get("id") for e in root.iter() if e.tag.endswith("}Image") or e.tag.endswith("}String")
                    or e.tag.endswith("}Url")} - {None}
        used = {e.get("resid") for e in root.iter() if e.get("resid")}
        assert used <= declared, (manifest, used - declared)


def test_the_runtime_page_loads_ribbon_js_after_office_js():
    src = _read("index.html")
    office = src.index("office.js")
    ribbon = src.find('<script src="ribbon.js"></script>')
    assert ribbon > office, "index.html must load ribbon.js after office.js"


def test_the_pane_snapshot_saves_its_parameters():
    # Both the pane's button and window.konsolidatSnapshotReport go through
    # snapshotAndRemember, which saves the parameters after the paint.
    src = _read("index.html")
    start = src.index("function snapshotAndRemember(")
    body = src[start:src.index("\n      function ", start + 1)]
    assert "applyCellMap" in body and "rememberSnapshot" in body, body
    assert body.index("applyCellMap") < body.index("rememberSnapshot"), body
    snap = src[src.index("function snapshotReport("):]
    snap = snap[:snap.index("\n      function ", 1)]
    assert "snapshotAndRemember(" in snap, "the pane's Snapshot button does not save its parameters"
    assert re.search(r"window\.konsolidatSnapshotReport\s*=\s*function[^{]*\{\s*return snapshotAndRemember\(", src)


def test_the_pane_exposes_whoami_and_its_log_to_the_ribbon():
    src = _read("index.html")
    assert re.search(r"window\.konsolWhoami\s*=\s*function", src)
    assert re.search(r"window\.konsolLog\s*=\s*appendLog", src)


def test_no_blocking_dialogs():
    src = _read("ribbon.js")
    assert not re.search(r"\b(alert|confirm|prompt)\s*\(", src), "ribbon.js must not block Excel with a dialog"


# -- ribbon.js under node ----------------------------------------------------

def _row1(cells):
    """Row 1 of a shipped template's cell map, as a list of cell values."""
    row = []
    for cell in cells:
        m = re.fullmatch(r"([A-Z])1", cell["range"])
        if not m:
            continue
        col = ord(m.group(1)) - ord("A")
        while len(row) <= col:
            row.append("")
        row[col] = (cell.get("values") or cell.get("formulas"))[0][0]
    return row


# Row 1 as the shipped templates paint it, so the G1 / J1 the docs promise
# follows report_compiler, not a copy of it.
PNL_ROW1 = _row1(report_compiler.build_cell_map("pnl_monthly", "ZZE", 2024,
                                                accounts=[("ZZ4000", "ZZ Sales")])["cells"])
TB_ROW1 = _row1(report_compiler.build_trial_balance_long_map("ZZE", 2024, [], 1, 12)["cells"])
PNL_FORMULAS = [["Monthly P&L"], ["Account", "Jan"],
                ["ZZ Sales", '=K.EPM("ZZE", 2024, 1, "ZZ4000")', '=K.EPM("ZZE", 2024, 2, "ZZ4000")'],
                ["Total", "=SUM(B3:B3)"]]
TB_PARAMS = {"template_id": "trial_balance_long", "entity": "ZZE", "year": 2024,
             "period_from": 1, "period_to": 12, "sheet_name": "TB"}


def _run(**scenario):
    node = shutil.which("node")
    # Not a skip: a host without node would report green while testing nothing.
    assert node, "node is required to run the Excel add-in tests"
    proc = subprocess.run([node, HARNESS, json.dumps(scenario)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _messages(out):
    return " | ".join(m for _, m in out["logs"])


def test_both_commands_are_associated():
    out = _run(action="rememberSnapshot", params=TB_PARAMS)
    assert sorted(out["registered"]) == sorted(COMMANDS), out["registered"]


def test_refresh_sheet_recalculates_the_sheet_and_stamps_row_1():
    out = _run(action="refreshSheet", active="P&L",
               sheets=[{"name": "P&L", "row1": PNL_ROW1, "formulas": PNL_FORMULAS}])
    assert out["completed"] == 1, out
    assert out["calcs"] == [{"sheet": "P&L", "markAllDirty": True}], out["calcs"]
    (stamp,) = out["stamps"]
    assert (stamp["sheet"], stamp["row"], stamp["col"]) == ("P&L", 0, 6), stamp  # G1
    assert STAMP.match(stamp["value"]), stamp
    assert "Refreshed sheet P&L: 2 formulas" in _messages(out), out["logs"]


def test_a_second_refresh_reuses_the_stamp_cell():
    row1 = PNL_ROW1 + ["Values as of 2026-01-01 00:00 UTC"]
    out = _run(action="refreshSheet", active="P&L",
               sheets=[{"name": "P&L", "row1": row1, "formulas": PNL_FORMULAS}])
    (stamp,) = out["stamps"]
    assert stamp["col"] == 6, stamp
    assert stamp["value"] != "Values as of 2026-01-01 00:00 UTC"


def test_text_that_only_starts_like_a_stamp_is_not_overwritten():
    row1 = PNL_ROW1 + ["Values as of last Friday, per finance"]
    out = _run(action="refreshSheet", active="P&L",
               sheets=[{"name": "P&L", "row1": row1, "formulas": PNL_FORMULAS}])
    (stamp,) = out["stamps"]
    assert stamp["col"] == 7, stamp  # H1, right of the user's G1


def test_used_ranges_are_read_values_only():
    # Formatting far below the data must not inflate what Excel sends back.
    out = _run(action="refreshWorkbook", active="P&L",
               sheets=[{"name": "P&L", "row1": PNL_ROW1, "formulas": PNL_FORMULAS}])
    assert out["usedRangeValuesOnly"] and all(v is True for v in out["usedRangeValuesOnly"]), out


def test_a_protected_sheet_is_reported_not_stamped_and_others_still_are():
    out = _run(action="refreshWorkbook", active="P&L",
               sheets=[{"name": "P&L", "row1": PNL_ROW1, "formulas": PNL_FORMULAS},
                       {"name": "Locked", "row1": ["x"], "formulas": PNL_FORMULAS, "protected": True}])
    assert out["completed"] == 1
    assert [s["sheet"] for s in out["stamps"]] == ["P&L"], out["stamps"]
    assert "Locked (the sheet is protected)" in _messages(out), out["logs"]


def test_not_signed_in_recalculates_nothing_and_stamps_nothing():
    out = _run(action="refreshSheet", user=None, active="P&L",
               sheets=[{"name": "P&L", "row1": PNL_ROW1, "formulas": PNL_FORMULAS}])
    assert out["completed"] == 1, out
    assert out["calcs"] == [] and out["stamps"] == [], out
    assert "sign in" in _messages(out).lower(), out["logs"]
    assert out["panes"] == 1, "the pane opens so the user can sign in"


def test_not_signed_in_workbook_refresh_also_refuses():
    out = _run(action="refreshWorkbook", user=None, active="P&L",
               snapshots={"TB": TB_PARAMS},
               sheets=[{"name": "P&L", "row1": PNL_ROW1, "formulas": PNL_FORMULAS},
                       {"name": "TB", "row1": ["Trial Balance (snapshot)"], "formulas": [["x"]]}])
    assert out["completed"] == 1
    assert out["calcs"] == [] and out["stamps"] == [] and out["snapshots"] == [], out
    assert "sign in" in _messages(out).lower()


def test_a_server_error_is_not_reported_as_signed_out():
    for mode in ("reject", "throw"):
        out = _run(action="refreshSheet", whoami=mode, active="P&L",
                   sheets=[{"name": "P&L", "row1": PNL_ROW1, "formulas": PNL_FORMULAS}])
        assert out["completed"] == 1, (mode, out)
        assert out["calcs"] == [] and out["stamps"] == [], (mode, out)
        msg = _messages(out)
        assert "could not reach" in msg and "not signed in" not in msg, (mode, out["logs"])


def test_a_second_click_while_running_is_refused_and_both_complete():
    out = _run(action="refreshSheet", clicks=2, active="P&L",
               sheets=[{"name": "P&L", "row1": PNL_ROW1, "formulas": PNL_FORMULAS}])
    assert out["completed"] == 2, out
    assert len(out["calcs"]) == 1, out["calcs"]
    assert "already running" in _messages(out), out["logs"]


def test_a_click_after_a_failed_one_runs():
    out = _run(action="refreshSheet", clicks=2, settleBetweenClicks=True, excelRunFailCount=1,
               active="P&L", sheets=[{"name": "P&L", "row1": PNL_ROW1, "formulas": PNL_FORMULAS}])
    assert out["completed"] == 2, out
    assert len(out["stamps"]) == 1, out
    assert "already running" not in _messages(out)


def test_the_pane_can_see_whether_the_ribbon_is_bound():
    out = _run(action="rememberSnapshot", params=TB_PARAMS)
    assert out["associatedFlag"] is True
    assert "ribbon=" in _read("index.html")


def test_a_failure_inside_excel_still_completes_the_event():
    out = _run(action="refreshSheet", excelRunThrows=True, active="P&L",
               sheets=[{"name": "P&L", "row1": PNL_ROW1, "formulas": PNL_FORMULAS}])
    assert out["completed"] == 1, out
    assert out["stamps"] == []
    assert any(level == "err" for level, _ in out["logs"]), out["logs"]
    assert out["panes"] == 1


def test_workbook_failure_inside_excel_still_completes_the_event():
    out = _run(action="refreshWorkbook", excelRunThrows=True, active="P&L",
               sheets=[{"name": "P&L", "row1": PNL_ROW1, "formulas": PNL_FORMULAS}])
    assert out["completed"] == 1, out
    assert any(level == "err" for level, _ in out["logs"]), out["logs"]


def test_a_sheet_without_konsolidat_data_is_recalculated_but_not_stamped():
    out = _run(action="refreshSheet", active="Notes",
               sheets=[{"name": "Notes", "row1": ["hello"], "formulas": [["hello"], ["=1+1"]]}])
    assert out["completed"] == 1
    assert out["calcs"] == [{"sheet": "Notes", "markAllDirty": True}]
    assert out["stamps"] == []
    assert "no Konsolidat formulas" in _messages(out), out["logs"]


def test_refresh_sheet_on_a_snapshot_sheet_snapshots_it_again():
    out = _run(action="refreshSheet", active="TB", snapshots={"TB": TB_PARAMS}, snapshotRow1=TB_ROW1,
               sheets=[{"name": "TB", "row1": ["old"], "formulas": [["old"]]}])
    assert out["completed"] == 1
    assert out["snapshots"] == ["TB"], out
    (stamp,) = out["stamps"]
    assert (stamp["sheet"], stamp["col"]) == ("TB", 9), stamp  # J1, right of I1 "Periods" value
    assert "1 snapshot" in _messages(out), out["logs"]


def test_a_failed_snapshot_is_reported_and_not_stamped():
    out = _run(action="refreshSheet", active="TB", snapshots={"TB": TB_PARAMS},
               snapshotFails={"TB": "HTTP 500 — boom"},
               sheets=[{"name": "TB", "row1": ["old"], "formulas": [["old"]]}])
    assert out["completed"] == 1
    assert out["stamps"] == [], out["stamps"]
    assert "boom" in _messages(out)
    assert out["panes"] == 1


def test_refresh_workbook_resnapshots_recalculates_and_stamps_every_sheet():
    out = _run(action="refreshWorkbook", active="P&L", snapshots={"TB": TB_PARAMS}, snapshotRow1=TB_ROW1,
               sheets=[{"name": "P&L", "row1": PNL_ROW1, "formulas": PNL_FORMULAS},
                       {"name": "TB", "row1": ["old"], "formulas": [["old"]]},
                       {"name": "Notes", "row1": ["hello"], "formulas": [["hello"]]}])
    assert out["completed"] == 1, out
    assert out["snapshots"] == ["TB"]
    assert {"workbook": "Full"} in out["calcs"], out["calcs"]
    assert sorted(s["sheet"] for s in out["stamps"]) == ["P&L", "TB"], out["stamps"]
    assert all(STAMP.match(s["value"]) for s in out["stamps"])
    assert out["active"] == "P&L", "the user's sheet is active again after the snapshot"
    msg = _messages(out)
    assert "2 formulas" in msg and "1 snapshot" in msg, out["logs"]


def test_a_snapshot_whose_sheet_is_gone_is_forgotten_not_recreated():
    out = _run(action="refreshWorkbook", active="P&L", snapshots={"TB": TB_PARAMS},
               sheets=[{"name": "P&L", "row1": PNL_ROW1, "formulas": PNL_FORMULAS}])
    assert out["completed"] == 1
    assert out["snapshots"] == [], out
    assert out["settings"] == {}, out["settings"]
    assert "TB" in _messages(out)


def test_remember_snapshot_saves_the_parameters_in_the_workbook():
    out = _run(action="rememberSnapshot", params=dict(TB_PARAMS, extra="dropped"))
    assert out["error"] is None, out
    assert out["settings"] == {"TB": TB_PARAMS}, out["settings"]
    assert out["saves"] == 1


def test_only_the_handler_completes_the_event_once():
    # A refresh that both succeeds and logs must not complete twice.
    out = _run(action="refreshWorkbook", active="P&L",
               sheets=[{"name": "P&L", "row1": PNL_ROW1, "formulas": PNL_FORMULAS}])
    assert out["completed"] == 1, out
