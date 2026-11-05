"""Every role a konsol report lists must be able to run it.

Frappe refuses a query report to a user without ``report`` permission on
the report's ref_doctype, whatever the report's own roles say. Twice on
5 Nov 2026 a report listed a role that was therefore always refused
(Consolidated Statement: System Manager on Trial Balance Submission; Close
Assertions: EPM Admin and System Manager on Assertion Run).
"""
import glob
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _doctype_report_roles():
    roles = {}
    for path in glob.glob(os.path.join(APP_DIR, "*", "doctype", "*", "*.json")):
        with open(path) as f:
            try:
                d = json.load(f)
            except ValueError:
                continue
        if isinstance(d, dict) and d.get("doctype") == "DocType":
            roles[d["name"]] = {p["role"] for p in d.get("permissions", []) if p.get("report")}
    return roles


def test_every_report_role_has_report_permission_on_its_ref_doctype():
    can_report = _doctype_report_roles()
    problems = []
    for path in glob.glob(os.path.join(APP_DIR, "*", "report", "*", "*.json")):
        with open(path) as f:
            r = json.load(f)
        if r.get("doctype") != "Report":
            continue
        ref = r.get("ref_doctype")
        allowed = can_report.get(ref)
        if allowed is None:
            continue  # a framework doctype: not konsol's to check here
        for role in (x["role"] for x in r.get("roles", [])):
            if role not in allowed:
                problems.append(f"{r['name']}: {role} has no report permission on {ref}")
    assert not problems, problems
