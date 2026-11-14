"""BUD-4: Budget Annual Input reaches ClickHouse only under an approved cycle.

Budget Annual Input is not submittable, so ``resolve_sync_filters`` used to
return None for it and every save published every row, whatever state the
Budget Cycle for its (scenario_id, fiscal_year) was in. The controller now
declares ``CH_SYNC_FILTERS``, worked out when read: the rows whose cycle has
docstatus 1. Both write paths (the per-save sync and ``reconcile_all``) read
it through ``resolve_sync_filters``, and the sync is TRUNCATE+INSERT, so a row
that leaves the set leaves the warehouse at the next sync.

These tests load the controller against stubbed frappe whose ``get_all`` runs
over in-memory rows, then apply the declared filter to the same rows.
"""
import importlib.util
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(APP_DIR, "epm", "doctype", "budget_annual_input",
                    "budget_annual_input.py")


def _match(row, filters):
    for field, cond in (filters or {}).items():
        val = row.get(field)
        if isinstance(cond, (list, tuple)):
            op, arg = cond[0], cond[1]
            if op == "in":
                if val not in arg:
                    return False
            elif op == "is" and arg == "not set":
                if val not in (None, ""):
                    return False
            else:
                raise AssertionError(f"unsupported filter {field}: {cond!r}")
        elif val != cond:
            return False
    return True


@pytest.fixture
def ctl(monkeypatch):
    tables = {"Budget Cycle": [], "Budget Annual Input": []}

    def get_all(doctype, filters=None, fields=None, **kw):
        out = []
        for r in tables[doctype]:
            if _match(r, filters):
                out.append({f: r.get(f) for f in (fields or ["name"])})
        return out

    class Document:
        pass

    frappe = types.ModuleType("frappe")
    frappe.flags = types.SimpleNamespace(in_install=False, in_migrate=False, in_import=False)
    frappe.get_all = get_all
    frappe.get_meta = lambda dt: types.SimpleNamespace(fields=[])
    model = types.ModuleType("frappe.model")
    docmod = types.ModuleType("frappe.model.document")
    docmod.Document = Document
    ch = types.ModuleType("konsol.clickhouse")
    ch.sync_doctype_after_commit = lambda *a, **k: None
    # The guard asks budget_intake for the Cycle workflow (V2); none is active here.
    intake = types.ModuleType("konsol.budget_intake")
    intake.workflow_for = lambda cycles: None
    for name, mod in {"frappe": frappe, "frappe.model": model,
                      "frappe.model.document": docmod,
                      "konsol.clickhouse": ch, "konsol.budget_intake": intake}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    import konsol
    monkeypatch.setattr(konsol, "budget_intake", intake, raising=False)

    spec = importlib.util.spec_from_file_location("_bai_gate_under_test", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return types.SimpleNamespace(mod=mod, tables=tables)


def _cycle(ctl, scenario, year, docstatus):
    ctl.tables["Budget Cycle"].append(
        {"name": f"BC-{scenario}-{year}-{docstatus}", "scenario_id": scenario,
         "fiscal_year": year, "docstatus": docstatus})


def _row(ctl, name, scenario, year):
    ctl.tables["Budget Annual Input"].append(
        {"name": name, "scenario_id": scenario, "fiscal_year": year})


def _synced(ctl):
    filters = ctl.mod.BudgetAnnualInput.CH_SYNC_FILTERS
    # resolve_sync_filters treats a falsy declaration as "no declaration" and
    # syncs every row; the declaration must never be empty.
    assert filters, "an empty CH_SYNC_FILTERS falls through to every row"
    return sorted(r["name"] for r in ctl.tables["Budget Annual Input"]
                  if _match(r, filters))


def test_declares_sync_filters(ctl):
    assert hasattr(ctl.mod.BudgetAnnualInput, "CH_SYNC_FILTERS")


def test_row_under_approved_cycle_syncs(ctl):
    _cycle(ctl, "ZZS", 2026, 1)
    _row(ctl, "ok", "ZZS", 2026)
    assert _synced(ctl) == ["ok"]


@pytest.mark.parametrize("docstatus", [0, 2])
def test_row_under_draft_or_cancelled_cycle_is_excluded(ctl, docstatus):
    _cycle(ctl, "ZZS", 2026, docstatus)
    _row(ctl, "held", "ZZS", 2026)
    assert _synced(ctl) == []


def test_row_without_a_cycle_is_excluded(ctl):
    _row(ctl, "orphan", "ZZS", 2026)
    assert _synced(ctl) == []


def test_no_approved_cycle_matches_nothing_never_empty_in(ctl):
    _cycle(ctl, "ZZS", 2026, 0)
    _row(ctl, "a", "ZZS", 2026)
    _row(ctl, "b", "ZZT", 2025)
    filters = ctl.mod.BudgetAnnualInput.CH_SYNC_FILTERS
    for cond in filters.values():
        assert cond != ["in", []] and cond != ("in", []), filters
    assert _synced(ctl) == []


def test_pair_is_matched_not_the_cross_product(ctl):
    """Approved (S1, 2026) and (S2, 2025) must not approve (S1, 2025)."""
    _cycle(ctl, "ZZS1", 2026, 1)
    _cycle(ctl, "ZZS2", 2025, 1)
    _row(ctl, "s1_26", "ZZS1", 2026)
    _row(ctl, "s2_25", "ZZS2", 2025)
    _row(ctl, "s1_25", "ZZS1", 2025)
    _row(ctl, "s2_26", "ZZS2", 2026)
    assert _synced(ctl) == ["s1_26", "s2_25"]


def test_cancelled_cycle_beside_an_approved_one_still_approves(ctl):
    """An amended cycle leaves a cancelled original; the approved one counts."""
    _cycle(ctl, "ZZS", 2026, 2)
    _cycle(ctl, "ZZS", 2026, 1)
    _row(ctl, "ok", "ZZS", 2026)
    assert _synced(ctl) == ["ok"]


def test_filter_is_worked_out_on_every_read(ctl):
    """Not fixed at import: a cycle approved later widens the next sync."""
    _row(ctl, "late", "ZZS", 2026)
    assert _synced(ctl) == []
    _cycle(ctl, "ZZS", 2026, 1)
    assert _synced(ctl) == ["late"]


# ---- the Cycle lock guard holds during a Data Import --------------------------

def test_the_cycle_lock_guard_refuses_during_a_data_import(ctl):
    # A Data Import into an approved Cycle was a way past the lock: the guard
    # skipped on in_import. Only install and migrate skip it.
    class Refused(Exception):
        pass

    def throw(msg, exc=None, *a, **k):
        raise (exc or Refused)(msg)

    ctl.mod.frappe.throw = throw
    ctl.mod.frappe.flags.in_import = True
    row = ctl.mod.BudgetAnnualInput()
    row.scenario_id, row.fiscal_year = "ZZS", 2026
    with pytest.raises(Refused, match="locked"):
        row._guard_cycle_locked([{"name": "BC-ZZS-2026", "docstatus": 1, "workflow_state": "Approved"}])
