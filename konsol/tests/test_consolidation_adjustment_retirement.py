"""Consolidation Adjustment is retired on existing sites (konsol#305 J13).

#305-D2-1 (Deepak Pai, 27 Sep 2026): the Consolidation Journal replaces
Consolidation Adjustment outright. J12 deleted the doctype's code; this patch
is the cut-over for a site that already migrated it. Frappe's migrate removes
an orphan DocType record (frappe/model/sync.py remove_orphan_doctypes) but
leaves the Workflow record and the table, so the patch removes all three.

Decided 27 Sep (Problems P14): the patch REFUSES while any Consolidation
Adjustment row exists, so a migrate never destroys adjustments that nobody
re-entered as journals.

The patch runs here against a stub frappe that models a site: which Workflow,
DocType and table exist, and how many rows the table holds.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
RETIRE_PY = os.path.join(APP_DIR, "patches", "retire_consolidation_adjustment.py")
DOTTED = "konsol.patches.retire_consolidation_adjustment"

WORKFLOW = ("Workflow", "Consolidation Adjustment Workflow")
DOCTYPE = ("DocType", "Consolidation Adjustment")
TABLE = "Consolidation Adjustment"


class _Site:
    """A site holding the old workflow, doctype and table, with `rows` rows."""

    def __init__(self, rows=0, workflow=True, doctype=True, table=True):
        self.records = set()
        if workflow:
            self.records.add(WORKFLOW)
        if doctype:
            self.records.add(DOCTYPE)
        self.tables = {TABLE} if table else set()
        self.rows = rows
        self.calls = []  # every destructive call, in order

    def frappe(self):
        site = self
        frappe = types.ModuleType("frappe")
        frappe.ValidationError = type("ValidationError", (Exception,), {})

        def throw(msg, exc=None, **k):
            raise (exc or frappe.ValidationError)(msg)

        def delete_doc(doctype, name, force=False, ignore_missing=False, **k):
            site.calls.append(("delete_doc", doctype, name))
            if (doctype, name) not in site.records:
                if not ignore_missing:
                    raise frappe.ValidationError("%s %s not found" % (doctype, name))
                return
            if doctype == "DocType" and (WORKFLOW in site.records):
                # Frappe refuses to delete a DocType a Workflow still names.
                raise frappe.ValidationError("DocType is linked with a Workflow")
            site.records.discard((doctype, name))

        def table_exists(name, *a, **k):
            return name in site.tables

        def count(name, *a, **k):
            if name not in site.tables:
                raise frappe.ValidationError("Table 'tab%s' doesn't exist" % name)
            return site.rows

        def exists(doctype, name=None, *a, **k):
            return name if (doctype, name) in site.records else None

        def sql_ddl(query, *a, **k):
            site.calls.append(("sql_ddl", query))
            q = " ".join(query.split())
            prefix = "DROP TABLE IF EXISTS `tab"
            if not q.startswith(prefix) or not q.endswith("`"):
                raise AssertionError("unexpected DDL: " + query)
            site.tables.discard(q[len(prefix):-1])

        def sql(query, *a, **k):
            site.calls.append(("sql", query))
            raise AssertionError("the patch must use sql_ddl for DDL: " + query)

        frappe.throw = throw
        frappe._ = lambda s: s
        frappe.delete_doc = delete_doc
        frappe.db = types.SimpleNamespace(
            table_exists=table_exists, count=count, exists=exists,
            sql_ddl=sql_ddl, sql=sql, commit=lambda: None)
        frappe.logger = lambda *a, **k: types.SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None)
        return frappe


def _run(site):
    frappe = site.frappe()
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location("retire_ca_under_test", RETIRE_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.execute()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    return frappe


def _raises(site):
    try:
        _run(site)
    except Exception as exc:  # noqa: BLE001 - the stub's ValidationError is per-load
        return exc
    return None


# --- registration ------------------------------------------------------------

def test_listed_once_after_retire_allocation():
    lines = [l.strip() for l in open(PATCHES_TXT).read().splitlines()]
    assert lines.count(DOTTED) == 1, "the retirement patch must be listed exactly once"
    assert lines.index(DOTTED) > lines.index("konsol.patches.retire_allocation"), \
        "the retirement patch must run after retire_allocation"


# --- the refusal (P14) -------------------------------------------------------

def test_refuses_while_rows_exist_and_touches_nothing():
    for rows in (1, 7):
        site = _Site(rows=rows)
        exc = _raises(site)
        assert exc is not None, "rows=%d: the patch must refuse" % rows
        msg = str(exc)
        assert "%d Consolidation Adjustment rows exist" % rows in msg, msg
        assert "Consolidation Journal" in msg and "migrate" in msg, msg
        assert site.calls == [], "rows=%d: nothing may be deleted before the refusal: %r" % (
            rows, site.calls)
        assert WORKFLOW in site.records and DOCTYPE in site.records
        assert TABLE in site.tables


# --- the removal -------------------------------------------------------------

def test_removes_workflow_doctype_and_table():
    site = _Site(rows=0)
    exc = _raises(site)
    assert exc is None, "zero rows must not refuse: %r" % exc
    assert WORKFLOW not in site.records, "the Workflow record must be deleted"
    assert DOCTYPE not in site.records, "the DocType record must be deleted"
    assert TABLE not in site.tables, "the table must be dropped"


def test_deletes_the_workflow_before_the_doctype():
    site = _Site(rows=0)
    _run(site)
    deletes = [c[1:] for c in site.calls if c[0] == "delete_doc"]
    assert WORKFLOW in deletes and DOCTYPE in deletes, deletes
    assert deletes.index(WORKFLOW) < deletes.index(DOCTYPE), deletes


def test_drops_the_table_after_the_doctype():
    site = _Site(rows=0)
    _run(site)
    kinds = [c for c in site.calls]
    drop = [i for i, c in enumerate(kinds) if c[0] == "sql_ddl"]
    dt = [i for i, c in enumerate(kinds) if c[0] == "delete_doc" and c[1:] == DOCTYPE]
    assert len(drop) == 1, "exactly one DROP TABLE: %r" % kinds
    assert dt and dt[0] < drop[0], kinds


# --- idempotent --------------------------------------------------------------

def test_a_rerun_is_a_no_op():
    site = _Site(rows=0)
    _run(site)
    after_first = (set(site.records), set(site.tables))
    exc = _raises(site)
    assert exc is None, "a rerun must not fail: %r" % exc
    assert (site.records, site.tables) == after_first


def test_a_fresh_install_without_the_table_is_a_no_op():
    # A site that never had the doctype: no table, no records. It must not
    # refuse, and must not count a table that is not there.
    site = _Site(rows=0, workflow=False, doctype=False, table=False)
    exc = _raises(site)
    assert exc is None, "a fresh install must not fail: %r" % exc


def test_a_site_whose_migrate_already_removed_the_doctype():
    # remove_orphan_doctypes may have deleted the DocType record first; the
    # Workflow and the table are still there and must still go.
    site = _Site(rows=0, doctype=False)
    exc = _raises(site)
    assert exc is None, repr(exc)
    assert WORKFLOW not in site.records
    assert TABLE not in site.tables
