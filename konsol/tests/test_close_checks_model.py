"""Checks model, pure: konsol/close/checks_model.py (konsol#305 A13; stories 7.2, 7.4).

Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import os
from datetime import datetime

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "close", "checks_model.py")
_spec = importlib.util.spec_from_file_location("close_checks_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _step(assertion, dimension, status, rows_failed=0, severity="error", message=""):
    return {
        "assertion": assertion,
        "dimension": dimension,
        "status": status,
        "rows_failed": rows_failed,
        "severity": severity,
        "message": message,
    }


def _t(day, hour=12):
    return datetime(2026, 9, day, hour, 0, 0)


# --- by_cause ------------------------------------------------------------------

def test_pass_steps_are_excluded_and_domains_follow_the_fixed_order():
    steps = [
        _step("assert_null_entity", "Data Quality", "Fail", 3),
        _step("assert_ic_balances", "Consolidation", "Fail", 2),
        _step("assert_cta_ties", "FX", "Pass"),
        _step("assert_other_thing", "Other", "Error"),
        _step("assert_ownership_sums", "Ownership", "Fail", 1),
        _step("assert_fx_rates_present", "FX", "Fail", 4),
        _step("assert_unique_keys", "Data Quality", "Pass"),
    ]
    out = M.by_cause(steps, {})
    assert [d["domain"] for d in out["domains"]] == [
        "FX", "Ownership", "Consolidation", "Data Quality", "Other",
    ]
    counts = {d["domain"]: d["count"] for d in out["domains"]}
    assert counts == {"FX": 1, "Ownership": 1, "Consolidation": 1, "Data Quality": 1, "Other": 1}
    names = [c["assertion"] for d in out["domains"] for c in d["failures"] + d["warnings"]]
    assert "assert_cta_ties" not in names
    assert "assert_unique_keys" not in names


def test_a_domain_with_only_passes_is_not_listed():
    steps = [_step("assert_cta_ties", "FX", "Pass"), _step("assert_ic_x", "Consolidation", "Fail", 1)]
    out = M.by_cause(steps, {})
    assert [d["domain"] for d in out["domains"]] == ["Consolidation"]


def test_all_pass_gives_no_domains_and_zero_totals():
    out = M.by_cause([_step("assert_cta_ties", "FX", "Pass")], {})
    assert out["domains"] == []
    assert out["failures"] == 0
    assert out["warnings"] == 0


def test_a_warn_is_kept_separate_from_fail_and_error():
    steps = [
        _step("assert_fx_rates_present", "FX", "Fail", 4),
        _step("assert_currency_known", "FX", "Error"),
        _step("assert_rate_drift", "FX", "Warn", 2, severity="warn"),
    ]
    out = M.by_cause(steps, {})
    (fx,) = out["domains"]
    assert [c["assertion"] for c in fx["failures"]] == ["assert_currency_known", "assert_fx_rates_present"]
    assert [c["assertion"] for c in fx["warnings"]] == ["assert_rate_drift"]
    assert fx["count"] == 2
    assert fx["warn_count"] == 1
    assert out["failures"] == 2
    assert out["warnings"] == 1


def test_a_domain_with_only_warnings_is_listed_with_zero_failures():
    out = M.by_cause([_step("assert_rate_drift", "FX", "Warn", 1, severity="warn")], {})
    (fx,) = out["domains"]
    assert fx["count"] == 0
    assert fx["warn_count"] == 1
    assert fx["failures"] == []


def test_one_cause_per_assertion_with_a_humanised_title_and_the_step_fields():
    step = _step("assert_ic_balances_net_to_zero", "Consolidation", "Fail", 7, message="7 rows")
    out = M.by_cause([step], {})
    (cause,) = out["domains"][0]["failures"]
    assert cause["assertion"] == "assert_ic_balances_net_to_zero"
    assert cause["title"] == "Ic balances net to zero"
    assert cause["status"] == "Fail"
    assert cause["rows_failed"] == 7
    assert cause["severity"] == "error"
    assert cause["message"] == "7 rows"


def test_a_name_without_the_assert_prefix_is_still_humanised():
    out = M.by_cause([_step("fctb_ties_out", "Consolidation", "Fail", 1)], {})
    assert out["domains"][0]["failures"][0]["title"] == "Fctb ties out"


def test_a_declared_description_is_carried():
    out = M.by_cause(
        [_step("assert_ic_balances", "Consolidation", "Fail", 1)],
        {"assert_ic_balances": "Intercompany balances must net to zero."},
    )
    cause = out["domains"][0]["failures"][0]
    assert cause["description"] == "Intercompany balances must net to zero."
    assert cause["description_missing"] is False


def test_a_missing_description_is_none_and_flagged_never_invented():
    """Problems 4 / decision P4: no check has a description (0/131); the UI
    prints "No description declared for <name>". The model invents nothing."""
    out = M.by_cause([_step("assert_ic_balances", "Consolidation", "Fail", 1)], {"assert_other": "x"})
    cause = out["domains"][0]["failures"][0]
    assert cause["description"] is None
    assert cause["description_missing"] is True


def test_a_blank_description_counts_as_missing():
    for blank in ("", "   ", None):
        out = M.by_cause([_step("assert_ic_balances", "Consolidation", "Fail", 1)], {"assert_ic_balances": blank})
        cause = out["domains"][0]["failures"][0]
        assert cause["description"] is None, blank
        assert cause["description_missing"] is True, blank


def test_the_same_assertion_twice_is_refused():
    steps = [_step("assert_ic_x", "Consolidation", "Fail", 1), _step("assert_ic_x", "Consolidation", "Fail", 2)]
    with pytest.raises(ValueError, match="assert_ic_x"):
        M.by_cause(steps, {})


def test_an_undeclared_domain_is_refused_not_dropped():
    with pytest.raises(ValueError, match="Tax"):
        M.by_cause([_step("assert_vat", "Tax", "Fail", 1)], {})


def test_an_unknown_status_is_refused_not_dropped():
    with pytest.raises(ValueError, match="Skipped"):
        M.by_cause([_step("assert_ic_x", "Consolidation", "Skipped")], {})


def test_domains_constant_is_the_classify_order():
    assert M.DOMAINS == ("FX", "Ownership", "Consolidation", "Data Quality", "Other")


# --- staleness -----------------------------------------------------------------

def test_no_run_is_not_run():
    assert M.staleness(None, _t(20))["state"] == "not_run"


def test_no_run_is_not_run_even_when_never_built():
    assert M.staleness(None, None)["state"] == "not_run"


def test_a_queued_or_running_run_is_running():
    for status in ("Queued", "Running"):
        run = {"name": "AR-1", "status": status, "completed_at": None}
        assert M.staleness(run, _t(20))["state"] == "running", status


def test_a_run_completed_before_the_numbers_is_stale():
    run = {"name": "AR-1", "status": "Green", "completed_at": _t(19)}
    out = M.staleness(run, _t(20))
    assert out["state"] == "stale"
    assert out["note"] is None


def test_a_run_completed_after_the_numbers_is_current():
    run = {"name": "AR-1", "status": "Red", "completed_at": _t(21)}
    out = M.staleness(run, _t(20))
    assert out["state"] == "current"
    assert out["note"] is None


def test_a_run_completed_at_the_build_instant_is_current():
    run = {"name": "AR-1", "status": "Amber", "completed_at": _t(20)}
    assert M.staleness(run, _t(20))["state"] == "current"


def test_numbers_never_built_is_current_with_a_note():
    run = {"name": "AR-1", "status": "Green", "completed_at": _t(19)}
    out = M.staleness(run, None)
    assert out["state"] == "current"
    assert out["note"] == "numbers never built"


def test_a_finished_run_without_completed_at_is_refused():
    run = {"name": "AR-9", "status": "Green", "completed_at": None}
    with pytest.raises(ValueError, match="AR-9"):
        M.staleness(run, _t(20))


# --- purity --------------------------------------------------------------------

def test_module_imports_no_frappe():
    with open(_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] in ("frappe", "konsol") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("frappe", "konsol")
