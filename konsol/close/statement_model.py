"""Statement model, pure: which periods feed each column, the amount per
heading (N48), and the statement itself — sign, columns, CTA, current-year
result, residual (N49) (konsol#305 Wave 4; stories 8.1, D2-4; #305-W4-1 1c,
W4-7 7a).

Imports nothing from frappe or konsol. Loaded by path in
konsol/tests/test_close_statement_model.py, mirroring
konsol/tests/test_fiscal_status_model.py.

Amounts are summed as Decimal (``Decimal(str(amount))``), quantized to 2 dp
only once, at the end — so 0.10 + 0.20 sums to 0.30 exactly, the same
convention as journal_model.py.

``heading_amounts`` (N48) carries no display sign: every amount there stays
a net DEBIT movement or balance. ``statement`` (N49) applies the display
sign:

- Profit and Loss: one section-wide flip (``-net_debit``) — income
  positive, costs negative (the SPA brackets negatives).
- Balance Sheet (#305-W4-2 2a-ii, AMENDED 4 Oct — W4-E3 debit-positive is
  WITHDRAWN): liabilities and equity are POSITIVE too. Each BS heading's
  own side comes from its Main Account ``normal_balance`` (Debit ->
  unflipped; Credit -> flipped); a BS heading with a blank
  ``normal_balance`` is never defaulted — ``statement`` raises, naming every
  such heading (``STATEMENT_HEADING_SIDE_UNDECLARED``).
- The "Unmatched residual" line stays in net-debit (unflipped) terms. That
  is deliberate, not an oversight: with assets displayed unflipped and
  liabilities/equity displayed flipped, ``assets_display - (liabilities +
  equity)_display`` equals the net-debit residual exactly (see the proof in
  ``test_close_statement_model.py``'s balanced fixture), so no second flip
  is needed to keep "assets = liabilities + equity" true after 2a-ii.
"""
from decimal import Decimal

_CENTS = Decimal("0.01")

#: Main Account ``statement_section`` options (main_account.json).
PL = "Profit and Loss"
BS = "Balance Sheet"
SECTIONS = (PL, BS)


def _dec(amount):
    return Decimal(str(amount))


def _round(amount):
    return float(amount.quantize(_CENTS))


def period_keys(period_rows, key):
    """``{"current", "comparison", "ytd", "cumulative_to"}`` for ``key``.

    ``period_rows`` are ``fiscal_calendar.fiscal_period_rows()`` dicts
    (keys include ``fiscal_year``, ``fiscal_period``, ``period_type``).
    ``key`` is ``(fiscal_year, fiscal_period)``; it must be declared in
    ``period_rows`` or this raises ``ValueError`` naming it.

    - ``comparison``: the previous Regular period in key order (calendar
      order, since ``(fiscal_year, fiscal_period)`` already sorts that
      way). A Regular P1 compares to the previous FY's last Regular
      period (#305-W4-7 7a). ``None`` when no earlier Regular period is
      declared.
    - ``ytd``: the keys of the same FY with key <= ``current``, Opening
      included, the Closing period excluded unless it is ``current``
      itself (otherwise YTD after P13 would always be 0).
    - ``cumulative_to``: ``current`` — the caller's upper bound for a BS
      cumulative balance (every declared row with key <= cumulative_to,
      Opening and Closing included).
    """
    declared = {(row["fiscal_year"], row["fiscal_period"]): row for row in period_rows}
    if key not in declared:
        raise ValueError(f"{key} is not a declared period")

    fiscal_year, fiscal_period = key

    regular_keys = sorted(
        k for k, row in declared.items() if row.get("period_type") == "Regular"
    )
    comparison = None
    for candidate in regular_keys:
        if candidate < key:
            comparison = candidate
        else:
            break

    ytd = []
    for k in sorted(declared):
        if k[0] != fiscal_year or k > key:
            continue
        row = declared[k]
        if row.get("period_type") == "Closing" and k != key:
            continue
        ytd.append(k)

    return {
        "current": key,
        "comparison": comparison,
        "ytd": ytd,
        "cumulative_to": key,
    }


def heading_amounts(rows, accounts, keys):
    """Sum ``rows`` whose key is in ``keys`` into statement buckets.

    ``rows`` are ``{"fiscal_year", "fiscal_period", "main_account",
    "adjustment_type", "amount", "null_rows"}`` (N51's grouped read).
    ``accounts`` is ``{code: {"account_name", "parent_account", "is_group",
    "statement_section", "lft"}}`` of the Published chart, headings
    included. ``keys`` is an iterable of ``(fiscal_year, fiscal_period)``.

    Returns ``{"headings", "no_heading", "not_in_chart", "cta",
    "pl_total"}``:
      - ``headings``: ``{heading_code: amount}`` — a leaf's heading is its
        ``parent_account``.
      - ``no_heading``: ``{section: amount}`` — a Published leaf with a
        blank ``parent_account`` (live: ``99 NET INCOME``, P&L).
      - ``not_in_chart``: ``{code: amount}`` — a code not in ``accounts``,
        or posted to a heading (``is_group``); CTA rows are excluded from
        this bucket even when ``main_account`` is the literal ``"CTA"``,
        which is never a chart code.
      - ``cta``: the sum of rows with ``adjustment_type == "cta"``.
      - ``pl_total``: the Profit and Loss section sum (headings +
        no_heading), used by N49 for the current-year result.

    A row with ``null_rows`` truthy, or ``amount is None``, raises
    ``ValueError`` naming the account and period — it is never read as 0.
    No row whose key is in ``keys`` is ever dropped: it lands in exactly
    one bucket (``cta`` and the other buckets are mutually exclusive).
    """
    keys = set(keys)

    headings = {}
    no_heading = {}
    not_in_chart = {}
    cta_total = Decimal("0")

    for row in rows:
        key = (row["fiscal_year"], row["fiscal_period"])
        if key not in keys:
            continue

        code = row["main_account"]
        if row.get("null_rows"):
            raise ValueError(
                f"{code} {row['fiscal_year']}/{row['fiscal_period']}: "
                "has null rows, amount cannot be read as 0"
            )
        if row["amount"] is None:
            raise ValueError(
                f"{code} {row['fiscal_year']}/{row['fiscal_period']}: "
                "amount is None, cannot be read as 0"
            )
        amount = _dec(row["amount"])

        if row.get("adjustment_type") == "cta":
            cta_total += amount
            continue

        entry = accounts.get(code)
        if entry is None or entry.get("is_group"):
            not_in_chart[code] = not_in_chart.get(code, Decimal("0")) + amount
            continue

        parent = entry.get("parent_account")
        if parent:
            headings[parent] = headings.get(parent, Decimal("0")) + amount
        else:
            section = entry.get("statement_section")
            no_heading[section] = no_heading.get(section, Decimal("0")) + amount

    pl_total = no_heading.get(PL, Decimal("0"))
    for heading, amount in headings.items():
        heading_entry = accounts.get(heading) or {}
        if heading_entry.get("statement_section") == PL:
            pl_total += amount

    return {
        "headings": {k: _round(v) for k, v in headings.items()},
        "no_heading": {k: _round(v) for k, v in no_heading.items()},
        "not_in_chart": {k: _round(v) for k, v in not_in_chart.items()},
        "cta": _round(cta_total),
        "pl_total": _round(pl_total),
    }


def heading_order(accounts):
    """``{PL: [codes], BS: [codes]}``: heading codes by ``lft`` (chart
    order, never alphabetical — a heading with no ``statement_section``
    raises ``ValueError`` naming it)."""
    order = {PL: [], BS: []}
    for code, entry in accounts.items():
        if not entry.get("is_group"):
            continue
        section = entry.get("statement_section")
        if section not in SECTIONS:
            raise ValueError(f"{code} has no statement_section")
        order[section].append((entry["lft"], code))

    return {section: [code for _, code in sorted(codes)] for section, codes in order.items()}


# -- the statement (N49) -----------------------------------------------------

#: #305-W4-2 2a-ii, AMENDED 4 Oct. Text given verbatim in the decision.
LEGEND = (
    "Profit and loss: income positive, costs in brackets. "
    "Balance sheet: assets, liabilities and equity positive."
)

#: A BS heading with no declared ``normal_balance`` (Debit/Credit): never a
#: silent default (#305-W4-2 2a-ii).
STATEMENT_HEADING_SIDE_UNDECLARED = "statement_heading_side_undeclared"

NET_RESULT_LABEL = "Net result"
_NO_HEADING_LABEL = "No heading"
_NOT_IN_CHART_LABEL = "Not in the group chart"
_RESIDUAL_LABEL = "Unmatched residual"
_CTA_INCLUDES_LABEL = "of which currency translation (CTA)"
_RESULT_INCLUDES_LABEL = "of which current-year result"
_CTA_NOT_PLACED = "CTA not placed (declare the CTA account)"
_RESULT_NOT_PLACED = "Current-year result not placed (declare the current-year result account)"


def _period_label(key):
    return "FY%s P%02d" % key


def _keys_up_to(period_rows, upto):
    """Every declared key <= ``upto`` (Opening and Closing included — the
    tuple order is already calendar order, N48 facts)."""
    return {
        (row["fiscal_year"], row["fiscal_period"])
        for row in period_rows
        if (row["fiscal_year"], row["fiscal_period"]) <= upto
    }


def _any_row_at(rows, keys):
    return any((row["fiscal_year"], row["fiscal_period"]) in keys for row in rows)


def _current_year_result_raw(rows, accounts, period_rows, key):
    """The P&L YTD total (net debit, unflipped) at ``key`` — the same FY's
    keys only, never the full BS cumulative history (#305-W4-7 7a)."""
    ytd_keys = period_keys(period_rows, key)["ytd"]
    return heading_amounts(rows, accounts, ytd_keys)["pl_total"]


def _bs_heading_sides(bs_order, accounts):
    """``{code: +1|-1}`` for every BS heading in ``bs_order``: +1 (shown
    as-is) for ``normal_balance`` Debit, -1 (flipped) for Credit. A blank
    ``normal_balance`` is a setup gap — raises ``ValueError`` naming every
    such heading, never a silent default (#305-W4-2 2a-ii, AMENDED 4 Oct)."""
    sides = {}
    missing = []
    for code in bs_order:
        side = (accounts.get(code) or {}).get("normal_balance")
        if side == "Debit":
            sides[code] = 1
        elif side == "Credit":
            sides[code] = -1
        else:
            missing.append(code)
    if missing:
        raise ValueError(
            "%s: %s have no normal_balance declared; declare Debit or "
            "Credit on each before the balance sheet can show its side."
            % (STATEMENT_HEADING_SIDE_UNDECLARED, ", ".join(missing))
        )
    return sides


def _pl_line(kind, label_or_heading_entry, cur_raw, comp_raw, ytd_raw, is_heading):
    cur = round(-cur_raw, 2)
    comp = round(-comp_raw, 2) if comp_raw is not None else None
    ytd = round(-ytd_raw, 2)
    line = {
        "kind": kind,
        "current": cur,
        "comparison": comp,
        "variance": round(cur - comp, 2) if comp is not None else None,
        "ytd": ytd,
    }
    if is_heading:
        line["heading"] = label_or_heading_entry[0]
        line["heading_name"] = label_or_heading_entry[1]
    else:
        line["label"] = label_or_heading_entry
    return line


def statement(rows, accounts, period_rows, key, declared):
    """Both statement sections, ready to show (konsol#305 N49; #305-W4-1
    1c, W4-2 2a-ii AMENDED 4 Oct, W4-3 3a, W4-7 7a).

    ``rows``/``accounts`` are ``heading_amounts``' inputs; ``period_rows``
    and ``key`` are ``period_keys``' inputs; ``accounts`` additionally
    carries ``normal_balance`` on every BS heading (Debit/Credit — a blank
    one raises, see ``_bs_heading_sides``). ``declared`` is N41's
    ``close_policy_model.statement_accounts(...)`` result.

    Returns ``{"legend", "periods": {"current", "comparison",
    "comparison_note"}, "sections": [{"section", "lines"}, ...], "gap"}``.
    Nothing here is ever balanced silently: an undeclared CTA/current-year
    result account is placed nowhere (named in the residual's
    ``explained``), a code outside the chart is its own line, and a missing
    comparison period is ``None`` with a note, never 0.
    """
    periods = period_keys(period_rows, key)
    current, comparison = periods["current"], periods["comparison"]
    order = heading_order(accounts)

    # -- Profit and Loss: this period / comparison / variance / YTD --------
    ha_current = heading_amounts(rows, accounts, {current})
    comparison_keys = {comparison} if comparison is not None else set()
    has_comparison_row = comparison is not None and _any_row_at(rows, comparison_keys)
    ha_comparison = heading_amounts(rows, accounts, comparison_keys) if has_comparison_row else None
    ha_ytd = heading_amounts(rows, accounts, periods["ytd"])

    if comparison is None:
        comparison_note = "No earlier period declared"
    elif not has_comparison_row:
        comparison_note = "No rows in the warehouse for %s" % _period_label(comparison)
    else:
        comparison_note = None

    pl_lines = []
    for code in order[PL]:
        entry = accounts[code]
        cur_raw = ha_current["headings"].get(code, 0.0)
        comp_raw = ha_comparison["headings"].get(code, 0.0) if ha_comparison else None
        ytd_raw = ha_ytd["headings"].get(code, 0.0)
        pl_lines.append(_pl_line("heading", (code, entry.get("account_name")),
                                  cur_raw, comp_raw, ytd_raw, is_heading=True))

    no_heading_cur = ha_current["no_heading"].get(PL, 0.0)
    no_heading_ytd = ha_ytd["no_heading"].get(PL, 0.0)
    no_heading_comp = ha_comparison["no_heading"].get(PL, 0.0) if ha_comparison else None
    if no_heading_cur or no_heading_ytd or no_heading_comp:
        pl_lines.append(_pl_line("no_heading", _NO_HEADING_LABEL,
                                  no_heading_cur, no_heading_comp, no_heading_ytd,
                                  is_heading=False))

    pl_lines.append(_pl_line(
        "net_result", NET_RESULT_LABEL,
        ha_current["pl_total"],
        ha_comparison["pl_total"] if ha_comparison else None,
        ha_ytd["pl_total"],
        is_heading=False,
    ))

    # -- Balance Sheet: cumulative this period / comparison / variance -----
    bs_order = order[BS]
    sides = _bs_heading_sides(bs_order, accounts)

    cum_current_keys = _keys_up_to(period_rows, periods["cumulative_to"])
    ha_cum_current = heading_amounts(rows, accounts, cum_current_keys)

    cum_comparison_keys = _keys_up_to(period_rows, comparison) if comparison is not None else set()
    has_comparison_cum_rows = comparison is not None and _any_row_at(rows, cum_comparison_keys)
    ha_cum_comparison = heading_amounts(rows, accounts, cum_comparison_keys) if has_comparison_cum_rows else None

    cta_cur = ha_cum_current["cta"]
    cta_comp = ha_cum_comparison["cta"] if ha_cum_comparison else None

    result_cur = _current_year_result_raw(rows, accounts, period_rows, current)
    result_comp = (
        _current_year_result_raw(rows, accounts, period_rows, comparison)
        if ha_cum_comparison else None
    )

    cta_account, result_account = declared.get("cta_account"), declared.get("result_account")
    cta_heading = (accounts.get(cta_account) or {}).get("parent_account") if cta_account else None
    result_heading = (accounts.get(result_account) or {}).get("parent_account") if result_account else None

    # BS heading raw totals, restricted to BS codes only — the cumulative
    # key window spans the whole history, so ``ha_cum_current["headings"]``
    # also carries P&L heading totals that must never leak into the BS
    # residual (an engineering call, N49: heading_amounts has no section
    # filter, by design — N48 is shared with the P&L "this period" read).
    bs_raw_cur = {code: ha_cum_current["headings"].get(code, 0.0) for code in bs_order}
    bs_raw_comp = (
        {code: ha_cum_comparison["headings"].get(code, 0.0) for code in bs_order}
        if ha_cum_comparison else None
    )

    bs_lines = []
    for code in bs_order:
        entry = accounts[code]
        mult = sides[code]
        raw_cur = bs_raw_cur[code]
        raw_comp = bs_raw_comp[code] if bs_raw_comp is not None else None
        includes = []
        if code == cta_heading:
            raw_cur += cta_cur
            if raw_comp is not None:
                raw_comp += (cta_comp or 0.0)
            includes.append({
                "kind": "cta", "label": _CTA_INCLUDES_LABEL,
                "current": round(mult * cta_cur, 2),
                "comparison": round(mult * cta_comp, 2) if cta_comp is not None else None,
            })
        if code == result_heading:
            raw_cur += result_cur
            if raw_comp is not None:
                raw_comp += (result_comp or 0.0)
            includes.append({
                "kind": "current_year_result", "label": _RESULT_INCLUDES_LABEL,
                "current": round(mult * result_cur, 2),
                "comparison": round(mult * result_comp, 2) if result_comp is not None else None,
            })
        cur = round(mult * raw_cur, 2)
        comp = round(mult * raw_comp, 2) if raw_comp is not None else None
        line = {
            "kind": "heading", "heading": code, "heading_name": entry.get("account_name"),
            "current": cur, "comparison": comp,
            "variance": round(cur - comp, 2) if comp is not None else None,
        }
        if includes:
            line["includes"] = includes
        bs_lines.append(line)

    no_heading_bs_cur = ha_cum_current["no_heading"].get(BS, 0.0)
    if no_heading_bs_cur:
        no_heading_bs_comp = ha_cum_comparison["no_heading"].get(BS, 0.0) if ha_cum_comparison else None
        bs_lines.append({
            "kind": "no_heading", "label": _NO_HEADING_LABEL,
            "current": round(no_heading_bs_cur, 2),
            "comparison": round(no_heading_bs_comp, 2) if no_heading_bs_comp is not None else None,
            "variance": (round(no_heading_bs_cur - no_heading_bs_comp, 2)
                         if no_heading_bs_comp is not None else None),
        })

    not_in_chart_cur = ha_cum_current["not_in_chart"]
    if not_in_chart_cur:
        not_in_chart_comp = ha_cum_comparison["not_in_chart"] if ha_cum_comparison else None
        total_cur = round(sum(not_in_chart_cur.values()), 2)
        total_comp = round(sum(not_in_chart_comp.values()), 2) if not_in_chart_comp else None
        bs_lines.append({
            "kind": "not_in_chart", "label": _NOT_IN_CHART_LABEL,
            "codes": sorted(not_in_chart_cur),
            "current": total_cur, "comparison": total_comp,
            "variance": round(total_cur - total_comp, 2) if total_comp is not None else None,
        })

    # -- residual: the sum of the BS lines, net-debit terms, unflipped -----
    residual_raw = sum(bs_raw_cur.values()) + no_heading_bs_cur + sum(not_in_chart_cur.values())
    explained = []
    if cta_account:
        residual_raw += cta_cur
    else:
        explained.append({"label": _CTA_NOT_PLACED, "amount": round(-cta_cur, 2)})
    if result_account:
        residual_raw += result_cur
    else:
        explained.append({"label": _RESULT_NOT_PLACED, "amount": round(-result_cur, 2)})
    if not_in_chart_cur:
        explained.append({"label": _NOT_IN_CHART_LABEL, "amount": round(sum(not_in_chart_cur.values()), 2)})

    residual_raw = round(residual_raw, 2)
    residual_line = {"kind": "residual", "label": _RESIDUAL_LABEL, "current": residual_raw}
    if residual_raw:
        residual_line["explained"] = explained
        residual_line["unexplained"] = round(residual_raw - sum(e["amount"] for e in explained), 2)
    bs_lines.append(residual_line)

    return {
        "legend": LEGEND,
        "periods": {"current": current, "comparison": comparison, "comparison_note": comparison_note},
        "sections": [
            {"section": PL, "lines": pl_lines},
            {"section": BS, "lines": bs_lines},
        ],
        "gap": declared.get("gap"),
    }
