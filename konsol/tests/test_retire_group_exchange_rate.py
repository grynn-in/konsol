"""Group Exchange Rate is retired (konsol#592 pair 1, task T26).

Rate Sheet replaced it: actuals rates on one Statutory sheet per period and
budget rates on one Constant sheet per year (Deepak Pai, 22 Nov 2026, #592).
``migrate_group_exchange_rates_to_rate_sheets`` (T25) moved every row onto
sheets and attached an audit CSV of every row to Rate Set CONSTANT; this
patch then removes the doctype.

These tests pin the patch and fail if the doctype comes back in runtime code:

* the patch deletes the site rows that name the doctype, the DocType record
  and ``tabGroup Exchange Rate``;
* it refuses, and deletes nothing, while the table has rows the migration has
  not taken (no Migrated sheet row and no audit file on CONSTANT);
* it never touches ClickHouse: ``epm_staging.group_exchange_rates`` is the
  live warehouse rates table, now written from the sheets;
* no konsol runtime module names the doctype, except the named allow-list.

Host-run: the sources are read as text or tokens; the patch runs over a stub
frappe and a stub konsol.clickhouse.
"""
import ast
import glob
import importlib.util
import io
import os
import re
import sys
import tokenize
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE = "Group Exchange Rate"
FOLDER = "group_exchange_rate"
WAREHOUSE_TABLE = "epm_staging.group_exchange_rates"
PATCH_PY = os.path.join(APP_DIR, "patches", "retire_group_exchange_rate.py")
MODULE = "konsol.patches.retire_group_exchange_rate"
AUDIT_FILE = "group-exchange-rate-migration-audit.csv"

#: The runtime files that may still name the retired doctype, and why. Every
#: other file under konsol/ outside tests/ and patches/ must not.
ALLOWED = {
    "rate_sheet_migration_model.py": "the T5 migration planner reads Group Exchange Rate rows",
    os.path.join("close", "trail_model.py"): "the trail resolves Close Events that name a retired GER (T23)",
    os.path.join("close", "trail_api.py"): "the trail resolves Close Events that name a retired GER (T23)",
    os.path.join("consolidation", "doctype", "rate_sheet_row", "rate_sheet_row.json"):
        "legacy_documents and the Migrated source describe the rows a sheet row came from",
    os.path.join("consolidation", "doctype", "rate_sheet", "rate_sheet.py"):
        "the Migrated source is set only by the migration from Group Exchange Rate",
}
#: Not runtime: the handbook and other docs (T28), and the built close-ui
#: bundle (the abandoned SPA, Desk only since 5 Nov 2026).
_NOT_RUNTIME = ("tests" + os.sep, "patches" + os.sep, "handbook_pages" + os.sep,
                os.path.join("public", "close") + os.sep)
_NAMES = re.compile(r"Group Exchange Rate|group_exchange_rate|GER-")


def _code_without_comments(path):
    """A Python file's tokens minus comments, so a history note may name it."""
    with open(path, encoding="utf-8") as f:
        tokens = tokenize.generate_tokens(io.StringIO(f.read()).readline)
        return " ".join(t.string for t in tokens if t.type != tokenize.COMMENT)


def _runtime_text(path):
    if path.endswith(".py"):
        text = _code_without_comments(path)
    else:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    # the warehouse table keeps its name: it is the live rates table
    return text.replace(WAREHOUSE_TABLE, "").replace("group_exchange_rates", "")


def _runtime_files():
    for ext in ("py", "js", "json", "html", "txt"):
        for path in glob.glob(os.path.join(APP_DIR, "**", f"*.{ext}"), recursive=True):
            rel = os.path.relpath(path, APP_DIR)
            if rel.startswith(_NOT_RUNTIME) or rel == "patches.txt":
                continue
            yield rel, path


# --- the doctype is gone ------------------------------------------------------

def test_no_doctype_folder_in_any_module():
    assert not glob.glob(os.path.join(APP_DIR, "*", "doctype", FOLDER)), "the doctype directory is deleted"


def test_no_runtime_module_names_the_doctype_outside_the_allow_list():
    offenders = sorted(f"{rel}: {sorted(set(_NAMES.findall(_runtime_text(path))))}"
                       for rel, path in _runtime_files()
                       if rel not in ALLOWED and _NAMES.search(_runtime_text(path)))
    assert not offenders, "\n".join(offenders)


def test_the_allow_list_is_not_stale():
    """Each allowed file exists and still names the doctype; one that no
    longer does leaves the list, so the list cannot grow quietly."""
    for rel in ALLOWED:
        path = os.path.join(APP_DIR, rel)
        assert os.path.exists(path), rel
        assert _NAMES.search(_runtime_text(path)), f"{rel} no longer names it: drop it from ALLOWED"


def test_hooks_give_rate_sheet_what_ger_had():
    with open(os.path.join(APP_DIR, "hooks.py"), encoding="utf-8") as f:
        src = f.read()
    assert '"Rate Sheet": "public/js/approval_actions.js"' in src
    [triggers] = [ast.literal_eval(n.value) for n in ast.parse(src).body if isinstance(n, ast.Assign)
                  and getattr(n.targets[0], "id", None) == "_dbt_trigger_doctypes"]
    assert "Rate Sheet" in triggers, "an approved or cancelled sheet requests the build, as GER did"
    with open(os.path.join(APP_DIR, "tasks.py"), encoding="utf-8") as f:
        assert '"Rate Sheet": {"scope": "consolidation", "risk": "high"}' in f.read()


def test_the_adoption_patch_is_a_no_op():
    """It created GER rows; it must not reload the deleted doctype or import
    the adoption, which is gone with it."""
    path = os.path.join(APP_DIR, "patches", "adopt_erp_rates_as_group_exchange_rates.py")
    code = _code_without_comments(path)
    assert "adopt_erp_rates" not in code.replace("adopt_erp_rates_as_group_exchange_rates", "")
    assert "reload_doc" not in code


# --- the patch ------------------------------------------------------------------

class Refused(Exception):
    pass


def _run(*, table_exists=True, rows=3, migrated_row=False, audit_file=False):
    calls = []
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})

    def throw(message, exc=None, *a, **k):
        calls.append(("throw", message))
        raise Refused(message)

    def exists(doctype, filters=None, *a, **k):
        calls.append(("exists", doctype, filters))
        if doctype == "Rate Sheet Row":
            return "row-1" if migrated_row else None
        if doctype == "File":
            return "file-1" if audit_file else None
        return None

    def sql(query, *a, **k):
        calls.append(("sql", query))
        if "COUNT" in query.upper():
            return [(rows,)]
        return []

    frappe.throw = throw
    frappe.db = types.SimpleNamespace(
        table_exists=lambda doctype, *a, **k: table_exists if doctype == DOCTYPE else True,
        exists=exists,
        sql=sql,
        count=lambda doctype, *a, **k: calls.append(("count", doctype)) or rows,
        delete=lambda doctype, filters=None, *a, **k: calls.append(("db.delete", doctype, filters)),
        sql_ddl=lambda statement, *a, **k: calls.append(("sql_ddl", statement)))
    frappe.get_all = lambda doctype, filters=None, *a, **k: (
        calls.append(("get_all", doctype, filters)) or (["ZZ GER Workflow"] if doctype == "Workflow" else []))
    frappe.delete_doc = lambda doctype, name, *a, **k: calls.append(("delete_doc", doctype, name, k))
    frappe.logger = lambda *a, **k: types.SimpleNamespace(
        info=lambda message, *ia, **ik: calls.append(("log.info", message)),
        warning=lambda message, *wa, **wk: calls.append(("log.warning", message)))
    clickhouse = types.ModuleType("konsol.clickhouse")
    clickhouse.execute = lambda statement, *a, **k: calls.append(("ch", statement))
    konsol = types.ModuleType("konsol")
    konsol.clickhouse = clickhouse
    stubs = {"frappe": frappe, "konsol": konsol, "konsol.clickhouse": clickhouse}
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    error = None
    try:
        assert os.path.exists(PATCH_PY), "no retire_group_exchange_rate.py"
        spec = importlib.util.spec_from_file_location("_retire_ger_under_test", PATCH_PY)
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        try:
            patch.execute()
        except Refused as e:
            error = e
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return calls, error


def _destructive(calls):
    return [c for c in calls if c[0] in ("db.delete", "delete_doc", "sql_ddl", "ch")]


def test_registered_in_patches_txt_after_the_migration_and_the_budget_cycle_drop():
    with open(os.path.join(APP_DIR, "patches.txt")) as f:
        lines = [line.strip() for line in f if line.strip()]
    assert lines.count(MODULE) == 1
    for earlier in ("konsol.patches.migrate_group_exchange_rates_to_rate_sheets",
                    "konsol.patches.drop_budget_cycle_rate_set"):
        assert lines.index(earlier) < lines.index(MODULE), earlier


def test_the_patch_removes_the_site_rows_the_record_and_the_table():
    calls, error = _run(migrated_row=True)
    assert error is None, error
    for row in (("db.delete", "Custom Field", {"dt": DOCTYPE}),
                ("db.delete", "Property Setter", {"doc_type": DOCTYPE}),
                ("db.delete", "Custom Field", {"options": DOCTYPE}),
                ("db.delete", "Client Script", {"dt": DOCTYPE}),
                ("db.delete", "Custom DocPerm", {"parent": DOCTYPE}),
                ("db.delete", "User Permission", {"allow": DOCTYPE}),
                ("db.delete", "Report", {"ref_doctype": DOCTYPE}),
                ("db.delete", "Number Card", {"document_type": DOCTYPE}),
                ("db.delete", "Dashboard Chart", {"document_type": DOCTYPE}),
                ("db.delete", "Workflow Action", {"reference_doctype": DOCTYPE}),
                ("delete_doc", "Workflow", "ZZ GER Workflow", {"ignore_missing": True, "force": True}),
                ("delete_doc", "DocType", DOCTYPE, {"ignore_missing": True, "force": True}),
                ("sql_ddl", f"DROP TABLE IF EXISTS `tab{DOCTYPE}`")):
        assert row in calls, row
    assert ("get_all", "Workflow", {"document_type": DOCTYPE}) in calls
    # the record goes before its table
    assert calls.index(("delete_doc", "DocType", DOCTYPE, {"ignore_missing": True, "force": True})) < \
        calls.index(("sql_ddl", f"DROP TABLE IF EXISTS `tab{DOCTYPE}`"))


def test_it_refuses_an_unmigrated_table_and_deletes_nothing():
    calls, error = _run(rows=3)
    assert error is not None, "an unmigrated table must never be dropped"
    message = str(error)
    assert "3" in message and DOCTYPE in message and "migrate_group_exchange_rates_to_rate_sheets" in message
    assert _destructive(calls) == [], _destructive(calls)


def test_a_migrated_table_is_retired_by_either_marker():
    """The marker the migration's own idempotency reads: a Migrated sheet row,
    or the audit file on CONSTANT (a site whose every row was dropped)."""
    for markers in ({"migrated_row": True}, {"audit_file": True}):
        calls, error = _run(**markers)
        assert error is None, (markers, error)
        assert ("sql_ddl", f"DROP TABLE IF EXISTS `tab{DOCTYPE}`") in calls, markers
    calls, _ = _run(audit_file=True)
    assert ("exists", "File", {"attached_to_doctype": "Rate Set", "attached_to_name": "CONSTANT",
                               "file_name": AUDIT_FILE}) in calls


def test_an_empty_or_missing_table_is_retired_and_a_rerun_is_a_no_op():
    calls, error = _run(rows=0)
    assert error is None and ("sql_ddl", f"DROP TABLE IF EXISTS `tab{DOCTYPE}`") in calls
    calls, error = _run(table_exists=False)
    assert error is None, error
    assert ("delete_doc", "DocType", DOCTYPE, {"ignore_missing": True, "force": True}) in calls


def test_it_never_touches_the_warehouse_rates_table():
    calls, _ = _run(migrated_row=True)
    assert [c for c in calls if c[0] == "ch"] == [], "nothing is sent to ClickHouse"
    code = _code_without_comments(PATCH_PY)
    assert "clickhouse" not in code and "epm_staging" not in code and "group_exchange_rates" not in code
