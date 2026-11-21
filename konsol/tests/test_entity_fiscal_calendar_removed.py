"""Entity Fiscal Calendar stays removed (konsol#577; Deepak Pai, 21 Nov 2026).

Nothing read the mapping since konsolidat#221 removed the ERP calendar
branch: the site's one declared calendar (EPM Fiscal Year) dates every
submission. These tests fail if the doctype comes back in the app's code,
hooks, Desk navigation or warehouse DDL, and they pin the retirement patch.

Host-run: the sources are read as text or AST; the patch runs over a stub
frappe and a stub konsol.clickhouse.
"""
import ast
import glob
import importlib.util
import io
import os
import sys
import tokenize
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE = "Entity Fiscal Calendar"
FOLDER = "entity_fiscal_calendar"
CH_TABLE = "epm_gold.entity_fiscal_calendars"
PATCH_PY = os.path.join(APP_DIR, "patches", "retire_entity_fiscal_calendar.py")
MODULE = "konsol.patches.retire_entity_fiscal_calendar"


def _literal(relative, name):
    with open(os.path.join(APP_DIR, relative)) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", None) == name):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {relative}")


def _code_without_comments(path):
    """The file's tokens minus comments, so a history note may name the doctype."""
    with open(path) as f:
        tokens = tokenize.generate_tokens(io.StringIO(f.read()).readline)
        return " ".join(t.string for t in tokens if t.type != tokenize.COMMENT)


# --- the doctype is gone ------------------------------------------------------

def test_no_doctype_folder_in_any_module():
    found = glob.glob(os.path.join(APP_DIR, "*", "doctype", FOLDER))
    assert not found, found


def test_hooks_do_not_name_it():
    assert DOCTYPE not in _code_without_comments(os.path.join(APP_DIR, "hooks.py"))
    assert FOLDER not in _code_without_comments(os.path.join(APP_DIR, "hooks.py"))


def test_no_python_module_names_it_outside_comments():
    """Labels, the setup checklist, a write-through: any live code path.
    clickhouse.py names the warehouse table once, in _RETIRED_TABLES, to drop
    it; the DDL tests below pin that, so only the doctype name is checked there."""
    offenders = []
    for path in glob.glob(os.path.join(APP_DIR, "**", "*.py"), recursive=True):
        rel = os.path.relpath(path, APP_DIR)
        if rel.startswith(("tests" + os.sep, "patches" + os.sep)):
            continue
        code = _code_without_comments(path)
        if DOCTYPE in code or (FOLDER in code and rel != "clickhouse.py"):
            offenders.append(rel)
    assert not offenders, offenders


def test_no_sidebar_workspace_or_shipped_json_links_it():
    offenders = []
    for path in glob.glob(os.path.join(APP_DIR, "**", "*.json"), recursive=True):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if DOCTYPE in text or FOLDER in text:
            offenders.append(os.path.relpath(path, APP_DIR))
    assert not offenders, offenders


def test_the_structure_sidebar_no_longer_lists_it_and_was_bumped():
    """A shipped Sidebar re-imports on migrate only when ``modified`` changes."""
    import json
    with open(os.path.join(APP_DIR, "structure", "sidebar", "structure", "structure.json")) as f:
        sidebar = json.load(f)
    assert DOCTYPE not in [i.get("link_to") for i in sidebar["items"]]
    assert sidebar["modified"] >= "2026-11-21"


# --- the warehouse table is gone ---------------------------------------------

def test_not_in_the_reference_ddl():
    assert CH_TABLE not in _literal("clickhouse.py", "_REFERENCE_TABLE_DDL")


def test_listed_as_retired_so_its_watermark_row_goes_too():
    """_RETIRED_TABLES drops the table on every migrate and deletes its
    sync_watermark row; a frozen row would fail assert_staging_not_stale."""
    assert CH_TABLE in _literal("clickhouse.py", "_RETIRED_TABLES")


# --- the patch ------------------------------------------------------------------

def _run(ch_raises=False):
    calls = []
    frappe = types.ModuleType("frappe")
    frappe.db = types.SimpleNamespace(
        delete=lambda doctype, filters=None, *a, **k: calls.append(("db.delete", doctype, filters)),
        sql_ddl=lambda statement, *a, **k: calls.append(("sql_ddl", statement)))
    frappe.delete_doc = lambda doctype, name, *a, **k: calls.append(("delete_doc", doctype, name, k))
    frappe.logger = lambda *a, **k: types.SimpleNamespace(
        info=lambda message, *ia, **ik: calls.append(("log.info", message)),
        warning=lambda message, *wa, **wk: calls.append(("log.warning", message)))
    clickhouse = types.ModuleType("konsol.clickhouse")

    def execute(sql, *a, **k):
        calls.append(("ch", sql))
        if ch_raises:
            raise RuntimeError("ZZ ClickHouse unreachable")
        return ""

    clickhouse.execute = execute
    konsol = types.ModuleType("konsol")
    konsol.clickhouse = clickhouse
    stubs = {"frappe": frappe, "konsol": konsol, "konsol.clickhouse": clickhouse}
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        assert os.path.exists(PATCH_PY), "no retire_entity_fiscal_calendar.py"
        spec = importlib.util.spec_from_file_location("_retire_efc_under_test", PATCH_PY)
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        patch.execute()
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return calls


def test_registered_in_patches_txt():
    with open(os.path.join(APP_DIR, "patches.txt")) as f:
        assert MODULE in [line.strip() for line in f]


def test_the_patch_deletes_the_doctype_and_drops_both_tables():
    calls = _run()
    assert ("delete_doc", "DocType", DOCTYPE, {"ignore_missing": True, "force": True}) in calls
    assert ("sql_ddl", f"DROP TABLE IF EXISTS `tab{DOCTYPE}`") in calls
    assert [c[1] for c in calls if c[0] == "ch"] == [f"DROP TABLE IF EXISTS {CH_TABLE}"]
    assert ("db.delete", "Custom Field", {"dt": DOCTYPE}) in calls
    assert ("db.delete", "Property Setter", {"doc_type": DOCTYPE}) in calls
    # the record goes before its table
    assert calls.index(next(c for c in calls if c[0] == "delete_doc")) < calls.index(
        next(c for c in calls if c[0] == "sql_ddl"))


def test_an_unreachable_warehouse_is_logged_never_raised():
    calls = _run(ch_raises=True)
    warnings = [c[1] for c in calls if c[0] == "log.warning"]
    assert warnings and CH_TABLE in warnings[0], warnings
    # the Frappe side still completed
    assert ("sql_ddl", f"DROP TABLE IF EXISTS `tab{DOCTYPE}`") in calls
