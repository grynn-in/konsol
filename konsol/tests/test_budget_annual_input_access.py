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

    locks = []   # lock acquisition, in order

    class Document:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def get(self, k):
            return self.__dict__.get(k)

        def check_if_latest(self, *args, **kwargs):
            # Frappe's: load_doc_before_save reads the row FOR UPDATE.
            locks.append(("annual input", self.name))

    def throw(msg, exc=None, *a, **k):
        # frappe.throw raises the type it is given.
        raise (exc or Exception)(msg)

    frappe = types.ModuleType("frappe")
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.QueryTimeoutError = type("QueryTimeoutError", (Exception,), {})
    frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
    frappe.flags = _Flags()
    frappe.session = types.SimpleNamespace(user="zz@example.com")
    statuses = {}
    locked = {"Budget Cycle": [{"name": "BC-ZZS-2026", "scenario_id": "ZZS", "fiscal_year": 2026, "docstatus": 0,
                                "workflow_state": "Draft"},
                               {"name": "BC-ZZOLD-2026", "scenario_id": "ZZOLD", "fiscal_year": 2026,
                                "docstatus": 0, "workflow_state": "Draft"}],
              "Budget Entry Line": [],
              "Budget Annual Input": []}

    held = types.SimpleNamespace(cycle=False)   # another session holds the Cycle rows
    waits = []

    def get_values(doctype, filters=None, fields=None, as_dict=False, order_by=None, for_update=False,
                   wait=True, **kw):
        # REPEATABLE READ: only a locking read sees what another session
        # committed after this transaction's first read.
        assert for_update and as_dict and order_by, (doctype, for_update, as_dict, order_by)
        if doctype == "Budget Cycle":
            waits.append(wait)
            if held.cycle and not wait:
                raise frappe.QueryTimeoutError("Lock wait timeout exceeded (NOWAIT)")
        def match(value, cond):
            if isinstance(cond, list) and cond[0] == "in":
                return value in cond[1]
            if isinstance(cond, list) and cond[0] == "!=":
                return str(value) != str(cond[1])
            if isinstance(cond, list) and cond == ["is", "not set"]:
                return value in (None, "")
            return str(value) == str(cond)
        rows = [r for r in locked[doctype] if all(match(r.get(f), v) for f, v in (filters or {}).items())]
        if doctype == "Budget Cycle":
            locks.extend(("cycle", r["name"]) for r in rows)
        else:
            locks.append(("read", doctype))
        return [types.SimpleNamespace(**{f: r.get(f) for f in fields}, get=r.get) for r in rows]

    def plain_read(*a, **k):
        # A writer check after the Cycle lock reads FOR UPDATE: a plain read
        # serves the snapshot and misses what another writer committed.
        raise AssertionError(f"plain read of {a[:1]}: a writer check reads with a locking read")

    frappe.db = types.SimpleNamespace(
        exists=plain_read,
        get_value=lambda dt, name, field, *a, **k: statuses.get(name, "Published"),
        get_values=get_values)
    frappe.throw = throw
    frappe.ValidationError = Exception
    frappe._ = lambda s: s
    frappe.get_roles = lambda user=None: ["Entity Accountant"]
    tables = {
        "Budget Layer": [{"name": "base", "writer_role": None, "sort_order": 1},
                         {"name": "board", "writer_role": "Budget Approver", "sort_order": 4}],
        "Scenario": [{"name": "ZZS", "scenario_type": "budget", "is_active": 1},
                     {"name": "ZZOLD", "scenario_type": "budget", "is_active": 0}],
        "Entity": [{"name": "ZZE1", "functional_currency": "EUR"},
                   {"name": "ZZE2", "functional_currency": "EUR"}],
    }

    def get_all(doctype, filters=None, fields=None, **kw):
        rows = [r for r in tables[doctype]
                if all(r.get(f) == v for f, v in (filters or {}).items())]
        return [types.SimpleNamespace(**r, get=r.get) for r in rows]

    frappe.get_all = get_all
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

    perms = types.ModuleType("konsol.entity_permissions")
    perms.BYPASS_ROLES = frozenset({"System Manager", "Administrator"})
    chart = types.ModuleType("konsol.group_chart")
    chart.chart_accounts = lambda: {}
    periods = types.ModuleType("konsol.period_status")
    periods.PeriodNotDeclared = type("PeriodNotDeclared", (Exception,), {})
    periods.period_row = lambda y, p: None

    # the real intake and its model: validate must go through them
    intake_spec = importlib.util.spec_from_file_location(
        "konsol.budget_intake", os.path.join(APP_DIR, "budget_intake.py"))
    intake = importlib.util.module_from_spec(intake_spec)

    for name, mod in {
        "konsol.entity_permissions": perms, "konsol.group_chart": chart,
        "konsol.period_status": periods,
        "frappe": frappe, "frappe.model": model,
        "frappe.model.document": docmod, "konsol.clickhouse": ch,
        "konsol.epm.doctype.scenario.scenario": scen, "konsol.api": api,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)

    intake_spec.loader.exec_module(intake)
    monkeypatch.setitem(sys.modules, "konsol.budget_intake", intake)
    monkeypatch.setattr(sys.modules["konsol"], "budget_intake", intake, raising=False)
    spec = importlib.util.spec_from_file_location("_bai_under_test", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return types.SimpleNamespace(mod=mod, frappe=frappe, calls=calls, statuses=statuses, locks=locks,
                                 locked=locked, held=held, waits=waits)


def _row(ctl, entity, **over):
    fields = dict(
        doctype="Budget Annual Input", name="x", scenario_id="ZZS", layer="base", currency="EUR",
        data_area_id=entity, fiscal_year="2026", main_account="4000",
        dim_cost_center="CC1", dim_region="EU", submitted_by="u")
    fields.update(over)
    return ctl.mod.BudgetAnnualInput(**fields)


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


def test_validate_goes_through_the_intake_not_a_private_check(ctl):
    assert not hasattr(ctl.mod.BudgetAnnualInput, "_assert_write_access")


def test_wrong_currency_is_refused_on_save(ctl):
    with pytest.raises(Exception, match="USD"):
        _row(ctl, "ZZE1", currency="USD").validate()


def test_undeclared_layer_is_refused_on_save(ctl):
    with pytest.raises(Exception, match="Layer nope is not declared"):
        _row(ctl, "ZZE1", layer="nope").validate()


def test_inactive_scenario_is_refused_as_inactive(ctl):
    with pytest.raises(Exception, match="Scenario ZZOLD is inactive"):
        _row(ctl, "ZZE1", scenario_id="ZZOLD").validate()


def test_layer_and_currency_are_not_judged_on_delete(ctl):
    _row(ctl, "ZZE1", layer="nope", currency="USD").on_trash()
    assert len(ctl.calls) == 1


# ---- Q16-c: a grain takes its numbers from one writer ---------------------------

def _line(**over):
    line = {"parent": "BENT-P3", "parenttype": "Budget Entry", "cycle": "BC-ZZS-2026",
            "scenario_id": "ZZS", "fiscal_year": 2026,
            "data_area_id": "ZZE1", "layer": "base", "fiscal_period": 3, "main_account": "4000",
            "dim_cost_center": "CC1", "dim_region": "EU"}
    line.update(over)
    return line


def test_an_annual_input_on_a_budget_entry_grain_is_refused_naming_the_entry(ctl):
    ctl.locked["Budget Entry Line"].append(_line())
    with pytest.raises(Exception) as raised:
        _row(ctl, "ZZE1").validate()
    assert "Budget Entry BENT-P3" in str(raised.value) and "period 3" in str(raised.value)


def test_an_annual_input_beside_another_layers_entry_saves(ctl):
    ctl.locked["Budget Entry Line"].append(_line(layer="board"))
    _row(ctl, "ZZE1").validate()


def test_validate_locks_the_cycle_before_reading_the_entry_lines(ctl):
    _row(ctl, "ZZE1").validate()
    assert ctl.locks.index(("cycle", "BC-ZZS-2026")) < ctl.locks.index(("read", "Budget Entry Line"))


def test_a_save_locks_the_cycle_before_frappe_locks_the_annual_input(ctl):
    """The order every budget writer keeps: the Cycle row first. Frappe's
    check_if_latest locks this row before validate runs."""
    _row(ctl, "ZZE1").check_if_latest()
    assert ctl.locks == [("cycle", "BC-ZZS-2026"), ("annual input", "x")]


def test_the_unique_grain_is_read_after_the_cycle_lock(ctl):
    """Two Annual Inputs on one grain: the second waits on the Cycle, then
    its duplicate check runs (gap 3 of the Q16 build), as a locking read."""
    _row(ctl, "ZZE1").validate()
    assert ctl.locks.index(("cycle", "BC-ZZS-2026")) < ctl.locks.index(("read", "Budget Annual Input"))


def test_a_duplicate_committed_while_the_save_waited_is_refused(ctl):
    """PR #522 review 2: another session committed the same grain after this
    transaction's snapshot. Only a locking read sees it."""
    ctl.locked["Budget Annual Input"].append(dict(
        name="BAI-OTHER", scenario_id="ZZS", data_area_id="ZZE1", fiscal_year=2026, layer="base",
        main_account="4000", dim_cost_center="CC1", dim_region="EU"))
    with pytest.raises(Exception, match="BAI-OTHER"):
        _row(ctl, "ZZE1").validate()


def test_a_duplicate_with_a_blank_dimension_matches_an_unset_one(ctl):
    ctl.locked["Budget Annual Input"].append(dict(
        name="BAI-OTHER", scenario_id="ZZS", data_area_id="ZZE1", fiscal_year=2026, layer="base",
        main_account="4000", dim_cost_center="CC1", dim_region=None))
    with pytest.raises(Exception, match="BAI-OTHER"):
        _row(ctl, "ZZE1", dim_region="").validate()


def test_the_row_itself_is_not_its_own_duplicate(ctl):
    ctl.locked["Budget Annual Input"].append(dict(
        name="x", scenario_id="ZZS", data_area_id="ZZE1", fiscal_year=2026, layer="base",
        main_account="4000", dim_cost_center="CC1", dim_region="EU"))
    _row(ctl, "ZZE1").validate()


def test_a_cycle_approved_while_the_save_waited_is_refused(ctl):
    """PR #522 review 2: the Cycle's state is read by the lock itself, so a
    Cycle approved while this save waited on it is seen. Approved is
    docstatus 1: the Cycle has no lock status since S2-C W2a."""
    ctl.locked["Budget Cycle"][0].update(docstatus=1, workflow_state="Approved")
    with pytest.raises(Exception, match="BC-ZZS-2026 is locked"):
        _row(ctl, "ZZE1").validate()


def test_a_cancelled_original_beside_a_draft_amendment_does_not_refuse(ctl):
    ctl.locked["Budget Cycle"][0].update(docstatus=2, workflow_state="Cancelled")
    ctl.locked["Budget Cycle"].append({"name": "BC-ZZS-2026-1", "scenario_id": "ZZS", "fiscal_year": 2026,
                                       "docstatus": 0, "workflow_state": "Draft"})
    _row(ctl, "ZZE1").validate()


def test_an_annual_input_without_a_budget_cycle_is_refused_by_name(ctl):
    # 20-b: no Cycle, nothing to lock, so nothing to serialise the
    # one-writer checks on; refused as a Budget Entry is (Q6-b).
    ctl.locked["Budget Cycle"][:] = [c for c in ctl.locked["Budget Cycle"] if c["scenario_id"] != "ZZS"]
    with pytest.raises(Exception) as raised:
        _row(ctl, "ZZE1").validate()
    assert str(raised.value) == ("No Budget Cycle exists for ZZS 2026. Create the Cycle before entering "
                                 "annual figures.")


@pytest.mark.parametrize("flag", ["in_install", "in_migrate"])
def test_install_and_migrate_skip_the_missing_cycle_refusal(ctl, flag):
    setattr(ctl.frappe.flags, flag, True)
    ctl.locked["Budget Cycle"].clear()
    _row(ctl, "ZZE1").validate()


@pytest.mark.parametrize("flag", ["in_install", "in_migrate"])
def test_install_and_migrate_skip_the_grain_check(ctl, flag):
    setattr(ctl.frappe.flags, flag, True)
    ctl.locked["Budget Entry Line"].append(_line())
    _row(ctl, "ZZE1").validate()
    assert [lock for lock in ctl.locks if lock != ("read", "Budget Annual Input")] == []


# ---- PR #522 review 3: a delete is under the Cycle lock, NOWAIT --------------------
# Frappe locks the Annual Input row before on_trash, so a delete cannot take
# the Cycle first: it takes it NOWAIT and fails fast, as Budget Entry's
# assert_cycle_draft(wait=False) does, instead of waiting while holding the row.

def test_a_delete_takes_the_cycle_lock_nowait(ctl):
    _row(ctl, "ZZE1").on_trash()
    assert ("cycle", "BC-ZZS-2026") in ctl.locks
    assert ctl.waits == [False]


def test_a_delete_under_an_approved_cycle_is_refused(ctl):
    ctl.locked["Budget Cycle"][0].update(docstatus=1, workflow_state="Approved")
    with pytest.raises(Exception, match="BC-ZZS-2026 is locked"):
        _row(ctl, "ZZE1").on_trash()


def test_a_delete_while_another_writer_holds_the_cycle_fails_fast_by_name(ctl):
    ctl.held.cycle = True
    with pytest.raises(ctl.frappe.QueryTimeoutError) as raised:
        _row(ctl, "ZZE1").on_trash()
    assert "being changed by another user" in str(raised.value), raised.value
    assert "ZZS 2026" in str(raised.value), raised.value


def test_a_delete_with_no_cycle_passes(ctl):
    # An orphan figure guards nothing, as an orphan Budget Entry may go.
    ctl.locked["Budget Cycle"].clear()
    _row(ctl, "ZZE1").on_trash()


def test_a_save_still_waits_for_the_cycle(ctl):
    _row(ctl, "ZZE1").validate()
    assert ctl.waits and all(ctl.waits)
