"""Budget Annual Input runs the budget write-access check on every save (#413).

The bottom-up path (`_upsert_budget_line`, `budget_cell_save`) calls
`api._assert_budget_write_access`; the top-down path, a Desk save of an
annual row, did not, so a user granted one entity could write another's
budget. These tests load the controller against stubbed frappe and assert
validate hands the check the row's entity, account and every `dim_*` value.
"""
import importlib.util
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(APP_DIR, "epm", "doctype", "budget_annual_input",
                    "budget_annual_input.py")

DIMS = ["dim_cost_center", "dim_region"]


class _Flags:
    in_install = False
    in_migrate = False
    in_import = False


@pytest.fixture
def ctl(monkeypatch):
    calls = []

    class Document:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def get(self, k):
            return self.__dict__.get(k)

    def throw(msg, *a, **k):
        raise Exception(msg)

    frappe = types.ModuleType("frappe")
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.flags = _Flags()
    frappe.session = types.SimpleNamespace(user="zz@example.com")
    statuses = {}
    frappe.db = types.SimpleNamespace(
        exists=lambda *a, **k: None,
        get_value=lambda dt, name, field, *a, **k: statuses.get(name, "Published"))
    frappe.throw = throw
    meta = types.SimpleNamespace(fields=[
        types.SimpleNamespace(fieldname=d, fieldtype="Data") for d in DIMS])
    frappe.get_meta = lambda dt: meta
    model = types.ModuleType("frappe.model")
    docmod = types.ModuleType("frappe.model.document")
    docmod.Document = Document

    ch = types.ModuleType("konsol.clickhouse")
    ch.sync_doctype_after_commit = lambda *a, **k: None

    scen = types.ModuleType("konsol.epm.doctype.scenario.scenario")
    scen.locked_scenario_type = lambda s: "budget"

    api = types.ModuleType("konsol.api")

    def _assert(data):
        calls.append(dict(data))
        if data.get("data_area_id") != "ZZE1":
            raise frappe.PermissionError("not granted")

    api._assert_budget_write_access = _assert

    for name, mod in {
        "frappe": frappe, "frappe.model": model,
        "frappe.model.document": docmod, "konsol.clickhouse": ch,
        "konsol.epm.doctype.scenario.scenario": scen, "konsol.api": api,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)

    spec = importlib.util.spec_from_file_location("_bai_under_test", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return types.SimpleNamespace(mod=mod, frappe=frappe, calls=calls, statuses=statuses)


def _row(ctl, entity):
    return ctl.mod.BudgetAnnualInput(
        doctype="Budget Annual Input", name="x", scenario_id="ZZS",
        data_area_id=entity, fiscal_year="2026", main_account="4000",
        dim_cost_center="CC1", dim_region="EU", submitted_by="u")


def test_ungranted_entity_is_refused(ctl):
    with pytest.raises(ctl.frappe.PermissionError):
        _row(ctl, "ZZE2").validate()


def test_granted_entity_saves(ctl):
    _row(ctl, "ZZE1").validate()


def test_check_receives_entity_account_and_every_dimension(ctl):
    _row(ctl, "ZZE1").validate()
    assert len(ctl.calls) == 1
    data = ctl.calls[0]
    assert data["data_area_id"] == "ZZE1"
    assert data["main_account"] == "4000"
    for d in DIMS:
        assert d in data
    assert data["dim_cost_center"] == "CC1"
    assert data["dim_region"] == "EU"


@pytest.mark.parametrize("flag", ["in_install", "in_migrate"])
def test_skipped_during_install_migrate(ctl, flag):
    setattr(ctl.frappe.flags, flag, True)
    _row(ctl, "ZZE2").validate()
    assert ctl.calls == []


def test_import_flag_does_not_skip_the_check(ctl):
    # Desk Data Import sets in_import; any user allowed to import must still
    # be held to their entity grants.
    ctl.frappe.flags.in_import = True
    with pytest.raises(ctl.frappe.PermissionError):
        _row(ctl, "ZZE2").validate()


def test_delete_of_ungranted_entity_is_refused(ctl):
    with pytest.raises(ctl.frappe.PermissionError):
        _row(ctl, "ZZE2").on_trash()


def test_delete_of_granted_entity_passes(ctl):
    _row(ctl, "ZZE1").on_trash()


def test_draft_main_account_is_refused_by_name(ctl):
    ctl.statuses["4000"] = "Draft"
    with pytest.raises(Exception) as e:
        _row(ctl, "ZZE1").validate()
    assert "4000" in str(e.value) and "Draft" in str(e.value)


def test_inactive_main_account_is_refused(ctl):
    ctl.statuses["4000"] = "Inactive"
    with pytest.raises(Exception, match="Inactive"):
        _row(ctl, "ZZE1").validate()


def test_published_main_account_passes(ctl):
    ctl.statuses["4000"] = "Published"
    _row(ctl, "ZZE1").validate()


@pytest.mark.parametrize("flag", ["in_install", "in_migrate"])
def test_main_account_status_skipped_during_install_migrate(ctl, flag):
    setattr(ctl.frappe.flags, flag, True)
    ctl.statuses["4000"] = "Draft"
    _row(ctl, "ZZE1").validate()
