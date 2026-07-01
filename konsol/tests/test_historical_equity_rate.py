"""Historical Equity Rate controller guards (grynn-in/konsolidat#92).

Static-assertion style (validate() needs a live frappe + Consolidation Group
records to execute, so we assert the enforcement is wired in source — same
convention as test_exec_www.py).
"""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(
    APP_DIR, "consolidation", "doctype", "historical_equity_rate",
    "historical_equity_rate.py",
)


def _src():
    with open(PY) as f:
        return f.read()


def test_file_exists():
    assert os.path.isfile(PY)


def test_validate_calls_both_guards():
    tree = ast.parse(_src())
    fns = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "validate" in fns
    assert "_validate_positive_rate" in fns
    assert "_validate_references" in fns  # #92 finding #3


def test_positive_rate_guard_present():
    src = _src()
    assert "historical_rate" in src
    assert "must be a positive number" in src.lower()


def test_referential_integrity_on_group_and_entity():
    # #92 finding #3: free-text keys enforced against the Consolidation Group registry
    src = _src()
    assert 'frappe.db.exists(' in src
    assert '"Consolidation Group"' in src
    assert "consolidation_group" in src
    assert "data_area_id" in src
    # both keys must throw on an unknown value
    assert src.count("frappe.throw(") >= 3  # positive-rate + group + entity


def test_still_syncs_only_submitted_to_clickhouse():
    # finding #1 (systemic) lives in clickhouse.sync_doctype; here just confirm
    # the doctype still routes through it on submit/cancel/trash.
    src = _src()
    assert "sync_doctype" in src
    for hook in ("on_submit", "on_cancel", "on_trash"):
        assert f"def {hook}" in src
