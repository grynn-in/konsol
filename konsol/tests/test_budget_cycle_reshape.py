"""Tests for the Budget Input → Cycle / Sheet / Line reshape.

Site-free, matching the repo convention: a pure check on the wide period
columns plus AST/source assertions that the lock gate, the wide→tall explode,
the cycle-open write guard, and the non-destructive migration are wired
correctly. (Live ClickHouse / D365 / DB behaviour is exercised by
``bench run-tests`` against a site, not here.)
"""
import ast
import os

from konsol.epm.budget_periods import PERIOD_FIELDS

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(APP_DIR, "api.py")
CYCLE_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_cycle", "budget_cycle.py")
SHEET_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_sheet", "budget_sheet.py")
D365_PY = os.path.join(APP_DIR, "d365_writeback.py")
PATCH_PY = os.path.join(APP_DIR, "patches", "reshape_budget_input_to_cycle.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")


def _src(path):
    with open(path) as fh:
        return fh.read()


def _func(src, name):
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.get_source_segment(src, n)
    return None


# --- wide layout -----------------------------------------------------------

def test_period_fields_are_twelve_in_order():
    assert PERIOD_FIELDS == tuple("period_%02d" % n for n in range(1, 13))
    assert len(PERIOD_FIELDS) == 12


def test_period_fields_module_is_frappe_free():
    # build_entries imports this without a site; it must not import frappe.
    # (Importing PERIOD_FIELDS at the top of this file already proves it loads
    # with no frappe present.)
    tree = ast.parse(_src(os.path.join(APP_DIR, "epm", "budget_periods.py")))
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    assert "frappe" not in imported


# --- the lock gate (Budget Cycle) ------------------------------------------

def test_cycle_lock_fires_sync_and_d365_once_per_sheet():
    on_submit = _func(_src(CYCLE_PY), "on_submit")
    assert "_sync_to_clickhouse" in on_submit
    assert "enqueue_push_budget_sheet" in on_submit
    assert 'status", "Locked"' in on_submit  # docstatus 0->1 sets Locked


def test_cycle_cancel_reopens_and_withdraws():
    on_cancel = _func(_src(CYCLE_PY), "on_cancel")
    assert 'status", "Open"' in on_cancel
    assert "active=False" in on_cancel          # ClickHouse rows withdrawn
    assert "withdraw_budget_sheet" in on_cancel


# --- wide -> tall explode (Budget Sheet) -----------------------------------

def test_sheet_sync_explodes_wide_to_tall():
    sync = _func(_src(SHEET_PY), "_sync_to_clickhouse")
    assert "PERIOD_FIELDS" in sync                      # iterates the 12 columns
    assert "budget_monthly_input" in _src(SHEET_PY)


def test_sheet_sync_key_is_scenario_entity_fy_layer():
    sync = _func(_src(SHEET_PY), "_sync_to_clickhouse")
    # one delete+insert per (scenario, entity, fiscal_year, layer) sheet grain
    assert '"layer"' in sync and "key_columns" in sync
    assert '"main_account"' not in sync.split("key_columns")[1].split("]")[0]


# --- cycle-open write guard (api) ------------------------------------------

def test_writes_resolve_cycle_and_reject_when_locked():
    src = _src(API_PATH)
    for fn in ("_upsert_budget_line", "budget_cell_save"):
        body = _func(src, fn)
        assert "_resolve_budget_cycle" in body
        assert "_assert_cycle_open" in body
    guard = _func(src, "_assert_cycle_open")
    assert "Locked" in guard and "423" in guard


# --- D365 sheet-grain entrypoints ------------------------------------------

def test_d365_has_sheet_grain_entrypoints():
    src = _src(D365_PY)
    assert _func(src, "push_budget_sheet") is not None
    assert _func(src, "enqueue_push_budget_sheet") is not None
    assert _func(src, "withdraw_budget_sheet") is not None
    assert _func(src, "purge_budget_model") is not None
    # build_entries now takes the parent cycle's fiscal_year explicitly
    assert "def build_entries(sheet, fiscal_year" in src


# --- non-destructive, idempotent migration ---------------------------------

def test_migration_is_registered():
    assert "konsol.patches.reshape_budget_input_to_cycle" in _src(PATCHES_TXT)


def test_migration_pivots_into_cycle_sheet_line():
    src = _src(PATCH_PY)
    assert _func(src, "execute") is not None
    for token in ("Budget Cycle", "Budget Sheet", "period_%02d"):
        assert token in src


def test_migration_is_non_destructive():
    # old Budget Input docs are retained for verification/rollback — the patch
    # must not delete or rename them.
    src = _src(PATCH_PY)
    assert "frappe.delete_doc" not in src
    assert "rename_doc" not in src


def test_migration_exposes_legacy_d365_purge():
    src = _src(PATCH_PY)
    purge = _func(src, "purge_legacy_d365")
    assert purge is not None
    assert "purge_budget_model" in purge       # reuses the delete-only $batch
    assert "budget_model_id" in purge
