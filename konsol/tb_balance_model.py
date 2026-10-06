"""A trial balance balances exactly in its declared currency (konsol#180). Pure; host-tested.

Decided by Deepak Pai, 5 Oct 2026 (option #180-1): debits must equal credits
in the minor unit of the trial balance's declared currency,
``ISO Currency.minor_unit`` (the ISO 4217 exponent: 0 for JPY, 2 for EUR, 3
for KWD). The currency is the one the file declares under konsol#252
(konsol.tb_currency_model). There is no tolerance to configure and none to
default: a trial balance exported from a ledger balances exactly, and an
imbalance is a defect to refuse, not a margin to allow. Rejected: #180-2 (a
required per-group tolerance), #180-3 (relative with a floor), #180-4 (a
per-Entity tolerance); each lets a declared amount of imbalance through.

Decided by Deepak Pai, 6 Oct 2026 (option #180-5): lines are never rounded
(one exception, option #180-9 of the same day: a numeric .xlsx cell is read
at Excel's 15 significant digits; see ``read_amount``).
Each amount is read exactly as written (``read_amount``), and a line with more
decimal places than its currency's minor unit is refused, naming the line
("Line 4: debit 0.3333 has 4 decimal places; EUR has 2."). Rejected: #180-6
(round lines to cents, as the parsers did: it altered amounts silently and was
wrong for 3-decimal currencies) and #180-7 (round lines half up to the
currency's places: it still alters the data). Once every line is within the
minor unit the totals are exact in it, so nothing is rounded anywhere.

Accepted trade-offs: an ERP export that rounds lines separately is refused
until the export is fixed (#180-1), and so is one with more decimals than its
currency allows (#180-5).

The rule, in order:

- a minor unit that is not a whole number of places (None, blank, negative,
  fractional, text) is refused naming the currency. It is never taken as 2:
  a silent default would judge JPY in cents and KWD to the wrong place;
- each line's debit and credit may have at most ``minor_unit`` decimal places.
  Trailing zeros are not places: 1.2300 is 1.23, two places, and EUR accepts
  it; 100.00 is a whole number and JPY accepts it. Only a non-zero digit past
  the currency's places is precision the currency does not have. When a line
  is refused the balance is not judged: it cannot be stated in a currency the
  lines are not in;
- the totals are exact decimals, never float sums, and must be equal.

The order (``currency_and_balance_problems``): the declared currency is judged
first, then the lines and the balance in it, because neither can be judged
without a valid currency. A file declaring the wrong currency reports that,
not a misleading sentence about decimals or the balance.

Every intake calls ``currency_and_balance_problems``, so none can accept what
another refuses: the Trial Balance Submission's validate() (Desk, and the bulk
load, which inserts one submission per entity-period), the bulk check
(tb_bulk_model.check_group) and the close app's check_tb / submit_tb. Every
parser reads its amounts with ``read_amount``. Stored reads (a file that
already landed) are read the same way and never judged.

Journals keep their own rule (konsol/close/journal_model.py, Problems P7b);
this module does not touch them.
"""
from decimal import MAX_EMAX, MAX_PREC, MIN_EMIN, Decimal, InvalidOperation, localcontext

from konsol.tb_currency_model import COLUMN as CURRENCY, code, currency_problems


#: The most digits a total, written out in the minor unit, may need. Decimal's
#: default context holds 28, so 1e26 in cents raised InvalidOperation
#: (konsol#180 review). The largest float amount, 1.8e308, needs 312 at 2
#: places; a total beyond this (only an absurd Minor Unit gets there) is
#: refused by name, never attempted.
MAX_DIGITS = 1000


class NotFinite(ValueError):
    """An amount that is a number but not a finite one (NaN, Infinity)."""


def read_amount(value):
    """A cell's amount as an exact Decimal (konsol#180-5, #180-9).

    Text (every CSV cell) is read exactly as written, surrounding spaces
    stripped: "0.3333" stays 0.3333, "1.2300" keeps its zeros, and
    "110.00000000000001" is that. None and blank (whitespace only included)
    are zero. An int (an .xlsx whole-number cell) is exact.

    A float is an .xlsx numeric cell: the only intake that hands this function
    floats is the bulk upload's openpyxl read (tb_bulk._xlsx_rows); CSV cells
    are text. Decided by Deepak Pai, 6 Oct 2026 (option #180-9): it is read
    at Excel's documented precision, 15 significant digits. A formula cell's
    cached value carries binary noise past that, 110.00000000000001 for a
    sheet showing 110.00, and is read as 110; 0.30000000000000004 is 0.3.
    THIS IS THE ONE EXCEPTION to "never rounded at intake" (#180-5), and it
    is for .xlsx cells only. Accepted trade-off: a 16th significant digit is
    lost (1234567890123456 reads as 1234567890123460), which Excel cannot
    hold faithfully either. Rejected: #180-8 (keep refusing such lines) and
    #180-10 (refuse formula cells). The result is judged exactly like any
    other amount: places, storable, balance.

    How: ``format(value, ".15g")`` rounds the float's exact binary value once,
    correctly, to 15 significant digits, and Decimal reads that text
    exactly. Rounding the 17-digit repr instead would round twice.

    Raises ValueError for anything that is not a number (a bool is not one)
    and NotFinite for NaN or Infinity.
    """
    if value is None:
        return Decimal(0)
    if isinstance(value, bool):
        raise ValueError(f"{value!r} is not a number")
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, float):
        amount = Decimal(format(value, ".15g"))      # an .xlsx cell (#180-9)
    elif isinstance(value, int):
        amount = Decimal(value)
    else:
        text = str(value).strip()
        if not text:
            return Decimal(0)
        try:
            amount = Decimal(text)
        except InvalidOperation:
            raise ValueError(f"{text!r} is not a number") from None
    if not amount.is_finite():
        raise NotFinite(f"{value!r} is not a finite number")
    return amount


def decimal_places(amount):
    """The decimal places ``amount`` really has: trailing zeros do not count
    (1.2300 has 2, 100.00 has 0). Exact; never rounds."""
    _, digits, exponent = amount.as_tuple()
    if exponent >= 0 or not any(digits):
        return 0      # a whole number, or zero however written (0.00, 0E-10)
    places, end = -exponent, len(digits)
    while places and end and digits[end - 1] == 0:
        places, end = places - 1, end - 1
    return places


def _written(amount):
    """The amount as the file wrote it, in plain notation unless that would be
    enormous (an exponent beyond MAX_DIGITS)."""
    if amount.adjusted() > MAX_DIGITS or amount.as_tuple().exponent < -MAX_DIGITS:
        return str(amount)
    return format(amount, "f")


def _valid_minor_unit(minor_unit):
    """True for a whole number of places, 0 or more (bool is not a number here)."""
    return isinstance(minor_unit, int) and not isinstance(minor_unit, bool) and minor_unit >= 0


def _places(minor_unit):
    return f"{minor_unit} decimal place{'' if minor_unit == 1 else 's'}"


def exact_total(rows, column):
    """The exact sum of ``column`` over ``rows``, at the largest precision, so
    no amount (1E+1000000 included) rounds or overflows (#180 review F3)."""
    with localcontext() as ctx:
        ctx.prec, ctx.Emax, ctx.Emin = MAX_PREC, MAX_EMAX, MIN_EMIN
        return sum((read_amount(r.get(column)) for r in rows), Decimal(0))


def exact_difference(debit, credit):
    """``debit - credit`` exactly, at the largest precision."""
    with localcontext() as ctx:
        ctx.prec, ctx.Emax, ctx.Emin = MAX_PREC, MAX_EMAX, MIN_EMIN
        return debit - credit


def _stored(amount):
    """What epm_raw's Float64 column would hold for ``amount``."""
    return float(amount)


def place_problems(currency, minor_unit, rows):
    """One sentence per amount that cannot be taken, naming its line (or its
    account when the row carries no line). ``minor_unit`` must already be valid.

    - konsol#180-5: more decimal places than ``currency`` has;
    - #180 review F3: too large for the warehouse's Float64 (it would be inf);
    - #180 review F2: not held exactly by the warehouse's Float64, so it would
      land changed (IDR 100000000000000.01 lands as .02). Refused until
      konsolidat#256 stores decimals.
    """
    problems = []
    for row in rows:
        line = row.get("line")
        where = f"Line {line}" if line is not None else f"Account {row.get('main_account', '?')}"
        for column in ("debit", "credit"):
            amount = read_amount(row.get(column))
            places = decimal_places(amount)
            stored = _stored(amount)
            if places > minor_unit:
                problems.append(f"{where}: {column} {_written(amount)} has "
                                f"{_places(places)}; {currency} has {minor_unit}.")
            elif stored in (float("inf"), float("-inf")):
                problems.append(f"{where}: {column} {_written(amount)} is too large to store.")
            elif Decimal(repr(stored)) != amount:
                problems.append(f"{where}: {column} {_written(amount)} cannot be stored exactly "
                                f"until konsolidat#256 (the warehouse would hold "
                                f"{_written(Decimal(repr(stored)).normalize())}).")
    return problems


def balance_problems(currency, minor_unit, rows):
    """Sentences saying why these rows do not balance in ``currency``; empty
    means they balance exactly. A line with more decimal places than the
    currency has is refused by name first (``place_problems``), and then the
    balance is not judged.

    ``currency`` is the declared ISO code, ``minor_unit`` its
    ``ISO Currency.minor_unit`` as stored (None when there is none). ``rows``
    carry ``debit`` and ``credit``. There is no default minor unit.
    """
    if not _valid_minor_unit(minor_unit):
        held = ("has no Minor Unit" if minor_unit is None or minor_unit == ""
                else f"has a Minor Unit of {minor_unit!r}, not a whole number of decimal places")
        return [
            f"ISO Currency {currency} {held}, so whether this trial balance balances cannot "
            f"be judged. Set its Minor Unit to the ISO 4217 number of decimal places for "
            f"{currency}, then submit again."
        ]
    problems = place_problems(currency, minor_unit, rows)
    if problems:
        return problems
    # Exact sums: addition at the largest precision allocates only the
    # digits the amounts themselves have (konsol#180 review).
    debit, credit = exact_total(rows, "debit"), exact_total(rows, "credit")
    # Written out in the minor unit: every digit left of the point, the minor
    # unit's places, and one for the sign's side of a difference.
    needed = max(debit.adjusted(), credit.adjusted(), 0) + 1 + minor_unit + 1
    if needed > MAX_DIGITS:
        return [
            f"The totals of this trial balance cannot be written out to the minor unit of "
            f"{currency} ({_places(minor_unit)}): that needs {needed} digits, more than "
            f"{MAX_DIGITS}. Check the amounts and ISO Currency {currency}'s Minor Unit."
        ]
    if debit == credit:
        return []
    with localcontext() as ctx:
        # Exact, not a rounding: every line is within the minor unit, so the
        # totals are too, and quantize only writes out the places.
        ctx.prec, ctx.Emax, ctx.Emin = needed, MAX_EMAX, MIN_EMIN
        unit = Decimal(1).scaleb(-minor_unit)
        debit, credit = debit.quantize(unit), credit.quantize(unit)
        heavier = "debits exceed credits" if debit > credit else "credits exceed debits"
        difference = abs(debit - credit)
    return [
        f"Debits ({debit:,} {currency}) do not equal credits ({credit:,} {currency}): "
        f"{heavier} by {difference:,} {currency}. A trial balance must balance exactly in "
        f"{currency} ({_places(minor_unit)})."
    ]


def currency_and_balance_problems(entity, functional_currency, minor_unit, rows):
    """The declared currency's problems (konsol.tb_currency_model) and, only
    when there are none, the balance's in that currency.

    ``functional_currency`` is the Entity's Functional Currency as stored, and
    ``minor_unit`` that currency's ``ISO Currency.minor_unit`` (None when the
    entity has no Functional Currency or the currency has no minor unit). A
    file with no currency problem declares exactly the Functional Currency, so
    its minor unit is the one to judge in.
    """
    declared = [(r.get("line"), r.get(CURRENCY)) for r in rows]
    problems = currency_problems(entity, functional_currency, declared)
    if problems:
        return problems
    # No currency problem: the rows declare exactly the Functional Currency,
    # or there are no rows. Named from the entity either way, so an empty
    # file's sentence names a currency (#180 review F5).
    return balance_problems(code(functional_currency), minor_unit, rows)
