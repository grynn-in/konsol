"""Consolidation Policy rules (konsol/consolidation_policy_model.py, konsolidat#198):
the group root configures its framework choices with no defaults, every choice
is required once the group has a Business Combination, the framework
constrains the choices, and each deal names the declared accounts it needs."""
import konsol.consolidation_policy_model as M

IFRS_PARTIAL = {
    "accounting_framework": "IFRS",
    "goodwill_method": "partial",
    "goodwill_treatment": "Impairment only",
    "acquisition_costs_treatment": "Expense",
    "measurement_period": "12 months",
    "bargain_purchase": "Recognise gain",
}
US_GAAP_FULL = dict(IFRS_PARTIAL, accounting_framework="US GAAP", goodwill_method="full")
LOCAL_AMORTISE = dict(
    IFRS_PARTIAL,
    accounting_framework="Local",
    framework_note="National GAAP: goodwill amortised over ten years",
    goodwill_treatment="Amortise",
    goodwill_amortisation_years=10,
    acquisition_costs_treatment="Capitalise",
    bargain_purchase="Refuse",
)

ALL_ACCOUNTS = {field: f"ZZ-{i}" for i, field in enumerate(
    ("goodwill_account", "fair_value_adjustment_account", "investment_account", "nci_account",
     "bargain_purchase_gain_account", "disposal_gain_loss_account", "disposal_proceeds_account",
     "goodwill_amortisation_expense_account", "acquisition_costs_account"), start=1)}


def deal(**over):
    base = {"kind": "acquisition", "share_pct": 100, "goodwill": 6720, "has_costs": False}
    base.update(over)
    return base


def test_options_and_field_lists_are_the_design_s():
    assert M.FRAMEWORKS == ("IFRS", "US GAAP", "Local")
    assert M.NCI == ("partial", "full")
    assert M.GOODWILL_TREATMENTS == ("Impairment only", "Amortise")
    assert M.COST_TREATMENTS == ("Expense", "Capitalise")
    assert M.MEASUREMENT_PERIODS == ("Off", "12 months")
    assert M.BARGAIN == ("Recognise gain", "Refuse")
    assert M.POLICY_FIELDS == (
        "accounting_framework", "framework_note", "goodwill_method", "goodwill_treatment",
        "goodwill_amortisation_years", "acquisition_costs_treatment", "measurement_period",
        "bargain_purchase")
    assert M.ACCOUNT_FIELDS == (
        "goodwill_account", "fair_value_adjustment_account", "investment_account", "nci_account",
        "bargain_purchase_gain_account", "disposal_gain_loss_account", "disposal_proceeds_account",
        "goodwill_amortisation_expense_account", "acquisition_costs_account")
    assert M.LABELS["goodwill_method"] == "NCI Measurement"
    assert set(M.LABELS) == set(M.POLICY_FIELDS) | set(M.ACCOUNT_FIELDS)


def test_the_settlement_account_is_labelled_for_all_deal_cash():
    """`disposal_proceeds_account` (fieldname and warehouse column pinned) is
    where the group settles every deal's cash, not only disposal proceeds, so
    the sentences name it as the Deal Settlement Account."""
    assert M.LABELS["disposal_proceeds_account"] == "Deal Settlement Account"
    group = dict(ALL_ACCOUNTS, disposal_proceeds_account="")
    found = M.account_problems(group, ["disposal_proceeds_account"], lambda a: True)
    assert len(found) == 1 and "Deal Settlement Account is required" in found[0], found
    assert "Disposal Proceeds Account" not in found[0], found


def test_module_docstring_names_the_three_layers():
    doc = M.__doc__.lower()
    assert "konsolidat#198" in M.__doc__
    for layer in ("input", "policy", "mechanic"):
        assert layer in doc


def test_happy_paths_have_no_problems():
    assert M.policy_problems(IFRS_PARTIAL, has_deals=True) == []
    assert M.policy_problems(US_GAAP_FULL, has_deals=True) == []
    assert M.policy_problems(LOCAL_AMORTISE, has_deals=True) == []


def test_an_empty_policy_is_fine_until_the_group_has_a_deal():
    assert M.policy_problems({}, has_deals=False) == []
    assert M.policy_problems({field: None for field in M.POLICY_FIELDS}, has_deals=False) == []


def test_every_required_policy_field_is_named_once_the_group_has_a_deal():
    problems = M.policy_problems({}, has_deals=True)
    labels = ("Accounting Framework", "NCI Measurement", "Goodwill Treatment",
              "Acquisition Costs Treatment", "Measurement Period", "Bargain Purchase")
    assert len(problems) == len(labels)
    for label, sentence in zip(labels, problems):
        assert sentence == (
            f"Consolidation Policy: {label} is required once the group has a Business Combination")
    # the conditional fields are not demanded blindly
    joined = " ".join(problems)
    assert "Framework Note" not in joined and "Amortisation Years" not in joined


def test_a_blank_field_is_only_reported_once():
    problems = M.policy_problems(dict(IFRS_PARTIAL, goodwill_treatment=""), has_deals=True)
    assert problems == [
        "Consolidation Policy: Goodwill Treatment is required once the group has a Business Combination"]


def test_an_unknown_option_names_the_value_and_the_allowed_ones():
    problems = M.policy_problems(dict(IFRS_PARTIAL, bargain_purchase="Ignore"), has_deals=False)
    assert len(problems) == 1
    assert "Bargain Purchase" in problems[0] and '"Ignore"' in problems[0]
    assert "Recognise gain" in problems[0] and "Refuse" in problems[0]

    problems = M.policy_problems(dict(IFRS_PARTIAL, accounting_framework="GAAP"), has_deals=True)
    assert len(problems) == 1
    assert '"GAAP"' in problems[0]
    for allowed in M.FRAMEWORKS:
        assert allowed in problems[0]


def test_us_gaap_requires_full_nci_measurement():
    problems = M.policy_problems(dict(US_GAAP_FULL, goodwill_method="partial"), has_deals=True)
    assert len(problems) == 1
    assert "US GAAP" in problems[0] and "NCI Measurement" in problems[0] and "Full" in problems[0]


def test_ifrs_does_not_amortise_goodwill():
    policy = dict(IFRS_PARTIAL, goodwill_treatment="Amortise", goodwill_amortisation_years=10)
    problems = M.policy_problems(policy, has_deals=True)
    assert len(problems) == 1
    assert "IFRS" in problems[0] and "Goodwill Treatment" in problems[0]
    assert "Impairment only" in problems[0]


def test_ifrs_and_us_gaap_expense_acquisition_costs():
    """konsol#203: IFRS 3.53 and ASC 805-10-25-23 expense acquisition-related
    costs as incurred; only a Local framework may capitalise them."""
    for base in (IFRS_PARTIAL, US_GAAP_FULL):
        framework = base["accounting_framework"]
        problems = M.policy_problems(dict(base, acquisition_costs_treatment="Capitalise"), has_deals=True)
        assert problems == [
            f"Consolidation Policy: {framework} expenses acquisition-related costs as incurred; "
            "set Acquisition Costs Treatment to Expense."], (framework, problems)


def test_local_framework_may_capitalise_acquisition_costs():
    assert LOCAL_AMORTISE["acquisition_costs_treatment"] == "Capitalise"
    problems = M.policy_problems(LOCAL_AMORTISE, has_deals=True)
    assert not any("Acquisition Costs Treatment" in p for p in problems), problems


def test_amortise_needs_at_least_one_year():
    for years in (None, 0, -3, ""):
        problems = M.policy_problems(dict(LOCAL_AMORTISE, goodwill_amortisation_years=years), has_deals=True)
        assert len(problems) == 1, (years, problems)
        assert "Goodwill Amortisation Years" in problems[0] and "Amortise" in problems[0]
        assert "at least 1" in problems[0]


def test_local_framework_needs_a_note():
    for note in (None, "", "   "):
        problems = M.policy_problems(dict(LOCAL_AMORTISE, framework_note=note), has_deals=False)
        assert len(problems) == 1, (note, problems)
        assert "Local" in problems[0] and "Framework Note" in problems[0]


def test_policy_accepts_an_object_with_attributes():
    class Group:
        pass

    group = Group()
    for field, value in US_GAAP_FULL.items():
        setattr(group, field, value)
    assert M.policy_problems(group, has_deals=True) == []
    group.goodwill_method = "partial"
    assert len(M.policy_problems(group, has_deals=True)) == 1


def test_required_accounts_for_a_whole_acquisition():
    assert M.required_accounts(IFRS_PARTIAL, deal()) == [
        "goodwill_account", "fair_value_adjustment_account", "investment_account"]


def test_required_accounts_add_nci_below_full_ownership():
    assert M.required_accounts(IFRS_PARTIAL, deal(share_pct=80)) == [
        "goodwill_account", "fair_value_adjustment_account", "investment_account", "nci_account"]
    assert "nci_account" not in M.required_accounts(IFRS_PARTIAL, deal(share_pct=100))


def test_required_accounts_for_a_bargain_purchase_follow_the_policy():
    recognise = M.required_accounts(IFRS_PARTIAL, deal(goodwill=-500))
    assert "bargain_purchase_gain_account" in recognise
    refuse = M.required_accounts(dict(IFRS_PARTIAL, bargain_purchase="Refuse"), deal(goodwill=-500))
    assert "bargain_purchase_gain_account" not in refuse
    assert "bargain_purchase_gain_account" not in M.required_accounts(IFRS_PARTIAL, deal(goodwill=0))


def test_required_accounts_for_costs_follow_the_treatment():
    expensed = M.required_accounts(IFRS_PARTIAL, deal(has_costs=True))
    assert "acquisition_costs_account" in expensed
    assert "acquisition_costs_account" not in M.required_accounts(IFRS_PARTIAL, deal(has_costs=False))
    capitalised = M.required_accounts(dict(IFRS_PARTIAL, acquisition_costs_treatment="Capitalise"),
                                      deal(has_costs=True))
    assert "acquisition_costs_account" not in capitalised


def test_required_accounts_for_amortised_goodwill():
    amortise = M.required_accounts(LOCAL_AMORTISE, deal())
    assert "goodwill_amortisation_expense_account" in amortise
    assert "goodwill_amortisation_expense_account" not in M.required_accounts(IFRS_PARTIAL, deal())


def test_required_accounts_for_a_disposal():
    required = M.required_accounts(IFRS_PARTIAL, {"kind": "disposal", "share_pct": 100})
    assert required == ["goodwill_account", "disposal_gain_loss_account", "disposal_proceeds_account"]


def test_required_accounts_keep_the_declared_order():
    required = M.required_accounts(LOCAL_AMORTISE, deal(share_pct=60, goodwill=-1, has_costs=True))
    assert required == [f for f in M.ACCOUNT_FIELDS if f in required]
    assert "goodwill_amortisation_expense_account" in required
    assert "acquisition_costs_account" not in required  # Capitalise
    assert "bargain_purchase_gain_account" not in required  # Refuse


def test_account_problems_are_silent_when_every_account_is_a_published_leaf():
    required = M.required_accounts(IFRS_PARTIAL, deal(share_pct=80))
    assert M.account_problems(ALL_ACCOUNTS, required, lambda acct: True) == []


def test_account_problems_name_a_blank_required_account():
    group = dict(ALL_ACCOUNTS, nci_account="")
    required = M.required_accounts(IFRS_PARTIAL, deal(share_pct=80))
    problems = M.account_problems(group, required, lambda acct: True)
    assert len(problems) == 1
    assert "Non-controlling Interest Account" in problems[0]
    assert "required" in problems[0]


def test_account_problems_name_an_account_that_is_not_a_published_leaf():
    required = M.required_accounts(IFRS_PARTIAL, deal())
    problems = M.account_problems(ALL_ACCOUNTS, required, lambda acct: acct != "ZZ-1")
    assert len(problems) == 1
    assert "Goodwill Account" in problems[0] and '"ZZ-1"' in problems[0]
    assert "Published leaf" in problems[0]


def test_account_problems_only_look_at_the_required_accounts():
    group = {"goodwill_account": "ZZ-1", "fair_value_adjustment_account": "ZZ-2",
             "investment_account": "ZZ-3"}  # every other account blank
    required = M.required_accounts(IFRS_PARTIAL, deal())
    assert M.account_problems(group, required, lambda acct: True) == []
    problems = M.account_problems(group, M.ACCOUNT_FIELDS, lambda acct: True)
    assert len(problems) == len(M.ACCOUNT_FIELDS) - 3
