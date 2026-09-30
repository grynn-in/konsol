"""Readiness model, pure: konsol/close/readiness_model.py (konsol#305 E201).

Turns the sign-off gate's problems, the period row, the rate gate and the
latest close run into the readiness checklist. Loaded by path; the module
imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(APP_DIR, "close", "readiness_model.py")
_spec = importlib.util.spec_from_file_location("close_readiness_model_under_test", MODEL_PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

SM_PATH = os.path.join(APP_DIR, "close", "signoff_model.py")
_sm_spec = importlib.util.spec_from_file_location("close_readiness_model_test_signoff_model", SM_PATH)
SM = importlib.util.module_from_spec(_sm_spec)
_sm_spec.loader.exec_module(SM)

CPM_PATH = os.path.join(APP_DIR, "close", "close_policy_model.py")
_cpm_spec = importlib.util.spec_from_file_location("close_readiness_model_test_close_policy_model", CPM_PATH)
CPM = importlib.util.module_from_spec(_cpm_spec)
_cpm_spec.loader.exec_module(CPM)


def _src(path):
    with open(path) as fh:
        return fh.read()


def _period_row(status="Open"):
    return {"code": "P09", "status": status, "period_type": "Regular"}


def _problems(config_gaps=None, order=None, completeness=None):
    return {
        "config_gaps": list(config_gaps or ()),
        "order": order,
        "completeness": completeness,
    }


def _rates(missing=(), error=None, blockers=()):
    return (list(missing), error, list(blockers))


def _run(status="Signed Off", failed=0, errored=0):
    if status is None:
        return None
    return {
        "name": "AR-1", "status": status, "signoff_status": status,
        "failed": failed, "errored": errored,
    }


def _by_code(result, code):
    for item in result["items"]:
        if item["code"] == code:
            return item
    raise AssertionError("no item %r in %r" % (code, [i["code"] for i in result["items"]]))


_UNSET = object()


def _readiness(period_row=None, problems=None, rates=None, run=_UNSET, allowed=None):
    return M.readiness(
        period_row if period_row is not None else _period_row(),
        problems if problems is not None else _problems(),
        rates if rates is not None else _rates(),
        _run() if run is _UNSET else run,
        allowed,
    )


def test_a_clean_open_period_is_fully_ready():
    result = _readiness()
    assert result["total"] == len(M.ITEMS) == 9
    assert result["ready"] == result["total"]
    assert [item["code"] for item in result["items"]] == list(M.ITEMS)
    for item in result["items"]:
        assert item["state"] == "ok", item


def test_a_closed_period_blocks_period_open_only():
    result = _readiness(period_row=_period_row(status="Closed"))
    item = _by_code(result, "period_open")
    assert item["state"] == "blocked"
    assert "P09" in item["detail"] and "Closed" in item["detail"]
    for other in result["items"]:
        if other["code"] != "period_open":
            assert other["state"] == "ok", other


def test_undeclared_first_close_blocks_first_close_and_leaves_previous_signed_unknown():
    gap = {"code": SM.FIRST_CLOSE_UNDECLARED, "message": "Declare the first close period in Close Settings."}
    result = _readiness(problems=_problems(config_gaps=[gap]))
    first_close = _by_code(result, "first_close")
    assert first_close["state"] == "blocked"
    assert first_close["detail"] == gap["message"]
    previous_signed = _by_code(result, "previous_signed")
    assert previous_signed["state"] == "unknown"
    assert previous_signed["detail"] == "Declare the first close period first"


def test_history_period_also_blocks_first_close():
    gap = {"code": SM.HISTORY_PERIOD, "message": "FY2025 P02 is before the first close period FY2025 P09."}
    result = _readiness(problems=_problems(config_gaps=[gap]))
    assert _by_code(result, "first_close")["state"] == "blocked"


def test_order_problem_blocks_previous_signed_with_its_message():
    order = {"blocking": "P08", "periods": ["P08"], "message": "Sign off and close P08 first"}
    result = _readiness(problems=_problems(order=order))
    assert _by_code(result, "first_close")["state"] == "ok"
    previous_signed = _by_code(result, "previous_signed")
    assert previous_signed["state"] == "blocked"
    assert previous_signed["detail"] == order["message"]


def test_each_policy_gap_blocks_policies():
    for code, message in (
        (CPM.SELF_APPROVAL_UNDECLARED, "Declare the self-approval policy."),
        (CPM.RATE_MOVE_UNDECLARED, "Declare the rate move threshold."),
    ):
        gap = {"code": code, "message": message}
        result = _readiness(problems=_problems(config_gaps=[gap]))
        policies = _by_code(result, "policies")
        assert policies["state"] == "blocked", code
        assert message in policies["detail"], code


def test_an_unknown_gap_code_shows_under_configuration_not_dropped():
    gap = {"code": "zz_new", "message": "a gap readiness_model has never heard of"}
    result = _readiness(problems=_problems(config_gaps=[gap]))
    assert result["total"] == 9
    configuration = _by_code(result, "configuration")
    assert configuration["state"] == "blocked"
    assert gap["message"] in configuration["detail"]


def test_the_289_gap_is_cut_to_allowed_and_never_leaks_a_hidden_entity():
    gap = SM.unowned_tb_gap(["ZZX", "ZZA"], (2025, 9))
    result = _readiness(problems=_problems(config_gaps=[gap]), allowed={"ZZA"})
    ownership = _by_code(result, "ownership")
    assert ownership["state"] == "blocked"
    assert ownership["entities"] == ["ZZA"]
    assert ownership["hidden"] == 1
    assert "ZZX" not in json.dumps(result)


def test_the_289_gap_with_no_scope_restriction_shows_every_entity():
    gap = SM.unowned_tb_gap(["ZZX", "ZZA"], (2025, 9))
    result = _readiness(problems=_problems(config_gaps=[gap]), allowed=None)
    ownership = _by_code(result, "ownership")
    assert ownership["entities"] == ["ZZA", "ZZX"]
    assert ownership["hidden"] == 0


def test_trial_balance_completeness_blocks_with_a_count():
    completeness = {"missing": ["ZZA", "ZZB"], "message": "No trial balance from ZZA, ZZB."}
    result = _readiness(problems=_problems(completeness=completeness))
    trial_balances = _by_code(result, "trial_balances")
    assert trial_balances["state"] == "blocked"
    assert "2" in trial_balances["detail"] and "missing" in trial_balances["detail"]
    assert trial_balances["entities"] == ["ZZA", "ZZB"]


def test_rates_error_blocks_naming_the_error():
    result = _readiness(rates=_rates(error="OperationalError UNKNOWN_TABLE"))
    rates = _by_code(result, "rates")
    assert rates["state"] == "blocked"
    assert "Rates cannot be checked" in rates["detail"]
    assert "OperationalError UNKNOWN_TABLE" in rates["detail"]


def test_rates_missing_blocks_naming_from_to_rate_type():
    result = _readiness(rates=_rates(missing=[("USD", "EUR", "Closing")]))
    rates = _by_code(result, "rates")
    assert rates["state"] == "blocked"
    assert "USD" in rates["detail"] and "EUR" in rates["detail"] and "Closing" in rates["detail"]
    assert "→" in rates["detail"]


def test_rates_blockers_block_naming_the_group():
    result = _readiness(rates=_rates(blockers=["Consolidation Group G1 has no reporting currency"]))
    rates = _by_code(result, "rates")
    assert rates["state"] == "blocked"
    assert "Consolidation Group G1 has no reporting currency" in rates["detail"]


def test_rates_clean_is_ok():
    result = _readiness(rates=_rates())
    assert _by_code(result, "rates")["state"] == "ok"


def test_checks_not_run_blocks():
    result = _readiness(run=None)
    checks = _by_code(result, "checks")
    assert checks["state"] == "blocked"
    assert "not run" in checks["detail"].lower()


def test_checks_signed_states_are_ok():
    for status in ("Signed Off", "Acknowledged", "Overridden"):
        result = _readiness(run=_run(status=status))
        checks = _by_code(result, "checks")
        assert checks["state"] == "ok", status
        assert checks["detail"] == "Signed off"


def test_checks_green_is_ok():
    result = _readiness(run=_run(status="Green"))
    assert _by_code(result, "checks")["state"] == "ok"


def test_checks_amber_is_ok_with_a_warning_detail():
    result = _readiness(run=_run(status="Amber"))
    checks = _by_code(result, "checks")
    assert checks["state"] == "ok"
    assert "warnings to acknowledge at sign-off" in checks["detail"]


def test_checks_red_or_error_blocks_with_a_failing_count():
    for status in ("Red", "Error"):
        result = _readiness(run=_run(status=status, failed=2, errored=1))
        checks = _by_code(result, "checks")
        assert checks["state"] == "blocked", status
        assert "3 failing" in checks["detail"]


def test_checks_unknown_status_raises():
    caught = None
    try:
        _readiness(run=_run(status="Bogus"))
    except ValueError as exc:
        caught = exc
    assert caught is not None


def test_a_fully_hidden_ownership_gap_still_blocks_and_never_leaks():
    # E201b: E201 read a fully-hidden entity list as "ok" while the sign-off
    # gate still blocks. Red today: state is "ok".
    gap = SM.unowned_tb_gap(["ZZX"], (2025, 9))
    result = _readiness(problems=_problems(config_gaps=[gap]), allowed={"ZZA"})
    ownership = _by_code(result, "ownership")
    assert ownership["state"] == "blocked"
    assert ownership["entities"] == []
    assert ownership["hidden"] == 1
    assert "1 entity you cannot see" in ownership["detail"]
    assert "ZZX" not in json.dumps(result)
    assert result["ready"] < result["total"]


def test_a_fully_hidden_ownership_gap_with_no_scope_restriction_is_unchanged():
    gap = SM.unowned_tb_gap(["ZZX"], (2025, 9))
    result = _readiness(problems=_problems(config_gaps=[gap]), allowed=None)
    ownership = _by_code(result, "ownership")
    assert ownership["state"] == "blocked"
    assert ownership["entities"] == ["ZZX"]
    assert ownership["hidden"] == 0


def test_hidden_trial_balance_gaps_still_block():
    completeness = {"missing": ["ZZX", "ZZY"], "message": "No trial balance from ZZX, ZZY."}
    result = _readiness(problems=_problems(completeness=completeness), allowed={"ZZA"})
    trial_balances = _by_code(result, "trial_balances")
    assert trial_balances["state"] == "blocked"
    assert trial_balances["entities"] == []
    assert trial_balances["hidden"] == 2
    dumped = json.dumps(result)
    assert "ZZX" not in dumped and "ZZY" not in dumped


def test_hidden_trial_balance_gaps_with_no_scope_restriction_are_unchanged():
    completeness = {"missing": ["ZZX", "ZZY"], "message": "No trial balance from ZZX, ZZY."}
    result = _readiness(problems=_problems(completeness=completeness), allowed=None)
    trial_balances = _by_code(result, "trial_balances")
    assert trial_balances["state"] == "blocked"
    assert trial_balances["entities"] == ["ZZX", "ZZY"]
    assert trial_balances["hidden"] == 0


def test_configuration_frequency_gap_is_scoped_and_never_leaks_a_hidden_entity():
    # Red today: _configuration_item joins gap["message"] unchanged, and the
    # real signoff_model message names every blank-frequency entity.
    gap = SM.config_gaps((2025, 1), (2025, 9), {"ZZA": None, "ZZX": None})[0]
    assert gap["code"] == SM.FREQUENCY_UNDECLARED
    assert "ZZA" in gap["message"] and "ZZX" in gap["message"]
    result = _readiness(problems=_problems(config_gaps=[gap]), allowed={"ZZA"})
    configuration = _by_code(result, "configuration")
    assert configuration["state"] == "blocked"
    assert "ZZX" not in json.dumps(result)


def test_configuration_frequency_gap_with_no_scope_restriction_is_unchanged():
    gap = SM.config_gaps((2025, 1), (2025, 9), {"ZZA": None, "ZZX": None})[0]
    result = _readiness(problems=_problems(config_gaps=[gap]), allowed=None)
    configuration = _by_code(result, "configuration")
    assert configuration["state"] == "blocked"
    assert gap["message"] in configuration["detail"]


def test_module_imports_no_frappe():
    tree = ast.parse(_src(M.__file__))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("frappe") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("frappe")
