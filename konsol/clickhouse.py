"""Shared ClickHouse write helper for EPM data doctypes.

Provides reusable functions to sync Frappe doctype data to ClickHouse
tables via HTTP API. Supports both legacy gold.* tables (seed replacement)
and epm_staging.* tables (PRD-8+ consolidation/allocation features).

Each data doctype calls sync_doctype() in its on_update / on_trash hook.
"""
from datetime import date, datetime, timezone

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


_WATERMARK_TABLE = "epm_staging.sync_watermark"


def _stamp_watermark(table, row_count, source_max_modified=None):
    """Record that ``table`` was successfully synced, for dbt to check.

    D11 wanted ClickHouse to read Frappe's MariaDB directly so metadata could
    never go stale. That was reversed (F4) — write-through avoids making every
    dbt run a live dependency on the Frappe database — but it gives up the
    "never stale" guarantee. This restores it: every successful sync stamps a
    row here, and a dbt test fails the run when what it claims no longer holds.

    ``_sync_failures`` already tracks breakage, but only in memory in the
    Frappe process: it dies on restart and dbt cannot see it. This is the
    durable, ClickHouse-side half.

    Best-effort — a watermark failure must never break a document save.
    """
    try:
        execute(
            f"CREATE TABLE IF NOT EXISTS {_WATERMARK_TABLE} ("
            "table_name String, "
            "synced_at DateTime, "
            "row_count UInt64, "
            "source_max_modified DateTime"
            ") ENGINE = ReplacingMergeTree(synced_at) ORDER BY table_name"
        )
        src = source_max_modified or "1970-01-01 00:00:00"
        execute(
            f"INSERT INTO {_WATERMARK_TABLE} "
            "(table_name, synced_at, row_count, source_max_modified) VALUES "
            f"('{table}', now(), {int(row_count)}, '{src}')"
        )
    except Exception as e:  # noqa: BLE001 — never break a save over telemetry
        frappe.logger().warning(f"watermark stamp failed for {table}: {e}")


def sync_table(table, columns, rows, source_max_modified=None, force=False):
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
    # in_install / in_import always block: EPM Settings (and the CH password)
    # may not exist yet, and a write here would crash install-app.
    if frappe.flags.in_install or frappe.flags.in_import:
        return
    # in_migrate / in_patch normally block for the same reason, but reconcile
    # runs *after* migrate specifically to repair tables that document events
    # could not reach, so it passes force=True.
    if not force and (frappe.flags.in_migrate or frappe.flags.in_patch):
        return

    try:
        _sync_table_inner(table, columns, rows)
        # Clear any previous failure for this table
        _sync_failures.pop(table, None)
        _stamp_watermark(table, len(rows), source_max_modified)
        return len(rows)
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


def _sql_value(v):
    """Render one Python value as a ClickHouse VALUES literal.

    Datetimes are truncated to whole seconds: ClickHouse's DateTime has
    one-second resolution and rejects a microsecond timestamp outright with a
    400. That is how epm_staging.allocation_runs silently never reconciled —
    every run carries a ``run_at`` straight from Frappe's now_datetime(), and
    the failure was invisible because reconcile_all reported frappe.db.count()
    whenever a sync returned nothing. With that fixed the table announced
    itself on the running stack. (allocation_run._format_run_cell already
    truncated by hand for its own direct-sync path, which is why *that* path
    worked and only the reconcile was broken.)
    """
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, datetime):
        return "'" + v.strftime("%Y-%m-%d %H:%M:%S") + "'"
    if isinstance(v, date):
        return "'" + v.strftime("%Y-%m-%d") + "'"
    escaped = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _sync_table_inner(table, columns, rows):
    """Internal: TRUNCATE and INSERT. Raises on failure."""
    execute(f"TRUNCATE TABLE IF EXISTS {table}")

    if not rows:
        return

    col_list = ", ".join(columns)
    value_rows = [f"({', '.join(_sql_value(v) for v in row)})" for row in rows]

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
    value_rows = [f"({', '.join(_sql_value(v) for v in row)})" for row in rows]

    batch_size = 1000
    for i in range(0, len(value_rows), batch_size):
        batch = value_rows[i:i + batch_size]
        values_sql = ", ".join(batch)
        execute(f"INSERT INTO {table} ({col_list}) VALUES {values_sql}")


def resolve_sync_filters(doctype):
    """Which rows of ``doctype`` belong in ClickHouse. The single answer.

    Two rules, in order:

    * the controller declares ``CH_SYNC_FILTERS`` — governed reference data
      (Dimension Mapping, Cash Flow Category) is published deliberately, and
      only Published rows may reach the warehouse;
    * a *submittable* doctype syncs docstatus=1 (submitted) rows only: drafts
      (0) and cancelled (2) must never reach ClickHouse. Without this the full
      TRUNCATE+INSERT re-synced drafts and cancelled docs — so on_cancel
      re-inserted the just-cancelled row and drafts leaked in on the next
      submit of any doc (grynn-in/konsolidat#92, finding #1). Keyed on the
      doctype's own ``is_submittable`` so it covers every submittable
      consolidation doctype at once (Ownership Period, IC Balance,
      Consolidation Adjustment, Historical Equity Rate, …).

    Both write paths go through here — the document hooks and
    ``reconcile_all`` — because they used to disagree. publish() synced
    Published rows only; reconcile synced *every* row of a non-submittable
    doctype, so one `bench migrate` re-filled epm_staging.cash_flow_categories
    with Drafts and Inactives that no consumer filters out.
    """
    declared = getattr(_controller(doctype), "CH_SYNC_FILTERS", None)
    if declared:
        return dict(declared)
    return {"docstatus": 1} if frappe.get_meta(doctype).is_submittable else None


def _controller(doctype):
    """The doctype's controller class, or None when it has no importable one."""
    from frappe.model.base_document import get_controller

    try:
        return get_controller(doctype)
    except Exception:  # noqa: BLE001 — no controller simply means no declaration
        return None


def sync_doctype(doctype, table, field_map, force=False):
    """Fetch the doctype's warehouse-eligible docs and sync them to ClickHouse.

    Which rows are eligible is ``resolve_sync_filters(doctype)`` — never a
    per-call-site decision.

    Args:
        doctype: Frappe DocType name (e.g. 'Allocation Rule').
        table: ClickHouse table name (e.g. 'gold.allocation_rules').
        field_map: Dict mapping CH column names to Frappe field names.
            e.g. {'allocation_rule_id': 'allocation_rule_id', 'rule_name': 'rule_name'}
    """
    return sync_doctype_filtered(
        doctype, table, field_map,
        filters=resolve_sync_filters(doctype), force=force,
    )


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


def _cell(value):
    """Warehouse cell value: an unset Frappe field is an empty string, not NULL."""
    return "" if value is None else value


def sync_doctype_filtered(doctype, table, field_map, filters=None, force=False):
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

    # Fetch `modified` alongside so the sync can stamp how fresh the source was
    # at the time. Only added when the field_map does not already carry it.
    fetch_fields = list(frappe_fields)
    if "modified" not in fetch_fields:
        fetch_fields.append("modified")

    docs = frappe.get_all(
        doctype,
        filters=filters or {},
        fields=fetch_fields,
        limit_page_length=0,
    )
    rows = []
    for doc in docs:
        # None -> '' , never a literal NULL. An unset Data/Link field arrives as
        # None, and every one of these columns is declared non-Nullable in
        # init-db.sql: the INSERT only survives because ClickHouse's default
        # input_format_null_as_default rewrites NULL to the column default. On a
        # server with that setting off, the INSERT is rejected *after*
        # _sync_table_inner has already TRUNCATEd — so one publish of a mapping
        # with a blank entity silently empties the whole crosswalk. Same
        # convention ReportingHierarchy.resync_staging already uses.
        row = [_cell(doc.get(f)) for f in frappe_fields]
        rows.append(row)

    modified = [d.get("modified") for d in docs if d.get("modified")]
    source_max_modified = (
        max(modified).strftime("%Y-%m-%d %H:%M:%S") if modified else None
    )

    return sync_table(
        table,
        ch_columns,
        rows,
        source_max_modified=source_max_modified,
        force=force,
    )


# ---------------------------------------------------------------------------
# Bootstrap DDL for the F3 reference tables
# ---------------------------------------------------------------------------
# KEEP IN SYNC with clickhouse/init-db.sql in konsolidat — the fresh-install
# owner of these three tables. Docker runs init-db.sql ONLY against an empty
# ClickHouse volume, so every stack that existed before F3 (including the
# demo server and every developer laptop) never receives them: the seeds that
# used to carry this data are deleted, the doctypes now write through, and
# each sync fails with "table does not exist" until someone runs the DDL by
# hand. Creating them here makes the upgrade path idempotent, the same way
# Trial Balance Submission._ensure_tables does for the epm_raw pair.
#
# The column types must match init-db.sql exactly; ORDER BY in particular
# cannot be changed later without dropping the table.
_REFERENCE_TABLE_DDL = {
    "epm_staging.dimension_mappings": (
        "(dimension String, erp_source String, entity String, source_value String, "
        "canonical_value String, canonical_label String, status String) "
        "ENGINE = MergeTree ORDER BY (dimension, erp_source, entity, source_value)"
    ),
    "epm_staging.cash_flow_categories": (
        "(main_account String, cf_category String, cf_line_item String, "
        "is_cash UInt8, sign Int8, status String) "
        "ENGINE = MergeTree ORDER BY main_account"
    ),
    "epm_staging.reporting_hierarchies": (
        "(hierarchy_name String, dimension String, member_code String, "
        "member_label String, parent_member_code String, is_group UInt8, "
        "hierarchy_level UInt16, path String, effective_from String, "
        "effective_to String, is_default UInt8, status String) "
        "ENGINE = MergeTree ORDER BY (hierarchy_name, member_code)"
    ),
}


def ensure_reference_tables():
    """Create the F3 write-through reference tables if they are missing.

    Best-effort and idempotent: CREATE TABLE IF NOT EXISTS never touches an
    existing table, and an unreachable ClickHouse must not fail a migrate — the
    sync that follows reports its own failure. Each statement is guarded on its
    own so a server that refuses CREATE DATABASE (the database already exists
    on every real stack) still gets its tables.
    """
    for sql in [
        "CREATE DATABASE IF NOT EXISTS epm_staging",
        *[f"CREATE TABLE IF NOT EXISTS {t} {body}"
          for t, body in _REFERENCE_TABLE_DDL.items()],
    ]:
        try:
            execute(sql)
        except Exception:  # noqa: BLE001 — never fail a migrate over bootstrap DDL
            frappe.logger().warning(
                f"reference table bootstrap skipped: {sql[:60]}…", exc_info=True)


def reconcile_all():
    """Re-sync every write-through table, regardless of document events.

    Syncing is driven entirely by on_update / on_submit / on_trash. A doctype
    that nobody touches therefore never re-syncs — and if its records vanish in
    a way that skips those hooks (a migration, a fixture reload, a site rebuilt
    against a ClickHouse volume that outlived it), the old rows stay in
    ClickHouse forever with nothing left to edit that would clear them.

    That is not hypothetical: epm_staging.ownership_periods held three rows
    dated 2026-06-19 for records Frappe no longer had, and one of them — AMDE at
    75% — was the ownership percentage every consolidated statement used, because
    gold_consolidated_trial_balance resolves ownership_periods before the
    hierarchy. Frappe's own Consolidation Group said 100%.

    TRUNCATE+INSERT means re-syncing an empty doctype empties its table, so this
    repairs exactly that class of drift. Best-effort per table: one
    misconfigured doctype must not stop the rest.

    Returns a dict of table -> row count synced, for logging and tests.
    """
    from frappe.model.base_document import get_controller

    ensure_reference_tables()

    synced = {}
    for doctype in _write_through_doctypes():
        try:
            cls = get_controller(doctype)
            if getattr(cls, "CH_TABLE", None):
                # Some controllers name the flat map CH_LEGACY_FIELD_MAP
                # ("legacy sync to gold.*"); missing that alias is how three of
                # the ten write-through doctypes silently escaped
                # reconciliation.
                field_map = (getattr(cls, "CH_FIELD_MAP", None)
                             or cls.CH_LEGACY_FIELD_MAP)
                _record(synced, cls.CH_TABLE,
                        sync_doctype(doctype, cls.CH_TABLE, field_map, force=True))

            # The second table. Three controllers use the generic map pattern;
            # Consolidation Group computes its rows (tree walk) and exposes
            # resync_staging() for exactly this call. A doctype at 0 records
            # still syncs — TRUNCATE+INSERT of nothing empties the table,
            # which is the point.
            if getattr(cls, "resync_staging", None):
                _record(synced, cls.CH_STAGING_TABLE, cls.resync_staging(force=True))
            elif getattr(cls, "CH_STAGING_TABLE", None) and getattr(cls, "CH_STAGING_FIELD_MAP", None):
                _record(synced, cls.CH_STAGING_TABLE,
                        sync_doctype(doctype, cls.CH_STAGING_TABLE,
                                     cls.CH_STAGING_FIELD_MAP, force=True))
        except Exception:
            frappe.logger().warning(
                f"reconcile: {doctype} skipped", exc_info=True
            )
    return synced


def _record(synced, table, written):
    """Record what a sync actually wrote — ``None`` when it did not write.

    ``sync_table`` swallows connection/timeout/HTTP failures and returns None.
    This used to substitute ``frappe.db.count(doctype)`` for that None, so a
    reconcile that reached nothing still reported a row count per table and the
    migrate log read as a clean repair. A table that did not sync now says so,
    and the watermark (which is only stamped on success) agrees with it.
    """
    synced[table] = written
    if written is None:
        frappe.logger().warning(
            f"reconcile: {table} NOT synced — see the ClickHouse SYNC FAILED "
            f"entry above, or check_health()"
        )


def _write_through_doctypes():
    """Every konsol doctype whose controller declares a ClickHouse target."""
    # frappe.get_controller does not exist at the top level in v15 — it lives in
    # frappe.model.base_document. Importing it explicitly rather than reaching
    # through frappe keeps the failure loud if that ever moves again.
    from frappe.model.base_document import get_controller

    modules = frappe.get_all(
        "Module Def", filters={"app_name": "konsol"}, pluck="name"
    )
    if not modules:
        frappe.logger().warning("reconcile: no Module Def rows for app 'konsol'")
        return []
    found = []
    for doctype in frappe.get_all(
        "DocType", filters={"module": ["in", modules]}, pluck="name"
    ):
        try:
            cls = get_controller(doctype)
        except Exception:
            # A doctype without an importable controller simply has no CH target.
            continue
        if (getattr(cls, "CH_TABLE", None) and (
                getattr(cls, "CH_FIELD_MAP", None)
                or getattr(cls, "CH_LEGACY_FIELD_MAP", None)))\
                or (getattr(cls, "CH_STAGING_TABLE", None)
                    and getattr(cls, "resync_staging", None)):
            # the second arm: staging-only controllers (Reporting Hierarchy)
            # whose rows are computed and synced via resync_staging()
            found.append(doctype)
    if not found:
        frappe.logger().warning(
            "reconcile: no write-through doctypes discovered — expected several; "
            "check that controllers still declare CH_TABLE/CH_FIELD_MAP"
        )
    return found
