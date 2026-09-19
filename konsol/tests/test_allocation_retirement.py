"""Allocation is RETIRED (konsol#264) — these tests guard the retirement.

Cost allocation (Allocation Rule + its Allocation Tier child table,
Allocation Driver, Allocation Run + its dormant per-doc workflow) is removed
entirely, not superseded — the four ``epm_staging.allocation_*`` tables were
empty on live at the time of removal, so there is no successor to migrate
into. The doctype code and the ``konsol/allocation`` package are deleted;
``retire_allocation.py`` is the guarded migration patch that drops the
leftover ``DocType`` records and MariaDB tables on an existing site. This is
a new file (there was no test_allocation.py source — only a stale pycache
from before the allocation package existed as tested code) rather than an
addition to an existing suite, since the whole subject is the retirement
itself, mirroring test_budget.py's role for retire_budget_input.

Site-free source assertions only: the code stays gone, the retire patch is
registered in patches.txt, it deletes the child table before its parent, it
deletes the Module Def after (not before) the DocTypes that claim it, it
never throws (there is no migration target to protect), and reruns are
no-ops.
"""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
RETIRE_PY = os.path.join(APP_DIR, "patches", "retire_allocation.py")


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

def test_allocation_package_is_deleted():
    assert not os.path.exists(os.path.join(APP_DIR, "allocation")), \
        "retired allocation package resurrected"


def test_allocation_doctype_dirs_are_deleted():
    for d in ("allocation_rule", "allocation_tier", "allocation_driver", "allocation_run"):
        for parent in ("epm", "allocation"):
            path = os.path.join(APP_DIR, parent, "doctype", d)
            assert not os.path.exists(path), f"retired doctype dir resurrected: {d}"


# --- the retire patch is registered ------------------------------------------

def test_retire_patch_listed_exactly_once_in_patches_txt():
    txt = _src(PATCHES_TXT)
    assert txt.count("konsol.patches.retire_allocation") == 1


# --- the retire patch never throws -------------------------------------------

def test_retire_patch_does_not_throw():
    # DEVIATION from retire_budget_input: allocation has no migration target
    # (the feature is gone, not superseded), so a parity-gate throw would
    # wedge bench migrate permanently on any site holding allocation data.
    execute = _func(_src(RETIRE_PY), "execute")
    assert "frappe.throw" not in execute


def test_retire_patch_logs_row_counts_guarded_by_table_exists():
    execute = _func(_src(RETIRE_PY), "execute")
    assert "frappe.logger()" in execute
    assert "frappe.db.table_exists" in execute
    assert execute.index("table_exists") < execute.index("logger()")


# --- the retire patch deletes workflow, doctypes and tables ------------------

def test_retire_patch_deletes_workflow_and_all_four_doctypes():
    execute = _func(_src(RETIRE_PY), "execute")
    for target in (
        '"Workflow", "Allocation Run Workflow"',
        '"DocType", "Allocation Tier"',
        '"DocType", "Allocation Rule"',
        '"DocType", "Allocation Driver"',
        '"DocType", "Allocation Run"',
    ):
        assert target in execute, f"retire patch must delete {target}"
    assert "ignore_missing=True" in execute  # fresh installs (and reruns) no-op


def test_retire_patch_deletes_module_def_after_doctypes():
    # A Module Def cannot be removed while a DocType still claims it, so the
    # Module Def delete_doc must come after all four DocType deletions
    # (konsol#264 row K11: the Allocation line is also gone from modules.txt,
    # but that alone does not delete an already-migrated site's Module Def).
    execute = _func(_src(RETIRE_PY), "execute")
    assert '"Module Def", "Allocation"' in execute
    doctype_targets = (
        '"DocType", "Allocation Tier"',
        '"DocType", "Allocation Rule"',
        '"DocType", "Allocation Driver"',
        '"DocType", "Allocation Run"',
    )
    module_def_delete = execute.index('"Module Def", "Allocation"')
    for target in doctype_targets:
        assert execute.index(target) < module_def_delete, \
            f"Module Def delete must come after {target}"


def test_retire_patch_deletes_child_table_before_parent():
    # Allocation Tier is a child table of Allocation Rule.
    execute = _func(_src(RETIRE_PY), "execute")
    tier_delete = execute.index('"DocType", "Allocation Tier"')
    rule_delete = execute.index('"DocType", "Allocation Rule"')
    assert tier_delete < rule_delete

    tier_drop = execute.index("DROP TABLE IF EXISTS `tabAllocation Tier`")
    rule_drop = execute.index("DROP TABLE IF EXISTS `tabAllocation Rule`")
    assert tier_drop < rule_drop


def test_retire_patch_drops_all_four_tables():
    # Frappe's delete_doc keeps the tables; the patch must drop them itself.
    execute = _func(_src(RETIRE_PY), "execute")
    for table in ("Allocation Tier", "Allocation Rule", "Allocation Driver", "Allocation Run"):
        assert f"DROP TABLE IF EXISTS `tab{table}`" in execute, table


def test_retire_patch_is_idempotent_on_rerun():
    # ignore_missing=True plus DROP TABLE IF EXISTS must make a rerun a no-op.
    execute = _func(_src(RETIRE_PY), "execute")
    assert "ignore_missing=True" in execute
    assert "IF EXISTS" in execute
    assert "ignore_missing=False" not in execute


# --- row K5: the patch also deletes the fixture-created Dataset / Build Model
# documents, which fixtures alone never remove (force-reimport, never delete) ---

def test_retire_patch_deletes_retired_dataset_rows():
    src = _src(RETIRE_PY)
    for name in ("headcount", "area_sqm", "revenue_by_product", "allocated"):
        assert f'"{name}"' in src, f"retired Dataset row {name} must be named in the patch"
    execute = _func(src, "execute")
    assert 'frappe.db.delete("Dataset"' in execute
    assert "frappe.db.table_exists(\"Dataset\")" in execute


def test_retire_patch_deletes_retired_build_model_rows():
    src = _src(RETIRE_PY)
    for name in ("gold_allocation_results", "gold_allocation_audit_trail"):
        assert f'"{name}"' in src, f"retired Build Model row {name} must be named in the patch"
    execute = _func(src, "execute")
    assert 'frappe.db.delete("Build Model"' in execute
    assert "frappe.db.table_exists(\"Build Model\")" in execute


def test_retire_patch_deletes_dataset_child_rows_guarded_by_table_exists():
    # Dataset Measure / Dataset Dimension are child tables of Dataset; their
    # rows must go before (or alongside) the parent, each guarded so a fresh
    # install (which never created the table) no-ops cleanly.
    execute = _func(_src(RETIRE_PY), "execute")
    for child in ("Dataset Measure", "Dataset Dimension"):
        assert f'frappe.db.table_exists("{child}")' in execute
        assert f'frappe.db.delete(\n                "{child}"' in execute or \
            f'frappe.db.delete("{child}"' in execute


def test_retire_patch_dataset_and_build_model_deletes_use_db_delete_not_delete_doc():
    # Dataset / Build Model rows are plain documents (not DocTypes) created by
    # fixtures; the retirement uses frappe.db.delete (no lifecycle hooks),
    # mirroring rekey_historical_equity_rate_to_group_corp's DB-level shape —
    # not frappe.delete_doc, which is reserved above for DocType/Workflow/
    # Module Def records.
    execute = _func(_src(RETIRE_PY), "execute")
    assert 'delete_doc(\n        "Dataset"' not in execute
    assert 'delete_doc("Dataset"' not in execute
    assert 'delete_doc(\n        "Build Model"' not in execute
    assert 'delete_doc("Build Model"' not in execute
