"""Budget Input is RETIRED (PRD-08) — these tests guard the retirement.

The live budget chain is Budget Cycle → Budget Sheet → Budget Line (covered by
``test_budget_cycle_reshape.py`` / ``test_budget_api.py``). ``Budget Input``
(+ ``Budget Input Child`` and its per-doc workflow) was the deprecated
pre-reshape entry point; its code is deleted and a guarded migration patch
drops the doctypes. Site-free source assertions here pin the safety gates:
the code stays gone, the retire patch is registered AFTER the historical
budget patches, it refuses to delete unmigrated data, and the historical
patches are guarded so fresh installs replay cleanly.
"""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
RETIRE_PY = os.path.join(APP_DIR, "patches", "retire_budget_input.py")
RESHAPE_PY = os.path.join(APP_DIR, "patches", "reshape_budget_input_to_cycle.py")
REKEY_PY = os.path.join(APP_DIR, "patches", "rekey_budget_input_dimensional_grain.py")


def _src(path):
    with open(path) as fh:
        return fh.read()


def _func(src, name):
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.get_source_segment(src, n)
    return None


# --- the code is gone --------------------------------------------------------

def test_budget_input_doctype_dirs_are_deleted():
    for d in ("budget_input", "budget_input_child"):
        path = os.path.join(APP_DIR, "epm", "doctype", d)
        assert not os.path.exists(path), f"retired doctype dir resurrected: {d}"


def test_live_budget_chain_doctypes_remain():
    for d in ("budget_cycle", "budget_sheet", "budget_line"):
        path = os.path.join(APP_DIR, "epm", "doctype", d, f"{d}.json")
        assert os.path.isfile(path), f"live budget chain doctype missing: {d}"


# --- the retire patch --------------------------------------------------------

def test_retire_patch_is_registered_after_historical_budget_patches():
    txt = _src(PATCHES_TXT)
    retire = txt.index("konsol.patches.retire_budget_input")
    assert txt.index("konsol.patches.rekey_budget_input_dimensional_grain") < retire
    assert txt.index("konsol.patches.reshape_budget_input_to_cycle") < retire


def test_retire_patch_verifies_migration_parity_before_deleting():
    # If old Budget Input rows exist but no Budget Sheet was ever produced,
    # the retire patch must throw instead of dropping the only copy.
    execute = _func(_src(RETIRE_PY), "execute")
    assert "frappe.throw" in execute
    assert '"Budget Sheet"' in execute
    assert execute.index("frappe.throw") < execute.index("delete_doc")


def test_retire_patch_deletes_workflow_child_and_parent():
    execute = _func(_src(RETIRE_PY), "execute")
    for target in (
        '"Workflow", "Budget Input Workflow"',
        '"DocType", "Budget Input Child"',
        '"DocType", "Budget Input"',
    ):
        assert target in execute, f"retire patch must delete {target}"
    assert "ignore_missing=True" in execute  # fresh installs no-op
    # Frappe's delete_doc keeps the tables; the patch must drop them itself.
    assert "DROP TABLE IF EXISTS `tabBudget Input Child`" in execute
    assert "DROP TABLE IF EXISTS `tabBudget Input`" in execute


# --- fresh-install guards on the historical patches ---------------------------

def test_historical_budget_patches_are_guarded_for_fresh_installs():
    for path in (RESHAPE_PY, REKEY_PY):
        execute = _func(_src(path), "execute")
        guard = 'if not frappe.db.table_exists("Budget Input")'
        assert guard in execute, f"missing fresh-install guard: {path}"
        assert execute.index("table_exists") < execute.index("frappe.get_all")
