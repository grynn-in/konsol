"""ISO Currency's minor unit comes from the shipped ISO 4217 list (konsol#330).

Decided by Deepak Pai, 11 Oct 2026 (option #330-1). The list konsol ships
(konsol/reference_data/iso_currencies.json) is the authority for
``ISO Currency.minor_unit``: a code on the list must carry the list's minor
unit, and a code not on the list is refused, because ISO 4217 is a closed list.

Why: the column is ``int NOT NULL DEFAULT 0``, so a Minor Unit left blank was
stored as 0 and judged in whole units by every reader (trial-balance balance,
konsol#180). A stored 0 cannot be told from a real ISO 0 (JPY, KRW, VND).

Pure: no frappe, no konsol imports.
"""


def minor_unit_problem(code, minor_unit, listed):
    """None, or why ``minor_unit`` is refused for ``code``.

    ``listed`` is ``{code: minor_unit}`` from the shipped list. A blank
    ``minor_unit`` (None or "") is never read as 0: it is refused unless the
    list says 0, and even then the message names the list's value."""
    if not code or code not in listed:
        return (f"{code or 'A blank code'} is not on the ISO 4217 list konsol ships. "
                "Use the ISO 4217 alphabetic code (for example USD); konsol accepts only listed codes.")
    expected = listed[code]
    try:
        given = None if minor_unit in (None, "") else int(str(minor_unit))
    except ValueError:
        given = None
    if given == expected:
        return None
    return (f"{code} has a Minor Unit of {expected} under ISO 4217 "
            f"({expected} decimal place{'' if expected == 1 else 's'}); "
            f"it is set to {'nothing' if minor_unit in (None, '') else repr(minor_unit)}. "
            f"Set Minor Unit to {expected}.")
