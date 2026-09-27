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
