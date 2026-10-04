"""Statement model part 1, pure: which periods feed each column, and the
amount per heading (konsol#305 Wave 4, N48; stories 8.1, D2-4; #305-W4-7 7a).

Imports nothing from frappe or konsol. Loaded by path in
konsol/tests/test_close_statement_model.py, mirroring
konsol/tests/test_fiscal_status_model.py.

Amounts are summed as Decimal (``Decimal(str(amount))``), quantized to 2 dp
only once, at the end — so 0.10 + 0.20 sums to 0.30 exactly, the same
convention as journal_model.py.

This module carries no display sign: every amount here stays a net DEBIT
movement or balance. The display sign (income positive / liabilities
bracketed) is a separate, undecided call for N49 — never encoded here.
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
