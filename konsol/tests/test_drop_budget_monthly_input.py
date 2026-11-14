"""epm_gold.budget_monthly_input leaves konsol (konsol#348 S2-C Z6, #282).

Budget Sheet wrote it, and S2-C Z1 retired Budget Sheet. konsolidat#356
already took it out of the warehouse contract and gold_spread_budget no
longer reads it. So konsol stops creating it (_REFERENCE_TABLE_DDL), stops
adding dimension columns to it and reading values from it, and a patch drops
it from an existing warehouse. A failed drop is logged, never raised: an
unread table must not fail a migrate.

A stub frappe and a stub konsol.clickhouse record the patch's calls.
"""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "drop_budget_monthly_input.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
MODULE = "konsol.patches.drop_budget_monthly_input"
TABLE = "epm_gold.budget_monthly_input"
DROP = f"DROP TABLE IF EXISTS {TABLE}"


def _literal(relative, name):
    with open(os.path.join(APP_DIR, relative)) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", None) == name):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {relative}")


def _run(raises=False):
    calls = []
    frappe = types.ModuleType("frappe")
    frappe.logger = lambda *a, **k: types.SimpleNamespace(
        info=lambda message, *ia, **ik: calls.append(("log.info", message)),
        warning=lambda message, *wa, **wk: calls.append(("log.warning", message)))
    clickhouse = types.ModuleType("konsol.clickhouse")

    def execute(sql, *a, **k):
        calls.append(("ch", sql))
        if raises:
            raise RuntimeError("ZZ ClickHouse unreachable")
        return ""

    clickhouse.execute = execute
    konsol = types.ModuleType("konsol")
    konsol.clickhouse = clickhouse
    stubs = {"frappe": frappe, "konsol": konsol, "konsol.clickhouse": clickhouse}
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        assert os.path.exists(PATCH_PY), "no drop_budget_monthly_input.py"
        spec = importlib.util.spec_from_file_location("_drop_bmi_under_test", PATCH_PY)
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        patch.execute()
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return calls


def test_absent_from_the_reference_ddl():
    assert TABLE not in _literal("clickhouse.py", "_REFERENCE_TABLE_DDL")


def test_no_dimension_columns_added_to_it():
    assert TABLE not in _literal("schema_apply.py", "_BUDGET_INPUT_TABLES")


def test_no_dimension_values_read_from_it():
    assert TABLE not in _literal("dimension_value_model.py", "VALUE_TABLES")


def test_registered_in_patches_txt():
    with open(PATCHES_TXT) as f:
        assert MODULE in [line.strip() for line in f]


def test_the_patch_issues_the_drop():
    calls = _run()
    assert [c[1] for c in calls if c[0] == "ch"] == [DROP]


def test_a_failed_drop_is_logged_never_raised():
    calls = _run(raises=True)
    warnings = [c[1] for c in calls if c[0] == "log.warning"]
    assert warnings and TABLE in warnings[0], warnings
