"""The one definition of "this currency has a magnitude reference" (konsol#103).

ISO Currency.usd_log10 is roughly log10 of a currency's units per 1 USD, the
reference of the group exchange rate guard. A currency has NO reference when
its value is NULL, NaN, or 0 for any currency but USD (USD is the anchor, and
really is 0). The same rule as the warehouse's (konsolidat
macros/fx_magnitude.sql, fx_has_reference): the shared case table
(tests/fx_magnitude_cases.json) runs both.

Frappe-free, so the rule module (group_rates) and the seed
(currency_references) share it, and a host test can load it.
"""
import math

REFERENCE_CURRENCY = "USD"


def usd_reference(code, value):
    """A currency's usd_log10 as a float, or None when it has no reference."""
    if value in (None, ""):
        return None
    value = float(value)
    if math.isnan(value) or (value == 0 and code != REFERENCE_CURRENCY):
        return None
    return value


def is_unset(code, value):
    """True when ``value`` is no reference for ``code``: usd_reference is None."""
    return usd_reference(code, value) is None
