"""Budget Cycle and Budget Annual Input carry their Scenario's type.

Decided by Deepak Pai, 7 Nov 2026, on #443 (option B; rejected A, separate
Budget and Forecast rail entries; C, Forecast doctypes; D, unfiltered
duplicates): one rail entry, Planning, lists budgets and forecasts apart. Budget
and forecast share these two doctypes; a forecast is one whose Scenario has
``scenario_type = forecast``. A Frappe list filters only on the doctype's own
columns, so each doctype gets a read-only copy of the type to filter on.

Frappe v16 mechanics this relies on (frappe/model/document.py
``get_invalid_links``, run by ``_validate_links`` on every insert and save):
for each Link field it reads ``frappe.db.get_value(<linked doctype>, <name>,
<source fields>)`` from the linked row's stored columns and copies each
``fetch_from`` value. It is copied only while the document is new or not
submitted, so a submitted cycle keeps the value it had; Scenario refuses a type
change while any cycle or annual input names it, so that value cannot go stale.

The copy is navigation only. It must never reach ClickHouse: the warehouse
table ``epm_gold.budget_annual_input`` has no such column, and the scenario's type is already in
``epm_gold.scenario_definitions``.
"""
import ast
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DT_DIR = os.path.join(APP_DIR, "epm", "doctype")

#: doctype folder -> (link field, fetch_from)
FETCHED = {
    "budget_cycle": ("scenario_id", "scenario_id.scenario_type"),
    "budget_annual_input": ("scenario_id", "scenario_id.scenario_type"),
}


def _meta(folder):
    with open(os.path.join(DT_DIR, folder, folder + ".json")) as f:
        return json.load(f)


def _field(meta, fieldname):
    return next((f for f in meta["fields"] if f["fieldname"] == fieldname), None)


def _src(*parts):
    with open(os.path.join(APP_DIR, *parts)) as f:
        return f.read()


def test_each_planning_doctype_carries_a_read_only_fetched_scenario_type():
    for folder, (link, fetch_from) in FETCHED.items():
        f = _field(_meta(folder), "scenario_type")
        assert f is not None, f"{folder}: no scenario_type field"
        assert f["fieldtype"] == "Data", (folder, f["fieldtype"])
        assert f.get("fetch_from") == fetch_from, (folder, f.get("fetch_from"))
        assert f.get("read_only") == 1, folder
        assert f.get("in_list_view") == 1 and f.get("in_standard_filter") == 1, folder
        assert f.get("label") == "Scenario Type", folder
        assert (f.get("description") or "").strip(), folder
        # Never fetch_if_empty: the copy follows the link, it is not a default.
        assert not f.get("fetch_if_empty"), folder
        assert not f.get("is_virtual"), folder


def test_the_link_each_fetch_follows_exists():
    """``fetch_from`` names ``<link field>.<source>``; the link must be a Link to
    the doctype that holds the source."""
    targets = {"scenario_id": "Scenario"}
    for folder, (link, _) in FETCHED.items():
        f = _field(_meta(folder), link)
        assert f is not None and f["fieldtype"] == "Link" and f["options"] == targets[link], folder


def test_the_field_follows_its_link():
    """Shown next to the link it copies from."""
    for folder, (link, _) in FETCHED.items():
        names = [f["fieldname"] for f in _meta(folder)["fields"]]
        assert names.index("scenario_type") == names.index(link) + 1, (folder, names)


# --- every write path goes through _validate_links -----------------------------

WRITERS = [
    ("api.py",),
    ("patches", "reshape_budget_input_to_cycle.py"),
    ("epm", "doctype", "budget_cycle", "budget_cycle.py"),
    ("epm", "doctype", "budget_annual_input", "budget_annual_input.py"),
]


def test_no_writer_skips_the_link_fetch():
    """``ignore_links`` or a raw ``db_insert`` skips ``_validate_links``, and with
    it the copy. The rows these files write go through ``insert``/``save``."""
    for parts in WRITERS:
        src = _src(*parts)
        assert "ignore_links" not in src, parts
        assert "db_insert" not in src, parts
        assert "bulk_insert" not in src, parts


def test_writers_set_the_link_then_insert_or_save():
    """api.py and the reshape patch create their documents with new_doc and set
    the link field before insert, so the fetch has something to follow."""
    api = _src("api.py")
    assert re.search(r'new_doc\("Budget Entry"\)\s*\n\s*doc\.update\(entry_key\)', api)
    reshape = _src("patches", "reshape_budget_input_to_cycle.py")
    assert "doc.scenario_id = scenario_id" in reshape and "doc.insert()" in reshape


# --- the copy never reaches ClickHouse ------------------------------------------

def _module_assign(src, name):
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == name:
            return ast.literal_eval(n.value)
    raise AssertionError(name)


def test_annual_input_field_map_does_not_send_scenario_type():
    """``sync_doctype_filtered`` sends exactly ``CH_FIELD_MAP``'s columns
    (clickhouse.py: ``frappe_fields = list(field_map.values())``). The map is
    the fixed columns plus ``dim_*`` Data fields, so the new field stays out."""
    src = _src("epm", "doctype", "budget_annual_input", "budget_annual_input.py")
    fixed = _module_assign(src, "FIXED_FIELD_MAP")
    assert "scenario_type" not in fixed and "scenario_type" not in fixed.values()
    assert 'startswith("dim_")' in src


def test_budget_cycle_has_no_warehouse_table():
    src = _src("epm", "doctype", "budget_cycle", "budget_cycle.py")
    assert "CH_TABLE" not in src and "CH_FIELD_MAP" not in src


def test_the_budget_input_table_has_no_scenario_type_column():
    src = _src("clickhouse.py")
    ddl = _module_assign(src, "_REFERENCE_TABLE_DDL")
    assert "scenario_type" not in ddl["epm_gold.budget_annual_input"]
