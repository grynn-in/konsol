"""A trial balance balances exactly in its declared currency (konsol#180). Pure; host-tested.

Decided by Deepak Pai, 5 Oct 2026 (option #180-1): debits must equal credits
once sum(debit) and sum(credit) are EACH rounded to the minor unit of the trial
balance's declared currency, ``ISO Currency.minor_unit`` (the ISO 4217
exponent: 0 for JPY, 2 for EUR, 3 for KWD). The currency is the one the file
declares under konsol#252 (konsol.tb_currency_model). There is no tolerance to
configure and none to default: a trial balance exported from a ledger
balances exactly, and an imbalance is a defect to refuse, not a margin to
allow. Rejected: #180-2 (a required per-group tolerance), #180-3 (relative
with a floor), #180-4 (a per-Entity tolerance); each lets a declared amount of
imbalance through.

Accepted trade-off: an ERP export that rounds lines separately is refused
until the export is fixed.

The rule:

- the totals are exact decimals, never float sums (ten 0.05 yen are 0.50
  yen and round to 1; in floats they are 0.49999999999999994 and round to 0);
- each total is rounded half up (``ROUND_HALF_UP``, the convention the other
  money models here use) to the minor unit, then the two are compared for
  equality;
- a minor unit that is not a whole number of places (None, blank, negative,
  fractional, text) is refused naming the currency. It is never taken as 2:
  a silent default would judge JPY in cents and KWD to the wrong place.

The order (``currency_and_balance_problems``): the declared currency is judged
first, and the balance only when the currency is good, because the balance
cannot be judged without a valid currency. A file declaring the wrong currency
reports that, not a misleading balance sentence.

Every intake calls ``currency_and_balance_problems``, so none can accept what
another refuses: the Trial Balance Submission's validate() (Desk, and the bulk
load, which inserts one submission per entity-period), the bulk check
(tb_bulk_model.check_group) and the close app's check_tb / submit_tb.

Journals keep their own rule (konsol/close/journal_model.py, Problems P7b);
this module does not touch them.
"""
from decimal import MAX_EMAX, MAX_PREC, MIN_EMIN, ROUND_HALF_UP, Decimal, localcontext

from konsol.tb_currency_model import COLUMN as CURRENCY, currency_problems, declared_currency


#: The most digits a rounded total may need. Decimal's default context holds
#: 28, so 1e26 rounded to cents raised InvalidOperation (konsol#180 review). The
#: largest float amount, 1.8e308, needs 312 at 2 places; a total beyond this
#: (only an absurd Minor Unit gets there) is refused by name, never attempted.
MAX_DIGITS = 1000


def _valid_minor_unit(minor_unit):
    """True for a whole number of places, 0 or more (bool is not a number here)."""
    return isinstance(minor_unit, int) and not isinstance(minor_unit, bool) and minor_unit >= 0


def _amount(value):
    """A row amount as an exact Decimal. A float is taken at its shortest repr,
    which is the number the file gave (the parsers round to cents first)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(value or 0)


def _places(minor_unit):
    return f"{minor_unit} decimal place{'' if minor_unit == 1 else 's'}"


def balance_problems(currency, minor_unit, rows):
    """Sentences saying why these rows do not balance in ``currency``; empty
    means they balance exactly.

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
    with localcontext() as ctx:
        # Exact sums: addition at the largest precision allocates only the
        # digits the amounts themselves have (konsol#180 review).
        ctx.prec, ctx.Emax, ctx.Emin = MAX_PREC, MAX_EMAX, MIN_EMIN
        debit = sum((_amount(r["debit"]) for r in rows), Decimal(0))
        credit = sum((_amount(r["credit"]) for r in rows), Decimal(0))
    # Rounding to the minor unit needs every digit left of the point, the
    # minor unit's places, and one for a carry.
    needed = max(debit.adjusted(), credit.adjusted(), 0) + 1 + minor_unit + 1
    if needed > MAX_DIGITS:
        return [
            f"The totals of this trial balance cannot be rounded to the minor unit of {currency} "
            f"({_places(minor_unit)}): that needs {needed} digits, more than {MAX_DIGITS}. "
            f"Check the amounts and ISO Currency {currency}'s Minor Unit."
        ]
    with localcontext() as ctx:
        ctx.prec, ctx.Emax, ctx.Emin = needed, MAX_EMAX, MIN_EMIN
        unit = Decimal(1).scaleb(-minor_unit)
        debit = debit.quantize(unit, ROUND_HALF_UP)
        credit = credit.quantize(unit, ROUND_HALF_UP)
        if debit == credit:
            return []
        heavier = "debits exceed credits" if debit > credit else "credits exceed debits"
        difference = abs(debit - credit)
    return [
        f"Debits ({debit:,} {currency}) do not equal credits ({credit:,} {currency}): "
        f"{heavier} by {difference:,} {currency}. A trial balance must balance "
        f"exactly once each total is rounded to the minor unit of {currency} "
        f"({_places(minor_unit)})."
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
    return balance_problems(declared_currency(declared), minor_unit, rows)
