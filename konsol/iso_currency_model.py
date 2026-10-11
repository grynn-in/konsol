"""The ISO 4217 list konsol ships, and the rule it sets for ``minor_unit``.

konsol#330, option #330-1 (Deepak Pai, 11 Oct 2026): the shipped list is the
authority for ``ISO Currency.minor_unit``. ``tabISO Currency.minor_unit`` is
``int NOT NULL DEFAULT 0``, so a row saved with Minor Unit blank was stored as
0 and the trial-balance balance check (#180-1) judged it in whole units.

* A code on the list must carry the list's minor unit. A different value, or
  none, is refused, and the message names both.
* A code not on the list is refused. ISO 4217 is a closed list; a currency
  konsol lacks is added to ``reference_data/iso_currencies.json`` in a release.

``reference_data/iso_currencies.json`` is the one copy. The seed
(konsol.currency_references) reads it through ``reference_rows`` here, so the
rows the seed inserts and the rule that judges them come from the same file.
Pure: no frappe.
"""
import json
import os

REFERENCE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference_data", "iso_currencies.json")


def reference_rows():
    """The shipped ISO list, one dict per currency."""
    with open(REFERENCE_FILE) as f:
        return json.load(f)


def shipped_minor_units(rows=None):
    """{code: minor_unit} from the shipped list (or ``rows``)."""
    return {r["currency_code"]: r["minor_unit"] for r in (reference_rows() if rows is None else rows)}


def _as_whole_number(value):
    """An int, or a string of digits, as an int; anything else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def minor_unit_problems(code, minor_unit, shipped=None):
    """Why ``minor_unit`` is wrong for ``code``; [] when it is the list's value."""
    shipped = shipped_minor_units() if shipped is None else shipped
    if code not in shipped:
        return [f"Currency code {code!r} is not on the ISO 4217 list konsol ships "
                "(reference_data/iso_currencies.json). ISO 4217 is a closed list: use the "
                "code as ISO publishes it, upper case, or ask for the list to be extended."]
    expected = shipped[code]
    value = _as_whole_number(minor_unit)
    if value == expected:
        return []
    if minor_unit is None or minor_unit == "":
        held = "has no Minor Unit"
    else:
        held = f"has Minor Unit {minor_unit!r}" if value is None else f"has Minor Unit {value}"
    return [f"ISO Currency {code} {held}; ISO 4217 gives {code} {expected}. "
            f"Set Minor Unit to {expected}."]
