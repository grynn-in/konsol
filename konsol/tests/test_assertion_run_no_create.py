"""No role may create an Assertion Run by hand (A02c).

``AssertionRun.before_insert`` refuses any insert that ``trigger_close_run``
did not start ("Start a close run with Run checks"), because a run inserted any
other way sits Queued with no job. The Desk still offered "Add Assertion Run"
to every role holding ``create`` (seen on the demo site, 22 Nov 2026), so the
button could only fail. ``trigger_close_run`` gates by role itself
(``frappe.only_for``) and inserts with ``ignore_permissions``, and sign-off
needs ``write``, not ``create``: no role needs ``create``.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DOCTYPE = os.path.join(HERE, "..", "consolidation", "doctype", "assertion_run")


def _read(name):
    with open(os.path.join(DOCTYPE, name)) as f:
        return f.read()


def test_no_role_holds_create_on_assertion_run():
    perms = json.loads(_read("assertion_run.json"))["permissions"]
    holders = [p["role"] for p in perms if p.get("create")]
    assert holders == [], f"create would show a button that always fails: {holders}"


def test_the_trigger_inserts_without_the_create_permission():
    src = _read("assertion_run.py")
    assert "doc.insert(ignore_permissions=True)" in src
    assert 'frappe.only_for(("EPM Admin", "EPM Analyst", "System Manager"))' in src


def test_sign_off_still_reads_write_not_create():
    perms = json.loads(_read("assertion_run.json"))["permissions"]
    writers = {p["role"] for p in perms if p.get("write") and not p.get("permlevel")}
    assert {"System Manager", "EPM Admin"} <= writers
