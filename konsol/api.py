"""EPM API — Frappe proxy to ClickHouse for Excel batch retrieval."""
import json
import re
from collections import defaultdict

import frappe
import requests

from konsol.clickhouse import get_connection as _get_ch_connection

MAX_BATCH_SIZE = 2000

# Period ranges: Q1-Q4, H1-H2, FY → tuple of fiscal_period integers
PERIOD_RANGES = {
    "Q1": (1, 2, 3),
    "Q2": (4, 5, 6),
    "Q3": (7, 8, 9),
    "Q4": (10, 11, 12),
    "H1": (1, 2, 3, 4, 5, 6),
    "H2": (7, 8, 9, 10, 11, 12),
    "FY": (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12),
}


def _resolve_period(period):
    """Resolve period to a tuple of fiscal_period integers.

    Accepts: 1-12 (single), "Q1"-"Q4", "H1"-"H2", "FY".
    Returns tuple of ints, e.g. (1,) or (1,2,3).
    """
    s = str(period).strip().upper()
    if s in PERIOD_RANGES:
        return PERIOD_RANGES[s]
    return (int(period),)


# Allowed measure columns per scenario (whitelist to prevent SQL injection)
ALLOWED_MEASURES = {
    "actuals": {
        "period_debit", "period_credit", "period_net_amount",
        "transaction_count", "ytd_debit", "ytd_credit", "ytd_net_amount",
    },
    "budget": {
        "period_amount", "annual_amount",
    },
    "variance": {
        "actual_amount", "budget_amount", "variance_abs", "variance_pct",
        "variance_favorable",
    },
}

SCENARIO_TABLES = {
    "actuals": "epm_gold.gold_trial_balance",
    "budget": "epm_gold.gold_spread_budget",
    "variance": "epm_gold.gold_variance_analysis",
}

# Tables that have a scenario_id column and support filtering by it
TABLES_WITH_SCENARIO_ID = {"epm_gold.gold_spread_budget"}


def _validate_scenario(scenario):
    """Validate scenario against known values. Raises on invalid."""
    if scenario not in SCENARIO_TABLES:
        frappe.throw(
            f"Invalid scenario '{scenario}'. "
            f"Allowed: {', '.join(sorted(SCENARIO_TABLES))}",
            frappe.ValidationError,
        )


def _validate_measure(measure, scenario):
    """Validate measure against whitelist. Raises on invalid."""
    allowed = ALLOWED_MEASURES.get(scenario, ALLOWED_MEASURES["actuals"])
    if measure not in allowed:
        frappe.throw(
            f"Invalid measure '{measure}' for scenario '{scenario}'. "
            f"Allowed: {', '.join(sorted(allowed))}",
            frappe.ValidationError,
        )


def _check_scenario(scenario):
    """Return error string if scenario is invalid, else None."""
    if scenario not in SCENARIO_TABLES:
        return f"Invalid scenario '{scenario}'. Allowed: {', '.join(sorted(SCENARIO_TABLES))}"
    return None


def _check_measure(measure, scenario):
    """Return error string if measure is invalid, else None."""
    allowed = ALLOWED_MEASURES.get(scenario, ALLOWED_MEASURES["actuals"])
    if measure not in allowed:
        return f"Invalid measure '{measure}' for scenario '{scenario}'. Allowed: {', '.join(sorted(allowed))}"
    return None


def _get_clickhouse_settings():
    """Get ClickHouse connection settings (cached per request)."""
    return _get_ch_connection()


def _clickhouse_query(sql, params, ch_settings):
    """Execute a single ClickHouse HTTP query. Returns response text or raises."""
    url = f"http://{ch_settings['host']}:{ch_settings['port']}/"
    query_params = dict(params)
    query_params["query"] = sql

    resp = requests.get(
        url,
        params=query_params,
        auth=(ch_settings["user"], ch_settings["password"]),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text.strip()


@frappe.whitelist(allow_guest=True)
def health():
    """Health check endpoint."""
    return {"status": "ok", "app": "konsol"}


@frappe.whitelist()
def epm_value(entity, year, period, account, measure="period_net_amount",
              scenario="actuals", cost_center="", department="",
              scenario_id=""):
    """Single value lookup — returns {"value": <number>}.

    Period accepts: 1-12 (single month), "Q1"-"Q4", "H1"-"H2", "FY".
    Range periods return the sum across constituent months.
    scenario_id: optional — filter to a specific scenario (e.g. "BUDGET_2025")
                 within the table. Only applies to tables that have a scenario_id column.
    """
    _validate_scenario(scenario)
    _validate_measure(measure, scenario)
    result = _batch_query_clickhouse([{
        "entity": entity, "year": int(year),
        "periods": _resolve_period(period),
        "account": account, "measure": measure, "scenario": scenario,
        "cost_center": cost_center, "department": department,
        "scenario_id": scenario_id,
    }])
    return {"value": result["values"][0]}


@frappe.whitelist(methods=["POST"])
def epm_batch():
    """Batch value retrieval — accepts JSON array, returns {"values": [...], "errors": [...]}."""
    data = frappe.request.get_data(as_text=True)
    requests_list = json.loads(data)

    if len(requests_list) > MAX_BATCH_SIZE:
        frappe.throw(
            f"Batch size {len(requests_list)} exceeds maximum of {MAX_BATCH_SIZE}",
            frappe.ValidationError,
        )

    # Normalize and validate per-request (bad items get inline errors, not batch abort)
    n = len(requests_list)
    normalized = [None] * n
    errors = [None] * n
    for i, req in enumerate(requests_list):
        scenario = req.get("scenario", "actuals")
        measure = req.get("measure", "period_net_amount")
        err = _check_scenario(scenario) or _check_measure(measure, scenario)
        if err:
            errors[i] = err
        else:
            try:
                periods = _resolve_period(req.get("period", 0))
            except (ValueError, TypeError):
                errors[i] = f"Invalid period '{req.get('period')}'"
                continue
            normalized[i] = {
                "entity": req.get("entity", ""),
                "year": int(req.get("year", 0)),
                "periods": periods,
                "account": req.get("account", ""),
                "measure": measure,
                "scenario": scenario,
                "cost_center": req.get("cost_center", ""),
                "department": req.get("department", ""),
                "scenario_id": req.get("scenario_id", ""),
            }

    # Only send valid requests to ClickHouse
    valid = [(i, r) for i, r in enumerate(normalized) if r is not None]
    if valid:
        valid_indices, valid_reqs = zip(*valid)
        ch_result = _batch_query_clickhouse(list(valid_reqs))
        # Map results back to original positions
        values = [None] * n
        for j, orig_idx in enumerate(valid_indices):
            values[orig_idx] = ch_result["values"][j]
            if ch_result.get("errors") and ch_result["errors"][j]:
                errors[orig_idx] = ch_result["errors"][j]
        # Fill invalid positions with None (VBA reads as 0)
    else:
        values = [None] * n

    result = {"values": values}
    if any(e is not None for e in errors):
        result["errors"] = errors
    return result


def _batch_query_clickhouse(requests_list):
    """Execute batched ClickHouse queries grouped by (scenario, measure, periods, dims).

    Groups requests that share the same table/measure/period-range/dimension-shape
    into a single query with IN (...) clauses, then maps results back to request order.
    Period ranges (Q1, H1, FY) sum across constituent months via fiscal_period IN (...).
    Returns {"values": [...], "errors": [...]}.
    """
    ch_settings = _get_clickhouse_settings()
    n = len(requests_list)
    values = [None] * n
    errors = [None] * n

    # Group requests by (scenario, measure, periods_tuple, has_cost_center, has_department, scenario_id)
    groups = defaultdict(list)
    for idx, req in enumerate(requests_list):
        key = (
            req["scenario"],
            req["measure"],
            req["periods"],
            bool(req.get("cost_center")),
            bool(req.get("department")),
            req.get("scenario_id", ""),
        )
        groups[key].append((idx, req))

    for (scenario, measure, periods, has_cc, has_dept, scenario_id), group_items in groups.items():
        # Defense-in-depth: assert identifiers are safe before SQL interpolation
        assert re.match(r'^[a-z_]+$', measure), f"Bad measure: {measure}"
        table = SCENARIO_TABLES[scenario]
        assert table in SCENARIO_TABLES.values(), f"Bad table: {table}"

        # Dimensions for GROUP BY / IN: entity, year, account + optional dims
        # fiscal_period is NOT in GROUP BY — it goes in a WHERE IN clause so
        # ranges (Q1, H1, FY) get summed across their constituent months.
        select_cols = ["data_area_id", "fiscal_year", "main_account"]
        if has_cc:
            select_cols.append("dim_cost_center")
        if has_dept:
            select_cols.append("dim_department")

        # Build IN tuples and params (without fiscal_period)
        in_tuples = []
        params = {}
        for i, (idx, req) in enumerate(group_items):
            parts = [f"{{e{i}:String}}", f"{{y{i}:Int32}}", f"{{a{i}:String}}"]
            params[f"param_e{i}"] = req["entity"]
            params[f"param_y{i}"] = str(req["year"])
            params[f"param_a{i}"] = req["account"]
            if has_cc:
                parts.append(f"{{cc{i}:String}}")
                params[f"param_cc{i}"] = req.get("cost_center", "")
            if has_dept:
                parts.append(f"{{dp{i}:String}}")
                params[f"param_dp{i}"] = req.get("department", "")
            in_tuples.append(f"({', '.join(parts)})")

        group_by = ", ".join(select_cols)
        in_cols = f"({group_by})"
        in_values = ", ".join(in_tuples)

        # fiscal_period IN (...) — parameterized
        period_placeholders = []
        for pi, p in enumerate(periods):
            pkey = f"fp{pi}"
            period_placeholders.append(f"{{{pkey}:Int32}}")
            params[f"param_{pkey}"] = str(p)
        period_in = ", ".join(period_placeholders)

        # Optional scenario_id filter (only for tables that have the column)
        scenario_id_clause = ""
        if scenario_id and table in TABLES_WITH_SCENARIO_ID:
            assert re.match(r'^[A-Za-z0-9_]+$', scenario_id), f"Bad scenario_id: {scenario_id}"
            params["param_sid"] = scenario_id
            scenario_id_clause = " AND scenario_id = {sid:String}"

        sql = (
            f"SELECT {group_by}, coalesce(sum({measure}), 0) as val "
            f"FROM {table} "
            f"WHERE {in_cols} IN ({in_values}) "
            f"AND fiscal_period IN ({period_in})"
            f"{scenario_id_clause} "
            f"GROUP BY {group_by}"
        )

        try:
            raw = _clickhouse_query(sql, params, ch_settings)
            # Parse TSV response into a lookup dict
            result_lookup = {}
            for line in raw.split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                # Last column is the value, preceding columns are the key
                key_parts = tuple(parts[:-1])
                val = float(parts[-1])
                result_lookup[key_parts] = val

            # Map results back to request indices (key excludes fiscal_period)
            for _, (idx, req) in enumerate(group_items):
                lookup_key = [req["entity"], str(req["year"]), req["account"]]
                if has_cc:
                    lookup_key.append(req.get("cost_center", ""))
                if has_dept:
                    lookup_key.append(req.get("department", ""))
                values[idx] = result_lookup.get(tuple(lookup_key), 0.0)

        except requests.exceptions.Timeout:
            for _, (idx, _) in enumerate(group_items):
                values[idx] = None
                errors[idx] = "ClickHouse query timeout"
        except requests.exceptions.ConnectionError:
            for _, (idx, _) in enumerate(group_items):
                values[idx] = None
                errors[idx] = "ClickHouse connection failed"
        except Exception as e:
            for _, (idx, _) in enumerate(group_items):
                values[idx] = None
                errors[idx] = str(e)

    # Only include errors array if there are actual errors
    result = {"values": values}
    if any(e is not None for e in errors):
        result["errors"] = errors
    return result


# --- Budget Write-Back API ---

def _validate_budget_fields(data):
    """Validate required fields for budget save. Raises on missing."""
    required = ["scenario_id", "data_area_id", "fiscal_year", "main_account", "periods"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        frappe.throw(
            f"Missing required fields: {', '.join(missing)}",
            frappe.ValidationError,
        )
    if not isinstance(data["periods"], list) or not data["periods"]:
        frappe.throw("periods must be a non-empty array", frappe.ValidationError)


def _upsert_budget_input(data):
    """Create or update a Budget Input doc from API data. Returns doc name."""
    # Build unique key for upsert
    filters = {
        "scenario_id": data["scenario_id"],
        "data_area_id": data["data_area_id"],
        "fiscal_year": int(data["fiscal_year"]),
        "main_account": data["main_account"],
    }
    existing = frappe.get_all("Budget Input", filters=filters, limit=1)

    if existing:
        doc = frappe.get_doc("Budget Input", existing[0].name)
        doc.periods = []
    else:
        doc = frappe.new_doc("Budget Input")
        doc.update(filters)

    # Optional dimension fields
    doc.dim_cost_center = data.get("dim_cost_center", "")
    doc.dim_department = data.get("dim_department", "")

    # Add period rows
    for p in data["periods"]:
        doc.append("periods", {
            "fiscal_period": int(p.get("period", p.get("fiscal_period", 0))),
            "amount": float(p.get("amount", 0)),
            "layer": p.get("layer", "base"),
        })

    doc.save()
    return doc.name


@frappe.whitelist(methods=["POST"])
def budget_save():
    """Save a single budget line — creates/updates Budget Input doc in Draft.

    Accepts JSON: {scenario_id, data_area_id, fiscal_year, main_account,
    dim_cost_center, dim_department, periods: [{period, amount, layer}]}

    Returns: {"name": doc_name}
    """
    data = json.loads(frappe.request.get_data(as_text=True))
    _validate_budget_fields(data)
    name = _upsert_budget_input(data)
    return {"name": name}


VALID_LAYERS = {"base", "challenge", "management", "board"}


@frappe.whitelist(methods=["POST"])
def budget_cell_save():
    """Save a single budget cell — upserts one period+layer in a Budget Input doc.

    Designed for EPMSAVE() immediate writes from Excel.
    Accepts JSON: {scenario_id, data_area_id, fiscal_year, main_account,
    fiscal_period, amount, layer, [dim_cost_center], [dim_department]}

    Returns: {"status": "ok", "name": doc_name, "value": amount}
    """
    data = json.loads(frappe.request.get_data(as_text=True))

    # Validate required fields
    required = ["scenario_id", "data_area_id", "fiscal_year",
                "main_account", "fiscal_period", "amount", "layer"]
    missing = [f for f in required if f not in data or data[f] == ""]
    if missing:
        frappe.throw(
            f"Missing required fields: {', '.join(missing)}",
            frappe.ValidationError,
        )

    layer = str(data["layer"]).strip().lower()
    if layer not in VALID_LAYERS:
        frappe.throw(
            f"Invalid layer '{layer}'. Allowed: {', '.join(sorted(VALID_LAYERS))}",
            frappe.ValidationError,
        )

    fp = int(data["fiscal_period"])
    if fp < 1 or fp > 12:
        frappe.throw("fiscal_period must be 1-12", frappe.ValidationError)

    amount = float(data["amount"])

    # Find or create the Budget Input doc
    filters = {
        "scenario_id": data["scenario_id"],
        "data_area_id": data["data_area_id"],
        "fiscal_year": int(data["fiscal_year"]),
        "main_account": data["main_account"],
    }
    existing = frappe.get_all("Budget Input", filters=filters, limit=1)

    if existing:
        doc = frappe.get_doc("Budget Input", existing[0].name)
    else:
        doc = frappe.new_doc("Budget Input")
        doc.update(filters)
        doc.dim_cost_center = data.get("dim_cost_center", "")
        doc.dim_department = data.get("dim_department", "")

    # Upsert the specific period+layer row
    found = False
    for row in doc.periods:
        if row.fiscal_period == fp and row.layer == layer:
            row.amount = amount
            found = True
            break

    if not found:
        doc.append("periods", {
            "fiscal_period": fp,
            "amount": amount,
            "layer": layer,
        })

    doc.save()
    return {"status": "ok", "name": doc.name, "value": amount}


@frappe.whitelist(methods=["POST"])
def budget_save_batch():
    """Save multiple budget lines at once.

    Accepts JSON array of budget line objects.
    Returns: {"results": [{"name": doc_name}, ...], "errors": [...]}
    """
    items = json.loads(frappe.request.get_data(as_text=True))
    if not isinstance(items, list):
        frappe.throw("Expected a JSON array", frappe.ValidationError)

    results = []
    errors = []
    for i, data in enumerate(items):
        try:
            _validate_budget_fields(data)
            name = _upsert_budget_input(data)
            results.append({"name": name, "index": i})
            errors.append(None)
        except Exception as e:
            results.append(None)
            errors.append({"index": i, "error": str(e)})

    response = {"results": results}
    if any(e is not None for e in errors):
        response["errors"] = errors
    return response
