"""The drop_cash_flow_category_sign migration patch (konsol#197), host-run.

`Cash Flow Category.sign` was a required Select that nothing read: no dbt model
selected it and the chart mirror just wrote "1". The field has left the doctype,
but Frappe never drops a column when its field leaves the JSON, so the old value
would sit in `tabCash Flow Category` indefinitely — invisible to the ORM, still
readable by raw SQL, and different from what a fresh install has. This patch
drops it, guarded: absent column, no DDL; a failing DDL is logged, never raised,
so a failed drop cannot fail a migrate; a second run is a no-op.

A stub frappe records every call in order over an in-memory column list.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "drop_cash_flow_category_sign.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
MODULE = "konsol.patches.drop_cash_flow_category_sign"
DDL = "alter table `tabCash Flow Category` drop column `sign`"


class _Site:
    def __init__(self, columns, ddl_raises=False):
        self.calls = []
        #: doctype -> the columns MariaDB still has
        self.columns = {k: list(v) for k, v in columns.items()}
        self.ddl_raises = ddl_raises

    def module(self):
        site = self
        frappe = types.ModuleType("frappe")

        def has_column(doctype, column):
            site.calls.append(("has_column", doctype, column))
            return column in site.columns.get(doctype, [])

        def sql_ddl(statement, *a, **k):
            site.calls.append(("sql_ddl", statement))
            if site.ddl_raises:
                raise RuntimeError("ZZ denied")
            for columns in site.columns.values():
                if "sign" in columns:
                    columns.remove("sign")

        def logger(*a, **k):
            def info(message, *ia, **ik):
                site.calls.append(("log.info", message))

            def warning(message, *wa, **wk):
                site.calls.append(("log.warning", message))

            return types.SimpleNamespace(info=info, warning=warning)

        frappe.db = types.SimpleNamespace(has_column=has_column, sql_ddl=sql_ddl)
        frappe.logger = logger
        return frappe


def _run(site):
    """Run the patch under the stub frappe."""
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = site.module()
    try:
        spec = importlib.util.spec_from_file_location(
            "_drop_cfc_sign_under_test", PATCH_PY)
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        patch.execute()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


def _site(**kw):
    return _Site({"Cash Flow Category": ["name", "cf_category", "is_cash", "sign", "status"]}, **kw)


def _site_without_the_column(**kw):
    return _Site({"Cash Flow Category": ["name", "cf_category", "is_cash", "status"]}, **kw)


def _ddl(site):
    return [c for c in site.calls if c[0] == "sql_ddl"]


def _warnings(site):
    return [c for c in site.calls if c[0] == "log.warning"]


def test_patch_module_exists():
    assert os.path.exists(PATCH_PY), PATCH_PY


def test_column_present_is_dropped_by_exactly_that_ddl():
    site = _site()
    _run(site)
    assert _ddl(site) == [("sql_ddl", DDL)], site.calls
    assert "sign" not in site.columns["Cash Flow Category"], site.columns


def test_column_absent_issues_no_ddl():
    site = _site_without_the_column()
    _run(site)
    assert _ddl(site) == [], site.calls
    assert not _warnings(site), site.calls


def test_guarded_by_has_column_before_any_ddl():
    site = _site()
    _run(site)
    assert site.calls[0] == ("has_column", "Cash Flow Category", "sign"), site.calls


def test_failing_ddl_is_logged_and_swallowed():
    site = _site(ddl_raises=True)
    _run(site)  # a failed drop must not fail a migrate
    assert _ddl(site) == [("sql_ddl", DDL)], site.calls
    warnings = _warnings(site)
    assert len(warnings) == 1, site.calls
    assert "sign" in warnings[0][1], warnings


def test_second_run_is_a_no_op():
    site = _site()
    _run(site)
    site.calls.clear()
    _run(site)
    assert _ddl(site) == [], site.calls


def test_listed_once_after_the_cash_flow_category_patches():
    """Registered exactly once, and after the patches that write that table.

    `rename_cash_flow_category_to_account` renames the rows and
    `fill_cash_flow_categories_from_chart` inserts them, so the drop runs once
    nothing else in the same migrate still touches `tabCash Flow Category`.
    What must NOT be asserted is that this module is the last line of
    patches.txt: the drop is guarded and idempotent, so a patch appended after
    it is none of this test's business — and every future patch is appended
    there.
    """
    with open(PATCHES_TXT) as f:
        lines = [l.strip() for l in f if l.strip()]
    assert lines.count(MODULE) == 1, lines.count(MODULE)
    mine = lines.index(MODULE)
    for earlier in ("konsol.patches.rename_cash_flow_category_to_account",
                    "konsol.patches.fill_cash_flow_categories_from_chart"):
        assert lines.index(earlier) < mine, (earlier, lines.index(earlier), mine)
