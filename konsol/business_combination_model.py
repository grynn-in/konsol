"""The arithmetic of one Business Combination (konsolidat#198). Pure; host-tested.

This is the mechanics layer for an acquisition of control: the inputs are the
deal document's lines (consideration, the acquired balance sheet, acquisition
costs), the policy is the group root's Consolidation Policy
(``konsol.consolidation_policy_model``), and this module only does what IFRS 3
prescribes once both are given:

* consideration is translated to the group currency at the acquisition
  period's closing rate, line by line, through a callback the controller
  supplies (no rate → the currency is refused by name);
* the acquired net assets are the book amounts stepped up by the fair value
  adjustments;
* the difference between the consideration and the group's share of those
  net assets is **goodwill** when positive and a **bargain** purchase gain when
  negative (the policy says whether a bargain is recognised or refused);
* **NCI** at acquisition is the policy's choice: *partial* is the NCI's share
  of the net assets at fair value, *full* is the fair value the price implies
  (consideration ÷ share × the NCI's share). No measurement chosen → no NCI
  computed, and the policy sentences say why;
* acquisition costs are expensed (outside goodwill) or capitalised (they
  join the consideration, and so the goodwill), per the policy.

All money is ``Decimal`` quantised to 0.01 (half up). ``problems()`` returns
sentences rather than raising, so the controller can throw them and a test
can read them.
"""
from decimal import ROUND_HALF_UP, Decimal

from konsol.consolidation_policy_model import (
    account_problems,
    policy_problems,
    required_accounts,
)

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
ZERO = Decimal("0").quantize(CENT)

_PREFIX = "Business Combination: "
BALANCE_SHEET_REQUIRED = (
    "Acquired Balance Sheet is required: "
    "the entity has no trial balance at or before the acquisition date"
)


def _get(source, field):
    """A field from a dict or a document-like object; None when absent."""
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(field)
    return getattr(source, field, None)


def _text(value):
    return str(value).strip() if value is not None else ""


def _decimal(value):
    """Any number-ish input as an exact Decimal; blank is zero."""
    if value is None or value == "":
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _money(value):
    return _decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _share(header):
    """The acquired share as a Decimal percentage, or None when unreadable."""
    raw = _get(header, "share_acquired_pct")
    if raw is None or raw == "":
        return None
    try:
        return _decimal(raw)
    except (ArithmeticError, ValueError):
        return None


def _rate(rate_to_group, currency):
    rate = rate_to_group(currency)
    if rate is None:
        raise ValueError(
            f"No Closing rate to the group currency for {currency} at the acquisition period"
        )
    return _decimal(rate)


def _translated_sum(lines, header, rate_to_group):
    """Σ amount × rate for lines whose currency is theirs or the header's."""
    total = Decimal(0)
    header_currency = _text(_get(header, "consideration_currency"))
    for row in lines or ():
        currency = _text(_get(row, "currency")) or header_currency
        total += _decimal(_get(row, "amount")) * _rate(rate_to_group, currency)
    return total


def totals(header, consideration, balances, costs, policy, rate_to_group):
    """The Result fields of a Business Combination, every value a Decimal
    quantised to 0.01.

    ``rate_to_group(currency)`` returns the closing rate to the group currency
    at the acquisition period (units of group currency per 1 unit of
    ``currency``; 1.0 for the group currency) or None when there is none,
    which raises ``ValueError`` naming the currency.
    """
    consideration_total = _translated_sum(consideration, header, rate_to_group)
    costs_total = _translated_sum(costs, header, rate_to_group)

    net_assets = sum((_decimal(_get(row, "book_amount")) for row in balances or ()), Decimal(0))
    fva = sum((_decimal(_get(row, "fair_value_adjustment")) for row in balances or ()), Decimal(0))
    nafv = net_assets + fva

    treatment = _text(_get(policy, "acquisition_costs_treatment"))
    capitalised = treatment == "Capitalise"
    costs_expensed = costs_total if treatment == "Expense" else Decimal(0)
    consideration_basis = consideration_total + costs_total if capitalised else consideration_total

    share = _share(header)
    share_fraction = share / HUNDRED if share is not None else Decimal(0)
    difference = consideration_basis - nafv * share_fraction
    goodwill = max(Decimal(0), difference)
    bargain = max(Decimal(0), -difference)

    nci_measurement = _text(_get(policy, "goodwill_method"))
    nci_fraction = Decimal(1) - share_fraction
    if nci_measurement == "partial":
        nci = nafv * nci_fraction
    elif nci_measurement == "full" and share_fraction > 0:
        nci = consideration_basis / share_fraction * nci_fraction
    else:
        nci = Decimal(0)

    return {
        "total_consideration": _money(consideration_total),
        "net_assets_acquired": _money(net_assets),
        "fair_value_adjustments": _money(fva),
        "net_assets_at_fair_value": _money(nafv),
        "goodwill": _money(goodwill),
        "bargain_purchase_gain": _money(bargain),
        "nci_at_acquisition": _money(nci),
        "costs_total": _money(costs_total),
        "costs_expensed": _money(costs_expensed),
        "consideration_including_costs": _money(consideration_basis),
    }


def _line_problems(lines, table_label, kind_field):
    problems = []
    for index, row in enumerate(lines or (), start=1):
        if _decimal(_get(row, "amount")) <= 0:
            kind = _text(_get(row, kind_field)) or "no component"
            problems.append(
                f"{_PREFIX}{table_label} line {index} ({kind}) must have an amount above 0."
            )
    return problems


def _totals_for(header, consideration, balances, costs, policy, facts):
    given = _get(facts, "totals")
    if given is not None:
        return given
    rate_to_group = _get(facts, "rate_to_group")
    if rate_to_group is None:
        raise ValueError(
            "problems() needs facts['totals'] (from totals()) or facts['rate_to_group'] "
            "to translate the consideration"
        )
    return totals(header, consideration, balances, costs, policy, rate_to_group)


def problems(header, consideration, balances, costs, policy, facts):
    """Sentences describing why this deal cannot be saved; empty when it can.

    ``facts`` is a dict the controller has looked up: ``has_tb_at_or_before``
    (the entity has a submitted trial balance at or before the acquisition
    period), ``rate_to_group`` or ``totals`` (see ``totals()``), and
    ``is_published_leaf(account)`` for the declared accounts (without it only
    blank accounts are reported).
    """
    found = []

    if not consideration:
        found.append(f"{_PREFIX}at least one Consideration line is required.")

    share = _share(header)
    if share is None or share <= 0 or share > HUNDRED:
        shown = _text(_get(header, "share_acquired_pct")) or "blank"
        found.append(
            f"{_PREFIX}Share Acquired must be above 0 and at most 100 (got {shown})."
        )

    found += _line_problems(consideration, "Consideration", "component")
    found += _line_problems(costs, "Cost", "kind")

    if not balances and not _get(facts, "has_tb_at_or_before"):
        found.append(BALANCE_SHEET_REQUIRED)

    result = _totals_for(header, consideration, balances, costs, policy, facts)
    bargain = _decimal(result.get("bargain_purchase_gain"))
    if bargain > 0 and _text(_get(policy, "bargain_purchase")) == "Refuse":
        found.append(
            f"{_PREFIX}this is a bargain purchase of {bargain:.2f} in the group currency "
            "and the Consolidation Policy says Refuse; check the consideration and the "
            "acquired balance sheet, or change the policy on the group root."
        )

    found += policy_problems(policy, has_deals=True)

    goodwill = _decimal(result.get("goodwill"))
    deal = {
        "kind": "acquisition",
        "share_pct": float(share) if share is not None else 100,
        "goodwill": float(-bargain if bargain > 0 else goodwill),
        "has_costs": bool(costs),
    }
    is_published_leaf = _get(facts, "is_published_leaf") or (lambda account: True)
    found += account_problems(policy, required_accounts(policy, deal), is_published_leaf)
    return found
