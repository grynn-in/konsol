"""One ClickHouse read helper for the close app (konsol#305 X01).

Pure contract, like group_rates.py's ``_ch_rows`` / ``ch_error_names`` /
``_not_built``, which this does not touch (E5-P15): main_account.py and
rates_api.py still import those. This mirrors the same shape for the close
app's own reads, returning dicts (``FORMAT JSON``) rather than
``_ch_rows``'s lists (``FORMAT JSONCompact``).

Nothing here catches a failure: ``rows`` propagates whatever ``execute``
raises, unchanged, so a caller decides what a failure shows.
"""
import json
import re

#: ClickHouse error names meaning "nothing has been built yet": the relation
#: or its database does not exist. Matched as the "(NAME)" token ClickHouse
#: puts in every error, never as a "Code: NN" substring ("Code: 60" also
#: matches 600-609).
NOT_BUILT_ERRORS = frozenset({"UNKNOWN_TABLE", "UNKNOWN_DATABASE"})
_ERROR_NAME = re.compile(r"\(([A-Z][A-Z0-9_]+)\)")


def rows(sql, params=None):
    """Rows of a ClickHouse SELECT, as dicts.

    Values are bound as HTTP query parameters (``{name:Type}`` in the SQL,
    ``param_<name>`` on the wire), never interpolated. Nothing is caught.
    """
    from konsol.clickhouse import execute

    raw = execute(sql + " FORMAT JSON",
                  {f"param_{k}": v for k, v in (params or {}).items()})
    return json.loads(raw).get("data", []) if raw else []


def error_names(exc):
    """The ClickHouse error names in an exception's text, e.g. {"UNKNOWN_TABLE"}."""
    text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
    return set(_ERROR_NAME.findall(text))


def not_built(exc):
    """True when the failure means "nothing built yet" (an unbuilt relation)."""
    return bool(error_names(exc) & NOT_BUILT_ERRORS)
