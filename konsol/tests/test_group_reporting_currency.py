"""The presentation currency lives on each Consolidation Group node (konsolidat#93).

Decided 13 Sep 2026: ``reporting_currency`` stays on the node, which is what
gold_consolidated_trial_balance reads, becomes a Link and is validated; the
unused EPM Settings field goes. The patch and the controller check run here
against a stub frappe."""
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CG_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "consolidation_group")
PATCH = os.path.join(APP_DIR, "patches", "link_consolidation_group_reporting_currency.py")
DROP = os.path.join(APP_DIR, "patches", "drop_epm_settings_consolidation_currency.py")


class Refused(Exception):
    pass


def _field(name):
    with open(os.path.join(CG_DIR, "consolidation_group.json")) as f:
        return next(f for f in json.load(f)["fields"] if f["fieldname"] == name)


def _exec(path, mods, name):
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return module


def _stub_frappe(**db):
    frappe = types.ModuleType("frappe")
    frappe.db = types.SimpleNamespace(**db)
    frappe.MandatoryError = type("MandatoryError", (Exception,), {})

    def throw(msg, *a, **k):
        raise Refused(msg)
    frappe.throw = throw
    return frappe


# -- the field ----------------------------------------------------------------

def test_reporting_currency_links_to_the_iso_currency_list():
    """ISO Currency, not Frappe's Currency: it is the list Entity's functional
    currency links to and the warehouse validates rate codes against
    (epm_gold.currencies). Frappe's Currency is named by its display name."""
    f = _field("reporting_currency")
    assert f["fieldtype"] == "Link"
    assert f["options"] == "ISO Currency"


def test_no_silent_usd_default():
    """A USD default filled every new group node with USD whether or not the
    group reports in it. A group node now has to say."""
    f = _field("reporting_currency")
    assert "default" not in f
    assert not f.get("reqd"), "an entity row's value is not read; required on group nodes only"
    assert f["mandatory_depends_on"] == "eval:doc.is_group || !doc.data_area_id"


# -- the controller -----------------------------------------------------------

def _controller():
    frappe = _stub_frappe()
    nestedset = types.ModuleType("frappe.utils.nestedset")
    nestedset.NestedSet = type("NestedSet", (), {"__init__": lambda self, **kw: self.__dict__.update(kw)})
    ch = types.ModuleType("konsol.clickhouse")
    ch.after_commit_once = ch.sync_doctype_after_commit = ch.sync_table = lambda *a, **k: None
    mods = {"frappe": frappe, "frappe.utils": types.ModuleType("frappe.utils"),
            "frappe.utils.nestedset": nestedset, "konsol": types.ModuleType("konsol"),
            "konsol.clickhouse": ch}
    return _exec(os.path.join(CG_DIR, "consolidation_group.py"), mods, "cg_under_test")


def _refused(doc):
    try:
        doc._validate_reporting_currency()
    except Refused:
        return True
    return False


def test_a_group_node_needs_a_reporting_currency():
    m = _controller()
    node = lambda **kw: m.ConsolidationGroup(**dict(dict(consolidation_group="ZZG", is_group=1,
                                                         data_area_id=None, reporting_currency=None), **kw))
    assert _refused(node())
    assert _refused(node(is_group=0))           # no entity: a roll-up node all the same
    assert _refused(node(reporting_currency=""))
    assert not _refused(node(reporting_currency="CHF"))


def test_an_entity_row_may_leave_it_blank():
    m = _controller()
    leaf = m.ConsolidationGroup(consolidation_group="ZZG", is_group=0, data_area_id="ZZOP",
                                reporting_currency=None)
    assert not _refused(leaf)


def test_the_group_node_rule():
    m = _controller()
    cg = lambda **kw: m.ConsolidationGroup(**dict(dict(consolidation_group="ZZG"), **kw))
    assert cg(is_group=1, data_area_id=None)._is_group_node()
    assert cg(is_group=1, data_area_id="ZZOP")._is_group_node()
    assert cg(is_group=0, data_area_id=None)._is_group_node(), "no entity: a roll-up node"
    assert not cg(is_group=0, data_area_id="ZZOP")._is_group_node()


def test_the_tree_dialog_asks_a_group_node_for_its_currency():
    """The Tree view's "New" dialog shows only treeview_settings.fields plus
    the DocType's reqd fields. reporting_currency is no longer reqd, so without
    this file the dialog had no field for what the server then demanded.
    Frappe loads <doctype>_tree.js as the DocType's __tree_js."""
    import re
    with open(os.path.join(CG_DIR, "consolidation_group_tree.js")) as f:
        src = f.read()
    assert 'frappe.treeview_settings["Consolidation Group"]' in src
    fields = {m.group(1): m.group(0) for m in re.finditer(r'fieldname: "(\w+)".*?\}', src, re.S)}
    order = [m.group(1) for m in re.finditer(r'fieldname: "(\w+)"', src)]
    # opts.fields replaces the dialog's default list, so is_group must be in it;
    # the node's own reqd fields are listed first so they don't land last
    assert set(fields) == {"consolidation_group", "entity_name", "is_group", "data_area_id", "reporting_currency"}
    assert order[:2] == ["consolidation_group", "entity_name"], order
    # ticking Is Group clears a hidden entity, or the group would be saved with it
    is_group_block = src.split('fieldname: "is_group"', 1)[1].split('fieldname: "data_area_id"', 1)[0]
    assert 'set_value("data_area_id", "")' in is_group_block
    rule = _field("reporting_currency")["mandatory_depends_on"]
    assert rule == "eval:doc.is_group || !doc.data_area_id"
    assert f'mandatory_depends_on: "{rule}"' in fields["reporting_currency"]
    assert f'depends_on: "{rule}"' in fields["reporting_currency"]
    assert 'options: "ISO Currency"' in fields["reporting_currency"]
    entity_rule = _field("data_area_id")["mandatory_depends_on"]
    assert f'mandatory_depends_on: "{entity_rule}"' in fields["data_area_id"]


def test_validate_runs_the_check():
    import ast
    with open(os.path.join(CG_DIR, "consolidation_group.py")) as f:
        tree = ast.parse(f.read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    validate = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "validate")
    assert "self._validate_reporting_currency()" in ast.unparse(validate)


# -- the mapping patch ----------------------------------------------------------

def _patch(rows=(), iso=("USD", "CHF", "EUR"), blanks=()):
    writes = []

    def sql(query, *a, **k):
        return list(blanks) if "TRIM(reporting_currency)" in query else list(rows)
    frappe = _stub_frappe(table_exists=lambda dt: True, sql=sql,
                          set_value=lambda *a, **k: writes.append((a, k)))
    frappe.get_all = lambda *a, **k: list(iso)
    frappe.get_app_path = lambda *parts: os.path.join(APP_DIR, *parts[1:])
    return _exec(PATCH, {"frappe": frappe}, "patch_under_test"), writes


def test_the_mapping_trims_and_upper_cases_codes_and_leaves_blanks():
    m, _ = _patch()
    updates, unmapped = m.plan(
        [("CG-A-", "USD"), ("CG-B-", "usd "), ("CG-C-", " chf"), ("CG-D-X", None), ("CG-E-Y", "  ")],
        {"USD", "CHF"})
    assert updates == {"CG-B-": "USD", "CG-C-": "CHF"}
    assert unmapped == {}


def test_a_value_that_is_not_a_code_stops_the_migrate_and_changes_nothing():
    m, writes = _patch(rows=[("CG-A-", "usd"), ("CG-B-", "US$"), ("CG-C-", "Dollar"), ("CG-D-", "US$")])
    try:
        m.execute()
    except ValueError as e:
        msg = str(e)
    else:
        raise AssertionError("an unmappable value must fail the migrate")
    assert "'US$' on CG-B-, CG-D-" in msg and "'Dollar' on CG-C-" in msg
    assert writes == [], "nothing is rewritten when any value fails to map"


def test_mappable_values_are_rewritten_without_touching_modified():
    m, writes = _patch(rows=[("CG-A-", "usd"), ("CG-B-", "CHF")])
    m.execute()
    assert writes == [(("Consolidation Group", "CG-A-", "reporting_currency", "USD"),
                       {"update_modified": False})]


def test_the_fixture_counts_as_the_code_list():
    """Patches run before fixtures, so a site without ISO Currency rows yet
    still maps against the list the Link will validate against."""
    m, _ = _patch(iso=())
    codes = m.iso_codes()
    assert {"USD", "CHF", "JPY", "EUR"} <= codes


# -- the EPM Settings drop --------------------------------------------------------

def test_the_settings_value_is_deleted_from_singles():
    deleted = []
    frappe = _stub_frappe(sql=lambda *a, **k: [("USD",)],
                          delete=lambda dt, filters: deleted.append((dt, filters)))
    m = _exec(DROP, {"frappe": frappe}, "drop_under_test")
    m.execute()
    assert ("Singles", {"doctype": "EPM Settings", "field": "consolidation_currency"}) in deleted
    assert ("Property Setter", {"doc_type": "EPM Settings", "field_name": "consolidation_currency"}) in deleted


def test_both_patches_are_registered():
    with open(os.path.join(APP_DIR, "patches.txt")) as f:
        lines = [l.strip() for l in f]
    assert "konsol.patches.link_consolidation_group_reporting_currency" in lines
    assert "konsol.patches.drop_epm_settings_consolidation_currency" in lines


def test_nothing_reads_the_retired_setting():
    hits = []
    for root, _dirs, files in os.walk(APP_DIR):
        if "node_modules" in root or os.sep + "tests" in root or os.sep + "patches" in root:
            continue
        for name in files:
            if name.endswith((".py", ".js", ".json")):
                with open(os.path.join(root, name), encoding="utf-8", errors="ignore") as f:
                    if "consolidation_currency" in f.read():
                        hits.append(os.path.relpath(os.path.join(root, name), APP_DIR))
    assert hits == [], hits
