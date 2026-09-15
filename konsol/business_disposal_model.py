"""The arithmetic of one Business Disposal (konsolidat#198). Pure; host-tested.

The mechanics layer for a loss of control, kept apart from the other two layers
on purpose (design 2a): the **inputs** are the disposal document's lines (what
was received for the holding), the **policy** is the group root's Consolidation
Policy (``konsol.consolidation_policy_model``), and this module only does what
follows once both are given:

* the proceeds are translated to the group currency at the disposal period's
  closing rate, line by line, through a callback the controller supplies (no
  rate → the currency is refused by name), exactly like a Business
  Combination's consideration;
* only a **full** disposal is in scope: the share disposed must equal the
  group's current holding in the entity on the disposal date (a fact the
  controller reads from the Ownership Periods) and no interest may be retained.
  A partial disposal is refused by a sentence that names the two supported
  ways to record it, never silently accepted or defaulted;
* a disposal **with no proceeds is real** (the stack's own 2020 disposal was
  priced 0 and the design keeps it): a line of 0 is accepted and the warehouse
  names it by a warning (``assert_disposal_has_proceeds``); a negative line is
  refused;
* the gain or loss itself is measured downstream (the dbt disposal journal,
  design 5: derecognition, goodwill, recycled CTA, proceeds, balancing line),
  so the Result here is the total proceeds only, and the accounts that journal
  posts to must be declared on the group root.

All money is ``Decimal`` quantised to 0.01 (half up). ``problems()`` returns
sentences rather than raising, so the controller can throw them and a test can
read them.
"""
from decimal import ROUND_HALF_UP, Decimal

from konsol.consolidation_policy_model import (
    account_problems,
    policy_problems,
    required_accounts,
)

CENT = Decimal("0.01")
HUNDRED = Decimal("100")

_PREFIX = "Business Disposal: "
RETAINED_INTEREST_UNSUPPORTED = (
    f"{_PREFIX}Partial disposals that keep an interest are not supported yet; "
    "dispose of the full holding or record the change as an ownership step."
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


def _percent(value):
    """A percentage as a Decimal, or None when blank or unreadable."""
    if value is None or value == "":
        return None
    try:
        return _decimal(value)
    except (ArithmeticError, ValueError):
        return None


def _rate(rate_to_group, currency):
    rate = rate_to_group(currency)
    if rate is None:
        raise ValueError(
            f"No Closing rate to the group currency for {currency} at the disposal period"
        )
    return _decimal(rate)


def totals(header, proceeds, rate_to_group):
    """The Result of a Business Disposal: ``total_proceeds`` as a Decimal
    quantised to 0.01.

    ``rate_to_group(currency)`` returns the closing rate to the group currency
    at the disposal period (units of group currency per 1 unit of
    ``currency``; 1.0 for the group currency) or None when there is none,
    which raises ``ValueError`` naming the currency. A line with no currency
    is in the header's ``proceeds_currency``.
    """
    header_currency = _text(_get(header, "proceeds_currency"))
    total = Decimal(0)
    for row in proceeds or ():
        currency = _text(_get(row, "currency")) or header_currency
        total += _decimal(_get(row, "amount")) * _rate(rate_to_group, currency)
    return {"total_proceeds": _money(total)}


def _line_problems(proceeds):
    found = []
    for index, row in enumerate(proceeds or (), start=1):
        if _decimal(_get(row, "amount")) < 0:
            component = _text(_get(row, "component")) or "no component"
            found.append(
                f"{_PREFIX}Proceeds line {index} ({component}) must not be below 0; "
                "a disposal with no proceeds is recorded as 0."
            )
    return found


def _holding_problems(header, facts):
    """Only a full disposal of the current holding is supported."""
    found = []
    share = _percent(_get(header, "share_disposed_pct"))
    if share is None or share <= 0 or share > HUNDRED:
        shown = _text(_get(header, "share_disposed_pct")) or "blank"
        found.append(f"{_PREFIX}Share Disposed must be above 0 and at most 100 (got {shown}).")

    retained = _percent(_get(header, "retained_interest_pct")) or Decimal(0)
    if retained != 0:
        found.append(RETAINED_INTEREST_UNSUPPORTED)

    holding = _percent(_get(facts, "current_holding_pct"))
    entity = _text(_get(header, "disposed_entity")) or "the entity"
    if holding is None:
        found.append(
            f"{_PREFIX}{entity} has no Ownership Period in {_text(_get(header, 'consolidation_group'))} "
            f"covering {_text(_get(header, 'disposal_date'))}: there is no holding to dispose of."
        )
    elif share is not None and share != holding:
        found.append(
            f"{_PREFIX}Share Disposed {share.normalize():f}% must equal the group's current holding "
            f"of {holding.normalize():f}% in {entity} on the disposal date: only a full disposal "
            "is supported."
        )
    return found


def problems(header, proceeds, policy, facts):
    """Sentences describing why this disposal cannot be saved; empty when it can.

    ``policy`` is the group root (policy fields + declared accounts). ``facts``
    is a dict the controller has looked up: ``current_holding_pct`` (the
    ownership percentage of the entity's submitted Ownership Period covering
    the disposal date; None when there is none), ``totals`` from ``totals()``
    or ``rate_to_group`` to compute it, and ``is_published_leaf(account)`` for
    the declared accounts (without it only blank accounts are reported).
    """
    found = []
    if not proceeds:
        found.append(f"{_PREFIX}at least one Proceeds line is required (an amount of 0 is allowed).")
    found += _line_problems(proceeds)
    found += _holding_problems(header, facts)

    if _get(facts, "totals") is None:
        rate_to_group = _get(facts, "rate_to_group")
        if rate_to_group is None:
            raise ValueError(
                "problems() needs facts['totals'] (from totals()) or facts['rate_to_group'] "
                "to translate the proceeds"
            )
        totals(header, proceeds, rate_to_group)  # a missing rate raises by name

    found += policy_problems(policy, has_deals=True)
    is_published_leaf = _get(facts, "is_published_leaf") or (lambda account: True)
    found += account_problems(policy, required_accounts(policy, {"kind": "disposal"}), is_published_leaf)
    return found
