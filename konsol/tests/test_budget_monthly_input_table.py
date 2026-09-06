"""Structural tests for the budget_monthly_input ensure-table patch.

Parsed from source without a live Frappe site, mirroring test_fact_registry.py.

The point of these is drift: the patch's DDL and BudgetSheet's INSERT column
list are written in two different files and nothing else ties them together.
If someone adds a column to one, these fail.
"""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PATH = os.path.join(
    APP_DIR, "patches", "ensure_budget_monthly_input_table.py"
)
BUDGET_SHEET_PATH = os.path.join(
    APP_DIR, "epm", "doctype", "budget_sheet", "budget_sheet.py"
)


def _read(path):
    with open(path) as handle:
        return handle.read()


def _assigned_list(source, target_name, func_name=None):
    """Return the string constants of a list assigned to ``target_name``."""
    tree = ast.parse(source)
    scope = tree
    if func_name:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                scope = node
                break
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == target_name:
                    return [
                        elt.value
                        for elt in node.value.elts
                        if isinstance(elt, ast.Constant)
                    ]
    raise AssertionError(f"{target_name} not found")


def test_patch_is_registered():
    patches = _read(os.path.join(APP_DIR, "patches.txt"))
    assert "konsol.patches.ensure_budget_monthly_input_table" in patches


def test_ddl_is_idempotent():
    source = _read(PATCH_PATH)
    assert "CREATE TABLE IF NOT EXISTS" in source


def test_ddl_covers_every_column_budget_sheet_writes():
    """The DDL must define each non-dimension column the sheet sync inserts.

    Dimension columns are excluded here: both sides derive them from
    budget_dimension_names() at runtime, so they cannot drift.
    """
    written = _assigned_list(
        _read(BUDGET_SHEET_PATH), "columns", func_name="_sync_to_clickhouse"
    )
    ddl_cols = {
        col.split()[0]
        for col in _assigned_list(_read(PATCH_PATH), "_BASE_COLUMNS")
    }

    missing = [name for name in written if name not in ddl_cols]
    assert not missing, f"DDL is missing columns the sheet writes: {missing}"


def test_ddl_targets_the_table_budget_sheet_writes_to():
    sheet_table = _read(BUDGET_SHEET_PATH).split('CLICKHOUSE_TABLE = "')[1]
    sheet_table = sheet_table.split('"')[0]
    patch_table = _read(PATCH_PATH).split('TABLE = "')[1].split('"')[0]
    assert sheet_table == patch_table


def test_install_does_not_silently_swallow_bootstrap_failure():
    """Regression: a bare warning here hid a broken gold layer for weeks."""
    source = _read(os.path.join(APP_DIR, "install.py"))
    start = source.index("def _bootstrap_budget_fixtures")
    block = source[start:start + 900]
    assert "frappe.log_error" in block, (
        "bootstrap failure must reach the Error Log, not just a debug warning"
    )
