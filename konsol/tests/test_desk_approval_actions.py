"""Approvals in the record's own Actions menu, as in the compliance app.

Deepak Pai, 5 Nov 2026: "Approvals screen can be improved...it can be like
how compliance app works for approvals". The compliance app has no approvals
screen: a reviewer opens a list filtered to what is waiting, opens the record
and picks Approve or Reject from its Actions menu. konsol now works the same:

- every approval doctype loads ``public/js/approval_actions.js`` on its form,
  which puts Approve and Reject in the Actions menu and asks the server
  (``approval_api.approve_mode``) whether a reason is needed;
- the sidebar's Approvals section and the home page's "Waiting for approval"
  shortcuts open each list filtered to what is waiting: a workflow doctype by
  its "Pending Approval" state, any other by ``docstatus`` 0;
- the Approvals report is gone, and a patch removes its Report record.

Rejected (same day): adding Frappe Workflows to the rate doctypes. Code
creates and submits Ownership Periods and exchange rates directly (Business
Combination, ERP adoption), and a workflow refuses a new document in any
state but its first.
"""
import ast
import glob
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = "public/js/approval_actions.js"
PENDING_STATE = "Pending Approval"


def _read(rel):
    with open(os.path.join(APP_DIR, rel)) as f:
        return f.read()


def _load(rel):
    return json.loads(_read(rel))


def _approval_doctypes():
    tree = ast.parse(_read("close/close_policy_model.py"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "APPROVAL_DOCTYPES":
            return tuple(ast.literal_eval(node.value))
    raise AssertionError("APPROVAL_DOCTYPES not found")


def _workflow_doctypes():
    tree = ast.parse(_read("workflows.py"))
    installed = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                     and getattr(n.targets[0], "id", None) == "INSTALLED")
    return set(installed) & set(_approval_doctypes())


def _hooks_value(name):
    tree = ast.parse(_read("hooks.py"))
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", None) == name)


def _pending_filter(doctype):
    if doctype in _workflow_doctypes():
        return [doctype, "status", "=", PENDING_STATE]
    return [doctype, "docstatus", "=", 0]


def test_every_approval_doctype_loads_the_actions_script():
    doctype_js = _hooks_value("doctype_js")
    for doctype in _approval_doctypes():
        scripts = doctype_js.get(doctype)
        scripts = [scripts] if isinstance(scripts, str) else list(scripts or ())
        assert SCRIPT in scripts, f"{doctype} form does not load {SCRIPT}"
    assert os.path.exists(os.path.join(APP_DIR, SCRIPT))


def test_the_script_routes_every_action_through_the_approval_api():
    js = _read(SCRIPT)
    for method in ("konsol.close.approval_api.approve_mode",
                   "konsol.close.approval_api.approve",
                   "konsol.close.approval_api.reject"):
        assert method in js, method
    # A workflow doctype's own Approve/Reject is intercepted, so a reason can be
    # asked for; the native Reject is refused without one.
    assert "before_workflow_action" in js
    # Writes are POSTs (a GET rolls back): approve and reject share one helper.
    assert 'type: "POST"' in js
    assert 'post(frm, "approve"' in js and 'post(frm, "reject"' in js


def test_the_workflow_doctypes_named_in_the_script_are_the_installed_ones():
    js = _read(SCRIPT)
    match = re.search(r"WORKFLOW_DOCTYPES\s*=\s*\[([^\]]*)\]", js)
    assert match, "WORKFLOW_DOCTYPES list missing from the script"
    named = set(re.findall(r'"([^"]+)"', match.group(1)))
    assert named == _workflow_doctypes(), named


def test_the_sidebar_approvals_section_lists_what_is_waiting():
    items = _load("consolidation/sidebar/konsol/konsol.json")["items"]
    assert not any(i.get("link_type") == "Report" and i.get("link_to") == "Approvals" for i in items)
    start = next(n for n, i in enumerate(items)
                 if i["type"] == "Section Break" and i["label"] == "Approvals")
    section = []
    for item in items[start + 1:]:
        if item["type"] == "Section Break":
            break
        section.append(item)
    by_doctype = {i["link_to"]: i for i in section}
    assert set(by_doctype) == set(_approval_doctypes()), sorted(by_doctype)
    for doctype, item in by_doctype.items():
        assert item["link_type"] == "DocType" and item["child"] == 1
        # The doctype's own unfiltered link (Monthly close, Deals) claims it.
        assert item["is_default_module"] == 0, doctype
        assert json.loads(item["filters"]) == [_pending_filter(doctype)], doctype


def test_home_shows_a_waiting_count_per_approval_doctype():
    ws = _load("consolidation/workspace/konsol/konsol.json")
    shortcuts = {s["label"]: s for s in ws["shortcuts"]}
    assert "Approvals" not in shortcuts
    waiting = {s["link_to"]: s for s in ws["shortcuts"] if s.get("stats_filter")}
    assert set(waiting) == set(_approval_doctypes()), sorted(waiting)
    for doctype, s in waiting.items():
        assert s["type"] == "DocType" and s["doc_view"] == "List"
        assert json.loads(s["stats_filter"]) == [_pending_filter(doctype) + [False]], doctype
        assert s["format"] == "{} waiting"
    blocks = json.loads(ws["content"])
    on_page = {b["data"]["shortcut_name"] for b in blocks if b["type"] == "shortcut"}
    assert {s["label"] for s in waiting.values()} <= on_page


def test_the_approvals_report_is_removed_with_a_patch():
    assert not os.path.exists(os.path.join(APP_DIR, "consolidation", "report", "approvals"))
    patches = _read("patches.txt")
    assert "konsol.patches.delete_approvals_report" in patches
    patch = _read("patches/delete_approvals_report.py")
    assert '"Report", "Approvals"' in patch


def test_my_work_sends_approvals_to_the_records_not_the_report():
    src = _read("close/mywork_report_model.py")
    assert "query-report/Approvals" not in src


def test_no_konsol_file_still_links_the_approvals_report():
    hits = []
    for path in glob.glob(os.path.join(APP_DIR, "**", "*.*"), recursive=True):
        if "/tests/" in path or "/patches/" in path or not path.endswith((".py", ".js", ".json")):
            continue
        with open(path, errors="ignore") as f:
            if "query-report/Approvals" in f.read():
                hits.append(os.path.relpath(path, APP_DIR))
    assert hits == [], hits
