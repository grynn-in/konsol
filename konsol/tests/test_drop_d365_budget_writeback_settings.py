"""EPM Settings no longer carries the D365 budget write-back (Q9-a on #348).

Deepak Pai decided to retire the D365 budget write-back with Budget Sheet
(konsol#348, option Q9-a; rejected: keep the write-back and its settings).
The doctype loses its fields; the patch deletes a site's stored values from
tabSingles and the client secret from __Auth. A second run deletes nothing new.
"""
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JSON_PY = os.path.join(APP_DIR, "pipeline", "doctype", "epm_settings", "epm_settings.json")
PATCH_PY = os.path.join(APP_DIR, "patches", "drop_d365_budget_writeback_settings.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")

DROPPED = {
    "tab_d365_writeback",
    "d365_writeback_section",
    "enable_d365_budget_writeback",
    "d365_resource_url",
    "d365_tenant_id",
    "d365_client_id",
    "d365_client_secret",
    "d365_fiscal_year_start_month",
}


def test_the_write_back_fields_are_gone():
    with open(JSON_PY) as f:
        doc = json.load(f)
    names = {f["fieldname"] for f in doc["fields"]}
    assert not (names & DROPPED), sorted(names & DROPPED)
    assert not (set(doc["field_order"]) & DROPPED)
    assert "airbyte_d365_source_definition_id" in names  # the extract side stays


def test_the_patch_is_registered():
    with open(PATCHES_TXT) as f:
        assert "konsol.patches.drop_d365_budget_writeback_settings" in f.read()


class _Db:
    def __init__(self):
        self.singles = {("EPM Settings", k): "x" for k in DROPPED}
        self.singles[("EPM Settings", "webhook_secret")] = "keep"
        self.singles[("Close Settings", "d365_tenant_id")] = "other doctype"
        self.auth = {("EPM Settings", "d365_client_secret"), ("EPM Settings", "webhook_secret")}

    def delete(self, table, filters):
        assert table == "Singles", table
        keys = filters["field"][1]
        for k in list(self.singles):
            if k[0] == filters["doctype"] and k[1] in keys:
                del self.singles[k]

    def sql(self, query, values=()):
        assert "__Auth" in query and "delete" in query.lower(), query
        doctype, name, fieldname = values
        assert name == doctype
        self.auth.discard((doctype, fieldname))


def _run(db):
    saved = sys.modules.get("frappe")
    frappe = types.ModuleType("frappe")
    frappe.db = db
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location("_patch_under_test", PATCH_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.execute()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


def test_the_patch_deletes_only_those_keys_and_is_idempotent():
    db = _Db()
    _run(db)
    assert db.singles == {
        ("EPM Settings", "webhook_secret"): "keep",
        ("Close Settings", "d365_tenant_id"): "other doctype",
    }
    assert db.auth == {("EPM Settings", "webhook_secret")}
    _run(db)
    assert len(db.singles) == 2 and len(db.auth) == 1
