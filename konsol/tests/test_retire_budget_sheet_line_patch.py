"""The retire_budget_sheet_line patch (konsol#348 S2-C Z1), host-run.

Budget Entry / Budget Entry Line (#522) replace Budget Sheet and Budget Line.
The doctype code is deleted, but Frappe never drops a table or the Custom
Fields of a doctype that leaves the app, so this patch does: it deletes the
Custom Fields and Property Setters on both doctypes, the two DocType records,
and drops both tables. Every step is guarded, so a rerun, or a site where
they never existed, is a no-op. Rows still present are named in the migrate
log before they are dropped.

A stub frappe records every call over an in-memory site.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "retire_budget_sheet_line.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
DOCTYPE_DIR = os.path.join(APP_DIR, "epm", "doctype")
MODULE = "konsol.patches.retire_budget_sheet_line"
DOCTYPES = ("Budget Sheet", "Budget Line")


class _Site:
    def __init__(self, present=True, rows=None):
        self.calls = []
        self.tables = {dt: (rows or {}).get(dt, 0) for dt in DOCTYPES} if present else {}
        self.doctypes = set(DOCTYPES) if present else set()
        self.custom_fields = (
            [{"dt": "Budget Line", "fieldname": "region"},
             {"dt": "Budget Sheet", "fieldname": "zz_note"},
             {"dt": "Budget Entry Line", "fieldname": "region"}]
            if present else [{"dt": "Budget Entry Line", "fieldname": "region"}])
        self.property_setters = (
            [{"doc_type": "Budget Line"}, {"doc_type": "Scenario"}] if present
            else [{"doc_type": "Scenario"}])

    def module(self):
        site = self
        frappe = types.ModuleType("frappe")

        def table_exists(doctype, *a, **k):
            site.calls.append(("table_exists", doctype))
            return doctype in site.tables

        def count(doctype, *a, **k):
            site.calls.append(("count", doctype))
            assert doctype in site.tables, "count on a missing table raises"
            return site.tables[doctype]

        def delete(doctype, filters=None, *a, **k):
            site.calls.append(("db.delete", doctype, filters))
            key = {"Custom Field": "dt", "Property Setter": "doc_type"}[doctype]
            op, values = filters[key]
            assert op == "in", filters
            attr = "custom_fields" if doctype == "Custom Field" else "property_setters"
            setattr(site, attr, [r for r in getattr(site, attr) if r[key] not in values])

        def sql_ddl(statement, *a, **k):
            site.calls.append(("sql_ddl", statement))
            for dt in DOCTYPES:
                if statement == f"DROP TABLE IF EXISTS `tab{dt}`":
                    site.tables.pop(dt, None)

        def delete_doc(doctype, name, *a, **k):
            site.calls.append(("delete_doc", doctype, name, k))
            assert doctype == "DocType"
            if name not in site.doctypes:
                assert k.get("ignore_missing"), "a missing DocType raises without ignore_missing"
                return
            site.doctypes.discard(name)

        def logger(*a, **k):
            return types.SimpleNamespace(
                info=lambda message, *ia, **ik: site.calls.append(("log.info", message)),
                warning=lambda message, *wa, **wk: site.calls.append(("log.warning", message)))

        frappe.db = types.SimpleNamespace(table_exists=table_exists, count=count, delete=delete,
                                          sql_ddl=sql_ddl)
        frappe.delete_doc = delete_doc
        frappe.logger = logger
        return frappe


def _run(site):
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = site.module()
    try:
        assert os.path.exists(PATCH_PY), "no retire_budget_sheet_line.py"
        spec = importlib.util.spec_from_file_location("_retire_bsl_under_test", PATCH_PY)
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        patch.execute()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


def _ddl(site):
    return [c[1] for c in site.calls if c[0] == "sql_ddl"]


def test_registered_in_patches_txt():
    with open(PATCHES_TXT) as f:
        assert MODULE in [line.strip() for line in f]


def test_the_doctype_dirs_are_gone():
    for folder in ("budget_sheet", "budget_line"):
        assert not os.path.exists(os.path.join(DOCTYPE_DIR, folder)), folder


def test_drops_both_tables():
    site = _Site()
    _run(site)
    assert site.tables == {}
    assert "DROP TABLE IF EXISTS `tabBudget Sheet`" in _ddl(site)
    assert "DROP TABLE IF EXISTS `tabBudget Line`" in _ddl(site)


def test_deletes_both_doctypes():
    site = _Site()
    _run(site)
    assert site.doctypes == set()
    deleted = [c[2] for c in site.calls if c[0] == "delete_doc"]
    assert deleted == ["Budget Sheet", "Budget Line"], "the parent goes before its child table"


def test_deletes_their_custom_fields_and_property_setters_only():
    site = _Site()
    _run(site)
    assert site.custom_fields == [{"dt": "Budget Entry Line", "fieldname": "region"}]
    assert site.property_setters == [{"doc_type": "Scenario"}]


def test_rows_still_present_are_named_before_the_drop():
    site = _Site(rows={"Budget Sheet": 3, "Budget Line": 41})
    _run(site)
    warnings = [c[1] for c in site.calls if c[0] == "log.warning"]
    assert any("3" in w and "Budget Sheet" in w for w in warnings), warnings
    assert any("41" in w and "Budget Line" in w for w in warnings), warnings
    first_drop = next(i for i, c in enumerate(site.calls) if c[0] == "sql_ddl")
    last_warning = max(i for i, c in enumerate(site.calls) if c[0] == "log.warning")
    assert last_warning < first_drop


def test_empty_tables_log_no_warning():
    site = _Site()
    _run(site)
    assert not [c for c in site.calls if c[0] == "log.warning"]


def test_a_second_run_is_a_no_op():
    site = _Site(rows={"Budget Line": 2})
    _run(site)
    site.calls.clear()
    _run(site)
    assert site.tables == {} and site.doctypes == set()
    assert not [c for c in site.calls if c[0] in ("count", "log.warning")]


def test_safe_on_a_site_where_they_are_absent():
    site = _Site(present=False)
    _run(site)
    assert site.custom_fields == [{"dt": "Budget Entry Line", "fieldname": "region"}]
    assert not [c for c in site.calls if c[0] == "count"]
    for c in site.calls:
        if c[0] == "delete_doc":
            assert c[3].get("ignore_missing") is True
