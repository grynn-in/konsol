"""My-work model, pure: konsol/close/mywork_model.py (konsol#305 A14; stories 1.2, 0.4).

One configuration gap is ONE item listing the affected entities (#291: never
one item per entity per month). Loaded by path; imports nothing from frappe.
"""
import ast
import importlib.util
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "close", "mywork_model.py")
_spec = importlib.util.spec_from_file_location("close_mywork_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

# S1: real producer contract. ic_model is pure too, so ic_fix_items' test
# input is built by calling it, never a hand-built dict.
_IC_PATH = os.path.join(APP_DIR, "close", "ic_model.py")
_ic_spec = importlib.util.spec_from_file_location("close_ic_under_test_for_mywork", _IC_PATH)
IC = importlib.util.module_from_spec(_ic_spec)
_ic_spec.loader.exec_module(IC)

# N47: close_policy_model is pure too; the statement-accounts gap fed into
# policy_gaps is the REAL gap it returns, never a hand-built dict.
_CPM_PATH = os.path.join(APP_DIR, "close", "close_policy_model.py")
_cpm_spec = importlib.util.spec_from_file_location("close_policy_model_under_test_for_mywork", _CPM_PATH)
CPM = importlib.util.module_from_spec(_cpm_spec)
_cpm_spec.loader.exec_module(CPM)

_STATEMENT_GAP = CPM.statement_accounts("", "", {})["gap"]


def _facts(**over):
    facts = {
        "first_close": (2025, 7),
        "chart_published": True,
        "frequency_missing": [],
        "ownership_missing": [],
        "accountants_without_entities": [],
        "policy_gaps": [],
        "ic_accounts_gap": None,
        "ic_tolerance_gap": None,
    }
    facts.update(over)
    return facts


def _by_id(items):
    return {i["id"]: i for i in items}


def test_nothing_missing_gives_no_items():
    assert M.setup_gap_items(_facts()) == []


def test_three_entities_without_ownership_is_one_item():
    items = M.setup_gap_items(_facts(ownership_missing=["FR01", "DE01", "US01"]))
    assert [i["id"] for i in items] == ["gap:ownership"]
    item = items[0]
    assert item["entities"] == ["DE01", "FR01", "US01"]
    assert "3 entities" in item["title"]
    assert item["action"] == {"desk": "/app/ownership-period"}


def test_one_entity_title_is_singular():
    item = M.setup_gap_items(_facts(ownership_missing=["FR01"]))[0]
    assert "1 entity" in item["title"]
    assert "entities" not in item["title"]


def test_duplicate_entities_listed_once():
    item = M.setup_gap_items(_facts(frequency_missing=["FR01", "FR01", "DE01"]))[0]
    assert item["entities"] == ["DE01", "FR01"]


def test_empty_lists_give_no_item():
    ids = [i["id"] for i in M.setup_gap_items(_facts(frequency_missing=[], ownership_missing=[],
                                                      accountants_without_entities=[]))]
    assert ids == []


_BOTH_POLICY_GAPS = [
    {"code": "self_approval_undeclared", "message": "Declare the self-approval policy."},
    {"code": "rate_move_undeclared", "message": "Declare the rate move threshold."},
]


_IC_ACCOUNTS_GAP = "Publish at least one Intercompany Account, or declare none in this group."
_IC_TOLERANCE_GAP = {"code": "ic_tolerance_undeclared", "groups": ["EMEA Group"],
                      "message": "Declare the intercompany tolerance for EMEA Group."}


def test_every_gap_present_ids_stable_and_ordered():
    facts = _facts(first_close=None, chart_published=False, frequency_missing=["FR01"],
                   ownership_missing=["DE01"], accountants_without_entities=["zz-a@example.com"],
                   policy_gaps=_BOTH_POLICY_GAPS, ic_accounts_gap=_IC_ACCOUNTS_GAP,
                   ic_tolerance_gap=_IC_TOLERANCE_GAP)
    items = M.setup_gap_items(facts)
    assert [i["id"] for i in items] == [
        "gap:first_close", "gap:self_approval", "gap:rate_move", "gap:chart", "gap:ic_accounts",
        "gap:ic_tolerance", "gap:frequency", "gap:ownership", "gap:accountants"]
    # Same input, same output: ids never depend on order or content.
    assert M.setup_gap_items(dict(facts)) == items


def test_desk_actions_point_at_configuration():
    items = _by_id(M.setup_gap_items(_facts(
        first_close=None, chart_published=False, frequency_missing=["FR01"],
        ownership_missing=["DE01"], accountants_without_entities=["zz-a@example.com"],
        policy_gaps=_BOTH_POLICY_GAPS, ic_accounts_gap=_IC_ACCOUNTS_GAP,
        ic_tolerance_gap=_IC_TOLERANCE_GAP)))
    assert items["gap:first_close"]["action"] == {"desk": "/app/close-settings"}
    assert items["gap:self_approval"]["action"] == {"desk": "/app/close-settings"}
    assert items["gap:rate_move"]["action"] == {"desk": "/app/close-settings"}
    assert items["gap:chart"]["action"] == {"desk": "/app/main-account"}
    assert items["gap:ic_accounts"]["action"] == {"desk": "/app/intercompany-account"}
    assert items["gap:ic_tolerance"]["action"] == {"desk": "/app/consolidation-group"}
    assert items["gap:frequency"]["action"] == {"desk": "/app/entity"}
    assert items["gap:ownership"]["action"] == {"desk": "/app/ownership-period"}
    assert items["gap:accountants"]["action"] == {"desk": "/app/user"}
    assert items["gap:accountants"]["users"] == ["zz-a@example.com"]
    assert items["gap:accountants"]["entities"] == []


def test_every_gap_is_blocking_with_an_owner_role():
    items = M.setup_gap_items(_facts(
        first_close=None, chart_published=False, frequency_missing=["FR01"],
        ownership_missing=["DE01"], accountants_without_entities=["zz-a@example.com"],
        policy_gaps=_BOTH_POLICY_GAPS, ic_accounts_gap=_IC_ACCOUNTS_GAP,
        ic_tolerance_gap=_IC_TOLERANCE_GAP))
    for item in items:
        assert item["kind"] == "blocking"
        assert item["owner"] in ("EPM Admin", "System Manager")
        assert item["title"] and item["detail"]


def test_ic_accounts_gap_is_one_item_pointed_at_the_desk():
    items = M.setup_gap_items(_facts(ic_accounts_gap=_IC_ACCOUNTS_GAP))
    assert [i["id"] for i in items] == ["gap:ic_accounts"]
    item = items[0]
    assert item["title"] == "No intercompany accounts declared"
    assert item["detail"] == _IC_ACCOUNTS_GAP
    assert item["owner"] == "EPM Admin"
    assert item["action"] == {"desk": "/app/intercompany-account"}


def test_ic_accounts_gap_none_gives_no_item():
    assert M.setup_gap_items(_facts(ic_accounts_gap=None)) == []


def test_missing_ic_accounts_gap_fact_raises_not_guessed():
    facts = _facts()
    del facts["ic_accounts_gap"]
    with pytest.raises(ValueError, match="ic_accounts_gap"):
        M.setup_gap_items(facts)


def test_ic_tolerance_gap_is_one_blocking_item():
    items = M.setup_gap_items(_facts(ic_tolerance_gap=_IC_TOLERANCE_GAP))
    assert [i["id"] for i in items] == ["gap:ic_tolerance"]
    item = items[0]
    assert item["detail"] == _IC_TOLERANCE_GAP["message"]
    assert item["owner"] == "EPM Admin"
    assert item["action"] == {"desk": "/app/consolidation-group"}
    assert "1 group" in item["title"]
    assert "groups" not in item["title"]


def test_ic_tolerance_gap_title_is_plural_for_more_than_one_group():
    gap = {"code": "ic_tolerance_undeclared", "groups": ["EMEA Group", "APAC Group"],
           "message": "Declare the intercompany tolerance."}
    item = M.setup_gap_items(_facts(ic_tolerance_gap=gap))[0]
    assert "2 groups" in item["title"]


def test_ic_tolerance_gap_none_gives_no_item():
    assert M.setup_gap_items(_facts(ic_tolerance_gap=None)) == []


def test_missing_ic_tolerance_gap_fact_raises_not_guessed():
    facts = _facts()
    del facts["ic_tolerance_gap"]
    with pytest.raises(ValueError, match="ic_tolerance_gap"):
        M.setup_gap_items(facts)


def test_a_policy_gap_is_one_item_with_owner_action_and_no_since():
    items = M.setup_gap_items(_facts(
        policy_gaps=[{"code": "self_approval_undeclared", "message": "Declare it in Close Settings."}]))
    assert [i["id"] for i in items] == ["gap:self_approval"]
    item = items[0]
    assert item["owner"] == "EPM Admin"
    assert item["action"] == {"desk": "/app/close-settings"}
    assert item["since"] is None
    assert item["detail"] == "Declare it in Close Settings."
    assert item["title"] == "Self-approval policy not declared"


def test_the_rate_move_gap_alone_is_one_item():
    items = M.setup_gap_items(_facts(
        policy_gaps=[{"code": "rate_move_undeclared", "message": "Declare the rate move threshold."}]))
    assert [i["id"] for i in items] == ["gap:rate_move"]
    assert items[0]["title"] == "Rate move threshold not declared"
    assert items[0]["detail"] == "Declare the rate move threshold."


def test_missing_policy_gaps_fact_raises_not_guessed():
    facts = _facts()
    del facts["policy_gaps"]
    with pytest.raises(ValueError, match="policy_gaps"):
        M.setup_gap_items(facts)


# --- N47: the statement-accounts gap (konsol#305-W4-1 1c) ---------------------
#
# The API layer appends signoff_gate.statement_gap() to policy_gaps (group
# personas only); this model resolves it through the same _POLICY_GAPS
# lookup as self-approval and rate-move, so it is one item.


def test_statement_accounts_gap_is_one_blocking_item_with_no_since():
    items = M.setup_gap_items(_facts(policy_gaps=[_STATEMENT_GAP]))
    assert [i["id"] for i in items] == ["gap:statement_accounts"]
    item = items[0]
    assert item["kind"] == "blocking"
    assert item["owner"] == "EPM Admin"
    assert item["action"] == {"desk": "/app/close-settings"}
    assert item["since"] is None
    assert item["detail"] == _STATEMENT_GAP["message"]
    assert item["title"] == "Statement setup incomplete"


def test_unknown_policy_gap_code_still_raises_not_silently_dropped():
    # Failure path (the import/stub rule's sibling for product code): a code
    # _POLICY_GAPS does not know is never swallowed.
    with pytest.raises(KeyError):
        M.setup_gap_items(_facts(policy_gaps=[{"code": "nonexistent_gap_code", "message": "x"}]))


def test_first_close_missing_is_an_item_even_when_everything_else_is_fine():
    items = M.setup_gap_items(_facts(first_close=None))
    assert [i["id"] for i in items] == ["gap:first_close"]
    assert "Close Settings" in items[0]["detail"]
    assert items[0]["owner"] == "EPM Admin"


def test_first_close_zero_key_is_undeclared():
    # Close Settings Int fields read back as 0 when unset (COORDINATOR NOTE A06).
    for key in ((0, 0), (2025, 0), (0, 7)):
        assert [i["id"] for i in M.setup_gap_items(_facts(first_close=key))] == ["gap:first_close"]


def test_missing_fact_key_raises_not_guessed():
    facts = _facts()
    del facts["ownership_missing"]
    with pytest.raises(ValueError, match="ownership_missing"):
        M.setup_gap_items(facts)


def test_chart_published_must_be_bool():
    with pytest.raises(ValueError, match="chart_published"):
        M.setup_gap_items(_facts(chart_published=None))


def test_module_imports_no_frappe():
    with open(_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] in ("frappe", "konsol") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("frappe", "konsol")


# --- A20: per-role period items and ranking -----------------------------------

FIRST = (2025, 7)
P07 = (2025, 7)
P08 = (2025, 8)


def _period(code, **over):
    facts = {
        "code": code,
        "ended": True,
        "my_missing": [],
        "missing": [],
        "checks": "current",
        "failed": 0,
        "signoff": "Not signed off",
        "gates_blocked": False,
        "rates_missing": 0,
        "status": "Open",
        "unowned": [],
    }
    facts.update(over)
    return facts


def _two_open():
    """P07 (ended, ZZA and ZZB missing, checks stale) and P08 (in progress, checks failing)."""
    return {
        P07: _period("FY2025 P07", my_missing=["ZZA"], missing=["ZZA", "ZZB"], checks="stale",
                     rates_missing=2),
        P08: _period("FY2025 P08", ended=False, my_missing=["ZZA"], missing=["ZZA"],
                     checks="failed", failed=3, signoff="Re-sign Needed"),
    }


def _ids(items):
    return [i["id"] for i in items]


def _titles(items):
    return {i["title"]: i for i in items}


def test_period_item_shape_and_period_tag():
    items = M.period_items("group_accountant", _two_open(), FIRST)
    assert items
    for item in items:
        assert set(item) == {"id", "kind", "title", "period", "owner", "action"}
        assert item["kind"] in ("blocking", "todo", "waiting")
        assert item["period"]["code"] in ("FY2025 P07", "FY2025 P08")
        assert (item["period"]["fiscal_year"], item["period"]["fiscal_period"]) in (P07, P08)
        assert item["action"]["screen"] in ("my-work", "trial-balances", "checks", "sign-off",
                                            "rates")
        assert "desk" not in item["action"]


def test_entity_accountant_gets_one_upload_item_per_own_missing_entity_per_period():
    items = M.period_items("entity_accountant", _two_open(), FIRST)
    by_period = {(i["period"]["fiscal_period"], i["title"]): i for i in items}
    assert set(by_period) == {(7, "Upload TB for ZZA"), (8, "Upload TB for ZZA")}
    # P07 has ended, so its upload is blocking; P08 has not, so it is a todo.
    assert by_period[(7, "Upload TB for ZZA")]["kind"] == "blocking"
    assert by_period[(8, "Upload TB for ZZA")]["kind"] == "todo"
    for item in items:
        assert item["owner"] == "Entity Accountant"
        assert item["action"] == {"screen": "trial-balances", "entity": "ZZA"}


def test_entity_accountant_never_sees_another_entitys_item_even_when_blocking():
    # ZZB is missing in an ended period (blocking for its owner), but it is not mine.
    per = {P07: _period("FY2025 P07", my_missing=[], missing=["ZZB"], checks="failed", failed=4,
                        rates_missing=3, signoff="Re-sign Needed")}
    assert M.period_items("entity_accountant", per, FIRST) == []
    per = {P07: _period("FY2025 P07", my_missing=["ZZA"], missing=["ZZA", "ZZB"])}
    items = M.period_items("entity_accountant", per, FIRST)
    assert [i["title"] for i in items] == ["Upload TB for ZZA"]
    assert all("ZZB" not in str(i) for i in items)


def test_group_accountant_items():
    items = M.period_items("group_accountant", _two_open(), FIRST)
    got = {(i["period"]["fiscal_period"], i["title"], i["kind"]) for i in items}
    assert got == {
        (7, "Rates missing (2)", "blocking"),
        (7, "Run checks", "todo"),
        (7, "Waiting on 2 trial balances", "waiting"),
        (8, "3 checks failing", "blocking"),
        (8, "Waiting on 1 trial balance", "waiting"),
    }
    for item in items:
        assert item["owner"] == "EPM Analyst"
    screens = {i["title"]: i["action"]["screen"] for i in items}
    assert screens["Run checks"] == "checks"
    assert screens["3 checks failing"] == "checks"
    assert screens["Waiting on 2 trial balances"] == "trial-balances"
    assert screens["Rates missing (2)"] == "rates"


def test_group_accountant_rates_missing_item_id_and_shape():
    # konsol#305 E412 (#305-W2-11): the Analyst gets "Rates missing" too,
    # built by the same helper as the Close Lead's.
    items = M.period_items("group_accountant", _two_open(), FIRST)
    item = next(i for i in items if i["id"] == "rates:2025-07")
    assert item["kind"] == "blocking"
    assert item["action"] == {"screen": "rates"}


def test_entity_accountant_and_viewer_get_no_rates_item():
    # Failure path: the rates item leaks to an entity persona.
    per = {P07: _period("FY2025 P07", my_missing=["ZZA"], missing=["ZZA"], rates_missing=4)}
    for persona in ("entity_accountant", "viewer"):
        items = M.period_items(persona, per, FIRST)
        assert not any(i["id"].startswith("rates:") for i in items), persona


def test_group_accountant_run_checks_when_not_run_and_nothing_when_current():
    per = {P07: _period("FY2025 P07", checks="not_run")}
    assert [i["title"] for i in M.period_items("group_accountant", per, FIRST)] == ["Run checks"]
    per = {P07: _period("FY2025 P07", checks="current")}
    assert M.period_items("group_accountant", per, FIRST) == []


def test_close_lead_items():
    items = M.period_items("close_lead", _two_open(), FIRST)
    got = {(i["period"]["fiscal_period"], i["title"], i["kind"]) for i in items}
    assert got == {
        (7, "Rates missing (2)", "blocking"),
        (7, "Waiting on 2 trial balances", "waiting"),
        (7, "Waiting on checks", "waiting"),
        (8, "Re-sign needed", "blocking"),
        (8, "Waiting on 1 trial balance", "waiting"),
        (8, "Waiting on 3 failing checks", "waiting"),
    }
    for item in items:
        assert item["owner"] == "EPM Admin"
    assert _titles(items)["Rates missing (2)"]["action"] == {"screen": "rates"}
    assert _titles(items)["Re-sign needed"]["action"] == {"screen": "sign-off"}


def test_close_lead_unowned_tb_is_one_blocking_item():
    # konsol#305 E206, #289: a submitted TB with no covering ownership.
    per = {P07: _period("FY2025 P07", unowned=["ZZX"])}
    items = M.period_items("close_lead", per, FIRST)
    item = next(i for i in items if i["id"] == "unowned:2025-07")
    assert item["kind"] == "blocking"
    assert item["action"] == {"desk": "/app/ownership-period"}
    assert item["entities"] == ["ZZX"]
    assert item["title"] == "Trial balance with no ownership (1)"


def test_only_the_close_lead_sees_the_unowned_tb_item():
    per = {P07: _period("FY2025 P07", unowned=["ZZX"])}
    for persona in ("group_accountant", "entity_accountant", "viewer"):
        items = M.period_items(persona, per, FIRST)
        assert not any(i["id"].startswith("unowned:") for i in items), persona


def test_missing_unowned_fact_raises_not_guessed():
    per = _two_open()
    del per[P07]["unowned"]
    with pytest.raises(ValueError, match="unowned"):
        M.period_items("close_lead", per, FIRST)


def test_close_lead_sign_off_todo_when_checks_current_and_no_gate_blocks():
    per = {P08: _period("FY2025 P08")}
    items = M.period_items("close_lead", per, FIRST)
    assert [(i["title"], i["kind"]) for i in items] == [("Sign off FY2025 P08", "todo")]
    assert items[0]["action"] == {"screen": "sign-off"}


def test_close_lead_no_sign_off_when_already_signed_or_checks_not_clean():
    for over in ({"signoff": "Signed Off"}, {"checks": "stale"}, {"checks": "failed", "failed": 1},
                 {"rates_missing": 1}):
        per = {P08: _period("FY2025 P08", **over)}
        titles = [i["title"] for i in M.period_items("close_lead", per, FIRST)]
        assert not any(t.startswith("Sign off") for t in titles), over


def test_gates_blocked_suppresses_sign_off_and_waits_on_the_earlier_period():
    per = {
        P07: _period("FY2025 P07", checks="stale"),
        P08: _period("FY2025 P08", gates_blocked=True),
    }
    items = M.period_items("close_lead", per, FIRST)
    p08 = [i for i in items if i["period"]["fiscal_period"] == 8]
    assert [(i["title"], i["kind"]) for i in p08] == [("Waiting on FY2025 P07", "waiting")]
    assert not any(i["title"].startswith("Sign off") for i in items)


def test_gates_blocked_with_no_earlier_period_still_waits_visibly():
    per = {P08: _period("FY2025 P08", gates_blocked=True)}
    items = M.period_items("close_lead", per, FIRST)
    assert [(i["title"], i["kind"]) for i in items] == [("Waiting on the sign-off gates", "waiting")]
    assert items[0]["action"] == {"screen": "sign-off"}


def test_viewer_gets_no_items():
    assert M.period_items("viewer", _two_open(), FIRST) == []


def test_history_periods_never_produce_items():
    per = dict(_two_open())
    per[(2025, 6)] = _period("FY2025 P06", my_missing=["ZZA"], missing=["ZZA"], checks="failed",
                             failed=9, rates_missing=5, signoff="Re-sign Needed")
    for persona in ("close_lead", "group_accountant", "entity_accountant"):
        items = M.period_items(persona, per, FIRST)
        assert items
        assert all(i["period"]["fiscal_period"] != 6 for i in items), persona


def test_history_period_does_not_become_the_waited_on_period():
    per = {
        (2025, 6): _period("FY2025 P06", checks="stale"),
        P08: _period("FY2025 P08", gates_blocked=True),
    }
    titles = [i["title"] for i in M.period_items("close_lead", per, FIRST)]
    assert titles == ["Waiting on the sign-off gates"]


def test_undeclared_first_close_raises_not_guessed():
    for first in (None, (0, 0), (2025, 0)):
        with pytest.raises(ValueError, match="first close"):
            M.period_items("close_lead", _two_open(), first)


def test_unknown_persona_raises():
    with pytest.raises(ValueError, match="persona"):
        M.period_items("auditor", _two_open(), FIRST)


def test_missing_period_field_raises():
    per = _two_open()
    del per[P07]["gates_blocked"]
    with pytest.raises(ValueError, match="gates_blocked"):
        M.period_items("close_lead", per, FIRST)


def test_unknown_checks_state_raises():
    per = {P07: _period("FY2025 P07", checks="green")}
    with pytest.raises(ValueError, match="checks"):
        M.period_items("group_accountant", per, FIRST)


def test_ids_are_unique_and_stable():
    items = M.period_items("close_lead", _two_open(), FIRST)
    assert len(set(_ids(items))) == len(items)
    assert _ids(M.period_items("close_lead", _two_open(), FIRST)) == _ids(items)
    ea = M.period_items("entity_accountant", _two_open(), FIRST)
    assert _ids(ea) == ["tb:2025-07:ZZA", "tb:2025-08:ZZA"]


def test_rank_blocking_then_todo_then_waiting_older_period_first_then_title():
    def it(kind, fy, fp, title):
        return {"id": "%s:%d-%d:%s" % (kind, fy, fp, title), "kind": kind, "title": title,
                "period": {"fiscal_year": fy, "fiscal_period": fp, "code": "FY%d P%02d" % (fy, fp)},
                "owner": "EPM Admin", "action": {"screen": "sign-off"}}

    items = [
        it("waiting", 2025, 7, "Waiting on checks"),
        it("todo", 2025, 8, "Sign off"),
        it("blocking", 2025, 8, "B"),
        it("blocking", 2026, 1, "A"),
        it("blocking", 2025, 8, "A"),
        it("todo", 2025, 7, "Run checks"),
        it("blocking", 2025, 12, "Z"),
    ]
    ranked = M.rank(items)
    assert [(i["kind"], i["period"]["fiscal_year"], i["period"]["fiscal_period"], i["title"])
            for i in ranked] == [
        ("blocking", 2025, 8, "A"),
        ("blocking", 2025, 8, "B"),
        ("blocking", 2025, 12, "Z"),
        ("blocking", 2026, 1, "A"),
        ("todo", 2025, 7, "Run checks"),
        ("todo", 2025, 8, "Sign off"),
        ("waiting", 2025, 7, "Waiting on checks"),
    ]
    assert M.rank([]) == []


# --- A45: a signed but still-Open period gives the Close Lead "Close <code>" ---


def test_signed_off_but_still_open_gives_close_lead_a_close_todo():
    per = {P08: _period("P08", signoff="Signed Off", status="Open")}
    items = M.period_items("close_lead", per, FIRST)
    assert [(i["id"], i["title"], i["kind"]) for i in items] == [
        ("close:2025-08", "Close P08", "todo")]
    assert items[0]["action"] == {"screen": "sign-off"}
    for persona in ("group_accountant", "entity_accountant"):
        assert M.period_items(persona, per, FIRST) == []


def test_signed_off_and_closed_gives_no_close_item():
    per = {P08: _period("P08", signoff="Signed Off", status="Closed")}
    assert M.period_items("close_lead", per, FIRST) == []


def test_rank_keeps_setup_gap_items_first_among_blocking():
    # Gap items carry no period; they rank before every period item of their kind.
    gap = M.setup_gap_items(_facts(ownership_missing=["ZZA"]))[0]
    period = M.period_items("entity_accountant", _two_open(), FIRST)
    ranked = M.rank(period + [gap])
    assert ranked[0]["id"] == "gap:ownership"


def test_signed_states_match_the_assertion_run_controller():
    # One source of truth: the pure model mirrors assertion_run.SIGNED_STATES.
    path = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run", "assertion_run.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    found = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
             and any(getattr(t, "id", None) == "SIGNED_STATES" for t in n.targets)]
    assert found == [M.SIGNED_STATES]


def test_re_sign_needed_is_not_a_signed_state():
    assert "Re-sign Needed" not in M.SIGNED_STATES


# --- A11: the Close Lead's "waiting for your approval" item ------------------


def test_approvals_item_singular():
    item = M.approvals_item({"count": 1, "oldest": "2025-07-15T09:00:00+00:00"})
    assert item == {
        "id": "approvals",
        "kind": "todo",
        "title": "Approve 1 item",
        "owner": M.OWNERS[M.CLOSE_LEAD],
        "action": {"screen": "approvals"},
        "since": "2025-07-15",
        "since_reason": "oldest waiting",
    }


def test_approvals_item_plural():
    item = M.approvals_item({"count": 3, "oldest": "2025-07-15T09:00:00+00:00"})
    assert item["title"] == "Approve 3 items"
    assert item["kind"] == "todo"
    assert item["action"] == {"screen": "approvals"}


def test_approvals_item_count_zero_gives_none():
    assert M.approvals_item({"count": 0, "oldest": None}) is None


def test_approvals_item_missing_count_raises_not_guessed():
    with pytest.raises(ValueError, match="count"):
        M.approvals_item({})


def test_approvals_item_has_no_period_and_ranks_before_period_todo_items():
    approvals = M.approvals_item({"count": 1, "oldest": "2025-07-15T09:00:00+00:00"})
    assert "period" not in approvals
    period_todo = {"id": "signoff:2025-07", "kind": "todo", "title": "Sign off P07",
                   "period": {"fiscal_year": 2025, "fiscal_period": 7, "code": "P07"},
                   "owner": "EPM Admin", "action": {"screen": "sign-off"}}
    ranked = M.rank([period_todo, approvals])
    assert ranked[0]["id"] == "approvals"


# --- C07 (W3-1, W3-2): the Entity Accountant's intercompany fix items ---------

IC_P07 = (2025, 7)
IC_P08 = (2025, 8)


_IC_EVENT = {
    "kind": "ic_sent_back", "name": "ZZ-EVT-1", "at": "2025-07-15T10:00:00",
    "actor": "alice@example.com", "reason": "Please review the booking.",
    "detail": {"entity_a": "UK01", "account_a": "140000",
               "entity_b": "DE01", "account_b": "240000"},
}

_IC_ROW = {
    "entity_a": "UK01", "account_a": "140000", "entity_b": "DE01", "account_b": "240000",
    "consolidation_group": "EMEA Group", "match_status": "over_tolerance",
    "difference": 360.65, "tolerance": 5.0, "balance_a": 1250.75, "balance_b": 890.10,
}


def _ic_fix(state="over_tolerance", error=None):
    """S1: built through the real producer (``ic_model.open_fixes``), never
    a hand-built dict. ``not_in_build`` and ``cannot_check`` are states the
    real function emits; an unrecognised ``state`` (for the defensive
    ValueError path, which the real function can never emit) is applied as
    a targeted mutation of its real output, not a from-scratch dict."""
    rows = [] if state == "not_in_build" else [dict(_IC_ROW)]
    fixes = IC.open_fixes(
        [dict(_IC_EVENT)], rows,
        error=(error or "ClickHouse timeout") if state == "cannot_check" else None)
    fix = fixes[0]
    if state not in M.IC_FIX_STATES:
        fix = dict(fix, state=state)
    return fix


def _ic_period(code="FY2025 P07", ended=True, since=None):
    facts = {"code": code, "ended": ended}
    if since is not None:
        facts["since"] = since
    return facts


def test_ic_fix_item_shape_for_the_allowed_entity_only():
    per = {IC_P07: _ic_period()}
    items = M.ic_fix_items({IC_P07: [_ic_fix()]}, per, {"UK01"})
    assert len(items) == 1
    item = items[0]
    assert item["action"] == {"screen": "trial-balances", "entity": "UK01"}
    assert item["owner"] == "Entity Accountant"
    assert item["period"]["fiscal_year"] == 2025
    assert item["period"]["fiscal_period"] == 7
    assert item["period"]["code"] == "FY2025 P07"


def test_ic_fix_item_kind_follows_whether_the_period_has_ended():
    fix = _ic_fix()
    blocking = M.ic_fix_items({IC_P07: [fix]}, {IC_P07: _ic_period(ended=True)}, {"UK01"})
    assert blocking[0]["kind"] == "blocking"
    todo = M.ic_fix_items({IC_P07: [fix]}, {IC_P07: _ic_period(ended=False)}, {"UK01"})
    assert todo[0]["kind"] == "todo"


def test_ic_fix_item_never_writes_the_partner_balance():
    # W3-2: the entity sees its own balance and the difference, never the partner's.
    per = {IC_P07: _ic_period()}
    fix = _ic_fix()
    group = fix["groups"][0]
    items = M.ic_fix_items({IC_P07: [fix]}, per, {"UK01"})
    text = items[0]["title"] + " " + items[0]["detail"]
    assert str(group["balance_a"]) in text
    assert str(group["difference"]) in text
    assert str(group["balance_b"]) not in text
    assert group["consolidation_group"] in text


def test_ic_fix_items_one_per_allowed_entity_both_sides_when_allowed_is_none():
    # R1: with no scope restriction, both entities of the pair get their own item.
    per = {IC_P07: _ic_period()}
    items = M.ic_fix_items({IC_P07: [_ic_fix()]}, per, None)
    entities = {i["action"]["entity"] for i in items}
    assert entities == {"UK01", "DE01"}
    assert len({i["id"] for i in items}) == 2


def test_ic_fix_items_empty_when_neither_entity_is_allowed():
    per = {IC_P07: _ic_period()}
    assert M.ic_fix_items({IC_P07: [_ic_fix()]}, per, {"FR01"}) == []


def test_ic_fix_items_not_in_build_and_cannot_check_sentences():
    per = {IC_P07: _ic_period()}
    nib = M.ic_fix_items({IC_P07: [_ic_fix(state="not_in_build")]}, per, {"UK01"})
    assert nib[0]["detail"].endswith(
        "The pair is not in the last build; this stays until it is within tolerance "
        "or the period closes.")

    cc = M.ic_fix_items({IC_P07: [_ic_fix(state="cannot_check", error="ClickHouse timeout")]},
                        per, {"UK01"})
    assert "ClickHouse timeout" in cc[0]["detail"]
    assert cc[0]["detail"].endswith(
        "Intercompany could not be checked (ClickHouse timeout); this stays until it can.")


def test_ic_fix_item_detail_carries_the_send_back_reason():
    per = {IC_P07: _ic_period()}
    fix = _ic_fix()
    item = M.ic_fix_items({IC_P07: [fix]}, per, {"UK01"})[0]
    assert item["detail"].startswith("Sent back by alice@example.com on 2025-07-15: "
                                     "Please review the booking.")


def test_ic_fix_items_unknown_state_raises():
    per = {IC_P07: _ic_period()}
    with pytest.raises(ValueError, match="cleared"):
        M.ic_fix_items({IC_P07: [_ic_fix(state="cleared")]}, per, {"UK01"})


def test_ic_fix_items_skips_a_key_not_in_per_period():
    # A fix for a period not among the caller's open periods is not mine to show.
    items = M.ic_fix_items({IC_P08: [_ic_fix()]}, {IC_P07: _ic_period()}, None)
    assert items == []


def test_ic_fix_items_no_fixes_gives_no_items():
    assert M.ic_fix_items({}, {IC_P07: _ic_period()}, None) == []
    assert M.ic_fix_items({IC_P07: []}, {IC_P07: _ic_period()}, None) == []


# --- A22: the preparer's "sent back" My work item -----------------------------
# A21's ``approvals_api.sent_back_for`` row shape:
# {"doctype", "name", "kind_label", "title", "fiscal_year", "fiscal_period",
#  "rejection": {"reason", "actor", "at"}}.

def _sb_row(doctype, name, kind_label, title, fiscal_year=None, fiscal_period=None,
           actor="alice@example.com", at="2025-08-20T10:00:00+01:00",
           reason="Fix the amount."):
    return {
        "doctype": doctype, "name": name, "kind_label": kind_label, "title": title,
        "fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
        "rejection": {"reason": reason, "actor": actor, "at": at},
    }


def test_sent_back_items_journal_routes_through_its_period():
    row = _sb_row("Consolidation Journal", "CJ-00001", "Adjustment · CJ-00001",
                 "Accrual reversal", fiscal_year=2025, fiscal_period=7)
    items = M.sent_back_items([row], M.CLOSE_LEAD, {(2025, 7): "P07"})
    assert len(items) == 1
    item = items[0]
    assert item["id"] == "sent-back:Consolidation Journal:CJ-00001"
    assert item["kind"] == "todo"
    assert item["title"] == "Sent back: Adjustment · CJ-00001 · Accrual reversal"
    assert item["detail"] == "alice@example.com on 2025-08-20: Fix the amount."
    assert item["owner"] == M.OWNERS[M.CLOSE_LEAD]
    assert item["action"] == {"screen": "adjustments"}
    assert item["period"] == {"fiscal_year": 2025, "fiscal_period": 7, "code": "P07",
                              "since": "2025-08-20"}
    assert "since" not in item and "since_reason" not in item


def test_sent_back_items_her_has_no_period():
    row = _sb_row("Historical Equity Rate", "HER-00001", "Historical equity rate",
                 "UK01 2024-12-31")
    item = M.sent_back_items([row], M.GROUP_ACCOUNTANT, {})[0]
    assert "period" not in item
    assert item["since"] == "2025-08-20"
    assert item["since_reason"] == "sent back"
    assert item["action"] == {"screen": "rates"}


def test_sent_back_items_ger_and_op_route_to_rates():
    ger = _sb_row("Group Exchange Rate", "GER-1", "Group rate · USD→EUR Closing",
                 "1.1000", fiscal_year=2025, fiscal_period=7)
    op = _sb_row("Ownership Period", "OP-1", "Ownership period", "UK01 100% from 2024-01-01")
    items = M.sent_back_items([ger, op], M.ENTITY_ACCOUNTANT, {(2025, 7): "P07"})
    assert items[0]["action"] == {"screen": "rates"}
    assert items[1]["action"] == {"screen": "rates"}
    assert "period" in items[0] and "period" not in items[1]


def test_sent_back_items_desk_actions_for_ic_bc_bd():
    ic = _sb_row("IC Balance", "ICB-1", "IC balance · UK01 → DE01", "IC balance · UK01 → DE01",
                fiscal_year=2025, fiscal_period=7)
    bc = _sb_row("Business Combination", "BC-1", "Business combination · UK01",
                "Business combination · UK01")
    bd = _sb_row("Business Disposal", "BD-1", "Business disposal · UK01",
                "Business disposal · UK01")
    items = M.sent_back_items([ic, bc, bd], M.CLOSE_LEAD, {(2025, 7): "P07"})
    assert items[0]["action"] == {"desk": "/app/ic-balance/ICB-1"}
    assert items[1]["action"] == {"desk": "/app/business-combination/BC-1"}
    assert items[2]["action"] == {"desk": "/app/business-disposal/BD-1"}


def test_sent_back_items_viewer_raises():
    row = _sb_row("Historical Equity Rate", "HER-00001", "Historical equity rate", "x")
    with pytest.raises(ValueError, match="[Vv]iewer"):
        M.sent_back_items([row], M.VIEWER, {})


def test_sent_back_items_period_missing_from_codes_raises():
    row = _sb_row("Consolidation Journal", "CJ-00001", "Adjustment · CJ-00001", "x",
                 fiscal_year=2025, fiscal_period=7)
    with pytest.raises(ValueError, match="2025"):
        M.sent_back_items([row], M.CLOSE_LEAD, {})


def test_sent_back_items_empty_rows_gives_no_items():
    assert M.sent_back_items([], M.CLOSE_LEAD, {}) == []


# --- #305-W5-1 (story 9.4, #157): the preparer's "sent back" sign-off item ------
#
# The events are the REAL producers' output: ``signoff_rejected`` from
# ``assertion_run.reject_signoff`` and ``signed_off`` from ``sign_off_close``
# (both run against their stub frappe in test_assertion_run_reject.py /
# test_assertion_warn_amber.py). Only ``name`` and ``at`` are added: the
# database assigns them, and mywork_api reads them back as ISO text.

_REJ_SPEC = importlib.util.spec_from_file_location(
    "assertion_run_reject_for_mywork", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                    "test_assertion_run_reject.py"))
REJ = importlib.util.module_from_spec(_REJ_SPEC)
_REJ_SPEC.loader.exec_module(REJ)

PREPARER = REJ.PREPARER
CODES = {(2099, 1): "P01", (2099, 2): "P02"}


def _stored(event, name, at, fiscal_period=None):
    out = {k: v for k, v in event.items() if k != "saved_before"}
    out.update(name=name, at=at)
    if fiscal_period is not None:
        out["fiscal_period"] = fiscal_period
    return out


def _rejected_event(name="CE-2", at="2099-02-03T10:00:00+00:00", reason="ZZA's TB is the draft",
                    fiscal_period=None):
    reject, frappe, _doc = REJ.load()
    reject("AR-1", reason)
    return _stored(frappe.events[0], name, at, fiscal_period)


def _signed_event(name="CE-1", at="2099-02-02T09:00:00+00:00", fiscal_period=None):
    module, frappe, _doc, _ = REJ._amber._load(status="Green", warned=0)
    module.sign_off_close("AR-1")
    event = dict(frappe.events[0], actor="acct@example.com")
    return _stored(event, name, at, fiscal_period)


def test_the_real_producers_give_the_two_kinds():
    assert _rejected_event()["kind"] == "signoff_rejected"
    assert _signed_event()["kind"] == "signed_off"


def test_a_rejected_signoff_gives_the_preparer_one_todo_item_on_checks():
    events = [_signed_event(), _rejected_event()]
    (item,) = M.signoff_sent_back_items(events, PREPARER, M.GROUP_ACCOUNTANT, CODES)
    assert item == {
        "id": "sent-back:signoff:2099-01",
        "kind": "todo",
        "title": "Sent back: sign-off · P01",
        "detail": "acct@example.com on 2099-02-03: ZZA's TB is the draft. Fix it and run the "
                  "checks again; the item stays until the period is signed.",
        "owner": "EPM Analyst",
        "action": {"screen": "checks"},
        "period": {"fiscal_year": 2099, "fiscal_period": 1, "code": "P01",
                   "since": "2099-02-03"},
    }


def test_someone_else_gets_no_item():
    events = [_signed_event(), _rejected_event()]
    assert M.signoff_sent_back_items(events, "other@example.com", M.CLOSE_LEAD, CODES) == []


def test_a_signature_after_the_reject_clears_the_item():
    events = [_signed_event("CE-1"), _rejected_event("CE-2"),
              _signed_event("CE-3", at="2099-02-04T09:00:00+00:00")]
    assert M.signoff_sent_back_items(events, PREPARER, M.CLOSE_LEAD, CODES) == []


def test_each_period_is_judged_on_its_own():
    events = [_rejected_event("CE-1"),
              _rejected_event("CE-2", fiscal_period=2),
              _signed_event("CE-3", at="2099-02-05T09:00:00+00:00", fiscal_period=2)]
    items = M.signoff_sent_back_items(events, PREPARER, M.CLOSE_LEAD, CODES)
    assert [i["id"] for i in items] == ["sent-back:signoff:2099-01"]


def test_the_latest_reject_gives_the_reason():
    events = [_rejected_event("CE-1", at="2099-02-03T10:00:00+00:00", reason="first"),
              _rejected_event("CE-2", at="2099-02-04T10:00:00+00:00", reason="second")]
    (item,) = M.signoff_sent_back_items(events, PREPARER, M.CLOSE_LEAD, CODES)
    assert "second" in item["detail"] and "first" not in item["detail"]


def test_the_same_time_is_ordered_by_name():
    at = "2099-02-03T10:00:00+00:00"
    events = [_rejected_event("CE-2", at=at), _signed_event("CE-1", at=at)]
    assert len(M.signoff_sent_back_items(events, PREPARER, M.CLOSE_LEAD, CODES)) == 1


def test_a_reject_with_no_preparer_is_nobodys_item():
    event = _rejected_event()
    event["detail"] = dict(event["detail"], preparer=None)
    assert M.signoff_sent_back_items([event], PREPARER, M.CLOSE_LEAD, CODES) == []


def test_the_viewer_and_an_unknown_persona_raise():
    with pytest.raises(ValueError):
        M.signoff_sent_back_items([_rejected_event()], PREPARER, M.VIEWER, CODES)
    with pytest.raises(ValueError):
        M.signoff_sent_back_items([_rejected_event()], PREPARER, "auditor", CODES)


def test_a_period_missing_from_the_codes_raises():
    with pytest.raises(ValueError):
        M.signoff_sent_back_items([_rejected_event()], PREPARER, M.CLOSE_LEAD, {})


def test_an_unknown_kind_raises():
    event = dict(_rejected_event(), kind="signoff_voided")
    with pytest.raises(ValueError):
        M.signoff_sent_back_items([event], PREPARER, M.CLOSE_LEAD, CODES)
