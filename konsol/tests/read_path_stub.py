"""Stubs for the registries the read path consults (konsol#251).

A read used to need neither. It now asks for two declarations:

- **the fiscal calendar** (``EPM Fiscal Year Period``) for which periods a
  token like ``13``, ``CLS`` or ``FY`` means for that year, instead of
  clamping to a hardcoded 1-12;
- **the measure registry** (``Measure.cube_type``) for how a measure combines
  across periods, instead of summing everything.

Both are read through ``frappe.cache()``, so a host test driving
``epm_value``, ``epm_batch``, ``_batch_query_clickhouse`` or the hierarchy
query has to model a cache and those two doctypes. The shapes are declared
once here. A test that wants a different calendar or a different aggregation
passes its own to ``install``.
"""
import types

# The measures the shipped Datasets register, all of them plain sums. Enough
# for any test that does not care about aggregation and only needs the lookup
# to succeed.
DEFAULT_MEASURES = {
    "period_net_amount": "sum",
    "period_debit": "sum",
    "period_credit": "sum",
    "transaction_count": "sum",
    "ytd_net_amount": "last",
    "period_amount": "sum",
    "annual_amount": "sum",
    "variance_abs": "sum",
    "variance_amount": "sum",
    "variance_pct": "avg",
    "variance_favorable": "sum",
    "actual_amount": "sum",
    "budget_amount": "sum",
    "consolidated_amount": "sum",
    "cash_flow_amount": "sum",
    "driver_value": "sum",
}


class MemoryCache:
    """``frappe.cache()`` for a host test: a dict that ignores the TTL.

    Per instance, not shared: a cached registry leaking from one test into the
    next would let a test pass on another's stub.
    """

    def __init__(self):
        self._store = {}

    def get_value(self, key):
        return self._store.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        self._store[key] = value


def measure_rows(measures=None):
    """Rows as ``frappe.get_all("Measure", ...)`` returns them."""
    return [
        types.SimpleNamespace(measure_name=name, cube_type=cube_type)
        for name, cube_type in (measures or DEFAULT_MEASURES).items()
    ]


def calendar_rows():
    """A year of the calendar the live site declares: OPN, P01-P12, CLS.

    Deliberately the shape that has a closing period, so a test that resolves
    ``FY`` proves FY excludes it rather than passing because nothing was
    declared beyond 12.
    """
    rows = [{"fiscal_period": 0, "period_code": "OPN",
             "period_type": "Opening", "quarter": ""}]
    for p in range(1, 13):
        rows.append({
            "fiscal_period": p,
            "period_code": "P%02d" % p,
            "period_type": "Regular",
            "quarter": "Q%d" % ((p - 1) // 3 + 1),
        })
    rows.append({"fiscal_period": 13, "period_code": "CLS",
                 "period_type": "Closing", "quarter": ""})
    return rows


def install(fake_frappe, measures=None, calendar=None):
    """Give a fake frappe module a cache, a Measure registry and a calendar.

    Wraps any ``get_all`` already on the module, so a test that stubs its own
    doctypes keeps its assertions: only the two doctypes the read path added
    are intercepted.
    """
    inner = getattr(fake_frappe, "get_all", None)
    rows = measure_rows(measures)
    periods = calendar_rows() if calendar is None else calendar

    def get_all(doctype=None, *args, **kwargs):
        if doctype == "Measure":
            return list(rows)
        if doctype == "EPM Fiscal Year Period":
            return [dict(r) for r in periods]
        if inner is None:
            return []
        return inner(doctype, *args, **kwargs)

    fake_frappe.get_all = get_all
    fake_frappe.cache = MemoryCache
    return fake_frappe
