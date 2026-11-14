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

def test_cycle_is_an_approval_and_pushes_no_sheet():
    # S2-C W2b (BUD-9-3, Q9-a): the Cycle no longer locks sheets or pushes
    # them to ClickHouse and D365; submit is the approval, and the submit and
    # cancel queue the Budget Entry and Annual Input syncs.
    src = _src(CYCLE_PY)
    for gone in ("_push_sheets", 'self.status = "'):
        assert gone not in src, gone
    for hook in ("on_submit", "on_cancel"):
        assert "_resync_entries()" in _func(src, hook), hook


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
    # No API write creates a Cycle or judges its lock itself: every budget
    # write goes through budget_intake, which refuses a missing or submitted
    # Cycle (S2-X1, S2-X2).
    for gone in ("_resolve_budget_cycle", "_assert_cycle_open"):
        assert "def " + gone not in src, gone
    assert "budget_intake" in _func(src, "_judge_budget_cell")


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

def test_no_api_write_creates_a_cycle():
    # The race this pinned (a Cycle created on first write) is gone with the
    # auto-create (S2-X1/X2); the Entry-create race is pinned in
    # test_budget_locking.py.
    assert 'new_doc("Budget Cycle")' not in _src(API_PATH)


def test_layer_is_canonicalized_on_every_write():
    # The API paths take the layer as given; budget_intake judges it (S2-X1, X2).
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


def test_budget_demo_is_not_shipped():
    """BUDGET_2024's cycle and sheets were the Alpine demo's. Budgets are
    entered per site; a fixture would force-reimport the demo's over them."""
    for name in ("budget_cycle.json", "budget_sheet.json"):
        assert not os.path.exists(os.path.join(APP_DIR, "fixtures", name)), name


def test_dashboard_includes_budget_cycle_shortcut():
    dash = _src(os.path.join(APP_DIR, "dashboard.py"))
    assert '("Budget Cycle"' in dash
    assert "_NUMBER_CARDS" not in dash
    assert "_CHARTS" not in dash
    assert "_workspace_needs_refresh" in dash


def test_dashboard_workflow_card_order():
    # The workspace was redesigned (#86) into one card per process, in process
    # order. The old Budget / EPM Registry / Data Pipeline cards survive only in
    # the set _workspace_needs_refresh() uses to detect and rebuild them.
    import ast
    tree = ast.parse(_src(os.path.join(APP_DIR, "dashboard.py")))
    cards = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                 and getattr(n.targets[0], "id", None) == "_CARDS")
    titles = [c.elts[0].value for c in cards.elts]
    budget = next(c for c in cards.elts if c.elts[0].value == "Budgeting & Planning")
    assert "Budget Cycle" in [e.value for e in budget.elts[1].elts]
    for old in ("Budget", "EPM Registry", "EPM Models", "Data Pipeline"):
        assert old not in titles, old
    order = ["Pipeline & Ingestion", "Model & Metadata", "Budgeting & Planning",
             "Consolidation"]
    assert [t for t in titles if t in order] == order


def test_shared_line_matcher_replaces_duplicates():
    # Fix #10: the line-match loop lives in one place (budget_grain.find_budget_line).
    assert "def find_budget_line" in _src(GRAIN_PY)
    api = _src(API_PATH)
    # the old standalone _find_line / _find_or_append_line copies are gone
    assert "def _find_line(" not in api and "def _find_or_append_line(" not in api


# --- review fix 6: the Cycle's scenario and year are set once ----------------

def test_scenario_and_fiscal_year_are_set_once():
    """A Cycle's entries carry its scenario and year (fetched on every save),
    so changing either under them would re-label every entry."""
    with open(os.path.join(APP_DIR, "epm", "doctype", "budget_cycle", "budget_cycle.json")) as fh:
        fields = {f["fieldname"]: f for f in json.load(fh)["fields"]}
    for name in ("scenario_id", "fiscal_year"):
        assert fields[name].get("set_only_once") == 1, name
