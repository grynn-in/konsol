"""Konsol Setup checklist, pure rules: konsol/setup_model.py (konsol#407 phase 4).

Each step's done / partial / not done on fixtures. The model decides no
accounting rule: every input is what an existing reader answers (the sign-off
gate, the rate gate, the ISO check, the freshness model), so these tests pin
only how an answer becomes a step. Loaded by path: no frappe.
"""
import importlib.util
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("konsol_setup_model_test", os.path.join(APP_DIR, "setup_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _group(name="G1", currency="EUR", diff="9999", nci="2900"):
    return {"consolidation_group": name, "reporting_currency": currency,
            "ic_difference_account": diff, "nci_account": nci}


# -- fiscal calendar ---------------------------------------------------------------

def test_fiscal_calendar_not_done_without_a_year():
    s = M.fiscal_calendar_step(0, [], 0)
    assert s["state"] == M.NOT_DONE and s["count"] == 0
    assert s["route"] == ["List", "EPM Fiscal Year"]


def test_fiscal_calendar_partial_with_a_year_but_no_periods():
    assert M.fiscal_calendar_step(1, [], 0)["state"] == M.PARTIAL


def test_fiscal_calendar_done_counts_open_periods_and_mapped_areas():
    rows = [{"status": "Open"}, {"status": "Open"}, {"status": "Closed"}]
    s = M.fiscal_calendar_step(1, rows, 2)
    assert s["state"] == M.DONE
    assert "1 fiscal year, 3 periods, 2 open" in s["detail"]
    assert "2 ERP data areas mapped" in s["detail"]


# -- consolidation group -----------------------------------------------------------

def test_group_not_done_without_a_group_node():
    assert M.consolidation_group_step([], False)["state"] == M.NOT_DONE


def test_group_not_done_when_no_node_has_a_reporting_currency():
    s = M.consolidation_group_step([_group(currency="")], False)
    assert s["state"] == M.NOT_DONE
    assert "No Reporting Currency on G1" in s["detail"]


def test_group_done_with_currency_accounts_and_cta():
    s = M.consolidation_group_step([_group()], True)
    assert s["state"] == M.DONE and s["count"] == 1


def test_group_difference_account_needed_only_when_intercompany_is_configured():
    g = _group(diff="")
    assert M.consolidation_group_step([g], False)["state"] == M.DONE
    s = M.consolidation_group_step([g], True)
    assert s["state"] == M.PARTIAL
    assert "No Intercompany Difference Account on G1" in s["detail"]


def test_group_missing_nci_account_is_reported_never_judged():
    # consolidation_policy_model.required_accounts needs it only for a deal below
    # 100%: a group owning every subsidiary outright is still done (review F3).
    s = M.consolidation_group_step([_group(nci="")], False)
    assert s["state"] == M.DONE and "Non-controlling Interest Account" in s["detail"]


def test_group_with_two_nodes_without_an_entity_is_partial():
    g = dict(_group(), nodes=2)
    s = M.consolidation_group_step([g], False)
    assert s["state"] == M.PARTIAL and "2 nodes without an entity" in s["detail"]


def test_statement_accounts_step_routes_to_close_settings():
    s = M.statement_accounts_step(None)
    assert s["state"] == M.DONE and s["route"] == ["Form", "Close Settings"]
    s = M.statement_accounts_step("No CTA account is declared in Close Settings.")
    assert s["state"] == M.NOT_DONE and "No CTA account" in s["detail"]


# -- entities ----------------------------------------------------------------------

def test_entities_not_done_without_an_active_leaf():
    assert M.entities_step([], set(), None)["state"] == M.NOT_DONE


def test_entities_done_when_grouped_with_a_currency():
    s = M.entities_step([{"name": "E1", "functional_currency": "EUR"}], {"E1"}, None)
    assert s["state"] == M.DONE and s["count"] == 1


def test_entities_partial_names_ungrouped_and_currencyless():
    ents = [{"name": "E1", "functional_currency": "EUR"}, {"name": "E2", "functional_currency": ""}]
    s = M.entities_step(ents, {"E2"}, None)
    assert s["state"] == M.PARTIAL
    assert "Not in any Consolidation Group: E1" in s["detail"]
    assert "No Functional Currency: E2" in s["detail"]


def test_entities_never_names_an_entity_the_caller_cannot_see():
    ents = [{"name": "SEEN", "functional_currency": "EUR"}, {"name": "HIDDEN", "functional_currency": "EUR"}]
    s = M.entities_step(ents, set(), {"SEEN"})
    assert "HIDDEN" not in s["detail"]
    assert "SEEN and 1 you cannot see" in s["detail"]
    s = M.entities_step(ents, set(), set())
    assert "SEEN" not in s["detail"] and "2 you cannot see" in s["detail"]


# -- chart -------------------------------------------------------------------------

def test_chart_not_done_without_a_published_account():
    s = M.chart_step(0, [], "")
    assert s["state"] == M.NOT_DONE and s["route_options"] == {"status": "Draft"}


def test_chart_done_with_published_accounts_and_no_problem():
    assert M.chart_step(40, [], "")["state"] == M.DONE


def test_chart_partial_on_unresolved_translation_or_allow_ic():
    s = M.chart_step(40, ["7000"], "")
    assert s["state"] == M.PARTIAL and "7000" in s["detail"]
    s = M.chart_step(40, [], "The group chart has not declared allow_ic on 1400.")
    assert s["state"] == M.PARTIAL and "allow_ic on 1400" in s["detail"]


# -- dimensions --------------------------------------------------------------------

def test_dimensions_not_done_when_none_is_declared():
    assert M.dimensions_step([], [], [])["state"] == M.NOT_DONE


def test_dimensions_budget_gating_is_reported_never_judged():
    open_ = M.dimensions_step(["region"], [], ["region"])
    gated = M.dimensions_step(["region"], ["region"], [])
    assert open_["state"] == gated["state"] == M.DONE
    assert "open to every budget user on region" in open_["detail"]
    assert "restricted by User Permission on region" in gated["detail"]


# -- currencies --------------------------------------------------------------------

def test_currencies_follow_the_330_check():
    assert M.currencies_step(0, {"blocking": None, "reported": None})["state"] == M.NOT_DONE
    assert M.currencies_step(150, {"blocking": None, "reported": None})["state"] == M.DONE
    s = M.currencies_step(150, {"blocking": None, "reported": {"message": "XDR disagrees."}})
    assert s["state"] == M.PARTIAL and s["detail"] == "XDR disagrees."
    s = M.currencies_step(150, {"blocking": {"message": "JPY blocks."}, "reported": {"message": "x"}})
    assert s["state"] == M.NOT_DONE and s["detail"] == "JPY blocks."


# -- intercompany ------------------------------------------------------------------

def _ic(state, message="m"):
    return {"state": state, "message": message}


def test_intercompany_states():
    assert M.intercompany_step(0, _ic("not_applicable"), None, None)["state"] == M.DONE
    s = M.intercompany_step(0, _ic("not_configured"), None, "Intercompany not configured.")
    assert s["state"] == M.NOT_DONE and s["detail"] == "Intercompany not configured."
    s = M.intercompany_step(2, _ic("checked"), {"message": "1 group has no tolerance: G1"}, None)
    assert s["state"] == M.PARTIAL and "G1" in s["detail"]
    assert M.intercompany_step(2, _ic("checked"), None, None)["state"] == M.DONE


def test_intercompany_declared_none_with_published_pairings_is_not_done():
    # ic_model.state calls this an error (review F2): never "none in this group".
    s = M.intercompany_step(3, _ic("error", "Close Settings declares no intercompany ... 3 ..."), None, None)
    assert s["state"] == M.NOT_DONE and "declares no intercompany" in s["detail"]


# -- users -------------------------------------------------------------------------

def test_users_not_done_without_an_epm_admin():
    assert M.users_step(0, 3, 0, True)["state"] == M.NOT_DONE


def test_users_link_only_for_a_caller_who_can_open_user_lists():
    # User and User Permission are System Manager's (review F4).
    s = M.users_step(1, 3, 2, False)
    assert s["route"] is None and "a System Manager" in s["detail"]


def test_users_partial_routes_to_entity_user_permissions():
    s = M.users_step(1, 3, 2, True)
    assert s["state"] == M.PARTIAL
    assert "2 Entity Accountants have no Entity User Permission" in s["detail"]
    assert s["route"] == ["List", "User Permission"] and s["route_options"] == {"allow": "Entity"}


def test_users_done_with_admin_and_every_accountant_assigned():
    assert M.users_step(1, 0, 0, True)["state"] == M.DONE


# -- trial balance and rates -------------------------------------------------------

def test_trial_balance_states():
    assert M.trial_balance_step(0, None)["state"] == M.NOT_DONE
    s = M.trial_balance_step(5, "FY2025 P03")
    assert s["state"] == M.DONE and "FY2025 P03" in s["detail"] and s["count"] == 5


def test_rates_not_done_without_a_trial_balance_period():
    assert M.rates_step(None, None)["state"] == M.NOT_DONE


def test_rates_follow_the_rate_gate():
    assert M.rates_step("FY2025 P03", ([], None, []))["state"] == M.DONE
    s = M.rates_step("FY2025 P03", ([("USD", "EUR", "Closing")], None, []))
    assert s["state"] == M.NOT_DONE and "USD → EUR Closing" in s["detail"] and s["count"] == 1
    s = M.rates_step("FY2025 P03", (None, "OperationalError UNKNOWN_TABLE", []))
    assert s["state"] == M.NOT_DONE and "cannot be checked" in s["detail"]
    s = M.rates_step("FY2025 P03", ([], None, ["Entity E1 has no functional currency"]))
    assert s["state"] == M.NOT_DONE and "E1" in s["detail"]


def test_rates_blockers_never_name_an_entity_the_caller_cannot_see():
    # review F1: rate_gate's blocker sentences name entities.
    gate = ([], None, ["Consolidation Group G1 has no reporting currency",
                       "Entity SEEN has no functional currency", "Entity HIDDEN has no functional currency"])
    s = M.rates_step("FY2025 P03", gate, {"SEEN"})
    assert "HIDDEN" not in s["detail"] and "SEEN and 1 you cannot see" in s["detail"]
    assert "Consolidation Group G1 has no reporting currency" in s["detail"]


def test_rates_entity_blocker_wording_is_group_rates_own():
    with open(os.path.join(APP_DIR, "group_rates.py")) as f:
        src = f.read()
    assert 'f"Entity {e} has no functional currency"' in src
    assert M.ENTITY_BLOCKER.match("Entity E 1 has no functional currency").group(1) == "E 1"


# -- warehouse ---------------------------------------------------------------------

def test_warehouse_states():
    down = {"clickhouse_reachable": False, "message": "ClickHouse is unreachable"}
    up = {"clickhouse_reachable": True}
    assert M.warehouse_step(down, None)["state"] == M.NOT_DONE
    assert M.warehouse_step(up, {"state": "never_built", "as_of": None})["state"] == M.NOT_DONE
    s = M.warehouse_step(up, {"state": "fresh", "as_of": "2026-11-07T10:00:00+01:00"})
    assert s["state"] == M.DONE and "2026-11-07T10:00:00+01:00" in s["detail"]
    s = M.warehouse_step(up, {"state": "stale", "as_of": "x", "changed_since": ["Entity"]})
    assert s["state"] == M.PARTIAL and "Entity" in s["detail"]
    s = M.warehouse_step(up, None, "ValueError: no build scope")
    assert s["state"] == M.NOT_DONE and "ValueError: no build scope" in s["detail"]


# -- the whole --------------------------------------------------------------------

def test_unchecked_step_is_not_done_and_names_the_failure():
    s = M.unchecked("rates", "ValueError: boom")
    assert s["state"] == M.NOT_DONE and "ValueError: boom" in s["detail"]


def test_summary_orders_steps_and_counts_done():
    steps = [M.unchecked(k, "x") for k, _, _ in reversed(M.STEPS)]
    steps[0] = M.trial_balance_step(1, "FY2025 P01") if steps[0]["key"] == "trial_balance" else steps[0]
    out = M.summary(steps)
    assert [s["key"] for s in out["steps"]] == [k for k, _, _ in M.STEPS]
    assert out["total"] == len(M.STEPS)


def test_summary_refuses_a_missing_step():
    with pytest.raises(ValueError):
        M.summary([M.unchecked("rates", "x")])


def test_unknown_state_is_refused():
    with pytest.raises(ValueError):
        M._step("rates", "maybe", "")


def test_model_imports_nothing_from_frappe_or_konsol():
    with open(os.path.join(APP_DIR, "setup_model.py")) as f:
        src = f.read()
    assert "import frappe" not in src and "from konsol" not in src and "import konsol" not in src
