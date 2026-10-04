"""The currency a trial balance declares (konsol#252). Pure; host-tested.

Decided by Deepak Pai, 18 Sep 2026: a trial-balance upload declares its
currency, konsol cross-checks it against the Entity's Functional Currency, and
an entity-period whose file says otherwise is refused, naming both values. The
rejected option was taking the Entity's currency silently: a file exported in
the wrong currency looks exactly like a correct one, and every number from it
would be wrong with nothing to detect it.

The declaration is a ``currency`` column on every row, on both intakes (the
single Trial Balance Submission CSV and the bulk upload). A column rather than
a field on the upload because one bulk file holds entities with different
currencies. It is required: a file without it is refused by the parser
(MISSING_HELP says what to add). There is no default and no fallback.

This module judges the declared values for ONE entity-period. Both intakes
call ``currency_problems``, so neither can accept what the other refuses:

- the entity has no Functional Currency: refused, naming the entity, because
  there is nothing to check the file against (the rate gate's silent skip of
  such an entity is konsol#311; this intake does not skip);
- a row gives no currency: refused;
- the rows give more than one currency: refused, one entity-period's trial
  balance is in one currency;
- the one currency given is not the Functional Currency: refused, naming both.

Comparison: surrounding whitespace is stripped and the code is compared in
upper case. ISO Currency stores every code upper case (its name is the ISO 4217
code, konsol/reference_data/iso_currencies.json), ISO 4217 codes are letters
only, and a Frappe Link matches a code case-insensitively too, so "eur" and
"EUR" name the same currency. Nothing else is mapped: "EURO" or "€" is not a
code and is refused as a mismatch.
"""

#: The column every trial-balance row declares its currency in.
COLUMN = "currency"

#: Appended to the parser's "Missing column(s)" refusal when this column is missing.
MISSING_HELP = (
    "Add a currency column giving, on every row, the ISO code of the currency the "
    "amounts are in (e.g. EUR); it must be the entity's Functional Currency."
)


def code(value):
    """A declared currency as compared: stripped and upper case; '' for blank."""
    return str(value or "").strip().upper()


def _where(lines):
    """' on line 2, 5' for the known lines, '' when none is known (the bulk path)."""
    known = [str(n) for n in lines if n is not None]
    if not known:
        return ""
    return f" on line{'' if len(known) == 1 else 's'} {', '.join(known)}"


def currency_problems(entity, functional_currency, declared):
    """Sentences saying why this entity-period's declared currency is refused;
    an empty list means every row declares the entity's Functional Currency.

    ``entity`` is the entity code, ``functional_currency`` its Functional
    Currency as stored (None or '' when it has none). ``declared`` is a list of
    ``(line, value)``, one per row, ``line`` the file line or None when the
    caller has no line numbers (the bulk upload reports per entity-period).
    """
    problems = []
    declared = list(declared)
    blank = [line for line, value in declared if not code(value)]
    if blank:
        problems.append(
            f"The currency is blank{_where(blank)}: every row must give the ISO code of the "
            "currency its amounts are in."
        )
    seen = {}
    for line, value in declared:
        if code(value):
            seen.setdefault(code(value), []).append(line)
    if len(seen) > 1:
        named = "; ".join(f"{c}{_where(lines)}" for c, lines in seen.items())
        problems.append(
            f"The rows give more than one currency ({named}): one entity-period's trial "
            "balance is in one currency."
        )
    expected = code(functional_currency)
    if not expected:
        problems.append(
            f"Entity {entity} has no Functional Currency, so the currency this trial balance "
            "declares cannot be checked. Set it on the Entity, then submit again."
        )
    elif len(seen) == 1:
        (given,) = seen
        if given != expected:
            problems.append(
                f"The file declares {given} but Entity {entity}'s Functional Currency is "
                f"{expected}: export the trial balance in {expected}, or correct the Entity's "
                "Functional Currency."
            )
    return problems


def declared_currency(declared):
    """The one currency ``declared`` gives (stripped, upper case), or '' when the
    rows give none or more than one. Read only after currency_problems is empty."""
    codes = {code(value) for _, value in declared if code(value)}
    return codes.pop() if len(codes) == 1 else ""
