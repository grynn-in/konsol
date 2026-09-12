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


# --- Budget Annual Input (konsolidat#146) -----------------------------------

def _budget_annual_dir():
    import os
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "epm", "doctype", "budget_annual_input")


def test_annual_budget_input_is_konsols_not_a_seed():
    """konsolidat#146: seeds/budget_annual_input.csv was the last seed the dbt
    project owned. konsol already had the bottom-up path (Budget Cycle → Sheet →
    Line → epm_gold.budget_monthly_input); this is the top-down half that
    gold_spread_budget spreads into months by profile."""
    import json
    import os

    with open(os.path.join(_budget_annual_dir(), "budget_annual_input.py")) as f:
        src = f.read()
    assert 'CH_TABLE = "epm_gold.budget_annual_input"' in src
    for column in ("scenario_id", "data_area_id", "fiscal_year", "main_account",
                   "annual_amount", "spread_profile_id"):
        assert f'"{column}"' in src, column

    with open(os.path.join(_budget_annual_dir(), "budget_annual_input.json")) as f:
        meta = json.load(f)
    fields = {f["fieldname"]: f for f in meta["fields"]}
    # the F1 invariant: a field named data_area_id is a Link to Entity
    assert fields["data_area_id"]["fieldtype"] == "Link"
    assert fields["data_area_id"]["options"] == "Entity"


def test_monthly_budget_table_is_created_by_something():
    """epm_gold.budget_monthly_input has always been a konsol write-through with
    NOTHING that creates it — no seed, no DDL — so gold_spread_budget failed
    every build with "Unknown table expression identifier". It is in
    _REFERENCE_TABLE_DDL now, which cleared one of the three documented baseline
    failures."""
    import os

    with open(os.path.join(
            os.path.dirname(_budget_annual_dir()), "..", "..", "clickhouse.py")) as f:
        src = f.read()
    assert '"epm_gold.budget_monthly_input": (' in src
    assert '"epm_gold.budget_annual_input": (' in src


def test_annual_budget_fixture_links_resolve():
    """Fixture import sets ignore_links, so a fixture can reference a record
    that does not exist and still load — and then nobody can create the same
    row through the UI. The annual budget fixture is BUDGET_2025, which was not
    in the Scenario fixture until this was caught.
    """
    import json
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "fixtures", "budget_annual_input.json")) as f:
        rows = json.load(f)
    with open(os.path.join(root, "fixtures", "scenario.json")) as f:
        scenarios = {r["scenario_id"] for r in json.load(f)}
    with open(os.path.join(root, "fixtures", "consolidation_group.json")) as f:
        entities = {r["data_area_id"] for r in json.load(f) if r.get("data_area_id")}

    for row in rows:
        assert row["scenario_id"] in scenarios, (
            f"{row['scenario_id']} is a Link to Scenario with no fixture behind it")
        assert row["data_area_id"] in entities, (
            f"{row['data_area_id']} is a Link to Entity with no node behind it")
