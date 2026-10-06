"""The one server-side period label (konsol#305 review-w5): "FY2025 P07".

The live ``period_code`` is "P07" alone (measured 6 Oct), ambiguous across a
year boundary (P12 reverses into next year's P01). Every server label names
a period from its fiscal year and period through ``period_name``; no module
keeps its own copy of the format. The client's twin is
close-ui/src/periodName.js.

Imports nothing: callers load it by path (``_load_sibling``), so the stub
``konsol.close`` packages the host tests build never need to know of it.
"""


def period_name(fiscal_year, fiscal_period):
    """``"FY%d P%02d"`` of the two parts, each read as a whole number. A
    missing or non-numeric part raises (TypeError/ValueError), never
    "FYNone"."""
    return "FY%d P%02d" % (int(fiscal_year), int(fiscal_period))
