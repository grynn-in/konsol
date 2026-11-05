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
  - the journal balances exactly at the warehouse's ``STORED_PLACES`` (4,
    konsol#353 option C): the debit and credit totals, summed as Decimals at
    4 places, are equal. No line carries more decimal places than the
    journal currency's minor unit (``currency_problems``), so that check is
    exact in the currency's own unit. There is no declared materiality floor here
    (Problems P7b). The Trial Balance's rule is its own
    (konsol/tb_balance_model.py, konsol#180: exact in the declared
    currency's minor unit) and is not the journal's.

Amounts are handled as Decimal, quantized to ``STORED_PLACES``, so 0.10 +
0.20 balances against 0.30 — a plain float sum would not.
"""
import importlib.util as _importlib_util
import os as _os
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def _load_period_name():
    """konsol/close/period_name.py loaded by path (konsol#305 review-w5): the
    one "FY2025 P07" format, reachable even under the host tests' stub
    ``konsol.close`` package."""
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "period_name.py")
    spec = _importlib_util.spec_from_file_location("konsol_close_period_name", path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.period_name


period_name = _load_period_name()


def _load_iso_currency_model():
    """konsol/iso_currency_model.py (pure) loaded by path: the konsol#330
    minor-unit rule, the one judge of a currency's minor unit (PR #387
    re-review), reachable under the host tests' stub packages."""
    path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         "iso_currency_model.py")
    spec = _importlib_util.spec_from_file_location("konsol_iso_currency_model", path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


minor_unit_problem = _load_iso_currency_model().minor_unit_problem


#: The scale of epm_staging.consolidation_adjustments.debit_amount and
#: credit_amount (``Decimal(20,4)``, konsol/clickhouse.py), and the scale at
#: which lines are summed and the balance is judged. konsol#353 option C
#: (Deepak Pai, 5 Nov 2026) widened it from 2, so a journal in a 3-decimal
#: currency (BHD, KWD, OMR) or a 4-decimal one (CLF, UYW) is stored exactly.
#: A journal in a currency with more places would lose them there, so it is
#: refused (option B's guard, kept). test_journal_currency_precision pins
#: this to the DDL.
STORED_PLACES = 4
_STORED_UNIT = Decimal(1).scaleb(-STORED_PLACES)

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


def _stored(amount):
    """A line amount (int, float, str or None) as a Decimal rounded to the
    warehouse's ``STORED_PLACES``. currency_problems refuses a line with more
    places than its currency's minor unit (at most ``STORED_PLACES``), so on
    a journal it accepts this rounding changes nothing (konsol#353)."""
    if amount is None:
        amount = 0
    try:
        return Decimal(str(amount)).quantize(_STORED_UNIT, rounding=ROUND_HALF_UP)
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


#: An amount this large or larger is refused (PR #387). A journal line's
#: amounts and the journal's total_debit / total_credit are Frappe Currency
#: fields, stored as MariaDB decimal(21,9): 12 whole digits, so 10^12 does
#: not fit. It also keeps rounding to Frappe's 9 places (21 digits) inside
#: Python's decimal context (28), which raised InvalidOperation above it.
MAX_AMOUNT = Decimal(10) ** 12
_LIMIT_TEXT = "1,000,000,000,000 (10^12): a Currency field stores 12 whole digits"


def amount_too_large(amount):
    """True when ``amount`` is a number whose size is MAX_AMOUNT or more
    (infinity included). A non-number is not too large: ``_stored`` names it."""
    try:
        value = Decimal(str(amount if amount is not None else 0))
    except InvalidOperation:
        return False
    return not value.is_nan() and abs(value) >= MAX_AMOUNT


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
        oversized = [(label, line.get(column))
                     for column, label in (("debit_amount", "debit"), ("credit_amount", "credit"))
                     if amount_too_large(line.get(column))]
        if oversized:
            for label, amount in oversized:
                problems.append(f"Line {idx}: the {label} {amount} is too large; an amount must be "
                                f"less than {_LIMIT_TEXT}.")
            continue
        debit = _stored(line.get("debit_amount"))
        credit = _stored(line.get("credit_amount"))
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
    """(total_debit, total_credit), each a Decimal rounded to ``STORED_PLACES``.

    Decimals, not floats (PR #394 review): above ~5.5e11 a float cannot hold
    0.0001, so two totals that differ by it would compare equal. The caller
    judges them as Decimals and converts to float only to store them."""
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    for line in lines:
        total_debit += _stored(line.get("debit_amount"))
        total_credit += _stored(line.get("credit_amount"))
    total_debit = total_debit.quantize(_STORED_UNIT, rounding=ROUND_HALF_UP)
    total_credit = total_credit.quantize(_STORED_UNIT, rounding=ROUND_HALF_UP)
    return total_debit, total_credit


def total_problems(total_debit, total_credit):
    """Sentences for a total of ``MAX_AMOUNT`` or more: each line may fit
    while several sum past what the total columns store. ``[]`` when both fit."""
    return [f"The total {label} {amount} is too large; a journal's totals must be less than "
            f"{_LIMIT_TEXT}."
            for label, amount in (("debit", total_debit), ("credit", total_credit))
            if amount_too_large(amount)]


def balance_problem(total_debit, total_credit):
    """None when the totals balance exactly at ``STORED_PLACES`` (so exactly
    in the journal currency's unit), else a sentence naming both."""
    debit = _stored(total_debit)
    credit = _stored(total_credit)
    if debit != credit:
        return (f"The journal does not balance: debit {_text(debit)} vs credit "
                f"{_text(credit)}.")
    return None


def _text(amount):
    """``amount`` (a Decimal) as plain text without padding zeros below the
    cent: 0.0160 reads 0.016, 100.0000 reads 100.00."""
    return format(max(amount.normalize(), amount.quantize(Decimal("0.01")),
                      key=lambda d: -d.as_tuple().exponent), "f")


#: Frappe keeps a Currency field to 9 decimal places (``decimal(21,9)``).
#: Places are counted on the value as stored, so a computed float such as
#: 0.1 + 0.2 (0.30000000000000004) counts as 0.3, one place (PR #387 review).
STORED_BY_FRAPPE = Decimal("1e-9")


def _decimal_places(amount):
    """How many decimal places ``amount`` (int, float, str or None) carries,
    once rounded to the 9 places Frappe stores; trailing zeros do not count."""
    value = Decimal(str(amount if amount is not None else 0))
    value = value.quantize(STORED_BY_FRAPPE, rounding=ROUND_HALF_UP).normalize()
    exponent = value.as_tuple().exponent
    return max(-exponent, 0) if isinstance(exponent, int) else 0


def currency_problems(currency, minor_unit, lines, listed, declared=False):
    """The journal's amounts judged in its currency (konsol#353 option B,
    chosen under Deepak Pai's standing rule of 5 Nov 2026).

    ``currency`` is the journal's currency (the root group's reporting
    currency); ``minor_unit`` and ``declared`` its ``ISO Currency`` row's
    Minor Unit and "Minor Unit Not Set by ISO 4217" tick (None, False when
    there is no row); ``listed`` the shipped ISO 4217 list as
    ``{code: minor_unit or None}``. Refused, each with a sentence:

    - no currency;
    - a minor unit the konsol#330 rule refuses
      (``iso_currency_model.minor_unit_problem``, never a copy of it: PR
      #387 re-review): a blank, one that disagrees with ISO 4217, or one ISO
      sets none for that is not declared (every site's XDR=0 row);
    - a minor unit above ``STORED_PLACES`` (4 since konsol#353 option C): the
      warehouse would drop places;
    - a line amount with more decimal places than the minor unit, so the
      totals ``balance_problem`` compares are exact in the currency's unit
      (two lines of 100.004 and 100.001 no longer balance as 100.00).

    An amount of ``MAX_AMOUNT`` or more is left to ``line_problems``.
    """
    if not currency:
        return ["The journal has no currency: set the reporting currency of its consolidation group."]
    problem = minor_unit_problem(currency, minor_unit, listed, declared=bool(declared))
    if problem:
        return [f"ISO Currency {currency} cannot be used for a journal yet (konsol#330): {problem}"]
    minor_unit = int(str(minor_unit))
    if minor_unit > STORED_PLACES:
        return [f"A journal in {currency} ({minor_unit} decimal places) cannot be stored yet: the "
                f"warehouse keeps journal amounts to {STORED_PLACES} decimal places (konsol#353)."]
    problems = []
    for pos, line in enumerate(lines, start=1):
        idx = line.get("idx", pos)
        for column, label in (("debit_amount", "debit"), ("credit_amount", "credit")):
            if amount_too_large(line.get(column)):
                continue  # line_problems names it
            try:
                places = _decimal_places(line.get(column))
            except InvalidOperation:
                continue  # line_problems names a non-number
            if places > minor_unit:
                problems.append(f"Line {idx}: {label} {line.get(column)} has {places} decimal places; "
                                f"{currency} has {minor_unit}.")
    return problems


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
            f"{period_name(ry, rp)} is not a declared period; declare it, or name "
            "another reversal period."
        )
    if row["period_type"] != "Regular":
        return (
            f"{period_name(ry, rp)} is a {row['period_type']} period; a reversal "
            "posts only into a Regular period."
        )
    if (ry, rp) <= (int(fiscal_year), int(fiscal_period)):
        return (
            f"{period_name(ry, rp)} is not after this journal's period "
            f"{period_name(fiscal_year, fiscal_period)}."
        )
    if row["status"] != "Open":
        return f"{period_name(ry, rp)} is {row['status']}; name an Open period."
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
    reversal". Otherwise "Reverses in FY2025 P07" (``period_name``, never the
    bare ``period_code``, which live is "P07" alone) for the matching
    ``period_rows`` row, or "Reverses in FY2025 P07 (not a declared
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
        return f"Reverses in {period_name(ry, rp)} (not a declared period)"
    return f"Reverses in {period_name(ry, rp)}"


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
        return f"Reverses here from {period_name(fy, fp)} (not a declared period)"
    return f"Reverses here from {period_name(fy, fp)}"


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
        group["net_debit"] += _stored(line.get("debit_amount")) - _stored(line.get("credit_amount"))

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
            "net_debit": float(group["net_debit"].quantize(_STORED_UNIT, rounding=ROUND_HALF_UP)),
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
        section_totals[s] += _stored(h["net_debit"])
    section_present.sort(key=lambda s: _SECTION_ORDER.get(s, 2))
    sections = [
        {"section": s, "net_debit": float(section_totals[s].quantize(_STORED_UNIT, rounding=ROUND_HALF_UP))}
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
