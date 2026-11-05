"""ISO Currency's minor unit comes from the shipped ISO 4217 list (konsol#330).

Decided by Deepak Pai, 11 Oct 2026 (option #330-1). The list konsol ships
(konsol/reference_data/iso_currencies.json) is the authority for
``ISO Currency.minor_unit``: a code the list gives a minor unit must carry it.

The list is ISO 4217 List One in full (SIX Financial Information, the ISO 4217
Maintenance Agency, published 2026-09-17: 178 codes), plus two codes konsol
already shipped that ISO has since withdrawn and history still needs: ANG
(withdrawn 2025-03) and BGN (2026-01), from List Three, each with the minor
unit 2 List One gave them (published 2024-06-25). A withdrawn row carries
``"withdrawn": "YYYY-MM"``.

Declared, never defaulted (PR #384 review; Deepak Pai's standing rule of
5 Nov 2026: the configurable option, a default declared and visible). ISO
4217 sets no minor unit for 13 List One codes ("N.A.": XAU, XDR, XTS ...),
which the list holds as None, and does not list a crypto-asset or an
internal unit at all. For those a site ticks ``minor_unit_declared``
("Minor Unit Not Set by ISO 4217") and enters the minor unit itself, 0 to 9.
A code ISO gives a minor unit can never be declared: the tick is not an
override.

Why: the column is ``int NOT NULL DEFAULT 0``, so a Minor Unit left blank was
stored as 0 and judged in whole units by every reader (trial-balance balance,
konsol#180). A stored 0 cannot be told from a real ISO 0 (JPY, KRW, VND).

Pure: no frappe, no konsol imports.
"""
import re

#: The ISO Currency checkbox, named in every refusal that it would resolve.
DECLARE_LABEL = "Minor Unit Not Set by ISO 4217"

#: The setup-gap code (My work, sign-off readiness) for stored rows that fail.
GAP_CODE = "iso_currency_minor_unit_disagrees"

#: A declared minor unit is a whole number in this range: Frappe keeps 9
#: decimal places at most.
DECLARED_RANGE = (0, 9)

_CODE = re.compile(r"[A-Z0-9]+")


def _whole(minor_unit):
    """The minor unit as an int, or None for a blank or a non-number. A blank
    is never read as 0."""
    if minor_unit in (None, ""):
        return None
    try:
        return int(str(minor_unit))
    except ValueError:
        return None


def _places(n):
    return f"{n} decimal place{'' if n == 1 else 's'}"


def minor_unit_problem(code, minor_unit, listed, declared=False):
    """None, or why ``minor_unit`` is refused for ``code``.

    ``listed`` is ``{code: minor_unit or None}`` from the shipped list (None:
    ISO 4217 sets none). ``declared`` is the ISO Currency checkbox."""
    given = _whole(minor_unit)
    shown = "nothing" if minor_unit in (None, "") else repr(minor_unit)
    on_list = bool(code) and code in listed
    expected = listed.get(code) if on_list else None
    if declared:
        if on_list and expected is not None:
            return (f"{code} has a Minor Unit of {expected} under ISO 4217 ({_places(expected)}), "
                    f"so it cannot be declared here. Untick {DECLARE_LABEL} and set Minor Unit "
                    f"to {expected}.")
        if not code or not _CODE.fullmatch(str(code)):
            return (f"{code or 'A blank code'} is not a currency code: use capital letters and "
                    "digits only, with no spaces.")
        low, high = DECLARED_RANGE
        if given is None or not low <= given <= high:
            return (f"Enter the Minor Unit of {code}: the number of decimal places its amounts "
                    f"are kept to, a whole number from {low} to {high}. It is set to {shown}; "
                    "a blank is not read as 0.")
        return None
    if not on_list:
        return (f"{code or 'A blank code'} is not on the ISO 4217 list konsol ships. "
                "Use the ISO 4217 alphabetic code (for example USD). For a unit ISO 4217 does "
                f"not list (a crypto-asset or an internal unit), tick {DECLARE_LABEL} and enter "
                "its Minor Unit.")
    if expected is None:
        return (f"{code} has no minor unit under ISO 4217 (N.A.). Tick {DECLARE_LABEL} and "
                f"enter the number of decimal places amounts in {code} are kept to.")
    if given == expected:
        return None
    return (f"{code} has a Minor Unit of {expected} under ISO 4217 ({_places(expected)}); "
            f"it is set to {shown}. Set Minor Unit to {expected}.")


def minor_unit_gap(rows, listed):
    """None, or the setup gap naming every stored ISO Currency the rule
    refuses. ``rows``: ``(code, minor_unit, declared)`` per stored row.

    A row like this rounds every amount in its currency to the wrong number
    of places, so the gap blocks sign-off (signoff_gate) and is a My work
    item, until the row is fixed."""
    problems = []
    for code, minor_unit, declared in rows:
        problem = minor_unit_problem(code, minor_unit, listed, bool(declared))
        if problem:
            problems.append((code, problem))
    if not problems:
        return None
    problems.sort()
    return {
        "code": GAP_CODE,
        "currencies": [c for c, _ in problems],
        "message": ("ISO Currency minor unit disagrees with ISO 4217 for "
                    f"{', '.join(c for c, _ in problems)}: amounts in these currencies would be "
                    "rounded to the wrong number of places (konsol#330). "
                    + " ".join(p for _, p in problems)),
    }
