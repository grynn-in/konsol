"""konsol#305 story 5.4 (#305-W5-4): ic_balance_model, pure.

``konsol/close/ic_balance_model.py`` is pure like ic_model.py: loaded by
path, no frappe or konsol import.

Decision #305-W5-4 (Deepak, 6 Oct 2026): an Analyst may draft an IC Balance;
an IC Balance (draft or approved) whose entity pair no unrealised-profit IC
Elimination Rule matches is a setup gap that blocks sign-off. Rejected:
refusing the balance at save. The rule stays configured in Desk.

The rule match mirrors konsolidat ``gold_ic_eliminations.sql``
``unrealized_profit_eliminations`` (the WHERE at its end): ``rule_type =
'unrealized_profit'``, ``margin_pct > 0``, and each entity pattern is ``'*'``
or the exact entity code (debit pattern = selling entity, credit pattern =
buying entity).
"""
import ast
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PY = os.path.join(APP_DIR, "close", "ic_balance_model.py")

_spec = importlib.util.spec_from_file_location("ic_balance_model_under_test", MODEL_PY)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _raises(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type as e:
        return e
    raise AssertionError("%s not raised" % exc_type.__name__)


def _rule(rule_id="R1", debit="*", credit="*", rule_type="unrealized_profit", margin="20",
          name=None):
    return {"rule_id": rule_id, "rule_name": name or rule_id, "rule_type": rule_type,
            "margin_pct": margin, "debit_entity_pattern": debit, "credit_entity_pattern": credit}


def _bal(name="ICB-1", sell="UK01", buy="DE01", docstatus=0, sales="1000", inventory="250",
         fy=2025, fp=7):
    return {"name": name, "selling_entity": sell, "buying_entity": buy, "fiscal_year": fy,
            "fiscal_period": fp, "ic_sales_amount": sales,
            "ending_inventory_from_ic": inventory, "docstatus": docstatus}


def test_module_is_pure():
    with open(MODEL_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in ("frappe", "konsol"), alias.name
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("frappe", "konsol"), node.module


# --- the rule match (mirrors gold_ic_eliminations.sql) --------------------------

def test_wildcard_rule_matches_any_pair():
    assert [r["rule_id"] for r in M.matching_rules(_bal(), [_rule()])] == ["R1"]


def test_exact_patterns_match_only_their_pair():
    rules = [_rule("R1", debit="UK01", credit="DE01"), _rule("R2", debit="DE01", credit="UK01")]
    assert [r["rule_id"] for r in M.matching_rules(_bal(sell="UK01", buy="DE01"), rules)] == ["R1"]
    assert [r["rule_id"] for r in M.matching_rules(_bal(sell="DE01", buy="UK01"), rules)] == ["R2"]
    assert M.matching_rules(_bal(sell="UK01", buy="FR01"), rules) == []


def test_debit_pattern_is_the_seller_credit_pattern_the_buyer():
    # Swapped direction never matches: dbt's debit_entity = selling_entity.
    assert M.matching_rules(_bal(sell="UK01", buy="DE01"), [_rule(debit="DE01", credit="UK01")]) == []


def test_half_wildcard():
    assert M.matching_rules(_bal(sell="UK01", buy="DE01"), [_rule(debit="UK01", credit="*")])
    assert M.matching_rules(_bal(sell="UK01", buy="DE01"), [_rule(debit="*", credit="DE01")])
    assert not M.matching_rules(_bal(sell="UK01", buy="DE01"), [_rule(debit="*", credit="FR01")])


def test_a_balance_rule_never_matches():
    assert M.matching_rules(_bal(), [_rule(rule_type="balance")]) == []


def test_zero_blank_or_negative_margin_never_matches():
    for margin in ("0", 0, None, "", "-5"):
        assert M.matching_rules(_bal(), [_rule(margin=margin)]) == [], margin


def test_blank_pattern_is_not_a_wildcard():
    # dbt compares '' with the entity code: a blank pattern matches nothing.
    assert M.matching_rules(_bal(), [_rule(debit="", credit="*")]) == []
    assert M.matching_rules(_bal(), [_rule(debit=None, credit="*")]) == []


# --- the setup gap ---------------------------------------------------------------

def test_no_balances_no_gap():
    assert M.rule_gap([], []) is None


def test_every_pair_covered_no_gap():
    assert M.rule_gap([_bal(), _bal("ICB-2", docstatus=1)], [_rule()]) is None


def test_draft_and_approved_both_count():
    for docstatus in (0, 1):
        gap = M.rule_gap([_bal(docstatus=docstatus)], [])
        assert gap["code"] == M.RULE_UNDECLARED


def test_cancelled_balance_raises():
    # The reader never passes a cancelled balance; one arriving is a reader bug.
    e = _raises(ValueError, M.rule_gap, [_bal(docstatus=2)], [])
    assert "ICB-1" in str(e)


def test_gap_names_each_pair_once_and_its_entities():
    balances = [_bal("A", "UK01", "DE01"), _bal("B", "UK01", "DE01", fp=8),
                _bal("C", "FR01", "DE01"), _bal("D", "UK01", "FR01")]
    rules = [_rule(debit="UK01", credit="FR01")]
    gap = M.rule_gap(balances, rules)
    assert gap["pairs"] == [{"selling_entity": "FR01", "buying_entity": "DE01"},
                            {"selling_entity": "UK01", "buying_entity": "DE01"}]
    assert gap["entities"] == ["DE01", "FR01", "UK01"]
    msg = gap["message"]
    assert "FR01 → DE01" in msg and "UK01 → DE01" in msg
    assert "UK01 → FR01" not in msg
    assert "2 IC Balance pairs" in msg
    assert "Desk" in msg and "margin" in msg


def test_gap_message_singular():
    gap = M.rule_gap([_bal()], [])
    assert "1 IC Balance pair:" in gap["message"]


def test_a_zero_margin_rule_is_still_a_gap():
    gap = M.rule_gap([_bal()], [_rule(margin="0")])
    assert gap["pairs"] == [{"selling_entity": "UK01", "buying_entity": "DE01"}]


# --- the screen rows ---------------------------------------------------------------

def test_rows_carry_status_amounts_and_matched_rule_margin():
    rows = M.balance_rows([_bal("A", docstatus=0), _bal("B", "FR01", "DE01", docstatus=1)],
                          [_rule("R1", debit="UK01", credit="*", margin="12.5", name="UK margin")])
    a, b = sorted(rows, key=lambda r: r["name"])
    assert a["name"] == "A" and a["status"] == "Draft"
    assert a["ic_sales_amount"] == 1000.0 and a["ending_inventory_from_ic"] == 250.0
    assert a["rules"] == [{"rule_id": "R1", "rule_name": "UK margin", "margin_pct": 12.5}]
    assert a["missing_rule"] is False
    assert b["status"] == "Approved" and b["rules"] == [] and b["missing_rule"] is True


def test_rows_unknown_docstatus_raises():
    _raises(ValueError, M.balance_rows, [_bal(docstatus=2)], [])


def test_rows_ordered_by_pair_then_name():
    rows = M.balance_rows([_bal("Z", "UK01", "DE01"), _bal("A", "FR01", "DE01")], [])
    assert [r["name"] for r in rows] == ["A", "Z"]


# --- entity scope -------------------------------------------------------------------

def test_mask_unrestricted_shows_all():
    balances = [_bal("A"), _bal("B", "FR01", "ES01")]
    assert M.visible(balances, None) == (balances, 0)


def test_mask_keeps_a_balance_with_either_entity_allowed():
    balances = [_bal("A", "UK01", "DE01"), _bal("B", "FR01", "UK01"), _bal("C", "FR01", "ES01")]
    shown, hidden = M.visible(balances, {"UK01"})
    assert [b["name"] for b in shown] == ["A", "B"]
    assert hidden == 1


def test_mask_empty_scope_hides_everything():
    shown, hidden = M.visible([_bal()], set())
    assert shown == [] and hidden == 1


# --- a draft's own problems (refused before any write) ------------------------------

def test_draft_problems_clean():
    assert M.draft_problems("UK01", "DE01", "1000", "250", {"UK01", "DE01"}) == []


def test_draft_problems_blank_same_unknown():
    assert any("selling entity" in p for p in M.draft_problems("", "DE01", 1, 1, {"DE01"}))
    assert any("same entity" in p for p in M.draft_problems("UK01", "UK01", 1, 1, {"UK01"}))
    probs = M.draft_problems("UK01", "XX99", 1, 1, {"UK01"})
    assert any("XX99" in p and "not an entity" in p for p in probs)


def test_draft_problems_amounts():
    probs = M.draft_problems("UK01", "DE01", "abc", None, {"UK01", "DE01"})
    assert any("IC sales amount" in p for p in probs)
    assert any("ending inventory" in p for p in probs)
    probs = M.draft_problems("UK01", "DE01", "-1", "-2", {"UK01", "DE01"})
    assert any("IC sales amount" in p and "negative" in p for p in probs)
    assert any("ending inventory" in p and "negative" in p for p in probs)


def test_draft_problems_inventory_above_sales_is_allowed():
    # Ending inventory can carry earlier periods' purchases: no cross-check.
    assert M.draft_problems("UK01", "DE01", "10", "500", {"UK01", "DE01"}) == []


def test_draft_problems_refuse_non_finite_amounts():
    # F51b / review S7: float("nan") < 0 is False, so "nan" passed the old check.
    for text in ("nan", "NaN", "inf", "-inf", "Infinity", float("nan"), float("inf")):
        probs = M.draft_problems("UK01", "DE01", text, text, {"UK01", "DE01"})
        assert any("IC sales amount" in p and "must be a number" in p for p in probs), text
        assert any("ending inventory" in p and "must be a number" in p for p in probs), text
