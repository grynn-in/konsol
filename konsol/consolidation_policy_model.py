"""Consolidation Policy of a group root (konsolidat#198). Pure; host-tested.

Consolidation has three layers, and this module is the middle one:

* **Inputs** are declared, never inferred: the deal documents (Business
  Combination, Business Disposal) state what was paid, what was acquired and
  when; the group root names the accounts the journals post to.
* **Policy** is configured on the group root, in the Consolidation Policy
  tab, with no defaults: the accounting framework, how NCI is measured, how
  goodwill is treated afterwards, where acquisition costs go, whether a
  measurement period applies, and what happens to a bargain purchase. Every
  choice is required once the group has a Business Combination. The framework
  constrains the choices (US GAAP measures NCI at fair value; IFRS does not
  amortise goodwill; a Local framework may pick anything but must say what it
  is).
* **Mechanics** are programmed and tested elsewhere: double entry, the
  IFRS 3 arithmetic, IAS 21 translation. They read the policy; they never
  guess it.

The functions here return sentences, not exceptions, so a controller can
throw them and a test can read them.
"""

FRAMEWORKS = ("IFRS", "US GAAP", "Local")
NCI = ("partial", "full")
GOODWILL_TREATMENTS = ("Impairment only", "Amortise")
COST_TREATMENTS = ("Expense", "Capitalise")
MEASUREMENT_PERIODS = ("Off", "12 months")
BARGAIN = ("Recognise gain", "Refuse")

#: The policy fieldnames on the Consolidation Group root (design 1a).
#: ``goodwill_method`` keeps its name and is the NCI measurement.
POLICY_FIELDS = (
    "accounting_framework",
    "framework_note",
    "goodwill_method",
    "goodwill_treatment",
    "goodwill_amortisation_years",
    "acquisition_costs_treatment",
    "measurement_period",
    "bargain_purchase",
)

#: The declared-account fieldnames on the root (design 1 and 1a), in the
#: order the warehouse columns follow.
ACCOUNT_FIELDS = (
    "goodwill_account",
    "fair_value_adjustment_account",
    "investment_account",
    "nci_account",
    "bargain_purchase_gain_account",
    "disposal_gain_loss_account",
    "disposal_proceeds_account",
    "goodwill_amortisation_expense_account",
    "acquisition_costs_account",
)

LABELS = {
    "accounting_framework": "Accounting Framework",
    "framework_note": "Framework Note",
    "goodwill_method": "NCI Measurement",
    "goodwill_treatment": "Goodwill Treatment",
    "goodwill_amortisation_years": "Goodwill Amortisation Years",
    "acquisition_costs_treatment": "Acquisition Costs Treatment",
    "measurement_period": "Measurement Period",
    "bargain_purchase": "Bargain Purchase",
    "goodwill_account": "Goodwill Account",
    "fair_value_adjustment_account": "Fair Value Adjustment Account",
    "investment_account": "Investment in Subsidiaries Account",
    "nci_account": "Non-controlling Interest Account",
    "bargain_purchase_gain_account": "Bargain Purchase Gain Account (P&L)",
    "disposal_gain_loss_account": "Gain or Loss on Disposal Account (P&L)",
    # The fieldname and the warehouse column keep `disposal_proceeds_account`
    # (the DDL is pinned in both repos); the group settles ALL deal cash here.
    "disposal_proceeds_account": "Deal Settlement Account",
    "goodwill_amortisation_expense_account": "Goodwill Amortisation Expense Account (P&L)",
    "acquisition_costs_account": "Acquisition Costs Account (P&L)",
}

#: Select fields and their allowed options. The two conditional fields
#: (``framework_note``, ``goodwill_amortisation_years``) are free text / a
#: number and have their own sentences.
_OPTIONS = {
    "accounting_framework": FRAMEWORKS,
    "goodwill_method": NCI,
    "goodwill_treatment": GOODWILL_TREATMENTS,
    "acquisition_costs_treatment": COST_TREATMENTS,
    "measurement_period": MEASUREMENT_PERIODS,
    "bargain_purchase": BARGAIN,
}

#: Required once the group has a deal; the conditional two are not listed.
_REQUIRED_WITH_DEALS = tuple(_OPTIONS)

_PREFIX = "Consolidation Policy: "


def _get(source, field):
    """A field from a dict or a document-like object; None when absent."""
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(field)
    return getattr(source, field, None)


def _text(value):
    return str(value).strip() if value is not None else ""


def _allowed(options):
    return ", ".join(f'"{option}"' for option in options)


def _years(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def policy_problems(group, has_deals):
    """Sentences describing what is wrong with the group root's policy.

    ``group`` is the Consolidation Group root (dict or document); ``has_deals``
    says whether the group already has a Business Combination, which is when
    every policy choice becomes required. An empty list means the policy is
    complete enough for the group's situation and consistent with its
    framework.
    """
    problems = []
    values = {}
    for field, options in _OPTIONS.items():
        raw = _text(_get(group, field))
        if not raw:
            values[field] = None
            if has_deals:
                problems.append(
                    f"{_PREFIX}{LABELS[field]} is required once the group has a Business Combination"
                )
            continue
        if raw not in options:
            values[field] = None
            problems.append(
                f'{_PREFIX}{LABELS[field]} "{raw}" is not one of {_allowed(options)}.'
            )
            continue
        values[field] = raw

    framework = values["accounting_framework"]
    nci = values["goodwill_method"]
    treatment = values["goodwill_treatment"]

    if framework == "US GAAP" and nci is not None and nci != "full":
        problems.append(
            f"{_PREFIX}US GAAP measures non-controlling interest at fair value; "
            f"set {LABELS['goodwill_method']} to Full."
        )
    if framework == "IFRS" and treatment == "Amortise":
        problems.append(
            f"{_PREFIX}IFRS does not amortise goodwill; "
            f"set {LABELS['goodwill_treatment']} to Impairment only."
        )
    if treatment == "Amortise" and _years(_get(group, "goodwill_amortisation_years")) < 1:
        problems.append(
            f"{_PREFIX}{LABELS['goodwill_amortisation_years']} must be at least 1 "
            f"when {LABELS['goodwill_treatment']} is Amortise."
        )
    if framework == "Local" and not _text(_get(group, "framework_note")):
        problems.append(
            f"{_PREFIX}A Local framework must carry a {LABELS['framework_note']} "
            "saying which framework it is."
        )
    return problems


def required_accounts(policy, deal):
    """The declared-account fieldnames a deal needs, in ACCOUNT_FIELDS order.

    ``policy`` is the group root (dict or document); ``deal`` is a dict with
    ``kind`` ("acquisition" unless "disposal"), ``share_pct``, ``goodwill``
    (negative for a bargain purchase) and ``has_costs``.
    """
    if _text(_get(deal, "kind")).lower() == "disposal":
        needed = {"goodwill_account", "disposal_gain_loss_account", "disposal_proceeds_account"}
    else:
        needed = {"goodwill_account", "fair_value_adjustment_account", "investment_account"}
        share = _get(deal, "share_pct")
        if share is not None and float(share) < 100:
            needed.add("nci_account")
        goodwill = _get(deal, "goodwill")
        if goodwill is not None and float(goodwill) < 0 \
                and _text(_get(policy, "bargain_purchase")) == "Recognise gain":
            needed.add("bargain_purchase_gain_account")
        if _text(_get(policy, "goodwill_treatment")) == "Amortise":
            needed.add("goodwill_amortisation_expense_account")
        if _get(deal, "has_costs") and _text(_get(policy, "acquisition_costs_treatment")) == "Expense":
            needed.add("acquisition_costs_account")
    return [field for field in ACCOUNT_FIELDS if field in needed]


def account_problems(group, required, is_published_leaf):
    """One sentence per required account field that is blank or whose account
    is not a Published leaf of the group's chart.

    ``is_published_leaf(account)`` is the caller's lookup (a chart query in
    the controller, a lambda in tests).
    """
    problems = []
    for field in required:
        label = LABELS.get(field, field)
        account = _text(_get(group, field))
        if not account:
            problems.append(
                f"{_PREFIX}{label} is required for this deal; "
                "declare a Published leaf of the group's chart on the Consolidation Group root."
            )
        elif not is_published_leaf(account):
            problems.append(
                f'{_PREFIX}{label} "{account}" is not a Published leaf of the group\'s chart.'
            )
    return problems
