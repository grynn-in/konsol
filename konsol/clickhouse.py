"""Shared ClickHouse write helper for EPM data doctypes.

Provides reusable functions to sync Frappe doctype data to ClickHouse
tables via HTTP API. Supports both legacy gold.* tables (seed replacement)
and epm_staging.* tables (PRD-8+ consolidation/allocation features).

Each data doctype calls sync_doctype() in its on_update / on_trash hook.
"""
import frappe
import requests


def get_connection():
    """Read ClickHouse connection settings from EPM Settings.

    Returns dict with host, port, user, password.
    """
    settings = frappe.get_single("EPM Settings")
    return {
        "host": settings.clickhouse_host or "localhost",
        "port": settings.clickhouse_port or "8123",
        "user": settings.clickhouse_user or "default",
        "password": settings.get_password("clickhouse_password") or "",
    }


def execute(sql, params=None):
    """Execute a ClickHouse SQL statement via HTTP POST.

    Args:
        sql: SQL string to execute.
        params: Optional dict of query parameters.

    Returns:
        Response text (stripped).
    """
    conn = get_connection()
    url = f"http://{conn['host']}:{conn['port']}/"
    query_params = dict(params or {})
    query_params["query"] = sql

    resp = requests.post(
        url,
        params=query_params,
        auth=(conn["user"], conn["password"]),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text.strip()


def sync_table(table, columns, rows):
    """TRUNCATE and INSERT all rows into a ClickHouse table.

    Args:
        table: Fully qualified table name (e.g. 'gold.allocation_rules').
        columns: List of column names.
        rows: List of tuples/lists matching column order.
    """
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


def sync_doctype(doctype, table, field_map):
    """Fetch all Frappe docs of a doctype and sync to ClickHouse.

    Args:
        doctype: Frappe DocType name (e.g. 'Allocation Rule').
        table: ClickHouse table name (e.g. 'gold.allocation_rules').
        field_map: Dict mapping CH column names to Frappe field names.
            e.g. {'allocation_rule_id': 'allocation_rule_id', 'rule_name': 'rule_name'}
    """
    ch_columns = list(field_map.keys())
    frappe_fields = list(field_map.values())

    docs = frappe.get_all(doctype, fields=frappe_fields, limit_page_length=0)
    rows = []
    for doc in docs:
        row = [doc.get(f) for f in frappe_fields]
        rows.append(row)

    sync_table(table, ch_columns, rows)


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
