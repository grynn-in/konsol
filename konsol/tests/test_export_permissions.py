"""Export permission is one rule, not a per-doctype accident (Deepak Pai, 6 Nov 2026).

Before this, the Entity list, Dimensions, Measures and the Build Approval /
Close Event audit trail could not be exported by any business role, while
exchange rates and trial balances could by most. Nothing recorded why.

The shipped rule: on every stored (not single, not virtual, not child)
konsol doctype, a permission row at level 0 that gives EPM Admin or EPM
Analyst read also gives export. It grants no new read access: a role with no
row, or a row without read, is left as it is. Other roles keep what they have.
A site changes any of this in Role Permissions Manager, without code.
"""
import glob
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT_ROLES = ("EPM Admin", "EPM Analyst")


def _stored_doctypes():
    for path in sorted(glob.glob(os.path.join(APP_DIR, "*", "doctype", "*", "*.json"))):
        if os.path.basename(path)[:-5] != os.path.basename(os.path.dirname(path)):
            continue
        with open(path) as f:
            meta = json.load(f)
        if meta.get("doctype") != "DocType":
            continue
        if meta.get("istable") or meta.get("issingle") or meta.get("is_virtual"):
            continue
        yield meta


def test_epm_admin_and_analyst_can_export_whatever_they_can_read():
    missing = sorted(
        f"{meta['name']} / {p['role']}"
        for meta in _stored_doctypes()
        for p in meta.get("permissions", [])
        if p.get("role") in EXPORT_ROLES and not p.get("permlevel")
        and p.get("read") and not p.get("export")
    )
    assert not missing, "read without export: " + ", ".join(missing)


def test_the_rule_covers_the_doctypes_it_was_written_for():
    names = {m["name"] for m in _stored_doctypes()}
    for doctype in ("Entity", "Dimension", "Measure", "Build Approval", "Close Event", "Main Account"):
        assert doctype in names
