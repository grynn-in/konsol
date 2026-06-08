"""EPM API — Frappe proxy to ClickHouse for Excel batch retrieval."""
import json
import re
from collections import defaultdict

import frappe
import requests

MAX_BATCH_SIZE = 2000

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
    settings = frappe.get_single("EPM Settings")
    return {
        "host": settings.clickhouse_host or "localhost",
        "port": settings.clickhouse_port or "8123",
        "user": settings.clickhouse_user or "default",
        "password": settings.get_password("clickhouse_password") or "",
    }


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
              scenario="actuals", cost_center="", department=""):
    """Single value lookup — returns {"value": <number>}."""
    _validate_scenario(scenario)
    _validate_measure(measure, scenario)
    result = _batch_query_clickhouse([{
        "entity": entity, "year": int(year), "period": int(period),
        "account": account, "measure": measure, "scenario": scenario,
        "cost_center": cost_center, "department": department,
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
            normalized[i] = {
                "entity": req.get("entity", ""),
                "year": int(req.get("year", 0)),
                "period": int(req.get("period", 0)),
                "account": req.get("account", ""),
                "measure": measure,
                "scenario": scenario,
                "cost_center": req.get("cost_center", ""),
                "department": req.get("department", ""),
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
    """Execute batched ClickHouse queries grouped by (scenario, measure, dimension_keys).

    Groups requests that share the same table/measure/dimension-shape into a single
    query with IN (...) clauses, then maps results back to request order.
    Returns {"values": [...], "errors": [...]}.
    """
    ch_settings = _get_clickhouse_settings()
    n = len(requests_list)
    values = [None] * n
    errors = [None] * n

    # Group requests by (scenario, measure, has_cost_center, has_department)
    groups = defaultdict(list)
    for idx, req in enumerate(requests_list):
        key = (
            req["scenario"],
            req["measure"],
            bool(req.get("cost_center")),
            bool(req.get("department")),
        )
        groups[key].append((idx, req))

    for (scenario, measure, has_cc, has_dept), group_items in groups.items():
        # Defense-in-depth: assert identifiers are safe before SQL interpolation
        assert re.match(r'^[a-z_]+$', measure), f"Bad measure: {measure}"
        table = SCENARIO_TABLES[scenario]
        assert table in SCENARIO_TABLES.values(), f"Bad table: {table}"

        # Build a single grouped query with IN (...) tuples
        # Dimensions: always (data_area_id, fiscal_year, fiscal_period, main_account)
        # Plus optional dim_cost_center, dim_department
        select_cols = [
            "data_area_id", "fiscal_year", "fiscal_period", "main_account",
        ]
        if has_cc:
            select_cols.append("dim_cost_center")
        if has_dept:
            select_cols.append("dim_department")

        # Build IN tuples and params
        in_tuples = []
        params = {}
        for i, (idx, req) in enumerate(group_items):
            parts = [f"{{e{i}:String}}", f"{{y{i}:Int32}}", f"{{p{i}:Int32}}", f"{{a{i}:String}}"]
            params[f"param_e{i}"] = req["entity"]
            params[f"param_y{i}"] = str(req["year"])
            params[f"param_p{i}"] = str(req["period"])
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

        sql = (
            f"SELECT {group_by}, coalesce(sum({measure}), 0) as val "
            f"FROM {table} "
            f"WHERE {in_cols} IN ({in_values}) "
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

            # Map results back to request indices
            for _, (idx, req) in enumerate(group_items):
                lookup_key = [req["entity"], str(req["year"]), str(req["period"]), req["account"]]
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
