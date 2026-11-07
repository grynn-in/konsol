"""Approvals in the record's own Actions menu, as in the compliance app.

Deepak Pai, 5 Nov 2026: "Approvals screen can be improved...it can be like
how compliance app works for approvals". The compliance app has no approvals
screen: a reviewer opens a list filtered to what is waiting, opens the record
and picks Approve or Reject from its Actions menu. konsol now works the same:

- every approval doctype loads ``public/js/approval_actions.js`` on its form,
  which puts Approve and Reject in the Actions menu and asks the server
  (``approval_api.approve_mode``) whether a reason is needed;
- the home page's "Waiting for approval" shortcuts open each list filtered to
  what is waiting: a workflow doctype by its "Pending Approval" state, any
  other by ``docstatus`` 0. The sidebars carry no filtered lists (Desk IA,
  7 Nov 2026: each doctype on exactly one sidebar, filtered links on
  workspaces only), so the sidebar's old Approvals section is gone;
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


#: Not an approval document (close_policy_model.APPROVAL_DOCTYPES) but approved
#: the same way, in its form's Actions menu: every upload or setup change asks
#: for a build, and a high-risk one waits in "Pending Review" (5 Nov 2026).
BUILD = "Build Approval"


def _listed():
    return set(_approval_doctypes()) | {BUILD}


def _pending_filter(doctype):
    if doctype == BUILD:
        return [doctype, "workflow_state", "=", "Pending Review"]
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
    # approve_mode is GET-only, and frappe.xcall defaults to POST: without the
    # explicit "GET" every Approve answered "Not permitted" (403), found by the
    # recorded browser walk-through on the v16 copy, 5 Nov 2026.
    assert re.search(r'xcall\(\s*METHODS\.approve_mode\s*,[^)]*,\s*"GET"\s*\)', js), \
        "approve_mode must be read with GET"
    # Writes are POSTs (a GET rolls back): approve and reject share one helper.
    assert 'type: "POST"' in js
    assert 'post(frm, "approve"' in js and 'post(frm, "reject"' in js


def test_the_workflow_doctypes_named_in_the_script_are_the_installed_ones():
    js = _read(SCRIPT)
    match = re.search(r"WORKFLOW_DOCTYPES\s*=\s*\[([^\]]*)\]", js)
    assert match, "WORKFLOW_DOCTYPES list missing from the script"
    named = set(re.findall(r'"([^"]+)"', match.group(1)))
    assert named == _workflow_doctypes(), named


def test_no_sidebar_lists_the_approvals_report_or_a_filtered_list():
    """Desk IA (7 Nov 2026): what is waiting is on the Close home page; a
    sidebar lists each doctype once, unfiltered."""
    for path in glob.glob(os.path.join(APP_DIR, "*", "sidebar", "*", "*.json")):
        items = _load(os.path.relpath(path, APP_DIR))["items"]
        assert not any(i.get("link_type") == "Report" and i.get("link_to") == "Approvals"
                       for i in items), path
        assert not any(i["type"] == "Section Break" and i["label"] == "Approvals"
                       for i in items), path
        assert not any(i.get("filters") for i in items), path


def test_home_shows_a_waiting_count_per_approval_doctype():
    ws = _load("consolidation/workspace/konsol/konsol.json")
    shortcuts = {s["label"]: s for s in ws["shortcuts"]}
    assert "Approvals" not in shortcuts
    waiting = {s["link_to"]: s for s in ws["shortcuts"] if s.get("stats_filter")}
    assert set(waiting) == _listed(), sorted(waiting)
    for doctype, s in waiting.items():
        assert s["type"] == "DocType" and s["doc_view"] == "List"
        # Four elements, never five: frappe.utils.cleanup_filters (utils.js) pops
        # the whole filter when the first one has five, so a trailing `false`
        # made every shortcut count all records (seen on the v16 copy, 5 Nov).
        assert json.loads(s["stats_filter"]) == [_pending_filter(doctype)], doctype
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


def test_a_sent_back_draft_loses_submit_too():
    """Review of #370 finding 1: the sent-back branch must clear the native
    Submit, or an approver submits the rejected draft unchanged."""
    js = _read(SCRIPT)
    branch = js[js.index('m.mode === "sent_back"'):js.index('set_indicator(__("Awaiting approval")')]
    assert "clear_primary_action()" in branch


def test_a_stale_answer_never_paints_another_record():
    """Review of #370 finding 4: Frappe reuses one frm per doctype."""
    js = _read(SCRIPT)
    assert "const asked = frm.doc.name" in js
    assert "frm.doc.name !== asked" in js
