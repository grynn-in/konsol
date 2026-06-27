"""FX surfacing — read view + query builder (PRD-18, konsolidat#91 B/C).

Surfaces the consolidation FX rates the dbt silver layer derives from D365 so
the konsol-exec SPA can show *which* rate translated each entity. The silver
model ``epm_silver.silver_exchange_rates`` (konsolidat#107) carries both the
directly-quoted pairs and the synthesised inverse pairs, with columns
``from_currency`` / ``to_currency`` / ``exchange_rate`` / ``valid_from`` /
``valid_to`` / ``exchange_rate_type``.

This module lands the **read view** only:

- :func:`build_fx_query` — the **pure testable core**. Builds an injection-safe
  ``SELECT`` over the silver table, filterable by from/to currency, as-of date,
  rate type and source. Currency codes are validated against ``^[A-Z]{3}$`` and
  every other filter is validated against a strict allow-list pattern, so no
  caller-supplied value reaches the SQL string unchecked.
- :func:`normalize_fx_rows` — shape raw ClickHouse result rows (tuples + a
  column-name header) into the canonical ``{from, to, rate, as_of, type,
  source}`` dicts the SPA consumes, tolerating the empty case.
- :func:`get_fx_rates` — the frappe/ClickHouse-bound ``@whitelist()`` API. Like
  the rest of the orchestrator core this module imports on the host **without**
  frappe: the ``whitelist`` decorator degrades to a no-op and the
  ``from konsol.clickhouse import execute`` call lives inside the function.

Part C (a manual-entry Exchange Rate doctype UNION'd in as a ``manual``
``erp_source``) is a follow-up — the ``source`` column is already projected
(constant ``'d365'`` for the silver rows) so the future UNION + filter slots in
without changing this read view's shape.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

try:  # frappe only exists inside a bench; host pytest must still import this module
    import frappe

    whitelist = frappe.whitelist
except Exception:  # pragma: no cover - host import path (no bench)

    def whitelist(*dargs, **dkwargs):
        def deco(fn):
            return fn

        return deco


# Fully-qualified silver FX table (konsolidat#107 — includes inverse pairs).
FX_TABLE = "epm_silver.silver_exchange_rates"

# Constant erp_source label for the silver rows. Part C will UNION a 'manual'
# source in; the column already exists here so the SPA filter is forward-stable.
SILVER_SOURCE = "d365"

# Validation patterns — every caller-supplied filter must match one of these
# before it is interpolated, so the query is injection-safe.
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9 _.-]+$")


def _validate_currency(code) -> str:
    """Return the upper-cased 3-letter ISO code or raise ``ValueError``."""
    s = str(code).strip().upper()
    if not _CURRENCY_RE.match(s):
        raise ValueError(
            f"invalid currency code: {code!r} (expected 3 uppercase letters)"
        )
    return s


def _validate_date(value) -> str:
    """Return an ISO ``YYYY-MM-DD`` date string or raise ``ValueError``."""
    s = str(value).strip()
    if not _DATE_RE.match(s):
        raise ValueError(f"invalid as-of date: {value!r} (expected YYYY-MM-DD)")
    return s


def _validate_token(value, what: str) -> str:
    """Return a safe alphanumeric token or raise ``ValueError``."""
    s = str(value).strip()
    if not _TOKEN_RE.match(s):
        raise ValueError(f"invalid {what}: {value!r}")
    return s


def build_fx_query(filters: Optional[Dict] = None) -> str:
    """Build an injection-safe ``SELECT`` over :data:`FX_TABLE`.

    ``filters`` may carry any of ``from_currency`` / ``to_currency`` (3-letter
    ISO codes), ``as_of`` (``YYYY-MM-DD`` — rates effective on or before),
    ``rate_type`` and ``source``. Each value is validated before interpolation;
    a value that fails its pattern raises ``ValueError`` (no unchecked value
    ever reaches the SQL string). An empty / ``None`` ``filters`` yields the
    unfiltered query.
    """
    filters = filters or {}
    where: List[str] = []

    fc = filters.get("from_currency")
    if fc:
        where.append(f"from_currency = '{_validate_currency(fc)}'")

    tc = filters.get("to_currency")
    if tc:
        where.append(f"to_currency = '{_validate_currency(tc)}'")

    asof = filters.get("as_of")
    if asof:
        where.append(f"as_of <= '{_validate_date(asof)}'")

    rt = filters.get("rate_type")
    if rt:
        where.append(f"rate_type = '{_validate_token(rt, 'rate type')}'")

    src = filters.get("source")
    if src:
        where.append(f"source = '{_validate_token(src, 'source')}'")

    sql = (
        "SELECT from_currency, to_currency, rate, as_of, rate_type, source\n"
        "FROM (\n"
        "    SELECT\n"
        "        from_currency,\n"
        "        to_currency,\n"
        "        exchange_rate AS rate,\n"
        "        valid_from AS as_of,\n"
        "        exchange_rate_type AS rate_type,\n"
        f"        '{SILVER_SOURCE}' AS source\n"
        f"    FROM {FX_TABLE}\n"
        ") AS fx"
    )
    if where:
        sql += "\nWHERE " + " AND ".join(where)
    sql += "\nORDER BY from_currency, to_currency, as_of"
    return sql


# Map raw / aliased ClickHouse column names to the canonical output keys.
_NORMALIZE_MAP = {
    "from_currency": "from",
    "to_currency": "to",
    "rate": "rate",
    "exchange_rate": "rate",
    "as_of": "as_of",
    "valid_from": "as_of",
    "rate_type": "type",
    "exchange_rate_type": "type",
    "source": "source",
}
_OUT_KEYS = ("from", "to", "rate", "as_of", "type", "source")


def normalize_fx_rows(rows: Optional[Sequence], cols: Optional[Sequence]) -> List[Dict]:
    """Shape raw CH result rows + a column-name header into canonical dicts.

    Each output dict carries all of ``{from, to, rate, as_of, type, source}``
    (missing columns default to ``None``). Tolerant of an empty / ``None``
    ``rows`` (→ ``[]``).
    """
    rows = rows or []
    cols = list(cols or [])
    out: List[Dict] = []
    for row in rows:
        rowdict = dict(zip(cols, row))
        rec = {k: None for k in _OUT_KEYS}
        for col, val in rowdict.items():
            key = _NORMALIZE_MAP.get(col)
            if key:
                rec[key] = val
        out.append(rec)
    return out


@whitelist()
def get_fx_rates(**filters) -> List[Dict]:
    """Read-only: return the FX rates for the SPA, filtered + normalized.

    Builds the safe query via :func:`build_fx_query`, runs it against ClickHouse
    (``konsol.clickhouse.execute``, function-local import so the host core stays
    frappe-free) with ``TabSeparatedWithNames`` so the header gives us the
    column names, then shapes the rows via :func:`normalize_fx_rows`.
    """
    from konsol.clickhouse import execute

    sql = build_fx_query(filters)
    raw = execute(sql + "\nFORMAT TabSeparatedWithNames")
    lines = (raw or "").splitlines()
    if not lines:
        return []
    cols = lines[0].split("\t")
    rows = [line.split("\t") for line in lines[1:] if line]
    return normalize_fx_rows(rows, cols)
