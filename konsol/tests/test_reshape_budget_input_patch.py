"""The reshape_budget_input_to_cycle patch after S2-C Z1, host-run.

The patch pivoted ``Budget Input`` rows into Budget Cycle / Sheet / Line.
Z1 retired Sheet and Line and the grain helpers the pivot imported, so there
is nothing left to pivot into. A site that still holds Budget Input rows here
must not migrate on as if they were handled: the patch refuses, naming the
counts and what to do, and Frappe does not mark it done. A site without the
table, or with an empty one, has nothing to lose: the patch does nothing.

A stub frappe records every call; anything but the two reads fails.
"""
import importlib.util
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "reshape_budget_input_to_cycle.py")
TABLES = ("Budget Input", "Budget Input Child")


class _Site:
    def __init__(self, rows=None):
        self.tables = dict(rows) if rows is not None else {}
        self.calls = []
        self.printed = []

    def run(self, monkeypatch):
        site = self

        class DB:
            def table_exists(self, doctype, *a, **k):
                site.calls.append(("table_exists", doctype))
                return doctype in site.tables

            def count(self, doctype, *a, **k):
                site.calls.append(("count", doctype))
                assert doctype in site.tables, "count on a missing table raises"
                return site.tables[doctype]

            def __getattr__(self, name):
                raise AssertionError(f"frappe.db.{name} called")

        frappe = types.ModuleType("frappe")
        frappe.db = DB()
        for name in ("get_all", "get_doc", "new_doc", "log_error", "delete_doc", "throw"):
            def refuse(*a, _name=name, **k):
                raise AssertionError(f"frappe.{_name} called")
            setattr(frappe, name, refuse)
        monkeypatch.setitem(sys.modules, "frappe", frappe)
        monkeypatch.setattr("builtins.print", lambda *a, **k: site.printed.append(" ".join(map(str, a))))
        spec = importlib.util.spec_from_file_location("_reshape_budget_input_under_test", PATCH_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.execute()
        return self


def test_rows_left_in_budget_input_refuse_the_migrate_naming_the_counts(monkeypatch):
    site = _Site({"Budget Input": 3, "Budget Input Child": 7})
    with pytest.raises(Exception) as raised:
        site.run(monkeypatch)
    msg = str(raised.value)
    assert "3 Budget Input" in msg and "7 Budget Input Child" in msg, msg
    assert "migrate again" in msg, msg          # says what to do
    assert "Budget Entr" in msg, msg            # and where budgets go now
    assert site.printed == []                   # refused, not printed and passed


def test_rows_only_in_the_child_table_refuse_too(monkeypatch):
    site = _Site({"Budget Input": 0, "Budget Input Child": 2})
    with pytest.raises(Exception) as raised:
        site.run(monkeypatch)
    assert "2 Budget Input Child" in str(raised.value)


def test_an_absent_table_is_a_no_op(monkeypatch):
    site = _Site().run(monkeypatch)
    assert site.printed == []
    assert all(call[0] == "table_exists" for call in site.calls), site.calls


def test_empty_tables_are_a_no_op(monkeypatch):
    site = _Site({"Budget Input": 0, "Budget Input Child": 0}).run(monkeypatch)
    assert site.printed == []
    assert {c[0] for c in site.calls} <= {"table_exists", "count"}, site.calls
