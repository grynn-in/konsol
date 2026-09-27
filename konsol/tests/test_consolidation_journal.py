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


# --- J03: the header ---------------------------------------------------------

HEADER = "consolidation_journal"
REVERSAL_DESCRIPTION = (
    "Leave both blank for no reversal. Otherwise the reversal posts in exactly this period. "
    "It must be a declared Regular period after this journal's own, and Open when the journal "
    "is approved (konsol#305-D2-11).")


def _data_fields(meta):
    return [f for f in meta["fields"] if f["fieldtype"] not in ("Tab Break", "Section Break", "Column Break")]


def test_header_is_a_submittable_journal():
    meta = _json(HEADER)
    assert meta["name"] == "Consolidation Journal"
    assert meta["doctype"] == "DocType"
    assert meta["module"] == "Consolidation"
    assert meta.get("istable", 0) == 0
    assert meta["is_submittable"] == 1
    assert meta["track_changes"] == 1
    assert meta["autoname"] == "CJ-.#####"
    assert meta["title_field"] == "description"


def test_header_tabs_are_journal_then_workflow():
    tabs = [f["label"] for f in _json(HEADER)["fields"] if f["fieldtype"] == "Tab Break"]
    assert tabs == ["Journal", "Workflow"], tabs


def test_header_fields_in_order():
    meta = _json(HEADER)
    names = [f["fieldname"] for f in _data_fields(meta)]
    assert names == [
        "consolidation_group", "adjustment_type", "fiscal_year", "fiscal_period", "currency",
        "description", "lines", "total_debit", "total_credit",
        "reverse_fiscal_year", "reverse_fiscal_period",
        "status", "approved_by", "approved_at", "amended_from",
    ], names
    # the journal fields sit on the Journal tab, the workflow fields on the Workflow tab
    tab, where = None, {}
    for f in meta["fields"]:
        if f["fieldtype"] == "Tab Break":
            tab = f["label"]
        else:
            where[f["fieldname"]] = tab
    for name in names[:11]:
        assert where[name] == "Journal", (name, where[name])
    for name in names[11:]:
        assert where[name] == "Workflow", (name, where[name])


def test_header_field_types():
    by = {f["fieldname"]: f for f in _json(HEADER)["fields"]}

    group = by["consolidation_group"]
    assert group["fieldtype"] == "Data" and group.get("reqd") == 1, "a Link to the group tree is impossible (P9)"

    kind = by["adjustment_type"]
    assert kind["fieldtype"] == "Select" and kind["options"] == "topside\nreclassification" and kind.get("reqd") == 1

    for name in ("fiscal_year", "fiscal_period"):
        assert by[name]["fieldtype"] == "Int" and by[name].get("reqd") == 1, name

    currency = by["currency"]
    assert currency["fieldtype"] == "Link" and currency["options"] == "Currency"
    assert currency.get("read_only") == 1
    assert currency["label"] == "Reporting Currency"
    assert currency["description"] == "The group's reporting currency. Every amount on this journal is in it."

    assert by["description"]["fieldtype"] == "Small Text"

    lines = by["lines"]
    assert lines["fieldtype"] == "Table" and lines["options"] == "Consolidation Journal Line"
    assert lines.get("reqd") == 1

    for name in ("total_debit", "total_credit"):
        f = by[name]
        assert f["fieldtype"] == "Currency" and f.get("read_only") == 1 and f.get("options") == "currency", name

    status = by["status"]
    assert status["fieldtype"] == "Select"
    assert status["options"] == "Draft\nPending Approval\nApproved\nReversed"
    assert status.get("default") == "Draft"
    assert status.get("in_list_view") == 1 and status.get("read_only") == 1

    approved_by = by["approved_by"]
    assert approved_by["fieldtype"] == "Link" and approved_by["options"] == "User" and approved_by.get("read_only") == 1
    assert by["approved_at"]["fieldtype"] == "Datetime" and by["approved_at"].get("read_only") == 1

    amended = by["amended_from"]
    assert amended["fieldtype"] == "Link" and amended["options"] == "Consolidation Journal"
    assert amended.get("no_copy") == 1 and amended.get("read_only") == 1


def test_reversal_fields_are_int_with_no_default():
    by = {f["fieldname"]: f for f in _json(HEADER)["fields"]}
    for name, label in (("reverse_fiscal_year", "Reverse In Fiscal Year"),
                        ("reverse_fiscal_period", "Reverse In Fiscal Period")):
        f = by[name]
        assert f["fieldtype"] == "Int", name
        assert f["label"] == label, name
        assert "default" not in f, f"{name}: blank reads as 0, no default (signoff_model.py:20-21)"
        assert not f.get("reqd"), name
        assert f["description"] == REVERSAL_DESCRIPTION, name


def test_header_drops_the_old_fields():
    names = {f["fieldname"] for f in _json(HEADER)["fields"]}
    for gone in ("data_area_id", "journal_id", "posted_by", "reversal_journal_id", "auto_reverse_period"):
        assert gone not in names, f"{gone} must not be on the journal header"


def test_header_permissions_are_consolidation_adjustments_matrix():
    perms = {p["role"]: {k: v for k, v in p.items() if k != "role" and v}
             for p in _json(HEADER)["permissions"]}
    assert perms == {
        "System Manager": dict(read=1, write=1, create=1, delete=1, submit=1, cancel=1, amend=1),
        "EPM Admin": dict(read=1, write=1, submit=1, cancel=1, report=1, print=1, export=1, email=1),
        "EPM Analyst": dict(read=1, write=1, create=1, delete=1, amend=1, report=1, print=1, export=1, email=1),
        "EPM User": dict(read=1, report=1, print=1, export=1, email=1),
    }, perms


def test_header_folder_has_the_three_files():
    for name in ("__init__.py", HEADER + ".json", HEADER + ".py"):
        assert os.path.exists(os.path.join(DOCTYPE_DIR, HEADER, name)), f"{HEADER}/{name} missing"


def test_line_entity_ignores_user_permissions():
    """#305-P21-1: journals are not entity-scoped. Frappe checks User Permissions
    on child-row Links too, so without this a Viewer scoped to one entity could
    list every journal but open none with a line on another entity (review
    finding 3, 27 Sep)."""
    fields = {f["fieldname"]: f for f in _json("consolidation_journal_line")["fields"]}
    assert fields["data_area_id"].get("ignore_user_permissions") == 1
