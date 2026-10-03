"""One ClickHouse read helper for the close app (konsol#305 X01).

Pure contract, like group_rates.py's ``_ch_rows`` / ``ch_error_names`` /
``_not_built``, which this does not touch (E5-P15): main_account.py and
rates_api.py still import those. This mirrors the same shape for the close
app's own reads, returning dicts (``FORMAT JSON``) rather than
``_ch_rows``'s lists (``FORMAT JSONCompact``).

Nothing here catches a failure: ``rows`` propagates whatever ``execute``
raises, unchanged, so a caller decides what a failure shows. An empty body
or a reply with no ``"data"`` key is also treated as a failure (S4): it
means ClickHouse did not answer the query, not that the query found
nothing, so ``rows`` raises instead of silently reading it as 0 rows. A
real empty result (``{"data": []}``) still comes back as ``[]``.
"""
import json
import re

#: ClickHouse error names meaning "nothing has been built yet": the relation
#: or its database does not exist. Matched as the "(NAME)" token ClickHouse
#: puts in every error, never as a "Code: NN" substring ("Code: 60" also
#: matches 600-609).
NOT_BUILT_ERRORS = frozenset({"UNKNOWN_TABLE", "UNKNOWN_DATABASE"})
_ERROR_NAME = re.compile(r"\(([A-Z][A-Z0-9_]+)\)")
#: The queried table, for the error sentence when the reply itself is bad.
_FROM_TABLE = re.compile(r"\bFROM\s+(\S+)", re.IGNORECASE)


def _queried_model(sql):
    match = _FROM_TABLE.search(sql)
    return match.group(1) if match else "the query"


def rows(sql, params=None):
    """Rows of a ClickHouse SELECT, as dicts.

    Values are bound as HTTP query parameters (``{name:Type}`` in the SQL,
    ``param_<name>`` on the wire), never interpolated. Nothing is caught.

    An empty body, or a parsed reply with no ``"data"`` key, raises
    ``ValueError`` naming the queried model (S4): neither means "0 rows".
    """
    from konsol.clickhouse import execute

    raw = execute(sql + " FORMAT JSON",
                  {f"param_{k}": v for k, v in (params or {}).items()})
    if not raw:
        raise ValueError(
            "ClickHouse returned an empty reply for %s." % _queried_model(sql))
    parsed = json.loads(raw)
    if "data" not in parsed:
        raise ValueError(
            "ClickHouse's reply for %s has no \"data\"." % _queried_model(sql))
    return parsed["data"]


def error_names(exc):
    """The ClickHouse error names in an exception's text, e.g. {"UNKNOWN_TABLE"}."""
    text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
    return set(_ERROR_NAME.findall(text))


def not_built(exc):
    """True when the failure means "nothing built yet" (an unbuilt relation)."""
    return bool(error_names(exc) & NOT_BUILT_ERRORS)
