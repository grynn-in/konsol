"""No doctype JSON grants a Budget* role (budget S2-C, Z8, decision Q7-c on #348).

konsol ships no Budget Submitter, Controller, Manager or Approver role. A site
that still holds one keeps it as site data (patches/retire_budget_roles.py) and
may name it as a Budget Layer's writer role; konsol's own permissions never do.
"""
import glob
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_no_doctype_json_grants_a_budget_role():
    found = []
    for path in glob.glob(os.path.join(APP_DIR, "*", "doctype", "*", "*.json")):
        with open(path) as f:
            meta = json.load(f)
        if not isinstance(meta, dict):
            continue
        roles = [p.get("role") for p in meta.get("permissions") or []]
        roles += [s.get("allow_edit") for s in meta.get("states") or [] if isinstance(s, dict)]
        roles += [t.get("allowed") for t in meta.get("transitions") or [] if isinstance(t, dict)]
        found += [(os.path.relpath(path, APP_DIR), r) for r in roles if (r or "").startswith("Budget ")]
    assert not found, found
