"""Consolidation Journal (konsol#292, #305-D2-1): replaces Consolidation
Adjustment. This file is shared across the journal rows (J02 the line child
table, J03 the header) and grows as each one lands.

J02 builds only `Consolidation Journal Line`: a child table naming the entity
on every line (#305-D2-12), so a cross-entity reclass is one journal instead
of one adjustment per entity.
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_DIR = os.path.join(APP_DIR, "consolidation", "doctype")
LINE = "consolidation_journal_line"


def _json(folder):
    with open(os.path.join(DOCTYPE_DIR, folder, folder + ".json")) as f:
        return json.load(f)


def test_line_is_a_child_table_with_the_five_fields():
    meta = _json(LINE)
    assert meta["name"] == "Consolidation Journal Line"
    assert meta["doctype"] == "DocType"
    assert meta["module"] == "Consolidation"
    assert meta["istable"] == 1
    assert meta.get("editable_grid") == 1
    assert meta.get("permissions") == []

    fields = meta["fields"]
    names = [f["fieldname"] for f in fields]
    assert names == [
        "data_area_id",
        "main_account",
        "debit_amount",
        "credit_amount",
        "description",
    ], names

    by_name = {f["fieldname"]: f for f in fields}

    entity = by_name["data_area_id"]
    assert entity["fieldtype"] == "Link"
    assert entity["options"] == "Entity"
    assert entity["label"] == "Entity"
    assert entity.get("reqd") == 1
    assert entity.get("in_list_view") == 1

    account = by_name["main_account"]
    assert account["fieldtype"] == "Link"
    assert account["options"] == "Main Account"
    assert account.get("reqd") == 1
    assert account.get("in_list_view") == 1

    for amount_field in ("debit_amount", "credit_amount"):
        amount = by_name[amount_field]
        assert amount["fieldtype"] == "Currency"
        assert amount.get("in_list_view") == 1
        assert amount.get("options") == "currency"
        assert not amount.get("reqd"), f"{amount_field} must not be reqd"

    description = by_name["description"]
    assert description["fieldtype"] == "Data"
    assert description.get("in_list_view") == 1

    assert not any(name.startswith("dim_") for name in names), (
        "no line dimensions until #255")


def test_line_folder_has_the_three_files():
    for name in ("__init__.py", LINE + ".json", LINE + ".py"):
        assert os.path.exists(os.path.join(DOCTYPE_DIR, LINE, name)), f"{LINE}/{name} missing"
