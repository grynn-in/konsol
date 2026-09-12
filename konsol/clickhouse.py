"""Shared ClickHouse write helper for EPM data doctypes.

Provides reusable functions to sync Frappe doctype data to ClickHouse
tables via HTTP API. Supports both legacy gold.* tables (seed replacement)
and epm_staging.* tables (PRD-8+ consolidation/allocation features).

Each data doctype calls sync_doctype() in its on_update / on_trash hook.
"""
from datetime import date, datetime, timezone

import functools

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

    An unset field is written as the ``DEFAULT`` keyword, which means "whatever
    this column declares" and is therefore right for every column type: '' for a
    String, 0 for a number, 1970-01-01 for a Date, NULL for a Nullable. The two
    obvious alternatives are both wrong somewhere:

    * a literal ``NULL`` into a non-Nullable column is accepted only while
      ClickHouse keeps ``input_format_null_as_default`` on, and is rejected
      *after* _sync_table_inner has already TRUNCATEd — one publish of a
      Dimension Mapping with a blank entity emptied the whole crosswalk
      (konsol #112, finding 4);
    * a literal ``''`` fixes that for String columns and breaks every Date one.
      It is why epm_staging.ownership_periods stopped syncing entirely: an
      Ownership Period with no acquisition_date sent '' into a Date column.

    Datetimes are truncated to whole seconds: ClickHouse's DateTime has
    one-second resolution and rejects a microsecond timestamp outright with a
    400. That is how epm_staging.allocation_runs silently never reconciled —
    every run carries a ``run_at`` straight from Frappe's now_datetime(), and
    the failure was invisible because reconcile_all reported frappe.db.count()
    whenever a sync returned nothing. (allocation_run._format_run_cell already
    truncated by hand for its own direct-sync path, which is why *that* path
    worked and only the reconcile was broken.)
    """
    if v is None:
        return "DEFAULT"
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


def after_commit_once(key, fn):
    """Run ``fn()`` once, after the current transaction commits (konsol#124).

    Document hooks must not write ClickHouse directly. ClickHouse has no
    transaction, so an INSERT made in a hook is final at once: a save that later
    rolls back leaves its row in the warehouse. The sync also reads its rows
    through the same uncommitted transaction. After the commit, it publishes
    exactly what MariaDB committed, and a rollback publishes nothing.

    ``CallbackManager.add`` doesn't dedupe, so a bulk edit of N documents would
    queue N full-table syncs. ``key`` keeps one per transaction: the queue
    itself is asked, so there's no marker to go stale after a failed commit.

    Nothing is queued where the inline sync would have been skipped (install,
    import, migrate, patch: ``sync_table``'s own guard): the queue runs at a
    commit after those flags are cleared, so an install would sync fixtures
    before the warehouse exists. reconcile_all repairs the tables after a
    migrate. A queued sync that fails is logged, never raised: the save it
    follows has already committed, and a raise would turn it into an error
    response and drop the syncs queued behind it (#141 review).
    """
    flags = frappe.flags
    if flags.in_install or flags.in_import or flags.in_migrate or flags.in_patch:
        return
    queued = getattr(frappe.db.after_commit, "_functions", ())
    if any(getattr(f, "_konsol_key", None) == key for f in queued):
        return

    def job():
        try:
            fn()
        except Exception:
            # This runs after the request's last commit, so a plain Error Log
            # insert would never be committed. Log to file and defer the row.
            frappe.logger().exception(f"ClickHouse sync after commit failed: {key}")
            frappe.log_error(title=f"ClickHouse sync after commit failed: {key}", defer_insert=True)

    job._konsol_key = key
    frappe.db.after_commit.add(job)


def sync_doctype_after_commit(doctype, table, field_map):
    """``sync_doctype`` for a document hook: once, after the commit (konsol#124)."""
    after_commit_once(("sync_doctype", doctype, table), functools.partial(sync_doctype, doctype, table, field_map))


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
    # An unset field reaches _sql_value as None and is written as DEFAULT — the
    # column's own declared default, whatever its type. See _sql_value: neither
    # a literal NULL nor a literal '' is right for every column here.
    rows = [[doc.get(f) for f in frappe_fields] for doc in docs]

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
    # F2: the consolidation structure. epm_gold.consolidation_groups used to be
    # created by `dbt seed` from seeds/consolidation_groups.csv — the SAME
    # relation konsol TRUNCATE+INSERTs, so every governed build overwrote the
    # live tree with a June CSV and every migrate overwrote it back. The seed is
    # deleted, so this is now the only thing that creates the table.
    "epm_gold.consolidation_groups": (
        "(consolidation_group String, data_area_id String, entity_name String, "
        "reporting_currency String) "
        "ENGINE = MergeTree ORDER BY (consolidation_group, data_area_id)"
    ),
    # F2: the flat tree. Listed here because _RETIRED_COLUMNS ALTERs it — on a
    # stack that has never run dbt the table would not exist, the ALTER would
    # fail, and the sync after it would fail too, invisibly.
    "epm_staging.consolidation_hierarchy": (
        "(consolidation_group String, data_area_id String, "
        "parent_group String DEFAULT '', hierarchy_level UInt8 DEFAULT 1, "
        "path String DEFAULT '', updated_at DateTime DEFAULT now()) "
        "ENGINE = ReplacingMergeTree(updated_at) "
        "ORDER BY (consolidation_group, data_area_id)"
    ),
    # konsolidat#146: two more relations a dbt seed and this write-through both
    # owned — seeds materialise into epm_gold, so seeds/spread_profiles.csv WAS
    # epm_gold.spread_profiles. The seeds are deleted, so nothing else creates
    # these. The other three colliding relations (allocation_rules,
    # ic_elimination_rules, consolidation_adjustments) had no reader left once
    # their dbt models moved to the staging tables, so their legacy write-through
    # is gone entirely rather than given DDL here.
    "epm_gold.spread_profiles": (
        "(profile_id String, profile_name String, fiscal_period Int32, "
        "weight Float32) "
        "ENGINE = MergeTree ORDER BY (profile_id, fiscal_period)"
    ),
    # konsolidat#146: the top-down annual budget, from the Budget Annual Input
    # doctype. It was seeds/budget_annual_input.csv.
    "epm_gold.budget_annual_input": (
        "(scenario_id String, data_area_id String, fiscal_year UInt16, "
        "main_account String, dim_cost_center String, dim_department String, "
        "annual_amount Decimal(18,2), spread_profile_id String, "
        "submitted_by String) "
        "ENGINE = MergeTree ORDER BY (scenario_id, data_area_id, fiscal_year, main_account)"
    ),
    # The bottom-up half, written by Budget Sheet. It has always been a konsol
    # write-through with NOTHING that creates it — no seed, no DDL — which is
    # why gold_spread_budget is one of the three baseline build failures
    # ("Unknown table expression identifier 'epm_gold.budget_monthly_input'").
    "epm_gold.budget_monthly_input": (
        "(scenario_id String, data_area_id String, fiscal_year UInt16, "
        "main_account String, dim_cost_center String, dim_department String, "
        "fiscal_period UInt8, amount Decimal(18,2), layer String) "
        "ENGINE = MergeTree ORDER BY (scenario_id, data_area_id, fiscal_year, layer)"
    ),
    # konsolidat#146: which fiscal calendar each ERP legal entity posts against.
    # It was seeds/entity_fiscal_calendars.csv, and it decides which calendar
    # every GL line is dated into.
    "epm_gold.entity_fiscal_calendars": (
        "(data_area_id String, fiscal_calendar_id String) "
        "ENGINE = MergeTree ORDER BY data_area_id"
    ),
    # konsolidat#146: the ISO 4217 reference list. It was seeds/currencies.csv —
    # the one seed with no second writer, but still a table the warehouse
    # validates against living in the dbt repo rather than the app.
    "epm_gold.currencies": (
        "(currency_code String, currency_name String, symbol String, "
        "minor_unit UInt8) "
        "ENGINE = MergeTree ORDER BY currency_code"
    ),
    "epm_gold.scenario_definitions": (
        "(scenario_id String, scenario_name String, scenario_type String, "
        "is_active Int32) "
        "ENGINE = MergeTree ORDER BY scenario_id"
    ),
    # F2: the link closure that makes consolidation multi-level. One row per
    # (ancestor group, entity, link between them), so dbt can multiply a chain of
    # dated ownership percentages without a recursive CTE.
    "epm_staging.consolidation_ancestry": (
        "(consolidation_group String, data_area_id String, link_group String, "
        "link_data_area_id String, link_depth UInt8, depth UInt8, path String) "
        "ENGINE = MergeTree ORDER BY (consolidation_group, data_area_id, link_depth)"
    ),
    # konsol#110: the governed entity registry, from the Entity doctype. The
    # warehouse knew entities only from ERP extraction, so one with no connector
    # had no accounting currency and was dropped at the consolidation join.
    "epm_staging.entities": (
        "(data_area_id String, entity_name String, parent_entity String, "
        "is_group UInt8, status String, accounting_currency String, "
        "country String, erp_source String) "
        "ENGINE = MergeTree ORDER BY data_area_id"
    ),
}

# Relations a previous release wrote and this one abandoned. Nothing truncates a
# table once its last writer is gone, so the rows sit there forever looking live
# — konsolidat#146 left three of them holding whichever of `dbt seed` and
# `bench migrate` had written last, which is exactly the stale-second-source
# confusion this work exists to remove. Dropped outright; every dbt reader moved
# to the staging tables.
# Retired 11 Sep 2026 (konsolidat#146). This list is permanent and unconditional:
# if a future release legitimately recreates one of these relations it must be
# removed from here first, or every migrate will drop it again. Nothing may
# appear in both this and _REFERENCE_TABLE_DDL —
# test_abandoned_relations_are_dropped_not_left_looking_live asserts that.
_RETIRED_TABLES = (
    "epm_gold.allocation_rules",
    "epm_gold.ic_elimination_rules",
    "epm_gold.consolidation_adjustments",
    "epm_gold.allocation_drivers_headcount",
    "epm_gold.allocation_drivers_revenue",
    "epm_gold.allocation_drivers_sqm",
)

# Columns that a previous release created and F2 retired. ClickHouse keeps a
# column the writer stopped sending, silently filled with its default — an
# ownership percentage that no longer updates is exactly the kind of second
# source of truth this work exists to delete, so drop them outright.
_RETIRED_COLUMNS = {
    # ownership is temporal and lives only in Ownership Period now
    "epm_staging.consolidation_hierarchy": ["effective_ownership_pct"],
    "epm_gold.consolidation_groups": ["ownership_pct", "consolidation_method"],
}


def ensure_reference_tables():
    """Create the write-through reference tables, and drop the retired columns.

    Best-effort and idempotent: CREATE TABLE IF NOT EXISTS never touches an
    existing table, and an unreachable ClickHouse must not fail a migrate — the
    sync that follows reports its own failure. Each statement is guarded on its
    own so a server that refuses CREATE DATABASE (the database already exists
    on every real stack) still gets its tables.
    """
    for sql in [
        "CREATE DATABASE IF NOT EXISTS epm_staging",
        "CREATE DATABASE IF NOT EXISTS epm_gold",
        *[f"CREATE TABLE IF NOT EXISTS {t} {body}"
          for t, body in _REFERENCE_TABLE_DDL.items()],
        *[f"ALTER TABLE {t} DROP COLUMN IF EXISTS {c}"
          for t, cols in _RETIRED_COLUMNS.items() for c in cols],
        *[f"DROP TABLE IF EXISTS {t}" for t in _RETIRED_TABLES],
        *_retired_watermark_cleanup(),
    ]:
        try:
            execute(sql)
        except Exception:  # noqa: BLE001 — never fail a migrate over bootstrap DDL
            frappe.logger().warning(
                f"reference table bootstrap skipped: {sql[:60]}…", exc_info=True)


def _retired_watermark_cleanup():
    """DELETE statements for retired tables' watermark rows — only if any exist.

    A retired table's stamp has to go with it: sync_table writes one per
    successful sync and assert_staging_not_stale compares them, so a row for a
    table that no longer has a writer stays frozen while every live table
    re-stamps, and the test reports it LAGGING and fails the build. It would
    also assert a row count for a relation that no longer exists.

    Checked first rather than issued blind, because ClickHouse records an entry
    in system.mutations for an `ALTER TABLE ... DELETE` even when it matches
    nothing — six of those per `bench migrate`, three times a day, forever. One
    SELECT replaces them, and after the first migrate there is nothing to do.
    """
    try:
        quoted = ", ".join(f"'{t}'" for t in _RETIRED_TABLES)
        stale = execute(
            f"SELECT DISTINCT table_name FROM {_WATERMARK_TABLE} "
            f"WHERE table_name IN ({quoted})"
        )
    except Exception:  # noqa: BLE001 — the watermark table may not exist yet
        return []
    return [
        f"ALTER TABLE {_WATERMARK_TABLE} DELETE WHERE table_name = '{name}'"
        for name in (line.strip() for line in stale.splitlines()) if name
    ]


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
                # The CH_LEGACY_FIELD_MAP alias is gone with the three "legacy
                # sync to gold.*" write-throughs it named (konsolidat#146): each
                # of those relations was also a dbt seed, and every dbt reader
                # moved to the staging table.
                _record(synced, cls.CH_TABLE,
                        sync_doctype(doctype, cls.CH_TABLE, cls.CH_FIELD_MAP,
                                     force=True))

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
        staging = getattr(cls, "CH_STAGING_TABLE", None)
        if (getattr(cls, "CH_TABLE", None) and getattr(cls, "CH_FIELD_MAP", None)) \
                or (staging and getattr(cls, "resync_staging", None)) \
                or (staging and getattr(cls, "CH_STAGING_FIELD_MAP", None)):
            # Three ways to be a write-through doctype, and reconcile_all's body
            # already handles all three: a flat field-mapped gold table, rows
            # COMPUTED by resync_staging (Reporting Hierarchy, Consolidation
            # Group), or a field-mapped STAGING table with no gold counterpart.
            #
            # That last arm was missing. It did not matter while every such
            # controller also had a CH_TABLE — but konsolidat#146 deleted the
            # legacy gold write-through from Allocation Rule, IC Elimination
            # Rule and Consolidation Adjustment, and all three silently dropped
            # out of reconcile with it. Their staging tables would then never be
            # repaired after a fixture import, which is the drift reconcile
            # exists for.
            found.append(doctype)
    if not found:
        frappe.logger().warning(
            "reconcile: no write-through doctypes discovered — expected several; "
            "check that controllers still declare CH_TABLE/CH_FIELD_MAP"
        )
    return found
