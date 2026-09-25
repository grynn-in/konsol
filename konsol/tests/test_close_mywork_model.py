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


def _facts(**over):
    facts = {
        "first_close": (2025, 7),
        "chart_published": True,
        "frequency_missing": [],
        "ownership_missing": [],
        "accountants_without_entities": [],
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


def test_every_gap_present_ids_stable_and_ordered():
    facts = _facts(first_close=None, chart_published=False, frequency_missing=["FR01"],
                   ownership_missing=["DE01"], accountants_without_entities=["zz-a@example.com"])
    items = M.setup_gap_items(facts)
    assert [i["id"] for i in items] == [
        "gap:first_close", "gap:chart", "gap:frequency", "gap:ownership", "gap:accountants"]
    # Same input, same output: ids never depend on order or content.
    assert M.setup_gap_items(dict(facts)) == items


def test_desk_actions_point_at_configuration():
    items = _by_id(M.setup_gap_items(_facts(
        first_close=None, chart_published=False, frequency_missing=["FR01"],
        ownership_missing=["DE01"], accountants_without_entities=["zz-a@example.com"])))
    assert items["gap:first_close"]["action"] == {"desk": "/app/close-settings"}
    assert items["gap:chart"]["action"] == {"desk": "/app/main-account"}
    assert items["gap:frequency"]["action"] == {"desk": "/app/entity"}
    assert items["gap:ownership"]["action"] == {"desk": "/app/ownership-period"}
    assert items["gap:accountants"]["action"] == {"desk": "/app/user"}
    assert items["gap:accountants"]["users"] == ["zz-a@example.com"]
    assert items["gap:accountants"]["entities"] == []


def test_every_gap_is_blocking_with_an_owner_role():
    items = M.setup_gap_items(_facts(
        first_close=None, chart_published=False, frequency_missing=["FR01"],
        ownership_missing=["DE01"], accountants_without_entities=["zz-a@example.com"]))
    for item in items:
        assert item["kind"] == "blocking"
        assert item["owner"] in ("EPM Admin", "System Manager")
        assert item["title"] and item["detail"]


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
        assert item["action"]["screen"] in ("my-work", "trial-balances", "checks", "sign-off")
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
    assert _titles(items)["Rates missing (2)"]["action"] == {"screen": "sign-off"}
    assert _titles(items)["Re-sign needed"]["action"] == {"screen": "sign-off"}


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


def test_rank_keeps_setup_gap_items_first_among_blocking():
    # Gap items carry no period; they rank before every period item of their kind.
    gap = M.setup_gap_items(_facts(ownership_missing=["ZZA"]))[0]
    period = M.period_items("entity_accountant", _two_open(), FIRST)
    ranked = M.rank(period + [gap])
    assert ranked[0]["id"] == "gap:ownership"
