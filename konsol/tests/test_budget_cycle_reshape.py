"""Tests for the Budget Input → Cycle / Sheet / Line reshape.

Site-free, matching the repo convention: a pure check on the wide period
columns plus AST/source assertions that the lock gate, the wide→tall explode,
the cycle-open write guard, and the non-destructive migration are wired
correctly. (Live ClickHouse / D365 / DB behaviour is exercised by
``bench run-tests`` against a site, not here.)
"""
import ast
import json
import os

from konsol.epm.budget_periods import PERIOD_FIELDS

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(APP_DIR, "api.py")
CYCLE_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_cycle", "budget_cycle.py")
SHEET_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_sheet", "budget_sheet.py")
D365_PY = os.path.join(APP_DIR, "d365_writeback.py")
PATCH_PY = os.path.join(APP_DIR, "patches", "reshape_budget_input_to_cycle.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
GRAIN_PY = os.path.join(APP_DIR, "epm", "budget_grain.py")
INSTALL_PY = os.path.join(APP_DIR, "install.py")


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


# --- review fixes -----------------------------------------------------------

def test_cycle_create_race_is_handled():
    # Fix #2: the cycle insert (outside the per-sheet retry) must absorb the
    # concurrent-create DuplicateEntryError instead of 500-ing the writer.
    resolve = _func(_src(API_PATH), "_resolve_budget_cycle")
    assert "DuplicateEntryError" in resolve and "rollback" in resolve


def test_layer_is_canonicalized_on_every_write():
    # Fix #3: both write paths normalize layer; the grain helper owns it.
    api = _src(API_PATH)
    assert "normalize_layer" in _func(api, "_upsert_budget_line")
    assert "normalize_layer" in _func(api, "budget_cell_save")
    grain = _src(GRAIN_PY)
    assert "def normalize_layer" in grain and "VALID_LAYERS" in grain
    # role gate normalizes too, so a mis-cased layer can't bypass it
    assert "normalize_layer" in _func(_src(SHEET_PY), "_validate_layer_permission")


def test_explode_skips_zero_months():
    # Fix #5: zero months are not emitted (D365 parity, no phantom rows).
    sync = _func(_src(SHEET_PY), "_sync_to_clickhouse")
    assert "if not amount" in sync and "continue" in sync


def test_cycle_and_sheet_use_digest_autoname():
    # Fix #4: collision-safe names; JSON format autoname removed.
    assert "digest_name" in _func(_src(CYCLE_PY), "autoname")
    assert "digest_name" in _func(_src(SHEET_PY), "autoname")
    assert "def digest_name" in _src(GRAIN_PY)
    for f in ("budget_cycle/budget_cycle", "budget_sheet/budget_sheet"):
        meta = json.load(open(os.path.join(APP_DIR, "epm", "doctype", f + ".json")))
        assert meta.get("autoname", "") == "", f"{f} must not use a raw format autoname"


def test_d365_dimension_values_are_dynamic():
    # Fix #9: every in_budget dim is emitted, not just cost center / department.
    src = _src(D365_PY)
    assert "def build_entries(sheet, fiscal_year, fiscal_calendar=None, dim_names=None)" in src
    dv = _func(src, "_dimension_values")
    assert "for dim in dims" in dv and "_d365_attribute" in dv
    assert _func(src, "_d365_attribute") is not None


def test_d365_jobs_guard_on_cycle_state():
    # Fix #7: push only while cycle locked; withdraw skips if re-locked.
    src = _src(D365_PY)
    assert "docstatus" in _func(src, "push_budget_sheet")
    assert "docstatus" in _func(src, "withdraw_budget_sheet")


def test_lock_is_isolated_per_sheet():
    # Fix #8: one sheet's failure doesn't abort the whole lock.
    on_submit = _func(_src(CYCLE_PY), "on_submit")
    assert "try:" in on_submit and "log_error" in on_submit


def test_migration_provisions_dims_and_normalizes_and_purges_ch():
    # Fix #1/#3/#6: dim fields provisioned before pivot; layer normalized; CH purge.
    src = _src(PATCH_PY)
    execute = _func(src, "execute")
    assert "_sync_budget_custom_fields" in execute      # provision before writing dims
    assert "normalize_layer" in execute                 # canonical layer
    assert "find_budget_line" in execute                # shared grain matcher
    assert _func(src, "purge_legacy_clickhouse") is not None


def test_after_migrate_provisions_budget_line_fields():
    # Fix #1: live path / fresh installs get the dim columns too.
    src = _src(INSTALL_PY)
    assert "_sync_budget_line_custom_fields" in _func(src, "after_migrate")
    assert "_sync_budget_custom_fields" in _func(src, "_sync_budget_line_custom_fields")


def test_shared_line_matcher_replaces_duplicates():
    # Fix #10: the line-match loop lives in one place (budget_grain.find_budget_line).
    assert "def find_budget_line" in _src(GRAIN_PY)
    api = _src(API_PATH)
    assert "find_budget_line" in api
    # the old standalone _find_line / _find_or_append_line copies are gone
    assert "def _find_line(" not in api and "def _find_or_append_line(" not in api
