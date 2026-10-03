"""Consolidation Journal rules, pure: konsol/close/journal_model.py
(konsol#305 J01; #292 "What validate() gains").

Imports nothing from frappe or konsol. Loaded by path in
konsol/tests/test_close_journal_model.py, mirroring
konsol/tests/test_fiscal_status_model.py.

Rules (#292):
  - each line is a debit *or* a credit: exactly one of debit_amount /
    credit_amount is greater than zero;
  - no negative amount;
  - at least two lines;
  - the journal balances exact to the cent — round(sum debit, 2) ==
    round(sum credit, 2). There is no declared materiality floor here
    (Problems P7b); BALANCE_TOLERANCE in close/tb_model.py is the Trial
    Balance's own rule, not the journal's.

Amounts are handled as Decimal, quantized to 2 dp, so 0.10 + 0.20 balances
against 0.30 — a plain float sum would not.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

_CENTS = Decimal("0.01")

MIN_LINES = 2


def _cents(amount):
    """A line amount (int, float, str or None) as a Decimal rounded to 2 dp."""
    if amount is None:
        amount = 0
    try:
        return Decimal(str(amount)).quantize(_CENTS, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError(f"'{amount}' is not a number")


def line_problems(lines):
    """Refuse a journal whose lines break the rules above.

    ``lines`` are dicts with ``idx``, ``main_account``, ``debit_amount`` and
    ``credit_amount``. Returns a list of sentences, each naming the line and
    the fix. ``[]`` means the lines are clean (the totals may still be
    unbalanced; see ``balance_problem``).
    """
    problems = []
    if len(lines) < MIN_LINES:
        problems.append(
            f"A journal needs at least {MIN_LINES} lines; it has {len(lines)}."
        )
    for pos, line in enumerate(lines, start=1):
        idx = line.get("idx", pos)
        debit = _cents(line.get("debit_amount"))
        credit = _cents(line.get("credit_amount"))
        if debit < 0 or credit < 0:
            problems.append(
                f"Line {idx}: an amount cannot be negative; enter a positive debit or credit."
            )
            continue
        if debit > 0 and credit > 0:
            problems.append(
                f"Line {idx}: enter a debit or a credit, not both."
            )
        elif debit == 0 and credit == 0:
            problems.append(
                f"Line {idx}: enter a debit or a credit."
            )
    return problems


def totals(lines):
    """(total_debit, total_credit), each a float rounded to 2 dp."""
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    for line in lines:
        total_debit += _cents(line.get("debit_amount"))
        total_credit += _cents(line.get("credit_amount"))
    total_debit = total_debit.quantize(_CENTS, rounding=ROUND_HALF_UP)
    total_credit = total_credit.quantize(_CENTS, rounding=ROUND_HALF_UP)
    return float(total_debit), float(total_credit)


def balance_problem(total_debit, total_credit):
    """None when the totals balance to the cent, else a sentence naming both."""
    debit = _cents(total_debit)
    credit = _cents(total_credit)
    if debit != credit:
        return f"The journal does not balance: debit {debit} vs credit {credit}."
    return None


def reversal_pair_problem(reverse_year, reverse_period):
    """None when both the reversal year and period are named, or neither is
    (a blank Int reads as 0). Naming only one is refused (#305-D2-11)."""
    ry = reverse_year or 0
    rp = reverse_period or 0
    if (ry == 0) != (rp == 0):
        return "Name both the reversal year and period, or leave both blank."
    return None


def reversal_problem(fiscal_year, fiscal_period, reverse_year, reverse_period, period_rows):
    """None when the named reversal period is fit to post into, or a
    sentence naming the fix (#305-D2-11).

    ``period_rows`` carry the keys of ``fiscal_calendar.fiscal_period_rows()``
    (``status`` is effective: fiscal_status_model.effective_status).
    """
    pair_problem = reversal_pair_problem(reverse_year, reverse_period)
    if pair_problem:
        return pair_problem
    ry = reverse_year or 0
    rp = reverse_period or 0
    if ry == 0 and rp == 0:
        return None
    row = next(
        (r for r in period_rows
         if int(r["fiscal_year"]) == ry and int(r["fiscal_period"]) == rp),
        None,
    )
    if row is None:
        return (
            f"FY{ry} P{rp} is not a declared period; declare it, or name "
            "another reversal period."
        )
    if row["period_type"] != "Regular":
        return (
            f"FY{ry} P{rp} is a {row['period_type']} period; a reversal "
            "posts only into a Regular period."
        )
    if (ry, rp) <= (int(fiscal_year), int(fiscal_period)):
        return (
            f"FY{ry} P{rp} is not after this journal's period "
            f"FY{fiscal_year} P{fiscal_period}."
        )
    if row["status"] != "Open":
        return f"FY{ry} P{rp} is {row['status']}; name an Open period."
    return None


#: Section order (#305-W3-4 option A): Profit and Loss, then Balance Sheet,
#: then None (no section, including any value that is not one of the two
#: declared Main Account statement_section options).
_SECTION_ORDER = {"Profit and Loss": 0, "Balance Sheet": 1}


def statement_effect(lines, accounts):
    """The journal's change per statement heading (#305 J01, stories 6.1/6.2).

    ``lines`` are dicts with ``main_account``, ``debit_amount`` and
    ``credit_amount``. ``accounts`` maps a line's ``main_account`` to
    ``{"heading": <parent code, or None/"">, "heading_name": <parent's
    account_name, or None>, "statement_section": <"Profit and
    Loss"|"Balance Sheet"|""|None>}`` (the API, A05/A10, builds this from
    each account's ``parent_account``, #305-D2-4).

    An account missing from ``accounts``, or whose ``heading`` is None/"",
    goes under heading None ("no heading"); no line is ever dropped, and a
    heading whose lines net to zero (a reclass inside one heading) is kept.
    It does not decide the sign by account type (E6-P6); the caller labels
    Dr/Cr.

    Returns ``{"headings": [...], "sections": [...], "no_heading": n}``:
    - ``headings``: ``[{"section", "heading", "heading_name", "net_debit"}]``,
      ``net_debit`` a float rounded to 2 dp, ordered by section
      ("Profit and Loss", "Balance Sheet", then None), then within a
      section by ``heading_name`` then ``heading``, with heading None last;
    - ``sections``: ``[{"section", "net_debit"}]``, same section order;
    - ``no_heading``: the number of distinct accounts with no heading.
    """
    groups = {}
    no_heading_accounts = set()
    for line in lines:
        code = line.get("main_account")
        entry = accounts.get(code) or {}
        heading = entry.get("heading") or None
        heading_name = entry.get("heading_name") or None if heading else None
        section = entry.get("statement_section")
        if section not in _SECTION_ORDER:
            section = None
        if heading is None:
            no_heading_accounts.add(code)
        key = (section, heading)
        group = groups.setdefault(key, {"heading_name": heading_name, "net_debit": Decimal("0")})
        if heading_name and not group["heading_name"]:
            group["heading_name"] = heading_name
        group["net_debit"] += _cents(line.get("debit_amount")) - _cents(line.get("credit_amount"))

    def heading_sort_key(item):
        (section, heading), group = item
        return (
            _SECTION_ORDER.get(section, 2),
            0 if heading is not None else 1,
            group["heading_name"] or "",
            heading or "",
        )

    ordered = sorted(groups.items(), key=heading_sort_key)
    headings = [
        {
            "section": section,
            "heading": heading,
            "heading_name": group["heading_name"],
            "net_debit": float(group["net_debit"].quantize(_CENTS, rounding=ROUND_HALF_UP)),
        }
        for (section, heading), group in ordered
    ]

    section_totals = {}
    section_present = []
    for h in headings:
        s = h["section"]
        if s not in section_totals:
            section_totals[s] = Decimal("0")
            section_present.append(s)
        section_totals[s] += _cents(h["net_debit"])
    section_present.sort(key=lambda s: _SECTION_ORDER.get(s, 2))
    sections = [
        {"section": s, "net_debit": float(section_totals[s].quantize(_CENTS, rounding=ROUND_HALF_UP))}
        for s in section_present
    ]

    return {
        "headings": headings,
        "sections": sections,
        "no_heading": len(no_heading_accounts),
    }


#: The columns the journal writes to epm_staging.consolidation_adjustments:
#: J05a's DDL order (clickhouse._REFERENCE_TABLE_DDL) without ``created_at``,
#: which the column's DEFAULT now() fills (konsol#305 J05, #305-D2-11/12).
STAGING_COLUMNS = (
    "consolidation_group", "adjustment_type", "journal_id", "data_area_id",
    "fiscal_year", "fiscal_period", "main_account", "debit_amount",
    "credit_amount", "description", "posted_by", "status", "approved_by",
    "approved_at", "reversal_journal_id", "reverse_fiscal_year",
    "reverse_fiscal_period",
)


def staging_rows(headers, lines):
    """One tuple per journal line, in ``STAGING_COLUMNS`` order.

    ``headers`` are the submitted journals (dicts with ``name``,
    ``consolidation_group``, ``adjustment_type``, ``fiscal_year``,
    ``fiscal_period``, ``description``, ``owner``, ``status``,
    ``approved_by``, ``approved_at``, ``reverse_fiscal_year`` and
    ``reverse_fiscal_period``); ``lines`` their line rows (``parent``,
    ``idx``, ``data_area_id``, ``main_account``, ``debit_amount``,
    ``credit_amount``, ``description``), in the order to write them.

    - ``journal_id`` is the journal's name; ``data_area_id`` the line's
      entity (#305-D2-12); ``posted_by`` the journal's owner (D2-3);
    - ``description`` is the line's, else the header's;
    - ``reversal_journal_id`` is blank: dbt generates the reversal rows;
    - the reversal pair is the header's (0/0 = no reversal, #305-D2-11);
    - ``status`` is always ``'Approved'``: only submitted journals are
      written, whatever the site calls that workflow state;
    - an empty ``approved_at`` stays None, which the insert writes as the
      column's DEFAULT.

    A line whose journal is not among ``headers`` (a draft's) is skipped.
    """
    by_name = {h["name"]: h for h in headers}
    rows = []
    for line in lines:
        header = by_name.get(line["parent"])
        if header is None:
            continue
        rows.append((
            header["consolidation_group"],
            header["adjustment_type"],
            header["name"],
            line["data_area_id"],
            header["fiscal_year"],
            header["fiscal_period"],
            line["main_account"],
            line["debit_amount"],
            line["credit_amount"],
            line.get("description") or header.get("description") or "",
            header["owner"],
            # The warehouse contract's status, not the site's workflow label:
            # resync writes only submitted journals, and dbt keeps only
            # Approved/Reversed (review finding 4, 27 Sep).
            "Approved",
            header.get("approved_by") or "",
            header.get("approved_at") or None,
            "",
            header.get("reverse_fiscal_year") or 0,
            header.get("reverse_fiscal_period") or 0,
        ))
    return rows
