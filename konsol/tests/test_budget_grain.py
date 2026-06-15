"""Tests for the dimensional budget grain (spec #51).

Follows the repo convention of site-free tests: pure-function checks on
``budget_grain`` (registry lookup monkeypatched) plus source-level assertions
that the grain is wired consistently across the upsert key, the document
autoname and the ClickHouse sync key.
"""
import ast
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(APP_DIR, "api.py")
BUDGET_INPUT_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_input", "budget_input.py")
BUDGET_INPUT_JSON = os.path.join(APP_DIR, "epm", "doctype", "budget_input", "budget_input.json")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")

# Load THIS worktree's budget_grain by path (not the installed app, which may be
# on another branch), with a stub `frappe` so the import succeeds without a
# site — the pure functions tested here never call into frappe.
if "frappe" not in sys.modules:
    try:  # pragma: no cover - prefers the real module when present
        import frappe  # noqa: F401
    except ModuleNotFoundError:
        sys.modules["frappe"] = types.ModuleType("frappe")

_bg_spec = importlib.util.spec_from_file_location(
    "konsol_budget_grain_under_test", os.path.join(APP_DIR, "epm", "budget_grain.py")
)
bg = importlib.util.module_from_spec(_bg_spec)
_bg_spec.loader.exec_module(bg)


# --- pure-function behaviour (no site) -------------------------------------

def test_slug_blanks_collapse_to_underscore():
    assert bg._slug("") == "_"
    assert bg._slug(None) == "_"
    assert bg._slug("   ") == "_"


def test_slug_sanitises_unsafe_chars():
    assert bg._slug("CC/100") == "CC_100"
    assert bg._slug("a b") == "a_b"
    assert bg._slug("6100") == "6100"


def test_budget_name_distinguishes_cost_centers(monkeypatch):
    """The whole point of the grain fix: same account, different cost center
    => different names (no clobber)."""
    monkeypatch.setattr(bg, "budget_dimension_names", lambda: ["dim_cost_center"])
    base = {"scenario_id": "BUDGET_2025", "data_area_id": "USMF",
            "fiscal_year": 2025, "main_account": "Travel"}
    sales = bg.budget_name({**base, "dim_cost_center": "Sales"})
    eng = bg.budget_name({**base, "dim_cost_center": "Engineering"})
    assert sales != eng
    assert sales == "BUD-BUDGET_2025-USMF-2025-Travel-Sales"


def test_budget_name_blank_dim_is_well_formed(monkeypatch):
    """Revenue lines with no cost center still produce a valid, distinct name."""
    monkeypatch.setattr(bg, "budget_dimension_names", lambda: ["dim_cost_center"])
    name = bg.budget_name({"scenario_id": "S", "data_area_id": "A",
                           "fiscal_year": 2025, "main_account": "Revenue",
                           "dim_cost_center": ""})
    assert name == "BUD-S-A-2025-Revenue-_"


def test_budget_name_is_deterministic_regardless_of_dim_order(monkeypatch):
    monkeypatch.setattr(bg, "budget_dimension_names", lambda: ["dim_cost_center", "dim_department"])
    vals = {"scenario_id": "S", "data_area_id": "A", "fiscal_year": 2025,
            "main_account": "X", "dim_department": "D", "dim_cost_center": "C"}
    assert bg.budget_name(vals) == "BUD-S-A-2025-X-C-D"


# --- source-level wiring (no site) -----------------------------------------

def _src(path):
    with open(path) as fh:
        return fh.read()


def test_budget_filters_includes_grain_dimensions():
    src = _src(API_PATH)
    assert "budget_dimension_names" in src, "_budget_filters must extend the key with in_budget dims"


def test_budget_input_has_dynamic_autoname():
    tree = ast.parse(_src(BUDGET_INPUT_PY))
    methods = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "autoname" in methods, "BudgetInput must define a controller autoname()"
    assert "budget_name" in _src(BUDGET_INPUT_PY)


def test_static_format_autoname_removed():
    """Static format: name would exclude dims and reintroduce the clobber."""
    meta = json.load(open(BUDGET_INPUT_JSON))
    assert not (meta.get("autoname") or "").startswith("format:")


def test_clickhouse_sync_key_includes_dimensions():
    """Incremental sync delete-by-key must include dims or it wipes siblings."""
    src = _src(BUDGET_INPUT_PY)
    assert "*dim_names" in src, "key_columns must include the grain dimensions"


def test_grain_migration_patch_registered():
    assert "rekey_budget_input_dimensional_grain" in _src(PATCHES_TXT)
