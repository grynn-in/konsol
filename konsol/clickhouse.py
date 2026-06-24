"""Shared ClickHouse write helper for EPM data doctypes.

Provides reusable functions to sync Frappe doctype data to ClickHouse
tables via HTTP API. Supports both legacy gold.* tables (seed replacement)
and epm_staging.* tables (PRD-8+ consolidation/allocation features).

Each data doctype calls sync_doctype() in its on_update / on_trash hook.
"""
from datetime import datetime, timezone

import frappe
import requests

# Track sync failures for monitoring. Key = table name, value = last error info.
_sync_failures = {}


def get_connection():
    """Read ClickHouse connection settings from EPM Settings.

    Returns dict with host, port, user, password, secure, verify.

    When ``clickhouse_secure`` is enabled the connection uses HTTPS so that
    credentials and query results are not sent in clear text. ``verify`` maps
    to ``clickhouse_verify_tls`` and controls certificate validation (leave on
    unless using a self-signed cert in a trusted network).
    """
    settings = frappe.get_single("EPM Settings")
    host = settings.clickhouse_host or "localhost"
    secure = bool(getattr(settings, "clickhouse_secure", 0))
    # Default to verifying TLS; only relax when explicitly disabled in settings.
    verify = bool(getattr(settings, "clickhouse_verify_tls", 1))
    return {
        "host": host,
        "port": settings.clickhouse_port or "8123",
        "user": settings.clickhouse_user or "default",
        "password": settings.get_password("clickhouse_password", raise_exception=False) or "",
        "secure": secure,
        "verify": verify,
    }


def connection_url(conn):
    """Build the ClickHouse HTTP(S) base URL for a connection dict.

    Uses HTTPS when ``conn['secure']`` is truthy so credentials are encrypted
    in transit. Loopback hosts stay on HTTP by default for local dev.
    """
    scheme = "https" if conn.get("secure") else "http"
    return f"{scheme}://{conn['host']}:{conn['port']}/"


def execute(sql, params=None):
    """Execute a ClickHouse SQL statement via HTTP POST.

    Args:
        sql: SQL string to execute.
        params: Optional dict of query parameters.

    Returns:
        Response text (stripped).
    """
    conn = get_connection()
    url = connection_url(conn)
    query_params = dict(params or {})
    query_params["query"] = sql

    resp = requests.post(
        url,
        params=query_params,
        auth=(conn["user"], conn["password"]),
        timeout=30,
        verify=conn.get("verify", True),
    )
    resp.raise_for_status()
    return resp.text.strip()


def sync_table(table, columns, rows):
    """TRUNCATE and INSERT all rows into a ClickHouse table.

    Best-effort: logs warning on connection failure instead of raising,
    so ClickHouse downtime doesn't break Frappe document saves.

    Args:
        table: Fully qualified table name (e.g. 'gold.allocation_rules').
        columns: List of column names.
        rows: List of tuples/lists matching column order.
    """
    # Skip during app install / migrate / fixture import: fixtures must not
    # push to ClickHouse (EPM Settings — and the CH password — may not be
    # configured yet, and a write here would crash install-app). Explicit
    # syncs (apply_schema, manual bench execute) run outside these phases.
    if (
        frappe.flags.in_install
        or frappe.flags.in_migrate
        or frappe.flags.in_patch
        or frappe.flags.in_import
    ):
        return

    try:
        _sync_table_inner(table, columns, rows)
        # Clear any previous failure for this table
        _sync_failures.pop(table, None)
    except requests.exceptions.ConnectionError as e:
        _record_sync_failure(table, "connection_refused", str(e))
        frappe.logger().error(
            f"ClickHouse SYNC FAILED (connection refused): {table} — "
            f"check ClickHouse is running and EPM Settings are correct"
        )
        frappe.publish_realtime(
            "clickhouse_sync_error",
            {"table": table, "error": "connection_refused", "message": str(e)},
        )
    except requests.exceptions.Timeout as e:
        _record_sync_failure(table, "timeout", str(e))
        frappe.logger().error(
            f"ClickHouse SYNC FAILED (timeout): {table} — "
            f"ClickHouse may be overloaded"
        )
        frappe.publish_realtime(
            "clickhouse_sync_error",
            {"table": table, "error": "timeout", "message": str(e)},
        )
    except requests.exceptions.HTTPError as e:
        _record_sync_failure(table, "http_error", str(e))
        frappe.logger().error(
            f"ClickHouse SYNC FAILED (HTTP {e.response.status_code}): {table} — "
            f"table may not exist yet; run dbt build to create it"
        )
        frappe.publish_realtime(
            "clickhouse_sync_error",
            {"table": table, "error": "http_error", "message": str(e)},
        )


def _sync_table_inner(table, columns, rows):
    """Internal: TRUNCATE and INSERT. Raises on failure."""
    execute(f"TRUNCATE TABLE IF EXISTS {table}")

    if not rows:
        return

    col_list = ", ".join(columns)
    # Build VALUES block — quote strings, pass numbers raw
    value_rows = []
    for row in rows:
        vals = []
        for v in row:
            if v is None:
                vals.append("NULL")
            elif isinstance(v, (int, float)):
                vals.append(str(v))
            else:
                escaped = str(v).replace("\\", "\\\\").replace("'", "\\'")
                vals.append(f"'{escaped}'")
        value_rows.append(f"({', '.join(vals)})")

    batch_size = 1000
    for i in range(0, len(value_rows), batch_size):
        batch = value_rows[i:i + batch_size]
        values_sql = ", ".join(batch)
        execute(f"INSERT INTO {table} ({col_list}) VALUES {values_sql}")


def sync_rows(table, columns, rows, key_columns, key_values):
    """Delete rows matching key, then insert new rows. Incremental sync.

    Unlike sync_table (TRUNCATE all), this only replaces rows matching the
    given key — safe for concurrent writes from different docs.

    Args:
        table: Fully qualified table name (e.g. 'epm_gold.budget_monthly_input').
        columns: List of column names for INSERT.
        rows: List of tuples/lists matching column order.
        key_columns: List of column names forming the unique key.
        key_values: Dict mapping key column names to values for DELETE WHERE.
    """
    try:
        _sync_rows_inner(table, columns, rows, key_columns, key_values)
        _sync_failures.pop(table, None)
    except requests.exceptions.ConnectionError as e:
        _record_sync_failure(table, "connection_refused", str(e))
        frappe.logger().error(
            f"ClickHouse SYNC FAILED (connection refused): {table}"
        )
    except requests.exceptions.Timeout as e:
        _record_sync_failure(table, "timeout", str(e))
        frappe.logger().error(f"ClickHouse SYNC FAILED (timeout): {table}")
    except requests.exceptions.HTTPError as e:
        _record_sync_failure(table, "http_error", str(e))
        frappe.logger().error(
            f"ClickHouse SYNC FAILED (HTTP {e.response.status_code}): {table}"
        )


def _sync_rows_inner(table, columns, rows, key_columns, key_values):
    """Internal: DELETE by key + INSERT. Raises on failure."""
    # Build WHERE clause for DELETE
    where_parts = []
    for col in key_columns:
        val = key_values[col]
        if isinstance(val, (int, float)):
            where_parts.append(f"{col} = {val}")
        else:
            escaped = str(val).replace("\\", "\\\\").replace("'", "\\'")
            where_parts.append(f"{col} = '{escaped}'")
    where_clause = " AND ".join(where_parts)

    execute(f"ALTER TABLE {table} DELETE WHERE {where_clause} SETTINGS mutations_sync = 1")

    if not rows:
        return

    col_list = ", ".join(columns)
    value_rows = []
    for row in rows:
        vals = []
        for v in row:
            if v is None:
                vals.append("NULL")
            elif isinstance(v, (int, float)):
                vals.append(str(v))
            else:
                escaped = str(v).replace("\\", "\\\\").replace("'", "\\'")
                vals.append(f"'{escaped}'")
        value_rows.append(f"({', '.join(vals)})")

    batch_size = 1000
    for i in range(0, len(value_rows), batch_size):
        batch = value_rows[i:i + batch_size]
        values_sql = ", ".join(batch)
        execute(f"INSERT INTO {table} ({col_list}) VALUES {values_sql}")


def sync_doctype(doctype, table, field_map):
    """Fetch all Frappe docs of a doctype and sync to ClickHouse.

    For *submittable* doctypes only docstatus=1 (submitted) rows are synced:
    drafts (0) and cancelled (2) must never reach ClickHouse. Without this the
    full TRUNCATE+INSERT re-synced drafts and cancelled docs — so on_cancel
    re-inserted the just-cancelled row and drafts leaked in on the next submit
    of any doc (grynn-in/konsolidat#92, finding #1). The guard is keyed on the
    doctype's own ``is_submittable`` so it fixes every submittable consolidation
    doctype at once (Ownership Period, IC Balance, Consolidation Adjustment,
    Historical Equity Rate, …) and leaves non-submittable doctypes (always
    docstatus 0, e.g. Consolidation Group) untouched.

    Args:
        doctype: Frappe DocType name (e.g. 'Allocation Rule').
        table: ClickHouse table name (e.g. 'gold.allocation_rules').
        field_map: Dict mapping CH column names to Frappe field names.
            e.g. {'allocation_rule_id': 'allocation_rule_id', 'rule_name': 'rule_name'}
    """
    filters = {"docstatus": 1} if frappe.get_meta(doctype).is_submittable else None
    sync_doctype_filtered(doctype, table, field_map, filters=filters)


def _record_sync_failure(table, error_type, message):
    """Track sync failure for monitoring/health check."""
    _sync_failures[table] = {
        "error_type": error_type,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def check_health():
    """Check ClickHouse connectivity and return health status.

    Returns dict with:
        - status: 'healthy' | 'degraded' | 'down'
        - clickhouse_reachable: bool
        - recent_sync_failures: list of failed tables
        - message: human-readable status
    """
    result = {
        "status": "healthy",
        "clickhouse_reachable": False,
        "recent_sync_failures": [],
        "message": "",
    }

    # Test connectivity
    try:
        resp = execute("SELECT 1")
        result["clickhouse_reachable"] = resp == "1"
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        result["clickhouse_reachable"] = False
        result["status"] = "down"
        result["message"] = "ClickHouse is unreachable"
        return result
    except Exception as e:
        result["clickhouse_reachable"] = False
        result["status"] = "down"
        result["message"] = f"ClickHouse error: {str(e)}"
        return result

    # Check for recent sync failures
    if _sync_failures:
        result["recent_sync_failures"] = [
            {"table": k, **v} for k, v in _sync_failures.items()
        ]
        result["status"] = "degraded"
        result["message"] = f"{len(_sync_failures)} table(s) have sync failures"
    else:
        result["message"] = "All systems operational"

    return result


def sync_doctype_filtered(doctype, table, field_map, filters=None):
    """Fetch filtered Frappe docs and sync to ClickHouse.

    Like sync_doctype() but with optional filters (e.g. docstatus=1 for
    submitted-only sync).

    Args:
        doctype: Frappe DocType name.
        table: ClickHouse table name.
        field_map: Dict mapping CH column names to Frappe field names.
        filters: Optional Frappe filter dict (e.g. {'docstatus': 1}).
    """
    ch_columns = list(field_map.keys())
    frappe_fields = list(field_map.values())

    docs = frappe.get_all(
        doctype,
        filters=filters or {},
        fields=frappe_fields,
        limit_page_length=0,
    )
    rows = []
    for doc in docs:
        row = [doc.get(f) for f in frappe_fields]
        rows.append(row)

    sync_table(table, ch_columns, rows)
