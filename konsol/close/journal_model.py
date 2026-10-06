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
    (Problems P7b). The Trial Balance's rule is its own
    (konsol/tb_balance_model.py, konsol#180: exact in the declared
    currency's minor unit) and is not the journal's.

Amounts are handled as Decimal, quantized to 2 dp, so 0.10 + 0.20 balances
against 0.30 — a plain float sum would not.
"""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

_CENTS = Decimal("0.01")

#: What a dimension column may be named, to be written at all. MUST stay
#: identical to schema_apply._SAFE_TB_DIM_COLUMN and
#: tb_dimension_model._LEGAL_DIMENSION_NAME — it is copied, not imported,
#: because this module is pure and those bring frappe or a cycle. \Z not $:
#: `$` also matches before a trailing newline. See
#: journal_dimension_columns for what a drift here would cost.
_LEGAL_DIM_COLUMN = re.compile(r"^dim_[a-z0-9_]+\Z")

MIN_LINES = 2

#: Consolidation Journal Line fields a user may send (consolidation_journal_line.json).
LINE_KEYS = ("data_area_id", "main_account", "debit_amount", "credit_amount", "description")

#: A legal declared dimension key: ``Dimension.dimension_name`` verbatim
#: (konsolidat#245 CONTRACT, konsol-50, 3 Oct; tb_dimension_model.py:55-57).
#: ``\Z`` only — never ``$``, which also matches just before a trailing
#: newline and would wrongly let an illegal key through.
_DIM_KEY_RE = re.compile(r"^dim_[a-z0-9_]+\Z")


def _cents(amount):
    """A line amount (int, float, str or None) as a Decimal rounded to 2 dp."""
    if amount is None:
        amount = 0
    try:
        return Decimal(str(amount)).quantize(_CENTS, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError(f"'{amount}' is not a number")


def clean_lines(lines, dim_keys=()):
    """``(rows, problems)``: the request's lines, kept to ``LINE_KEYS`` plus
    the declared dimension keys (#305 A02; konsolidat#245 option D, D01).

    ``lines`` must be a list of dicts (the API parses JSON first; a str is
    not parsed here). A non-list ``lines``, or a non-dict item, is a single
    problem and contributes no row — never an exception.

    ``dim_keys`` are the dimension keys declared for the journal (injected
    by the caller — this module reads nothing from frappe). Only the ones
    matching the legal pattern ``^dim_[a-z0-9_]+\\Z`` are honoured; an
    illegal one (for example a trailing newline that only a ``$`` anchor
    would accept) is dropped as if it had never been declared. Duplicates
    collapse to one, in first-seen order, after ``LINE_KEYS``.

    A dict with any key outside ``LINE_KEYS`` and the (legal, deduped)
    ``dim_keys`` gives one problem per such key, naming the line and the
    key — a forged doctype field and an undeclared or illegal dimension key
    are refused the same way; the row is still returned, carrying only the
    allowed keys. Within ``LINE_KEYS``, a missing key is ``None`` (#305 A02,
    unchanged). A missing or explicitly-``None`` dimension key is ``''``
    (blank), never ``None`` — dimensions are optional, never typed-checked
    for content. Amounts are passed through unvalidated: ``line_problems``
    and ``balance_problem`` still decide.
    """
    problems = []
    rows = []
    if not isinstance(lines, list):
        return [], ["The journal's lines must be a list."]
    allowed_dim_keys = []
    for key in dim_keys:
        if _DIM_KEY_RE.match(key) and key not in allowed_dim_keys:
            allowed_dim_keys.append(key)
    allowed = LINE_KEYS + tuple(allowed_dim_keys)
    for pos, line in enumerate(lines, start=1):
        if not isinstance(line, dict):
            problems.append(f"Line {pos}: not a valid line.")
            continue
        for key in line:
            if key not in allowed:
                problems.append(f"Line {pos}: {key} cannot be set here.")
        row = {key: line.get(key) for key in LINE_KEYS}
        for key in allowed_dim_keys:
            value = line.get(key)
            row[key] = "" if value is None else value
        rows.append(row)
    return rows, problems


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


def reversal_choices(fiscal_year, fiscal_period, period_rows):
    """``[{"fiscal_year", "fiscal_period", "code"}]``: every period row a
    reversal may name (#305 A02), in period order.

    A row qualifies when ``reversal_problem(fiscal_year, fiscal_period,
    row_fy, row_fp, period_rows) is None`` — Regular, after the journal's own
    period, Open. The rule is reused, never copied. ``code`` is the row's own
    ``period_code``.
    """
    choices = []
    for row in period_rows:
        row_fy = int(row["fiscal_year"])
        row_fp = int(row["fiscal_period"])
        if reversal_problem(fiscal_year, fiscal_period, row_fy, row_fp, period_rows) is None:
            choices.append({
                "fiscal_year": row_fy,
                "fiscal_period": row_fp,
                "code": row["period_code"],
            })
    choices.sort(key=lambda c: (c["fiscal_year"], c["fiscal_period"]))
    return choices


def duration_label(reverse_year, reverse_period, period_rows):
    """The W3-3 duration text for a journal's reversal choice (#305-W3-3
    option A). Never blank.

    ``(0, 0)`` (a blank Int reads as 0) gives "This period only, no
    reversal". Otherwise "Reverses in {period_code}" for the matching
    ``period_rows`` row, or "Reverses in FY{y} P{p:02d} (not a declared
    period)" when no row matches.
    """
    ry = reverse_year or 0
    rp = reverse_period or 0
    if ry == 0 and rp == 0:
        return "This period only, no reversal"
    row = next(
        (r for r in period_rows
         if int(r["fiscal_year"]) == ry and int(r["fiscal_period"]) == rp),
        None,
    )
    if row is None:
        return f"Reverses in FY{ry} P{rp:02d} (not a declared period)"
    return f"Reverses in {row['period_code']}"


def reverses_here_label(fiscal_year, fiscal_period, period_rows):
    """The text a reversal shows in the period it posts into (konsol#305
    story 6.5): "Reverses here from FY{y} P{p:02d}" naming the original
    journal's fiscal year and period. Never the bare ``period_code``: live it
    is "P06" alone (measured 6 Oct), ambiguous across a year boundary where
    P12 reverses into next year's P01 (konsol#305 U1). Never blank: an
    undeclared original period reads "Reverses here from FY{y} P{p:02d} (not
    a declared period)"."""
    fy = int(fiscal_year or 0)
    fp = int(fiscal_period or 0)
    row = next(
        (r for r in period_rows
         if int(r["fiscal_year"]) == fy and int(r["fiscal_period"]) == fp),
        None,
    )
    if row is None:
        return f"Reverses here from FY{fy} P{fp:02d} (not a declared period)"
    return f"Reverses here from FY{fy} P{fp:02d}"


def reversal_lines(lines):
    """The reversal posting of ``lines`` (konsol#305 story 6.5): each line
    copied with ``debit_amount`` and ``credit_amount`` swapped — exactly the
    one ``auto_reversal`` row per line gold_consolidation_adjustments posts in
    the named period (#305-D2-11, V01). The input is not mutated."""
    return [
        dict(line, debit_amount=line.get("credit_amount"), credit_amount=line.get("debit_amount"))
        for line in lines
    ]


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


def journal_dimension_columns(declared, present):
    """The declared journal dimensions that are actually fields, in order. Pure.

    ``declared`` is the Published Dimensions ticked ``in_journal``; ``present``
    the field names Consolidation Journal Line has. The intersection matters
    because the Custom Field sync is queued after the commit (konsol#135): a
    dimension is Published with the flag set before its field exists, and
    selecting a field that does not exist makes ``frappe.get_all`` raise, which
    would take out the whole resync rather than one column.

    An orphan field the declared set no longer names is left out: the column
    keeps its history and stays readable, and nothing new is written to it
    (konsol#255, Deepak Pai's option A).

    A name the warehouse cannot spell is left out too, and this is the one that
    bites (PR #324 review, finding 2). schema_apply refuses a declared name
    failing ``^dim_[a-z0-9_]+$`` and creates no ClickHouse column — but the
    Custom Field sync has no prefix rule, so the Frappe field exists, and a
    non-``dim_`` name is legal on a Dimension outside the trial balance
    (``business_unit`` is the example dimension.py itself gives). Naming such a
    column in the INSERT would hit a table that does not have it, and
    ``clickhouse.sync_table`` swallows that with ``force=False``: every later
    submit, cancel and delete would leave the staging table frozen with no
    visible error. ``get_valid_columns()`` also returns ``name``, ``parent``,
    ``idx``, ``description`` and ``main_account``, so the same rule stops a
    Dimension called ``description`` putting a duplicate column in the list.
    """
    have = set(present or ())
    return tuple(d for d in declared
                 if d in have and _LEGAL_DIM_COLUMN.fullmatch(d or ""))


def staging_columns(declared=()):
    """``STAGING_COLUMNS`` plus one column per declared journal dimension.

    The dimensions go LAST, because `epm_staging.consolidation_adjustments`
    already exists and gains them per-site through
    ``schema_apply._sync_journal_dimension_columns`` — the
    same reason `main_account`'s CH_FIELD_MAP keeps `is_retained_earnings` at
    the end. ``declared`` is the caller's list of Published Dimensions ticked
    ``in_journal``, in a stable order; this module reads no doctype.
    """
    return STAGING_COLUMNS + tuple(declared)


def staging_rows(headers, lines, declared=()):
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
            # konsolidat#245 option D: only the dimensions the site declared,
            # read by name off the line. A dim_* key the site has not declared
            # is ignored rather than written — data never creates configuration
            # (konsol#247). Absent or blank is '', the column's default: blank
            # is a valid declaration, not a missing one.
            *(line.get(d) or "" for d in declared),
        ))
    return rows
