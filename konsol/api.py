"""EPM API — Frappe proxy to ClickHouse for Excel batch retrieval.

Also provides consolidation & allocation workflow APIs (PRD-8, PRD-16, PRD-21).
"""
import json
import re
from collections import defaultdict

import frappe
import requests
from frappe.utils import now_datetime

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


# ---------------------------------------------------------------------------
# PRD-8: Consolidation Hierarchy API
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_hierarchy_tree(consolidation_group=None):
    """Return consolidation hierarchy as nested JSON tree.

    Uses Frappe's native tree (is_tree=1) with lft/rgt for efficient subtree queries.
    If consolidation_group is given, returns subtree rooted at that group.
    Otherwise returns all root groups (no parent) with their children.

    Returns: {"tree": [{group, entity, children: [...], ...}]}
    """
    filters = {}
    if consolidation_group:
        # Use lft/rgt for efficient subtree query
        root = frappe.get_all(
            "Consolidation Group",
            filters={"consolidation_group": consolidation_group},
            fields=["lft", "rgt"],
            limit=1,
        )
        if root:
            filters = [
                ["lft", ">=", root[0].lft],
                ["rgt", "<=", root[0].rgt],
            ]

    docs = frappe.get_all(
        "Consolidation Group",
        filters=filters,
        fields=[
            "name", "consolidation_group", "data_area_id", "entity_name",
            "parent_consolidation_group", "lft", "rgt", "ownership_pct",
            "reporting_currency", "consolidation_method", "goodwill_method",
        ],
        order_by="lft asc",
        limit_page_length=0,
    )

    # Index by name for parent lookups
    by_name = {d.name: d for d in docs}

    def build_node(doc):
        node = {
            "name": doc.name,
            "consolidation_group": doc.consolidation_group,
            "data_area_id": doc.data_area_id,
            "entity_name": doc.entity_name,
            "parent_group": doc.parent_consolidation_group,
            "ownership_pct": doc.ownership_pct,
            "consolidation_method": doc.consolidation_method,
            "children": [],
        }
        # Find direct children via parent_consolidation_group
        for d in docs:
            if d.parent_consolidation_group == doc.name:
                node["children"].append(build_node(d))
        return node

    # Roots: no parent, or parent not in our result set
    tree = []
    for d in docs:
        if not d.parent_consolidation_group or d.parent_consolidation_group not in by_name:
            tree.append(build_node(d))

    return {"tree": tree}


# ---------------------------------------------------------------------------
# PRD-16: Consolidation Adjustment Workflow API
# ---------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"])
def approve_adjustment(name):
    """Approve a Consolidation Adjustment (Pending Approval -> Approved).

    Args:
        name: Document name (e.g. "CADJ-IC001-0001")

    Returns: {"status": "Approved", "approved_by": user, "approved_at": timestamp}
    """
    doc = frappe.get_doc("Consolidation Adjustment", name)
    if doc.status != "Pending Approval":
        frappe.throw(
            f"Cannot approve: current status is '{doc.status}', expected 'Pending Approval'",
            frappe.ValidationError,
        )
    doc.status = "Approved"
    doc.approved_by = frappe.session.user
    doc.approved_at = now_datetime()
    doc.save()
    return {
        "status": doc.status,
        "approved_by": doc.approved_by,
        "approved_at": str(doc.approved_at),
    }


@frappe.whitelist(methods=["POST"])
def reverse_adjustment(name):
    """Reverse an Approved Consolidation Adjustment.

    Creates a new reversal doc with negated amounts and links both via
    reversal_journal_id. Cancels the original.

    Args:
        name: Document name of the adjustment to reverse.

    Returns: {"original": name, "reversal": reversal_name, "status": "Reversed"}
    """
    doc = frappe.get_doc("Consolidation Adjustment", name)
    if doc.status != "Approved":
        frappe.throw(
            f"Cannot reverse: current status is '{doc.status}', expected 'Approved'",
            frappe.ValidationError,
        )

    # Create reversal document with negated amounts
    reversal = frappe.new_doc("Consolidation Adjustment")
    reversal.consolidation_group = doc.consolidation_group
    reversal.adjustment_type = doc.adjustment_type
    reversal.journal_id = f"REV-{doc.journal_id}"
    reversal.data_area_id = doc.data_area_id
    reversal.fiscal_year = doc.fiscal_year
    reversal.fiscal_period = doc.fiscal_period
    reversal.main_account = doc.main_account
    reversal.debit_amount = doc.credit_amount  # swap
    reversal.credit_amount = doc.debit_amount  # swap
    reversal.description = f"Reversal of {doc.name}"
    reversal.posted_by = frappe.session.user
    reversal.status = "Approved"
    reversal.approved_by = frappe.session.user
    reversal.approved_at = now_datetime()
    reversal.reversal_journal_id = doc.name
    reversal.insert()
    reversal.submit()

    # Mark original as reversed
    doc.status = "Reversed"
    doc.reversal_journal_id = reversal.name
    doc.save()

    return {
        "original": doc.name,
        "reversal": reversal.name,
        "status": "Reversed",
    }


# ---------------------------------------------------------------------------
# PRD-21: Allocation Run & Reversal API
# ---------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"])
def run_allocation(fiscal_year, fiscal_period):
    """Create and submit an Allocation Run for a given period.

    Args:
        fiscal_year: Integer fiscal year (e.g. 2025)
        fiscal_period: Integer fiscal period (1-12)

    Returns: {"name": doc_name, "allocation_run_id": id, "status": "Active"}
    """
    doc = frappe.new_doc("Allocation Run")
    doc.fiscal_year = int(fiscal_year)
    doc.fiscal_period = int(fiscal_period)
    doc.insert()
    doc.submit()  # triggers before_submit (sets run_id, run_by, run_at, status=Active)

    return {
        "name": doc.name,
        "allocation_run_id": doc.allocation_run_id,
        "status": doc.status,
        "run_by": doc.run_by,
        "run_at": str(doc.run_at),
    }


@frappe.whitelist(methods=["POST"])
def reverse_allocation(name):
    """Reverse an Active Allocation Run.

    Creates a new reversal run linked via reversal_of, then cancels the original.

    Args:
        name: Document name of the Allocation Run to reverse.

    Returns: {"original": name, "reversal": reversal_name, "status": "Reversed"}
    """
    doc = frappe.get_doc("Allocation Run", name)
    if doc.status != "Active":
        frappe.throw(
            f"Cannot reverse: current status is '{doc.status}', expected 'Active'",
            frappe.ValidationError,
        )

    # Create reversal run
    reversal = frappe.new_doc("Allocation Run")
    reversal.fiscal_year = doc.fiscal_year
    reversal.fiscal_period = doc.fiscal_period
    reversal.reversal_of = doc.name
    reversal.insert()
    reversal.submit()

    # Cancel original
    doc.cancel()  # triggers on_cancel (sets status=Reversed)

    return {
        "original": doc.name,
        "reversal": reversal.name,
        "status": "Reversed",
    }


@frappe.whitelist()
def allocation_history(fiscal_year=None, fiscal_period=None):
    """Return allocation run history with optional filters.

    Args:
        fiscal_year: Optional filter by fiscal year.
        fiscal_period: Optional filter by fiscal period.

    Returns: {"runs": [{name, allocation_run_id, fiscal_year, fiscal_period,
              status, run_by, run_at, reversal_of}, ...]}
    """
    filters = {}
    if fiscal_year:
        filters["fiscal_year"] = int(fiscal_year)
    if fiscal_period:
        filters["fiscal_period"] = int(fiscal_period)

    runs = frappe.get_all(
        "Allocation Run",
        filters=filters,
        fields=[
            "name", "allocation_run_id", "fiscal_year", "fiscal_period",
            "status", "run_by", "run_at", "reversal_of",
        ],
        order_by="run_at desc",
        limit_page_length=0,
    )
    return {"runs": runs}
