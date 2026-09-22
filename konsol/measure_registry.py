"""How each measure combines across periods, read from the Measure registry.

Its own module, not a helper in api.py, because both read paths need it and
hierarchy_query must not import konsol.api: that import is what made the
hierarchy tests stop importing on a host with no frappe, and a file that stops
importing is counted as skipped, not failed (konsol#248).

The rules for applying an aggregation are pure and live in
konsol/period_read_model.py; only the registry lookup is here.

``frappe`` is bound at module load, as api.py binds it: the host tests stub
frappe in ``sys.modules`` only while a module is being loaded and restore it
before calling in, so a function-level ``import frappe`` would miss the stub
and fail at call time. hierarchy_query therefore imports this module inside
the function rather than at its own load, which is what keeps it importable
on a host with no frappe (konsol#248).
"""
import frappe

# measure_name -> cube_type, so the per-cell read path does not re-read the
# registry per measure per batch (konsol#251).
_CACHE_KEY = "epm_measure_aggregations"
_TTL = 300  # seconds


def aggregations():
    """measure_name -> declared aggregation (Measure.cube_type), cached.

    One read of the registry per TTL rather than one per measure per batch:
    an Excel sheet arrives as a single batch spanning several measures, and
    this sits on the per-cell path.
    """
    cache = frappe.cache()
    mapping = cache.get_value(_CACHE_KEY)
    if mapping is None:
        mapping = {
            m.measure_name: m.cube_type
            for m in frappe.get_all(
                "Measure", fields=["measure_name", "cube_type"],
                limit_page_length=0,
            )
        }
        cache.set_value(_CACHE_KEY, mapping, expires_in_sec=_TTL)
    return mapping


def aggregation_for(measure_name):
    """The aggregation a measure declares (Measure.cube_type).

    Until konsol#251 both read paths summed every measure regardless of this
    field, so a cumulative measure read over a range came back as the sum of
    its balances. The field was already there and already honoured by Cube;
    only these paths ignored it.

    A measure with no registry row is refused rather than assumed to be a sum:
    the caller has already checked the measure is Published and allowed by its
    fact, so reaching here without a row means the registry changed
    mid-request, not that a default is wanted.
    """
    mapping = aggregations()
    if measure_name not in mapping:
        raise ValueError(
            f"Measure '{measure_name}' is not in the registry, so how to "
            f"aggregate it across periods is undeclared."
        )
    return mapping[measure_name]
