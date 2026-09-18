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
