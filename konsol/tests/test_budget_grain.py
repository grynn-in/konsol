"""Tests for the dimensional budget grain (spec #51).

Follows the repo convention of site-free tests: pure-function checks on
``budget_grain`` (registry lookup monkeypatched) plus source-level assertions
that the grain is wired consistently into the upsert key. (The retired
``Budget Input`` doctype's autoname/CH-sync wiring checks were removed with
the doctype — PRD-08; ``budget_name`` stays for the historical rekey patch.)
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(APP_DIR, "api.py")
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


def test_slug_drops_separator_chars():
    """`-` and `.` must not survive inside a component (they'd alias the
    field separator)."""
    assert bg._slug("Y-Z") == "Y_Z"
    assert bg._slug("6010.00") == "6010_00"


def test_budget_name_distinguishes_cost_centers(monkeypatch):
    """The whole point of the grain fix: same account, different cost center
    => different names (no clobber)."""
    monkeypatch.setattr(bg, "budget_dimension_names", lambda: ["dim_cost_center"])
    base = {"scenario_id": "BUDGET_2025", "data_area_id": "USMF",
            "fiscal_year": 2025, "main_account": "Travel"}
    sales = bg.budget_name({**base, "dim_cost_center": "Sales"})
    eng = bg.budget_name({**base, "dim_cost_center": "Engineering"})
    assert sales != eng
    assert sales.startswith("BUD-BUDGET_2025-USMF-2025-Travel-Sales-")


def test_budget_name_injective_under_separator_ambiguity(monkeypatch):
    """Distinct keys whose naive concat would alias must get distinct names."""
    monkeypatch.setattr(bg, "budget_dimension_names", lambda: ["dim_a", "dim_b"])
    base = {"scenario_id": "S", "data_area_id": "A", "fiscal_year": 2025, "main_account": "X"}
    k1 = bg.budget_name({**base, "dim_a": "Y", "dim_b": "Z-W"})
    k2 = bg.budget_name({**base, "dim_a": "Y-Z", "dim_b": "W"})
    assert k1 != k2


def test_budget_name_blank_dim_is_well_formed(monkeypatch):
    """Revenue lines with no cost center still produce a valid, distinct name."""
    monkeypatch.setattr(bg, "budget_dimension_names", lambda: ["dim_cost_center"])
    name = bg.budget_name({"scenario_id": "S", "data_area_id": "A",
                           "fiscal_year": 2025, "main_account": "Revenue",
                           "dim_cost_center": ""})
    assert name.startswith("BUD-S-A-2025-Revenue-_-")
    assert len(name) <= 140


def test_budget_name_is_deterministic_regardless_of_dim_order(monkeypatch):
    monkeypatch.setattr(bg, "budget_dimension_names", lambda: ["dim_cost_center", "dim_department"])
    vals = {"scenario_id": "S", "data_area_id": "A", "fiscal_year": 2025,
            "main_account": "X", "dim_department": "D", "dim_cost_center": "C"}
    name1 = bg.budget_name(vals)
    name2 = bg.budget_name(dict(vals))
    assert name1 == name2
    assert name1.startswith("BUD-S-A-2025-X-C-D-")


# --- source-level wiring (no site) -----------------------------------------

def _src(path):
    with open(path) as fh:
        return fh.read()


def test_budget_filters_includes_grain_dimensions():
    src = _src(API_PATH)
    assert "budget_dimension_names" in src, "_budget_filters must extend the key with in_budget dims"


def test_grain_migration_patch_registered():
    assert "rekey_budget_input_dimensional_grain" in _src(PATCHES_TXT)
