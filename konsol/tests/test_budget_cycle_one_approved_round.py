"""Interim rule until S6: a forecast (scenario, year) holds at most ONE approved
round (konsol#348, review of #540 / konsolidat#362, 15 Nov 2026).

Four readers sum a forecast's rounds across ``cycle``: gold_scenario_trial_
balance, the cost-allocation pool (cost_allocation_run.py), the
``forecast_input`` Dataset and ``hierarchy_query``. Until the latest-estimate
read picks the round (S6, FC-2-1), approving a second round would add two
forecasts together silently. So Budget Cycle's ``before_submit`` refuses the
Approve (the workflow's Approve is ``doc.submit()``, frappe/model/workflow.py)
when another approved, non-cancelled Cycle of the same forecast pair exists,
naming it. Draft rounds are not limited; a budget pair is not read here (its
rule is one live Cycle per pair, test_budget_cycle_amend.py).

REMOVED IN S6 with the controller method it pins, when approved rounds sit side
by side (FC-4) and the warehouse's assert_forecast_one_approved_round_until_s6
goes with them.

Site-free: the controller is loaded against the stubbed modules of
test_budget_cycle_rounds.py (its ``env`` fixture), whose ``pair`` is what
``lock_cycles`` returns under the pair lock.
"""
import datetime

import pytest

from konsol.tests.test_budget_cycle_rounds import _Thrown, _row, env  # noqa: F401  (fixture)

REFUSAL = ("Budget Cycle FC-Q1 (as of 2027-03-31) is already the approved round of ZZFC 2027. Until the "
           "latest-estimate read is built (S6), cancel it before approving another round.")


def test_the_second_approved_round_of_a_forecast_is_refused_by_name_under_the_pair_lock(env):
    env.pair[:] = [_row("FC-Q1", 1, datetime.date(2027, 3, 31)), _row("FC-Q2", 0, "2027-06-30")]
    doc = env.cycle("FC-Q2", scenario_type="forecast", as_of_date="2027-06-30", horizon_periods=12, docstatus=1)
    with pytest.raises(_Thrown) as raised:
        doc.before_submit()
    assert str(raised.value) == REFUSAL, raised.value
    assert env.locked == [("ZZFC", 2027)], "read under the pair lock, not a plain read"


def test_the_first_approval_of_a_forecast_round_passes_beside_drafts_and_cancelled_rounds(env):
    env.pair[:] = [_row("FC-Q0", 2, "2026-12-31"), _row("FC-Q1", 0, "2027-03-31"), _row("FC-Q2", 0, "2027-06-30")]
    env.cycle("FC-Q2", scenario_type="forecast", as_of_date="2027-06-30", horizon_periods=12, docstatus=1).before_submit()


def test_the_cycle_being_approved_does_not_refuse_itself(env):
    # The Cycle's own row under the lock is skipped by name, whatever docstatus it reads.
    env.pair[:] = [_row("FC-Q2", 1, "2027-06-30")]
    env.cycle("FC-Q2", scenario_type="forecast", as_of_date="2027-06-30", horizon_periods=12, docstatus=1).before_submit()


def test_a_budget_pair_is_not_judged_by_the_forecast_rule(env):
    env.kind[0] = "budget"
    env.pair[:] = [_row("B-A", 1)]
    env.cycle("B-B", scenario_id="ZZB", scenario_type="budget", docstatus=1).before_submit()
    assert env.locked == [], "a budget pair takes no lock here: its one-live-Cycle rule already holds it"
