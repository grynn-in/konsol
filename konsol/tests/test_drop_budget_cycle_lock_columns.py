"""The drop_budget_cycle_lock_columns patch (konsol#348 S2-C W3), host-run.

S2-C W2a removed `status`, `locked_by` and `locked_on` from Budget Cycle: the
Cycle is a workflow approval (BUD-9-3), not a deadline lock. Frappe never
drops a column whose field leaves the JSON, so this patch does. It drops only
the columns that exist, so a second run is a no-op; a site without the table
is left alone.

A stub frappe records every call over an in-memory column list.
"""
import importlib.util
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "drop_budget_cycle_lock_columns.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
MODULE = "konsol.patches.drop_budget_cycle_lock_columns"
LOCK_COLUMNS = ("status", "locked_by", "locked_on")


class _Site:
    def __init__(self, columns, table=True, ddl_raises=False):
        self.calls = []
        self.columns = list(columns)
        self.table = table
        self.ddl_raises = ddl_raises

    def module(self):
        site = self
        frappe = types.ModuleType("frappe")

        def table_exists(doctype, *a, **k):
            site.calls.append(("table_exists", doctype))
            return site.table

        def has_column(doctype, column):
            site.calls.append(("has_column", doctype, column))
            assert site.table, "has_column on a missing table raises TableMissingError"
            return column in site.columns

        def sql_ddl(statement, *a, **k):
            site.calls.append(("sql_ddl", statement))
            if site.ddl_raises:
                raise RuntimeError("ZZ denied")
            for column in LOCK_COLUMNS:
                if f"drop column `{column}`" in statement and column in site.columns:
                    site.columns.remove(column)

        def logger(*a, **k):
            return types.SimpleNamespace(
                info=lambda message, *ia, **ik: site.calls.append(("log.info", message)),
                warning=lambda message, *wa, **wk: site.calls.append(("log.warning", message)))

        frappe.db = types.SimpleNamespace(table_exists=table_exists, has_column=has_column, sql_ddl=sql_ddl)
        frappe.logger = logger
        return frappe


def _run(site):
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = site.module()
    try:
        assert os.path.exists(PATCH_PY), "no drop_budget_cycle_lock_columns.py"
        spec = importlib.util.spec_from_file_location("_drop_bcyc_lock_under_test", PATCH_PY)
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


def test_drops_every_lock_column_that_exists():
    site = _Site(["name", "scenario_id", *LOCK_COLUMNS])
    _run(site)
    assert site.columns == ["name", "scenario_id"]
    assert len(_ddl(site)) == 1, _ddl(site)
    assert _ddl(site)[0] == ("alter table `tabBudget Cycle` drop column `status`, "
                             "drop column `locked_by`, drop column `locked_on`")


@pytest.mark.parametrize("present", [("status",), ("locked_by", "locked_on"), ("locked_on",)])
def test_drops_only_the_columns_that_exist(present):
    site = _Site(["name", *present])
    _run(site)
    assert site.columns == ["name"]
    (ddl,) = _ddl(site)
    for column in LOCK_COLUMNS:
        assert (f"drop column `{column}`" in ddl) == (column in present), (column, ddl)


def test_a_second_run_is_a_no_op():
    site = _Site(["name", *LOCK_COLUMNS])
    _run(site)
    site.calls.clear()
    _run(site)
    assert _ddl(site) == []


def test_no_table_no_ddl():
    site = _Site([], table=False)
    _run(site)
    assert _ddl(site) == []
    assert not [c for c in site.calls if c[0] == "has_column"]


def test_a_failed_drop_is_logged_never_raised():
    site = _Site(["name", *LOCK_COLUMNS], ddl_raises=True)
    _run(site)
    warnings = [c[1] for c in site.calls if c[0] == "log.warning"]
    assert warnings and "status, locked_by, locked_on" in warnings[0], warnings
