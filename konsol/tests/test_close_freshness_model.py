"""Freshness model, pure: konsol/close/freshness_model.py (konsol#305 A05).

Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import os
from datetime import datetime

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "close", "freshness_model.py")
_spec = importlib.util.spec_from_file_location("close_freshness_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

# Mirrors build_lock.FLAGGED_STATES and tasks.DOCTYPE_BUILD_MAP; passed in, never imported.
FLAGGED = ("Draft", "Pending Review", "Approved", "Running")
SCOPE_OF = {
    "Trial Balance Submission": "consolidation",
    "Group Exchange Rate": "consolidation",
    "Entity": "consolidation",
    "Consolidation Adjustment": "staging",
    "Ownership Period": "staging",
}


def _t(day, hour=12):
    return datetime(2026, 9, day, hour, 0, 0)


def _build(name, scope, state, at=None, error=None):
    return {
        "name": name,
        "build_scope": scope,
        "workflow_state": state,
        "completed_at": at,
        "error_message": error,
    }


def _change(doctype, at):
    return {"doctype": doctype, "modified": at}


def _run(builds, changes=()):
    return M.freshness(builds, list(changes), SCOPE_OF, FLAGGED)


def test_completed_build_after_every_change_is_fresh():
    out = _run(
        [_build("BA-1", "consolidation", "Completed", _t(10))],
        [_change("Trial Balance Submission", _t(9)), _change("Entity", _t(8))],
    )
    assert out["state"] == "fresh"
    assert out["changed_since"] == []
    assert out["as_of"] == _t(10)
    assert out["pending"] == 0
    assert out["last_failed"] is None


def test_tb_modified_after_last_consolidation_build_is_stale():
    out = _run(
        [_build("BA-1", "consolidation", "Completed", _t(10))],
        [_change("Trial Balance Submission", _t(11)), _change("Entity", _t(9))],
    )
    assert out["state"] == "stale"
    assert out["changed_since"] == ["Trial Balance Submission"]
    assert out["as_of"] == _t(10)


def test_a_change_at_the_build_instant_is_covered():
    out = _run(
        [_build("BA-1", "consolidation", "Completed", _t(10))],
        [_change("Trial Balance Submission", _t(10))],
    )
    assert out["state"] == "fresh"


def test_adjustment_covered_by_later_staging_build_is_not_stale():
    out = _run(
        [
            _build("BA-1", "consolidation", "Completed", _t(10)),
            _build("BA-2", "staging", "Completed", _t(12)),
        ],
        [_change("Consolidation Adjustment", _t(11))],
    )
    assert out["state"] == "fresh"
    assert out["changed_since"] == []
    # a staging build does not move as_of
    assert out["as_of"] == _t(10)


def test_staging_build_does_not_cover_a_consolidation_doctype():
    out = _run(
        [
            _build("BA-1", "consolidation", "Completed", _t(10)),
            _build("BA-2", "staging", "Completed", _t(12)),
        ],
        [_change("Group Exchange Rate", _t(11))],
    )
    assert out["state"] == "stale"
    assert out["changed_since"] == ["Group Exchange Rate"]


def test_full_build_covers_every_scope_and_sets_as_of():
    out = _run(
        [
            _build("BA-1", "consolidation", "Completed", _t(10)),
            _build("BA-2", "full", "Completed", _t(12)),
        ],
        [_change("Consolidation Adjustment", _t(11)), _change("Trial Balance Submission", _t(11))],
    )
    assert out["state"] == "fresh"
    assert out["as_of"] == _t(12)


def test_changed_since_is_unique_and_sorted():
    out = _run(
        [_build("BA-1", "consolidation", "Completed", _t(10))],
        [
            _change("Trial Balance Submission", _t(11)),
            _change("Entity", _t(12)),
            _change("Trial Balance Submission", _t(13)),
        ],
    )
    assert out["changed_since"] == ["Entity", "Trial Balance Submission"]


def test_two_pending_review_builds_are_pending():
    out = _run(
        [
            _build("BA-1", "consolidation", "Completed", _t(10)),
            _build("BA-2", "staging", "Pending Review"),
            _build("BA-3", "consolidation", "Pending Review"),
        ],
        [_change("Trial Balance Submission", _t(11))],
    )
    assert out["pending"] == 2
    assert out["state"] == "pending"
    # the stale doctypes are still reported under a pending state
    assert out["changed_since"] == ["Trial Balance Submission"]


def test_cancelled_builds_are_not_pending_and_not_terminal():
    out = _run(
        [
            _build("BA-1", "consolidation", "Completed", _t(10)),
            _build("BA-2", "consolidation", "Cancelled"),
        ],
    )
    assert out["pending"] == 0
    assert out["state"] == "fresh"


# --- failure paths ---------------------------------------------------------


def test_latest_terminal_consolidation_build_failed():
    out = _run(
        [
            _build("BA-1", "consolidation", "Completed", _t(10)),
            _build("BA-2", "full", "Failed", _t(11), "dbt exit 2"),
        ],
    )
    assert out["state"] == "failed"
    assert out["last_failed"] == {"name": "BA-2", "at": _t(11), "reason": "dbt exit 2"}
    assert out["as_of"] == _t(10)


def test_failed_with_blank_error_message_names_the_build():
    for blank in (None, "", "   "):
        out = _run(
            [
                _build("BA-1", "consolidation", "Completed", _t(10)),
                _build("BA-2", "consolidation", "Failed", _t(11), blank),
            ],
        )
        assert out["state"] == "failed"
        assert out["last_failed"]["reason"] == "No error recorded on BA-2"


def test_a_later_success_clears_the_failure():
    out = _run(
        [
            _build("BA-1", "consolidation", "Failed", _t(10), "dbt exit 2"),
            _build("BA-2", "consolidation", "Completed", _t(11)),
        ],
    )
    assert out["state"] == "fresh"
    assert out["last_failed"] is None


def test_a_failed_staging_build_does_not_fail_the_numbers():
    out = _run(
        [
            _build("BA-1", "consolidation", "Completed", _t(10)),
            _build("BA-2", "staging", "Failed", _t(11), "dbt exit 1"),
        ],
    )
    assert out["state"] == "fresh"
    assert out["last_failed"] is None


def test_no_builds_ever_is_never_built():
    out = _run([], [_change("Trial Balance Submission", _t(11))])
    assert out["state"] == "never_built"
    assert out["as_of"] is None
    assert out["pending"] == 0
    assert out["last_failed"] is None


def test_only_staging_builds_is_never_built():
    out = _run([_build("BA-1", "staging", "Completed", _t(10))])
    assert out["state"] == "never_built"
    assert out["as_of"] is None


def test_doctype_missing_from_scope_of_raises():
    with pytest.raises(ValueError, match="Budget Line"):
        _run(
            [_build("BA-1", "consolidation", "Completed", _t(10))],
            [_change("Budget Line", _t(9))],
        )


def test_terminal_build_without_completed_at_raises():
    with pytest.raises(ValueError, match="BA-9"):
        _run([_build("BA-9", "consolidation", "Completed", None)])


def test_module_imports_no_frappe():
    with open(_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] in ("frappe", "konsol") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("frappe", "konsol")
