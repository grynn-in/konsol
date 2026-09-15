"""The arithmetic of one Business Combination (konsolidat#198). Pure; host-tested.

This is the mechanics layer for an acquisition of control: the inputs are the
deal document's lines (consideration, the acquired balance sheet, acquisition
costs), the policy is the group root's Consolidation Policy
(``konsol.consolidation_policy_model``), and this module only does what IFRS 3
prescribes once both are given:

* consideration is translated to the group currency at the acquisition
  period's closing rate, line by line, through a callback the controller
  supplies (no rate → the currency is refused by name);
* the acquired balance sheet is **complete**: assets, liabilities AND the
  equity lines, Dr positive / Cr negative, so its book amounts sum to zero.
  The equity lines are required because the pre-acquisition equity is what
  the consolidation eliminates (it is never group reserves); the non-equity
  lines are the net assets acquired. Which line is equity is answered by a
  callback the controller supplies from the chart (``is_equity``), never
  guessed from the amounts. An incomplete sheet (does not sum to zero, or no
  equity line) is refused by name;
* the acquired net assets are those non-equity book amounts stepped up by the
  fair value adjustments;
* the difference between the consideration and the group's share of those
  net assets is **goodwill** when positive and a **bargain** purchase gain when
  negative (the policy says whether a bargain is recognised or refused);
* **NCI** at acquisition is the policy's choice: *partial* is the NCI's share
  of the net assets at fair value, *full* is the minority's own
  acquisition-date fair value, declared on the deal (``nci_fair_value``, in
  the consideration currency; konsol#204, IFRS 3.19). It is never grossed up
  from the price paid for control, which carries a control premium the
  minority does not have: undeclared under full with less than 100% acquired
  → refused by name. No measurement chosen → no NCI computed, and the policy
  sentences say why;
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
    f"{_PREFIX}the Acquired Balance Sheet is empty, so net assets would be zero "
    "and goodwill would absorb the whole consideration. Enter the acquisition-date "
    "balances, or use Get Balances from Trial Balance."
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


def _book_amounts(balances, is_equity):
    """(Σ book_amount of non-equity lines, Σ book_amount of equity lines, Σ fva)."""
    net_assets = equity = fva = Decimal(0)
    for row in balances or ():
        book = _decimal(_get(row, "book_amount"))
        if is_equity(_get(row, "main_account")):
            equity += book
        else:
            net_assets += book
        fva += _decimal(_get(row, "fair_value_adjustment"))
    return net_assets, equity, fva


def _nci_fair_value(header, rate_to_group):
    """The declared ``nci_fair_value`` in the group currency (it is in the
    consideration currency); zero or below counts as not declared."""
    value = _decimal(_get(header, "nci_fair_value"))
    if value <= 0:
        return Decimal(0)
    return value * _rate(rate_to_group, _text(_get(header, "consideration_currency")))


def _nci_fair_value_problems(header, policy, share):
    """Full method with a minority left: its own fair value must be declared."""
    if _text(_get(policy, "goodwill_method")) != "full":
        return []
    if share is None or not (0 < share < HUNDRED):
        return []
    if _decimal(_get(header, "nci_fair_value")) > 0:
        return []
    return [
        f"{_PREFIX}NCI Fair Value is required: NCI is measured at full and {share.normalize():f}% "
        "is acquired. The price paid for control is not the minority's value."
    ]


def totals(header, consideration, balances, costs, policy, rate_to_group, is_equity):
    """The Result fields of a Business Combination, every value a Decimal
    quantised to 0.01.

    ``rate_to_group(currency)`` returns the closing rate to the group currency
    at the acquisition period (units of group currency per 1 unit of
    ``currency``; 1.0 for the group currency) or None when there is none,
    which raises ``ValueError`` naming the currency.

    ``is_equity(main_account)`` says whether an acquired balance line is an
    equity account (the controller answers from the chart). Equity lines are
    not net assets: they are reported as ``equity_eliminated`` (sign flipped,
    so a credit balance of −650 eliminates 650) and the non-equity lines are
    ``net_assets_acquired``. Fair value adjustments count on every line.
    """
    consideration_total = _translated_sum(consideration, header, rate_to_group)
    costs_total = _translated_sum(costs, header, rate_to_group)

    net_assets, equity_book, fva = _book_amounts(balances, is_equity)
    nafv = net_assets + fva

    treatment = _text(_get(policy, "acquisition_costs_treatment"))
    capitalised = treatment == "Capitalise"
    costs_expensed = costs_total if treatment == "Expense" else Decimal(0)
    consideration_basis = consideration_total + costs_total if capitalised else consideration_total

    share = _share(header)
    share_fraction = share / HUNDRED if share is not None else Decimal(0)

    nci_measurement = _text(_get(policy, "goodwill_method"))
    nci_fraction = Decimal(1) - share_fraction
    if nci_measurement == "partial":
        nci = nafv * nci_fraction
    elif nci_measurement == "full" and 0 < share_fraction < 1:
        # konsol#204: the minority's own declared fair value, translated like
        # the consideration; blank is no NCI (problems() refuses it by name).
        nci = _nci_fair_value(header, rate_to_group)
    else:
        nci = Decimal(0)

    # IFRS 3.32: goodwill = consideration (+ NCI) − net assets at fair value.
    # Partial method: only the acquirer's share of net assets is compared with
    # the consideration. Full method: the NCI is at fair value, so goodwill is
    # the whole business's — consideration + NCI − 100% of net assets.
    if nci_measurement == "full":
        difference = consideration_basis + nci - nafv
    else:
        difference = consideration_basis - nafv * share_fraction
    goodwill = max(Decimal(0), difference)
    bargain = max(Decimal(0), -difference)

    return {
        "total_consideration": _money(consideration_total),
        "net_assets_acquired": _money(net_assets),
        "equity_eliminated": _money(-equity_book),
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


def _equity_rule(facts, balances):
    """``facts['is_equity']``; required as soon as there are balance lines to classify."""
    is_equity = _get(facts, "is_equity")
    if is_equity is None:
        if balances:
            raise ValueError(
                "problems() needs facts['is_equity'](main_account) to tell the equity lines "
                "of the Acquired Balance Sheet from the net assets"
            )
        return lambda account: False
    return is_equity


def _totals_for(header, consideration, balances, costs, policy, facts, is_equity):
    given = _get(facts, "totals")
    if given is not None:
        return given
    rate_to_group = _get(facts, "rate_to_group")
    if rate_to_group is None:
        raise ValueError(
            "problems() needs facts['totals'] (from totals()) or facts['rate_to_group'] "
            "to translate the consideration"
        )
    return totals(header, consideration, balances, costs, policy, rate_to_group, is_equity)


def _balance_sheet_problems(balances, is_equity):
    """A given Acquired Balance Sheet must be complete: sum to zero and carry equity."""
    if not balances:
        return []
    found = []
    total = sum((_decimal(_get(row, "book_amount")) for row in balances), Decimal(0))
    if abs(total) > CENT:
        found.append(
            "Acquired Balance Sheet does not balance "
            f"(assets − liabilities − equity = {_money(total):.2f}); it must include the equity lines"
        )
    if not any(is_equity(_get(row, "main_account")) for row in balances):
        found.append(
            "Acquired Balance Sheet has no equity line: "
            "the pre-acquisition equity is what the consolidation eliminates"
        )
    return found


def problems(header, consideration, balances, costs, policy, facts):
    """Sentences describing why this deal cannot be saved; empty when it can.

    ``facts`` is a dict the controller has looked up: ``rate_to_group`` or
    ``totals`` (see ``totals()``), ``is_equity(main_account)`` (required when
    there are balance lines) and ``is_published_leaf(account)`` for the
    declared accounts (without it only blank accounts are reported).

    An empty Acquired Balance Sheet is always refused (konsol#206): net assets
    are measured only from its lines, so without them goodwill would absorb the
    whole consideration. A trial balance on file does not waive it; any
    ``has_tb_at_or_before`` in ``facts`` is ignored here.
    """
    found = []
    is_equity = _equity_rule(facts, balances)

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
    found += _nci_fair_value_problems(header, policy, share)

    if not balances:
        found.append(BALANCE_SHEET_REQUIRED)
    found += _balance_sheet_problems(balances, is_equity)

    result = _totals_for(header, consideration, balances, costs, policy, facts, is_equity)
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


def derive_acquired_balances(tb_rows, retained_earnings_account, fva_lines, period_label):
    """The Acquired Balance Sheet from trial-balance amounts (konsol#207).

    ``tb_rows``: dicts ``main_account``, ``amount`` (Dr+/Cr−, entity currency,
    cumulative through the acquisition period) and ``is_pnl`` (0/1). Amounts
    are summed per account; the P&L accounts' total (the result not yet closed
    into retained earnings; closed years net to zero) is folded into
    ``retained_earnings_account``'s line and the P&L lines are dropped. Lines
    that round to 0.00 are dropped and the rest sorted by account; then each
    ``(main_account, amount)`` of ``fva_lines`` is added to that account's
    ``fair_value_adjustment``, on a new line with book amount 0 when absent.

    Returns ``(lines, problems)``: lines are dicts ``main_account,
    book_amount, fair_value_adjustment, note`` (money as Decimal to 0.01);
    problems are sentences, empty when the lines can be used.
    """
    rows = list(tb_rows or ())
    if not rows:
        return [], [f"{_PREFIX}no trial balance amounts at or before {period_label}."]

    found = []
    book = {}
    pnl_total = Decimal(0)
    for row in rows:
        amount = _decimal(_get(row, "amount"))
        if int(_decimal(_get(row, "is_pnl"))):
            pnl_total += amount
        else:
            account = _text(_get(row, "main_account"))
            book[account] = book.get(account, Decimal(0)) + amount

    total = sum(book.values(), Decimal(0)) + pnl_total
    if _money(pnl_total) != ZERO:
        if retained_earnings_account:
            book[retained_earnings_account] = book.get(retained_earnings_account, Decimal(0)) + pnl_total
        else:
            found.append(
                f"{_PREFIX}the chart declares no Retained Earnings Account: tick it on the "
                f"retained-earnings Main Account so the result of {period_label} can be folded in."
            )
    if abs(total) > Decimal("0.005"):
        found.append(
            f"{_PREFIX}the trial balance through {period_label} does not balance: "
            f"it is off by {_money(total):.2f}."
        )

    lines = [
        {"main_account": account, "book_amount": _money(amount),
         "fair_value_adjustment": ZERO, "note": "From the trial balance"}
        for account, amount in sorted(book.items())
        if _money(amount) != ZERO
    ]
    by_account = {line["main_account"]: line for line in lines}
    for account, amount in fva_lines or ():
        amount = _money(amount)
        if amount == ZERO:
            continue
        line = by_account.get(account)
        if line is None:
            line = {"main_account": account, "book_amount": ZERO,
                    "fair_value_adjustment": ZERO, "note": "Fair value adjustment"}
            lines.append(line)
            by_account[account] = line
        line["fair_value_adjustment"] = _money(line["fair_value_adjustment"] + amount)
    return lines, found


def split_by_weights(total, weights):
    """``total`` spread over ``weights`` (``(main_account, weight)``, percentages
    summing to 100) as ``[(main_account, amount)]``, amounts Decimal to 0.01.
    The rounding remainder goes to the line with the largest weight (the first
    on ties), so the amounts add up to ``total`` exactly. Weights not summing
    to 100 raise ``ValueError``.
    """
    pairs = [(account, _decimal(weight)) for account, weight in weights or ()]
    weight_sum = sum((weight for _, weight in pairs), Decimal(0))
    if abs(weight_sum - HUNDRED) > Decimal("0.0001"):
        raise ValueError(
            f"{_PREFIX}the allocation weights add up to {weight_sum}; they must add up to 100."
        )
    target = _money(total)
    amounts = [_money(target * weight / HUNDRED) for _, weight in pairs]
    largest = max(range(len(pairs)), key=lambda i: (pairs[i][1], -i))
    amounts[largest] += target - sum(amounts, Decimal(0))
    return [(account, amount) for (account, _), amount in zip(pairs, amounts)]
