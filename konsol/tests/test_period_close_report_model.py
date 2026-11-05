"""Period Close Desk report, pure: konsol/close/period_close_report_model.py.

The Desk Script Report "Period Close" shows one row per Regular period,
built from the close app's own read endpoints: ``period_api.get_context``
(the period states), ``signoff_api.get_signoff`` (the sign-off summary, its
next action and the gate messages) and ``checks_api.get_checks`` (the latest
run and ``can_run``). The model maps those payloads to rows; it decides no
rule. Which buttons a row offers follows the payload's own flags, the way the
close app's signoffMachine and Checks screen read them.

Loaded by path; the module imports nothing from frappe or konsol.
"""
import importlib.util
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, *parts):
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, *parts))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = _load("period_close_report_model_under_test", "close", "period_close_report_model.py")


def _state(fy=2025, fp=7, status="Open", checks="Green", signoff="Not Signed Off"):
    """A ``period_model.period_states`` state as ``get_context`` returns it."""
    return {"key": [fy, fp], "code": "P%02d" % fp, "label": "P%02d" % fp, "status": status,
            "start_date": "%d-%02d-01" % (fy, fp), "end_date": None, "run": "AR-1",
            "checks": checks, "signoff": signoff, "is_signed": False,
            "is_history": False, "catch_up": None}


def _signoff(action="sign", label="Sign off", status="Open", run="AR-1", run_status="Green",
             signoff_status="Not Signed Off", messages=(), **flags):
    payload = {
        "action": action, "label": label,
        "gates": {"config_gaps": [], "order": None, "completeness": None,
                  "messages": list(messages)},
        "checks": {"run": run, "status": run_status if run else None,
                   "signoff_status": signoff_status if run else None,
                   "failed": 0, "errored": 0},
        "period_status": status,
        "can_sign": True, "can_override": True, "can_reject": True,
        "commentary": [
            {"consolidation_group": "ZZG", "headings": 5, "with_commentary": 3,
             "missing": ["Revenue", "Cost"]},
            {"consolidation_group": "ZZH", "headings": 5, "with_commentary": 5, "missing": []},
        ],
    }
    payload.update(flags)
    return payload


def _checks(status="Green", staleness="current", can_run=True, note=None):
    return {"latest": ({"name": "AR-1", "status": status, "completed_at": "2025-08-01T10:00:00+00:00"}
                       if status else None),
            "staleness": staleness, "staleness_note": note, "can_run": can_run}


# --- which periods ----------------------------------------------------------

def test_periods_for_a_year_are_that_years_periods_in_order():
    states = [_state(2025, 2), _state(2024, 12), _state(2025, 1)]
    assert [s["key"] for s in M.periods_for(states, 2025)] == [[2025, 1], [2025, 2]]


def test_periods_without_a_year_are_the_open_periods():
    states = [_state(2025, 2, "Closed"), _state(2024, 12, "Open"), _state(2025, 3, "Locked"),
              _state(2025, 1, "Open")]
    assert [s["key"] for s in M.periods_for(states, None)] == [[2024, 12], [2025, 1]]


# --- the row ----------------------------------------------------------------

def test_row_carries_the_payloads_state():
    r = M.row(_state(), _signoff(), _checks(),
              {"signed_off_by": None, "signed_off_at": None}, None)
    assert r["period"] == "FY2025 P07"
    assert (r["fiscal_year"], r["fiscal_period"]) == (2025, 7)
    assert r["period_status"] == "Open"
    assert r["checks"] == "Green"
    assert r["run"] == "AR-1"
    assert r["signoff"] == "Not Signed Off"
    assert r["next_step"] == "Sign off"
    assert r["blockers"] == ""


def test_period_status_is_the_signoff_payloads_not_the_context_state():
    r = M.row(_state(status="Open"), _signoff(status="Closed", action="blocked",
                                              label="The period is Closed; reopen it"),
              _checks(can_run=False), None, None)
    assert r["period_status"] == "Closed"


def test_no_run_reads_not_run():
    r = M.row(_state(), _signoff(action="run_checks", label="Run the checks", run=None),
              _checks(status=None, staleness="not_run"), None, None)
    assert r["checks"] == "Not run"
    assert r["signoff"] == "No checks run"
    assert r["run"] is None


def test_staleness_is_shown_beside_the_status():
    r = M.row(_state(), _signoff(), _checks(staleness="stale"), None, None)
    assert r["checks"] == "Green (stale: the numbers changed after this run)"
    r = M.row(_state(), _signoff(action="wait", label="wait"), _checks(status="Running", staleness="running"),
              None, None)
    assert r["checks"] == "Running"


def test_staleness_note_is_kept():
    r = M.row(_state(), _signoff(), _checks(note="numbers never built"), None, None)
    assert r["checks"] == "Green (numbers never built)"


def test_unknown_staleness_is_refused():
    with pytest.raises(ValueError):
        M.row(_state(), _signoff(), _checks(staleness="soon"), None, None)


def test_signed_by_and_at_come_from_the_signed_run():
    r = M.row(_state(), _signoff(action="signed", label="Signed Off", signoff_status="Signed Off"),
              _checks(), {"signed_off_by": "lead@zz.test", "signed_off_at": "2025-08-02 09:00:00"}, None)
    assert r["signoff"] == "Signed Off"
    assert r["signed_by"] == "lead@zz.test"
    assert r["signed_at"] == "2025-08-02 09:00:00"


def test_blockers_are_the_gate_messages():
    r = M.row(_state(), _signoff(action="blocked", label="No trial balance from ZZA.",
                                 messages=["No trial balance from ZZA.", "Sign off P06 first."]),
              _checks(), None, None)
    assert r["blockers"] == "No trial balance from ZZA.; Sign off P06 first."


def test_a_blocked_label_with_no_gate_message_is_a_blocker():
    label = "The period is Closed; reopen it to run the checks or sign off"
    r = M.row(_state(), _signoff(action="blocked", label=label, status="Closed"), _checks(can_run=False),
              None, None)
    assert r["blockers"] == label


def test_commentary_for_the_chosen_group():
    r = M.row(_state(), _signoff(), _checks(), None, "ZZG")
    assert r["commentary"] == "3 of 5 headings; missing: Revenue, Cost"


def test_commentary_for_every_group_without_a_choice():
    r = M.row(_state(), _signoff(), _checks(), None, None)
    assert r["commentary"] == "ZZG: 3 of 5 headings; ZZH: 5 of 5 headings"


def test_commentary_for_a_group_the_payload_does_not_list_says_so():
    r = M.row(_state(), _signoff(), _checks(), None, "ZZX")
    assert r["commentary"] == "ZZX is not a root consolidation group of this site"


def test_an_api_refusal_is_the_rows_blocker_and_offers_nothing():
    r = M.row(_state(), None, None, None, None, error="P07 is not declared")
    assert r["blockers"] == "P07 is not declared"
    assert r["checks"] is None and r["signoff"] is None
    assert not any(r["allow"].values())


# --- which actions a row offers ---------------------------------------------

def _allow(signoff, checks):
    return M.row(_state(), signoff, checks, None, None)["allow"]


def test_run_checks_follows_can_run():
    assert _allow(_signoff(), _checks(can_run=True))["run_checks"] is True
    assert _allow(_signoff(), _checks(can_run=False))["run_checks"] is False


def test_run_checks_is_not_offered_while_a_run_is_in_flight():
    assert _allow(_signoff(action="wait"), _checks(status="Running", staleness="running"))["run_checks"] is False


@pytest.mark.parametrize("action", ["sign", "acknowledge"])
def test_sign_and_acknowledge_need_can_sign(action):
    assert _allow(_signoff(action=action), _checks())["sign"] is True
    assert _allow(_signoff(action=action, can_sign=False), _checks())["sign"] is False


def test_override_needs_can_sign_and_can_override():
    assert _allow(_signoff(action="override"), _checks())["sign"] is True
    assert _allow(_signoff(action="override", can_override=False), _checks())["sign"] is False


@pytest.mark.parametrize("action", ["blocked", "run_checks", "rerun", "wait", "signed"])
def test_no_sign_for_other_actions(action):
    assert _allow(_signoff(action=action), _checks())["sign"] is False


def test_reject_and_close_need_a_signed_open_period():
    signed = dict(action="signed", label="Signed Off", signoff_status="Signed Off")
    a = _allow(_signoff(**signed), _checks())
    assert a["reject"] is True and a["close"] is True and a["reopen"] is False
    a = _allow(_signoff(status="Closed", **signed), _checks())
    assert a["reject"] is False and a["close"] is False
    assert _allow(_signoff(can_reject=False, **signed), _checks())["reject"] is False
    assert _allow(_signoff(), _checks())["close"] is False


def test_reopen_is_offered_for_closed_never_locked():
    assert _allow(_signoff(action="blocked", status="Closed"), _checks(can_run=False))["reopen"] is True
    assert _allow(_signoff(action="signed", status="Locked"), _checks(can_run=False))["reopen"] is False


def test_row_carries_the_sign_action_for_the_dialog():
    r = M.row(_state(), _signoff(action="acknowledge", label="Acknowledge and sign off"), _checks(), None, None)
    assert r["action"] == "acknowledge"
    assert r["action_label"] == "Acknowledge and sign off"


# --- columns and message ----------------------------------------------------

def test_columns_name_every_shown_field():
    names = [c["fieldname"] for c in M.columns()]
    assert names == ["period", "period_status", "checks", "run", "signoff", "signed_by", "signed_at",
                     "next_step", "blockers", "commentary"]
    row = M.row(_state(), _signoff(), _checks(), None, None)
    assert set(names) <= set(row)


def test_message_names_an_empty_year():
    assert M.message(2023, [], []) == "No Regular period of FY2023 is declared."
    assert M.message(None, [], []) == "No Regular period is Open."


def test_message_carries_config_gaps():
    gaps = [{"code": "first_close_undeclared", "message": "Declare the first close period."}]
    assert M.message(2025, [{"period": "FY2025 P01"}], gaps) == "Declare the first close period."
    assert M.message(2025, [{"period": "FY2025 P01"}], []) is None


# -- coordinator review of desk-period-close (5 Nov 2026) --------------------

def test_blockers_are_short_enough_for_a_table_cell():
    """Live, one row's blockers ran to ~2,800 characters (a list of every
    entity missing a trial balance). The cell shows the first three messages,
    each cut at 200 characters, and says how many more there are."""
    long_msg = "No trial balance from " + ", ".join("ZZ%03d" % i for i in range(300))
    msgs = [long_msg, "Declare the tolerance", "Declare the CTA account", "Fourth", "Fifth"]
    r = M.row(_state(), _signoff(action="blocked", label="Blocked", messages=msgs), _checks(), None, None)
    text = r["blocks"] if "blocks" in r else next(v for k, v in r.items() if "block" in k)
    assert len(text) <= 3 * 205 + 40, len(text)
    assert "and 3 more" in text, text
    assert text.startswith("Blocked; No trial balance from ZZ000")
