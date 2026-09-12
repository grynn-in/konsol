"""The governed entity registry (konsol#110).

F8 shipped a trial-balance intake for subsidiaries with no ERP connector, and
those entities then vanished at consolidation: gold_consolidated_trial_balance
INNER JOINed silver_legal_entities — an ERP-sourced table — for each entity's
accounting currency, and a konsol-only entity had no row there. Entity now
writes through to epm_staging.entities and dbt resolves the currency from it
first, the ERP second.

These tests ENUMERATE rather than name the cases: every DDL column against
every mapped field, every lifecycle hook that changes the row set.
"""
import ast
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTITY_DIR = os.path.join(APP_DIR, "epm", "doctype", "entity")


def _src():
    with open(os.path.join(ENTITY_DIR, "entity.py")) as f:
        return f.read()


def _meta():
    with open(os.path.join(ENTITY_DIR, "entity.json")) as f:
        return json.load(f)


def _class_attr(name):
    """A literal class attribute of Entity, read without importing frappe."""
    tree = ast.parse(_src())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Entity")
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Entity.{name} not declared")


def _method(name):
    tree = ast.parse(_src())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Entity")
    return next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name), None)


def _calls(fn):
    """Names of everything a method calls — `self._resync()` -> '_resync',
    `super().on_update()` -> 'super', 'on_update'."""
    out = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                out.add(f.attr)
            elif isinstance(f, ast.Name):
                out.add(f.id)
    return out


def _ddl_columns():
    with open(os.path.join(APP_DIR, "clickhouse.py")) as f:
        ch = f.read()
    block = ch.split('"epm_staging.entities": (')[1].split("),")[0]
    body = "".join(re.findall(r'"([^"]*)"', block))
    cols = body.split("(", 1)[1].split(")", 1)[0]
    return [c.strip().split()[0] for c in cols.split(",")]


def test_writes_through_to_the_registry_table():
    assert _class_attr("CH_TABLE") == "epm_staging.entities"


def test_every_ddl_column_is_mapped_and_nothing_else_is():
    """A column the DDL has and the map does not is written as DEFAULT forever;
    a mapped column the DDL lacks makes every INSERT fail, and sync_rows only
    logs that. Either way the registry silently stops meaning anything."""
    mapped = _class_attr("CH_FIELD_MAP")
    assert list(mapped) == _ddl_columns(), (
        "CH_FIELD_MAP keys must match the DDL columns, in order")


def test_every_mapped_field_exists_on_the_doctype():
    fields = {f["fieldname"] for f in _meta()["fields"]} | {"name"}
    for column, field in _class_attr("CH_FIELD_MAP").items():
        assert field in fields, f"{column} maps to {field!r}, which Entity does not have"


def test_the_join_key_is_the_record_name():
    """The name IS the normalised code, and it is what a rename changes — the
    field could in principle lag it; the name cannot."""
    assert _class_attr("CH_FIELD_MAP")["data_area_id"] == "name"


def test_the_currency_is_sent_under_the_name_the_consolidation_joins_on():
    assert _class_attr("CH_FIELD_MAP")["accounting_currency"] == "functional_currency"


def test_functional_currency_is_a_link_to_the_iso_list():
    """As free text, 'usd' or 'EURO' would reach the warehouse, miss every rate
    and translate at the 1.0 parity fallback — caught only by a warehouse test
    after the build, instead of by the form."""
    f = next(x for x in _meta()["fields"] if x["fieldname"] == "functional_currency")
    assert f["fieldtype"] == "Link"
    assert f["options"] == "ISO Currency"


def test_every_lifecycle_that_changes_the_row_set_resyncs():
    """Insert and save (on_update), delete (after_delete), rename
    (after_rename — rename_doc never calls on_update). Each one."""
    for hook in ("on_update", "after_delete", "after_rename"):
        fn = _method(hook)
        assert fn is not None, f"Entity.{hook} is missing"
        assert "_resync" in _calls(fn), f"Entity.{hook} does not resync"


def test_nested_set_hooks_are_chained_not_replaced():
    """NestedSet.on_update maintains lft/rgt; NestedSet.after_rename re-points
    the children. Overriding either without super() corrupts the tree."""
    for hook in ("on_update", "after_rename"):
        calls = _calls(_method(hook))
        assert "super" in calls and hook in calls, f"Entity.{hook} does not call super().{hook}"


def test_delete_never_syncs_from_on_trash():
    """on_trash runs before the row is gone, so a sync there re-publishes it
    (konsol#120). Entity must leave on_trash to NestedSet."""
    assert _method("on_trash") is None


def test_one_sync_call():
    """The hooks all go through _resync, and _resync is the only place that
    names sync_doctype."""
    assert _src().count("sync_doctype(") == 1
    assert "sync_doctype" in _calls(_method("_resync"))


def test_a_currency_change_triggers_a_rebuild():
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        hooks = f.read()
    triggers = hooks.split("_dbt_trigger_doctypes = [")[1].split("]")[0]
    assert '"Entity",' in triggers
    with open(os.path.join(APP_DIR, "tasks.py")) as f:
        tasks = f.read()
    build_map = tasks.split("DOCTYPE_BUILD_MAP = {")[1].split("\n}")[0]
    assert '"Entity":' in build_map


def test_doctype_json_is_marked_modified_after_the_link_change():
    """Frappe re-syncs a DocType only when the file's `modified` is newer than
    the database's, so a fieldtype change under the old stamp never applies."""
    assert _meta()["modified"] >= "2026-09-12"
