"""Amount basis of a trial balance (konsolidat#199). Pure; host-tested.

Every trial-balance row is read by the warehouse as a period movement, but
ERPs export three different things: this period's movements, the year-to-date
movements, or the closing balance at period end. Nothing in konsol said which
a batch was, so a file of period-end balances was summed as if each month were
new activity and every balance downstream was counted many times over. Each
Trial Balance Submission therefore declares its amount basis, the file may
repeat it in an optional ``amount_basis`` column that must agree with the
form, and the warehouse normalises all three into period movements.

There is no default: a site default in EPM Settings only pre-fills the form.
"""

AMOUNT_BASES = ("Period movement", "Year-to-date movement", "Period-end balance")

COLUMN = "amount_basis"
ALIASES = ("amount_basis", "basis", "amount basis")

_BY_KEY = {basis.casefold(): basis for basis in AMOUNT_BASES}


def _key(value):
    return " ".join(str(value).split()).casefold() if value is not None else ""


def canonical(value):
    """The exact basis string for a case- and space-insensitive match, else None."""
    return _BY_KEY.get(_key(value))


def _allowed():
    return ", ".join(f'"{basis}"' for basis in AMOUNT_BASES)


def basis_problems(form_basis, column_values):
    """Sentences describing what is wrong with the declared basis.

    ``form_basis`` is the value on the submission form; ``column_values`` is a
    list of ``(lineno, value)`` from the file's optional amount_basis column.
    An empty list means the file is consistent with the form.
    """
    problems = []
    form = canonical(form_basis)
    if not _key(form_basis):
        problems.append(
            "Amount Basis is required: say whether the file holds period movements, "
            "year-to-date movements or period-end balances."
        )
    elif form is None:
        problems.append(
            f'Amount Basis "{str(form_basis).strip()}" is not one of {_allowed()}.'
        )

    seen_bad = set()
    seen_blank = False
    seen_differing = set()
    for lineno, value in column_values:
        key = _key(value)
        if not key:
            if not seen_blank:
                seen_blank = True
                problems.append(
                    f"Line {lineno}: the amount_basis column is blank; "
                    f"leave the column out or give one of {_allowed()} on every row."
                )
            continue
        basis = _BY_KEY.get(key)
        if basis is None:
            if key not in seen_bad:
                seen_bad.add(key)
                problems.append(
                    f'Line {lineno}: amount_basis "{str(value).strip()}" is not one of {_allowed()}.'
                )
            continue
        if form is not None and basis != form and basis not in seen_differing:
            seen_differing.add(basis)
            problems.append(
                f'Line {lineno}: the file says "{basis}" but the form says "{form}"; '
                "one file holds one amount basis, so fix the form or the file."
            )
    return problems
