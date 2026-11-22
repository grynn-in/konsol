"""Rate Sheet and Rate Sheet Row doctype definitions (konsol#592 pair 1, T6).

Reads the JSON only. Decisions: QA-1 (one rate for a Constant sheet), P1-Q2 B
(a Constant sheet locks when its year closes), P1-Q3 A (no Disabled flag),
P1-Q4 A (year and period entered, dates derived), #312-3 (a correction
supersedes the approved sheet).
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(APP_DIR, "consolidation", "doctype")
LAYOUT = ("Tab Break", "Section Break", "Column Break")


def _load(name):
    with open(os.path.join(BASE, name, f"{name}.json")) as f:
        return json.load(f)


def _fields(name):
    return {f["fieldname"]: f for f in _load(name)["fields"]}


def test_both_doctypes_have_python_modules():
    for name in ("rate_sheet", "rate_sheet_row"):
        assert os.path.exists(os.path.join(BASE, name, "__init__.py"))
        assert os.path.exists(os.path.join(BASE, name, f"{name}.py"))


def test_rate_sheet_is_submittable_and_tracked():
    d = _load("rate_sheet")
    assert d["name"] == "Rate Sheet" and d["module"] == "Consolidation"
    assert d["is_submittable"] == 1 and d["track_changes"] == 1
    assert d["istable"] == 0
    assert d["title_field"] == "title"
    for f in ("rate_basis", "fiscal_year", "status"):
        assert f in d["search_fields"]


def test_tabs_and_sections():
    d = _load("rate_sheet")
    kinds = {f["fieldname"]: f for f in d["fields"]}
    tabs = [kinds[n]["label"] for n in d["field_order"] if kinds[n]["fieldtype"] == "Tab Break"]
    assert tabs == ["Sheet", "Rates", "Approval"]
    sections = [kinds[n]["fieldtype"] for n in d["field_order"] if kinds[n]["fieldtype"] == "Section Break"]
    assert sections
    # every field is placed, and every placed field is defined
    assert sorted(d["field_order"]) == sorted(kinds)
    source = kinds["source_section"]
    assert source["collapsible"] == 1
    order = d["field_order"]
    assert order.index("copied_from") > order.index("source_section")
    assert order.index("rates") > order.index(next(n for n in order if kinds[n]["label"] == "Rates"))
    assert order.index("change_reason") > order.index(next(n for n in order if kinds[n]["label"] == "Approval"))


def test_sheet_tab_fields():
    f = _fields("rate_sheet")
    assert f["rate_basis"]["fieldtype"] == "Select"
    assert f["rate_basis"]["options"].split("\n") == ["Statutory", "Constant"]
    assert f["rate_basis"]["reqd"] == 1 and f["rate_basis"]["set_only_once"] == 1
    assert f["fiscal_year"]["fieldtype"] == "Int" and f["fiscal_year"]["reqd"] == 1
    assert not f["fiscal_year"].get("read_only")
    p = f["fiscal_period"]
    assert p["fieldtype"] == "Int" and not p.get("reqd") and not p.get("read_only")
    assert p["mandatory_depends_on"] == "eval:doc.rate_basis == 'Statutory'"
    assert p["depends_on"] == "eval:doc.rate_basis == 'Statutory'"
    for n in ("valid_from", "valid_to"):
        assert f[n]["fieldtype"] == "Date" and f[n]["read_only"] == 1
    assert f["period_label"]["read_only"] == 1
    assert f["status"]["read_only"] == 1
    assert f["status"]["options"].split("\n") == ["Draft", "Pending Approval", "Approved", "Cancelled"]
    assert f["status"]["default"] == "Draft"
    assert f["title"]["hidden"] == 1


def test_rate_basis_help_states_the_constant_rules():
    text = _fields("rate_sheet")["rate_basis"]["description"]
    assert "one rate" in text.lower()          # QA-1
    assert "year is closed" in text.lower()    # P1-Q2 B


def test_rates_tab():
    f = _fields("rate_sheet")
    r = f["rates"]
    assert r["fieldtype"] == "Table" and r["options"] == "Rate Sheet Row" and r["reqd"] == 1
    for n in ("copied_from", "source_note"):
        assert f[n]["read_only"] == 1
    assert f["copied_from"]["options"] == "Rate Sheet"


def test_approval_tab_and_supersedes():
    f = _fields("rate_sheet")
    s = f["supersedes"]
    assert s["fieldtype"] == "Link" and s["options"] == "Rate Sheet"
    assert s["read_only"] == 1 and s["no_copy"] == 1 and s.get("description")
    a = f["amended_from"]
    assert a["options"] == "Rate Sheet" and a["read_only"] == 1 and a["no_copy"] == 1
    c = f["change_reason"]
    assert c["fieldtype"] == "Small Text"
    assert c["mandatory_depends_on"] == "eval:doc.amended_from || doc.supersedes"


def test_there_is_no_disabled_field():
    for name in ("rate_sheet", "rate_sheet_row"):
        for fieldname in _fields(name):
            assert "disabled" not in fieldname.lower()


def test_permissions():
    perms = {p["role"]: p for p in _load("rate_sheet")["permissions"]}
    assert set(perms) == {"System Manager", "EPM Admin", "EPM Analyst", "EPM User", "Entity Accountant"}
    for role in ("EPM Admin", "System Manager"):
        assert perms[role]["submit"] == 1 and perms[role]["cancel"] == 1 and perms[role]["amend"] == 1
    a = perms["EPM Analyst"]
    assert a["read"] == a["write"] == a["create"] == a["amend"] == 1
    assert not a.get("submit") and not a.get("cancel")
    for role in ("EPM User", "Entity Accountant"):
        p = perms[role]
        assert p["read"] == 1
        assert not any(p.get(k) for k in ("write", "create", "submit", "cancel", "delete"))


def test_row_is_an_editable_child_table():
    d = _load("rate_sheet_row")
    assert d["istable"] == 1 and d["editable_grid"] == 1
    assert not d.get("is_submittable")
    assert d["module"] == "Consolidation"


def test_row_fields():
    f = _fields("rate_sheet_row")
    for n in ("from_currency", "to_currency"):
        assert f[n]["fieldtype"] == "Link" and f[n]["options"] == "ISO Currency" and f[n]["reqd"] == 1
    q = f["quoted_per"]
    assert q["options"].split("\n") == ["1", "10", "100", "1000", "10000"] and q["default"] == "1"
    for n in ("average_quote", "closing_quote", "rate"):
        assert f[n]["fieldtype"] == "Float" and f[n]["precision"] == "9"
    for n in ("quote_label", "source", "erp_average_quote", "erp_closing_quote", "legacy_documents"):
        assert f[n]["read_only"] == 1, n
    assert f["source"]["options"].split("\n") == ["Manual", "ERP pre-fill", "Duplicated", "Migrated"]
