"""Business Combination arithmetic (konsol/business_combination_model.py, konsolidat#198):
the IFRS 3 mechanics of one acquisition of control. Consideration is translated to
the group currency at the acquisition period's closing rate, the acquired net
assets are stepped up by the fair value adjustments, the sign of the difference
decides between goodwill and a bargain purchase, NCI follows the policy's
measurement, and acquisition costs are expensed or join the consideration.
The acquired balance sheet is complete (assets, liabilities AND equity, Dr
positive / Cr negative, so it sums to zero): the non-equity lines are the net
assets acquired, the equity lines are what the consolidation eliminates.
Every rule that refuses a deal is a sentence, so the controller throws it."""
from decimal import Decimal

import konsol.business_combination_model as M

GROUP_CCY = "ZZG"
OTHER_CCY = "ZZO"

IFRS_PARTIAL = {
    "accounting_framework": "IFRS",
    "goodwill_method": "partial",
    "goodwill_treatment": "Impairment only",
    "acquisition_costs_treatment": "Expense",
    "measurement_period": "12 months",
    "bargain_purchase": "Recognise gain",
    "goodwill_account": "ZZ-1",
    "fair_value_adjustment_account": "ZZ-2",
    "investment_account": "ZZ-3",
    "nci_account": "ZZ-4",
    "bargain_purchase_gain_account": "ZZ-5",
    "acquisition_costs_account": "ZZ-9",
}
US_GAAP_FULL = dict(IFRS_PARTIAL, accounting_framework="US GAAP", goodwill_method="full")
CAPITALISE = dict(IFRS_PARTIAL, acquisition_costs_treatment="Capitalise")


def header(**over):
    base = {"share_acquired_pct": 100, "consideration_currency": GROUP_CCY}
    base.update(over)
    return base


def cash(amount, currency=GROUP_CCY, component="Cash"):
    return {"component": component, "amount": amount, "currency": currency}


def line(book_amount, fair_value_adjustment=0, main_account="ZZ-BS"):
    return {"main_account": main_account, "book_amount": book_amount,
            "fair_value_adjustment": fair_value_adjustment}


def cost(amount, currency=GROUP_CCY, kind="Legal"):
    return {"kind": kind, "amount": amount, "currency": currency}


EQUITY_ACCOUNT = "ZZ-EQ"


def is_equity(main_account):
    """The controller answers this from the chart; here one test account is equity."""
    return main_account == EQUITY_ACCOUNT


def equity(book_amount, fair_value_adjustment=0):
    return line(book_amount, fair_value_adjustment, main_account=EQUITY_ACCOUNT)


# The design's worked example: 8,300 paid for 100%, book net assets 650 (assets
# 1,000 − liabilities 350), fair value adjustments 930 → net assets at fair value
# 1,580, goodwill 6,720. The balance sheet is complete: the equity line of −650
# makes it sum to zero, and it is what the consolidation eliminates.
WORKED_CONSIDERATION = [cash(8300)]
WORKED_BALANCES = [line(1000, 900), line(-350, 30), equity(-650)]
# P4's incomplete version: assets and liabilities only, no equity line.
BALANCES_WITHOUT_EQUITY = [line(1000, 900), line(-350, 30)]

RATES = {GROUP_CCY: 1.0, OTHER_CCY: 0.25}


def rate_to_group(currency):
    return RATES[currency]


def totals(hdr, consideration, balances, costs, policy, rate=rate_to_group, equity_rule=is_equity):
    return M.totals(hdr, consideration, balances, costs, policy, rate, equity_rule)


def facts(**over):
    base = {"has_tb_at_or_before": True, "rate_to_group": rate_to_group,
            "is_published_leaf": lambda account: True, "is_equity": is_equity}
    base.update(over)
    return base


def test_module_docstring_names_the_mechanics_and_the_issue():
    doc = M.__doc__
    assert "konsolidat#198" in doc
    for word in ("goodwill", "bargain", "NCI", "Decimal"):
        assert word in doc, word


def test_module_docstring_says_why_the_equity_lines_are_required():
    doc = M.__doc__.lower()
    assert "equity" in doc
    assert "sum" in doc and "zero" in doc, "the docstring must say a complete balance sheet sums to zero"
    assert "eliminat" in doc, "the docstring must say the equity lines are what is eliminated"


def test_the_worked_example_measures_goodwill_of_6720():
    t = totals(header(), WORKED_CONSIDERATION, WORKED_BALANCES, [], IFRS_PARTIAL)
    assert t["total_consideration"] == Decimal("8300.00")
    assert t["net_assets_acquired"] == Decimal("650.00")
    assert t["equity_eliminated"] == Decimal("650.00")
    assert t["fair_value_adjustments"] == Decimal("930.00")
    assert t["net_assets_at_fair_value"] == Decimal("1580.00")
    assert t["goodwill"] == Decimal("6720.00")
    assert t["bargain_purchase_gain"] == Decimal("0.00")
    assert t["nci_at_acquisition"] == Decimal("0.00")
    assert t["costs_total"] == Decimal("0.00")
    assert t["costs_expensed"] == Decimal("0.00")
    assert t["consideration_including_costs"] == Decimal("8300.00")


def test_equity_lines_are_eliminated_not_acquired():
    # A complete balance sheet sums to zero; only the non-equity lines are net assets.
    assert sum(row["book_amount"] for row in WORKED_BALANCES) == 0
    t = totals(header(), WORKED_CONSIDERATION, WORKED_BALANCES, [], IFRS_PARTIAL)
    assert t["net_assets_acquired"] == Decimal("650.00")
    assert t["equity_eliminated"] == Decimal("650.00")
    # two equity accounts (share capital, retained earnings) eliminate the same 650
    split = [line(1000, 900), line(-350, 30), equity(-100), equity(-550)]
    t = totals(header(), WORKED_CONSIDERATION, split, [], IFRS_PARTIAL)
    assert t["net_assets_acquired"] == Decimal("650.00")
    assert t["equity_eliminated"] == Decimal("650.00")
    assert t["goodwill"] == Decimal("6720.00")
    # the callback decides which lines are equity: with none, everything is a net asset
    t = totals(header(), WORKED_CONSIDERATION, WORKED_BALANCES, [], IFRS_PARTIAL,
               equity_rule=lambda account: False)
    assert t["net_assets_acquired"] == Decimal("0.00")
    assert t["equity_eliminated"] == Decimal("0.00")


def test_every_total_is_a_decimal_quantized_to_cents():
    t = totals(header(share_acquired_pct=80), [cash(8300.555)], [line(650.333, 930.1), equity(-650.333)],
               [cost(10.005)], IFRS_PARTIAL)
    for key, value in t.items():
        assert isinstance(value, Decimal), key
        assert value == value.quantize(Decimal("0.01")), (key, value)
    assert t["total_consideration"] == Decimal("8300.56")
    assert t["costs_total"] == Decimal("10.01")
    assert t["equity_eliminated"] == Decimal("650.33")


def test_partial_nci_is_its_share_of_net_assets_at_fair_value():
    t = totals(header(share_acquired_pct=80), WORKED_CONSIDERATION, WORKED_BALANCES, [], IFRS_PARTIAL)
    # goodwill = 8,300 − 1,580 × 80% = 8,300 − 1,264
    assert t["goodwill"] == Decimal("7036.00")
    assert t["nci_at_acquisition"] == Decimal("316.00")  # 20% of 1,580
    assert t["bargain_purchase_gain"] == Decimal("0.00")


def test_full_nci_is_measured_at_the_fair_value_the_price_implies():
    t = totals(header(share_acquired_pct=80), WORKED_CONSIDERATION, WORKED_BALANCES, [], US_GAAP_FULL)
    # 8,300 for 80% values the whole at 10,375; the 20% NCI is 2,075. Under the
    # full method goodwill is the whole business's: consideration + NCI at fair
    # value − net assets at fair value = 8,300 + 2,075 − 1,580 = 8,795 (IFRS 3.32),
    # against 7,036 under the partial method.
    assert t["nci_at_acquisition"] == Decimal("2075.00")
    assert t["goodwill"] == Decimal("8795.00")


def test_nci_is_not_guessed_when_the_policy_has_no_measurement():
    t = totals(header(share_acquired_pct=80), WORKED_CONSIDERATION, WORKED_BALANCES, [],
               dict(IFRS_PARTIAL, goodwill_method=None))
    assert t["nci_at_acquisition"] == Decimal("0.00")
    assert t["goodwill"] == Decimal("7036.00")


def test_a_bargain_purchase_is_a_gain_and_no_goodwill():
    t = totals(header(), [cash(1000)], WORKED_BALANCES, [], IFRS_PARTIAL)
    assert t["goodwill"] == Decimal("0.00")
    assert t["bargain_purchase_gain"] == Decimal("580.00")


def test_expensed_costs_stay_outside_goodwill():
    t = totals(header(), WORKED_CONSIDERATION, WORKED_BALANCES, [cost(200), cost(50, kind="Advisory")],
               IFRS_PARTIAL)
    assert t["costs_total"] == Decimal("250.00")
    assert t["costs_expensed"] == Decimal("250.00")
    assert t["consideration_including_costs"] == Decimal("8300.00")
    assert t["goodwill"] == Decimal("6720.00")


def test_capitalised_costs_join_the_consideration_and_the_goodwill():
    t = totals(header(), WORKED_CONSIDERATION, WORKED_BALANCES, [cost(200), cost(50, kind="Advisory")],
               CAPITALISE)
    assert t["costs_total"] == Decimal("250.00")
    assert t["costs_expensed"] == Decimal("0.00")
    assert t["total_consideration"] == Decimal("8300.00")
    assert t["consideration_including_costs"] == Decimal("8550.00")
    assert t["goodwill"] == Decimal("6970.00")


def test_a_line_in_another_currency_is_translated_by_the_callback():
    seen = []

    def spy(currency):
        seen.append(currency)
        return RATES[currency]

    consideration = [cash(8000), cash(1200, currency=OTHER_CCY, component="Deferred")]
    t = totals(header(), consideration, WORKED_BALANCES, [cost(400, currency=OTHER_CCY)],
               IFRS_PARTIAL, rate=spy)
    assert t["total_consideration"] == Decimal("8300.00")  # 8,000 + 1,200 × 0.25
    assert t["costs_total"] == Decimal("100.00")
    assert OTHER_CCY in seen


def test_a_line_without_a_currency_uses_the_header_s():
    consideration = [{"component": "Cash", "amount": 1200, "currency": None}]
    t = totals(header(consideration_currency=OTHER_CCY), consideration, WORKED_BALANCES, [], IFRS_PARTIAL)
    assert t["total_consideration"] == Decimal("300.00")


def test_a_currency_without_a_rate_is_refused_by_name():
    try:
        totals(header(), [cash(10, currency="ZZX")], [], [], IFRS_PARTIAL, rate=lambda currency: None)
    except ValueError as exc:
        assert "ZZX" in str(exc)
    else:
        raise AssertionError("a missing rate must not pass as 1.0")


def test_the_worked_example_has_no_problems():
    assert M.problems(header(), WORKED_CONSIDERATION, WORKED_BALANCES, [], IFRS_PARTIAL, facts()) == []
    assert M.problems(header(share_acquired_pct=80), WORKED_CONSIDERATION, WORKED_BALANCES, [cost(200)],
                      US_GAAP_FULL, facts()) == []


def test_no_consideration_line_is_a_problem():
    problems = M.problems(header(), [], WORKED_BALANCES, [], IFRS_PARTIAL, facts())
    assert len(problems) == 1
    assert "Consideration" in problems[0] and "at least one" in problems[0]


def test_share_must_be_above_zero_and_at_most_100():
    for share in (0, -5, 100.01, 150, None, ""):
        problems = M.problems(header(share_acquired_pct=share), WORKED_CONSIDERATION, WORKED_BALANCES, [],
                              IFRS_PARTIAL, facts())
        assert len(problems) == 1, (share, problems)
        assert "Share Acquired" in problems[0], problems[0]
    assert M.problems(header(share_acquired_pct=100), WORKED_CONSIDERATION, WORKED_BALANCES, [],
                      IFRS_PARTIAL, facts()) == []


def test_a_consideration_or_cost_line_needs_a_positive_amount():
    consideration = [cash(8300), cash(0, component="Deferred")]
    problems = M.problems(header(), consideration, WORKED_BALANCES, [], IFRS_PARTIAL, facts())
    assert len(problems) == 1
    assert "Consideration" in problems[0] and "2" in problems[0] and "Deferred" in problems[0]

    problems = M.problems(header(), WORKED_CONSIDERATION, WORKED_BALANCES, [cost(-10, kind="Advisory")],
                          IFRS_PARTIAL, facts())
    assert len(problems) == 1
    assert "Cost" in problems[0] and "Advisory" in problems[0] and "above 0" in problems[0]


def test_the_acquired_balance_sheet_is_required_without_an_earlier_trial_balance():
    problems = M.problems(header(), WORKED_CONSIDERATION, [], [], IFRS_PARTIAL,
                          facts(has_tb_at_or_before=False))
    assert problems == [
        "Acquired Balance Sheet is required: the entity has no trial balance at or before the acquisition date"]
    # with a trial balance the model measures the net assets later; no lines is fine
    assert M.problems(header(), WORKED_CONSIDERATION, [], [], IFRS_PARTIAL, facts(has_tb_at_or_before=True)) == []


def test_a_balance_sheet_that_does_not_sum_to_zero_is_refused():
    # equity of −600 against net assets of 650: 50 is unexplained
    balances = [line(1000, 900), line(-350, 30), equity(-600)]
    problems = M.problems(header(), WORKED_CONSIDERATION, balances, [], IFRS_PARTIAL, facts())
    assert problems == [
        "Acquired Balance Sheet does not balance (assets − liabilities − equity = 50.00); "
        "it must include the equity lines"]
    # the other way round too
    balances = [line(1000, 900), line(-350, 30), equity(-700)]
    problems = M.problems(header(), WORKED_CONSIDERATION, balances, [], IFRS_PARTIAL, facts())
    assert len(problems) == 1 and "= -50.00" in problems[0], problems
    # a cent of rounding is not a problem
    balances = [line(1000, 900), line(-350, 30), equity(-649.995)]
    assert M.problems(header(), WORKED_CONSIDERATION, balances, [], IFRS_PARTIAL, facts()) == []


def test_a_balance_sheet_without_an_equity_line_is_refused():
    # P4's shape: assets and liabilities only. It neither balances nor has equity.
    problems = M.problems(header(), WORKED_CONSIDERATION, BALANCES_WITHOUT_EQUITY, [], IFRS_PARTIAL, facts())
    assert problems == [
        "Acquired Balance Sheet does not balance (assets − liabilities − equity = 650.00); "
        "it must include the equity lines",
        "Acquired Balance Sheet has no equity line: "
        "the pre-acquisition equity is what the consolidation eliminates"]
    # balanced but with the equity hidden in a non-equity account: still no equity line
    disguised = [line(1000, 900), line(-350, 30), line(-650, main_account="ZZ-NOT-EQ")]
    problems = M.problems(header(), WORKED_CONSIDERATION, disguised, [], IFRS_PARTIAL, facts())
    assert problems == [
        "Acquired Balance Sheet has no equity line: "
        "the pre-acquisition equity is what the consolidation eliminates"]
    # no balances at all (a trial balance exists): neither sentence applies
    assert M.problems(header(), WORKED_CONSIDERATION, [], [], IFRS_PARTIAL, facts()) == []


def test_problems_need_the_equity_rule_when_balances_are_given():
    no_rule = facts()
    del no_rule["is_equity"]
    try:
        M.problems(header(), WORKED_CONSIDERATION, WORKED_BALANCES, [], IFRS_PARTIAL, no_rule)
    except ValueError as exc:
        assert "is_equity" in str(exc)
    else:
        raise AssertionError("without is_equity the equity lines cannot be told apart")
    # without balances there is nothing to classify
    assert M.problems(header(), WORKED_CONSIDERATION, [], [], IFRS_PARTIAL, no_rule) == []


def test_a_bargain_purchase_is_refused_when_the_policy_says_so():
    refuse = dict(IFRS_PARTIAL, bargain_purchase="Refuse")
    problems = M.problems(header(), [cash(1000)], WORKED_BALANCES, [], refuse, facts())
    assert len(problems) == 1
    assert "bargain purchase" in problems[0].lower() and "580.00" in problems[0]
    assert "Refuse" in problems[0]
    assert M.problems(header(), [cash(1000)], WORKED_BALANCES, [], IFRS_PARTIAL, facts()) == []


def test_an_incomplete_policy_is_reported_in_the_policy_s_words():
    policy = dict(IFRS_PARTIAL, goodwill_treatment="")
    problems = M.problems(header(), WORKED_CONSIDERATION, WORKED_BALANCES, [], policy, facts())
    assert problems == [
        "Consolidation Policy: Goodwill Treatment is required once the group has a Business Combination"]


def test_blank_required_accounts_are_reported_for_this_deal():
    policy = dict(IFRS_PARTIAL, nci_account="", acquisition_costs_account="")
    problems = M.problems(header(share_acquired_pct=80), WORKED_CONSIDERATION, WORKED_BALANCES, [cost(200)],
                          policy, facts())
    joined = " ".join(problems)
    assert len(problems) == 2, problems
    assert "Non-controlling Interest Account" in joined and "Acquisition Costs Account" in joined
    # at 100% with no costs neither account is needed
    assert M.problems(header(), WORKED_CONSIDERATION, WORKED_BALANCES, [], policy, facts()) == []


def test_the_bargain_gain_account_is_needed_only_when_the_deal_is_a_bargain():
    policy = dict(IFRS_PARTIAL, bargain_purchase_gain_account="")
    assert M.problems(header(), WORKED_CONSIDERATION, WORKED_BALANCES, [], policy, facts()) == []
    problems = M.problems(header(), [cash(1000)], WORKED_BALANCES, [], policy, facts())
    assert len(problems) == 1 and "Bargain Purchase Gain Account" in problems[0]


def test_an_account_that_is_not_a_published_leaf_is_refused():
    problems = M.problems(header(), WORKED_CONSIDERATION, WORKED_BALANCES, [], IFRS_PARTIAL,
                          facts(is_published_leaf=lambda account: account != "ZZ-2"))
    assert len(problems) == 1
    assert "Fair Value Adjustment Account" in problems[0] and '"ZZ-2"' in problems[0]


def test_problems_accept_precomputed_totals_instead_of_a_rate():
    t = totals(header(), [cash(1000)], WORKED_BALANCES, [], IFRS_PARTIAL)
    refuse = dict(IFRS_PARTIAL, bargain_purchase="Refuse")
    problems = M.problems(header(), [cash(1000)], WORKED_BALANCES, [], refuse,
                          {"has_tb_at_or_before": True, "totals": t, "is_equity": is_equity})
    assert len(problems) == 1 and "580.00" in problems[0]


def test_lines_may_be_objects_with_attributes():
    class Row:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    t = totals(Row(**header()), [Row(**cash(8300))], [Row(**l) for l in WORKED_BALANCES], [], IFRS_PARTIAL)
    assert t["goodwill"] == Decimal("6720.00")
    assert t["equity_eliminated"] == Decimal("650.00")
