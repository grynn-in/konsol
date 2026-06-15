"""EPM API — Frappe proxy to ClickHouse for Excel batch retrieval.

Also provides consolidation & allocation workflow APIs (PRD-8, PRD-16, PRD-21).
"""
import hmac
import json
import re
from collections import defaultdict

import frappe
import requests
from frappe.utils import now_datetime

from konsol.clickhouse import connection_url as _ch_url
from konsol.clickhouse import get_connection as _get_ch_connection

MAX_BATCH_SIZE = 2000

# DocType whose User Permissions gate which entities (data areas) a user may
# query. Configured in EPM Settings.entity_permission_doctype. When unset, no
# entity-level filtering is applied (backwards compatible).
def _entity_permission_doctype():
    return (frappe.get_cached_value(
        "EPM Settings", "EPM Settings", "entity_permission_doctype") or "").strip()

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

VALID_LAYERS = {"base", "challenge", "management", "board"}

_SAFE_IDENTIFIER = re.compile(r'^[a-z][a-z0-9_]*$')
_SAFE_SCENARIO_ID = re.compile(r'^[A-Za-z0-9_]+$')
# Fully-qualified ClickHouse table: schema.table, lowercase identifiers only.
_SAFE_TABLE_NAME = re.compile(r'^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$')


# ---------------------------------------------------------------------------
# Entity-level authorization
# ---------------------------------------------------------------------------

def _resolve_allowed_entities(user, roles, perm_doctype, user_permissions):
    """Pure policy: which entities may a user query? (no Frappe access)

    Returns ``None`` when access is unrestricted — the caller is a System
    Manager / Administrator, or no entity permission doctype is configured
    (backwards-compatible default). Otherwise returns the set of entity codes
    granted via Frappe User Permissions for ``perm_doctype`` (an empty set
    means the user may see no entities).

    Kept side-effect-free so it can be unit-tested without a site.
    """
    if user == "Administrator" or "System Manager" in (roles or []):
        return None
    if not perm_doctype:
        return None
    entries = (user_permissions or {}).get(perm_doctype) or []
    return {e.get("doc") for e in entries if e.get("doc")}


def _allowed_entities():
    """Resolve the current user's entity allow-list (see _resolve_allowed_entities)."""
    return _resolve_allowed_entities(
        frappe.session.user,
        frappe.get_roles(),
        _entity_permission_doctype(),
        frappe.permissions.get_user_permissions(),
    )


def _assert_entity_access(entity):
    """Raise PermissionError if the current user may not query ``entity``."""
    allowed = _allowed_entities()
    if allowed is not None and entity not in allowed:
        raise frappe.PermissionError(
            f"Not permitted to access entity '{entity}'")


# ---------------------------------------------------------------------------
# Fact Table registry helpers
# ---------------------------------------------------------------------------

_FACT_FIELDS = [
    "fact_name", "scenario_key", "clickhouse_table", "has_scenario_id",
    "measures", "dimensions",
    "reroute_table", "reroute_column", "reroute_measure",
]


def _get_fact_by_scenario(scenario):
    """Load Fact Table doc by scenario_key. Returns dict or None."""
    facts = frappe.get_all(
        "Fact Table",
        filters={"scenario_key": scenario},
        fields=_FACT_FIELDS,
        limit=1,
    )
    return facts[0] if facts else None


def _get_fact(fact=None, scenario=None):
    """Resolve a Fact Table. `fact` (fact_name) wins over `scenario`.

    fact_name match is case-insensitive (names are stored normalized lowercase).
    If both are supplied, fact wins and a warning is logged. Returns dict or None.
    """
    if fact:
        if scenario and scenario != "actuals":
            frappe.logger().warning(
                f"epm: both fact='{fact}' and scenario='{scenario}' given; fact wins"
            )
        facts = frappe.get_all(
            "Fact Table",
            filters={"fact_name": (fact or "").lower()},
            fields=_FACT_FIELDS,
            limit=1,
        )
        return facts[0] if facts else None
    return _get_fact_by_scenario(scenario)


def _get_allowed_measures(fact):
    """Parse measures JSON from a Fact Table doc. Returns set."""
    return set(json.loads(fact.measures or "[]"))


def _get_fact_dimensions(fact):
    """Parse dimensions JSON from a Fact Table doc. Returns set."""
    return set(json.loads(fact.dimensions or "[]"))


def _published_measures():
    """Set of measure names with status=Published in the Measure registry."""
    return {
        m.measure_name
        for m in frappe.get_all(
            "Measure", filters={"status": "Published"},
            fields=["measure_name"], limit_page_length=0,
        )
    }


def _parse_dimensions_arg(dimensions):
    """Accept a dict or a JSON-encoded string; return a plain dict.

    GET epm_value sends `dimensions` as a JSON string; epm_batch items send a
    native object. Empty values are dropped by the caller.
    """
    if not dimensions:
        return {}
    if isinstance(dimensions, dict):
        return dict(dimensions)
    try:
        parsed = json.loads(dimensions)
    except (json.JSONDecodeError, TypeError):
        frappe.throw("dimensions must be a JSON object", frappe.ValidationError)
    if not isinstance(parsed, dict):
        frappe.throw("dimensions must be a JSON object", frappe.ValidationError)
    return parsed


def _resolve_and_validate(fact_name, scenario, measure, dim_names):
    """Resolve the fact, validate measure (Published registry ∩ fact) and
    dimensions (must be allowed by the fact). Returns (fact_doc, error_or_None)."""
    fact = _get_fact(fact=fact_name, scenario=scenario)
    if not fact:
        if fact_name:
            allowed = sorted(
                f.fact_name for f in frappe.get_all(
                    "Fact Table", fields=["fact_name"], limit_page_length=0)
            )
            return None, f"Invalid fact '{fact_name}'. Allowed: {', '.join(allowed)}"
        allowed = sorted(set(
            f.scenario_key for f in frappe.get_all(
                "Fact Table", fields=["scenario_key"], limit_page_length=0)
        ))
        return None, f"Invalid scenario '{scenario}'. Allowed: {', '.join(allowed)}"

    valid_measures = _get_allowed_measures(fact) & _published_measures()
    if measure not in valid_measures:
        return None, (
            f"Invalid measure '{measure}' for fact '{fact.fact_name}'. "
            f"Allowed: {', '.join(sorted(valid_measures))}"
        )

    fact_dims = _get_fact_dimensions(fact)
    for dn in dim_names:
        if dn not in fact_dims:
            return None, (
                f"Invalid dimension '{dn}' for fact '{fact.fact_name}'. "
                f"Allowed: {', '.join(sorted(fact_dims))}"
            )
    return fact, None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_json_body():
    """Parse JSON request body. Used by all POST endpoints."""
    return json.loads(frappe.request.get_data(as_text=True))


def _resolve_period(period):
    """Resolve period to a tuple of fiscal_period integers.

    Accepts: 1-12 (single), "Q1"-"Q4", "H1"-"H2", "FY".
    Returns tuple of ints, e.g. (1,) or (1,2,3).
    Raises ValueError on invalid input.
    """
    s = str(period).strip().upper()
    if s in PERIOD_RANGES:
        return PERIOD_RANGES[s]
    p = int(period)
    if p < 1 or p > 12:
        raise ValueError(f"fiscal_period must be 1-12, got {p}")
    return (p,)


def _budget_filters(data):
    """Build the unique-key filter dict for Budget Input upsert."""
    return {
        "scenario_id": data["scenario_id"],
        "data_area_id": data["data_area_id"],
        "fiscal_year": int(data["fiscal_year"]),
        "main_account": data["main_account"],
    }


# ---------------------------------------------------------------------------
# ClickHouse query helpers
# ---------------------------------------------------------------------------

def _clickhouse_query(sql, params, ch_settings):
    """Execute a single ClickHouse HTTP query. Returns response text or raises."""
    url = _ch_url(ch_settings)
    query_params = dict(params)
    query_params["query"] = sql

    resp = requests.get(
        url,
        params=query_params,
        auth=(ch_settings["user"], ch_settings["password"]),
        timeout=30,
        verify=ch_settings.get("verify", True),
    )
    resp.raise_for_status()
    return resp.text.strip()


def _batch_query_clickhouse(requests_list):
    """Execute batched ClickHouse queries grouped by (scenario, measure, periods, dims).

    Supports dynamic dimensions — each request carries a `dimensions` dict
    mapping dimension names to filter values.  Requests are grouped by shared
    table/measure/period-range/dimension-shape for efficient batching.

    Returns {"values": [...], "errors": [...]}.
    """
    ch_settings = _get_ch_connection()
    n = len(requests_list)
    values = [None] * n
    errors = [None] * n

    # Group by (fact, measure, periods_tuple, dim_names_frozenset, scenario_id).
    # `fact` (the resolved fact_name) is the table-determining element; scenario
    # is retained per-request for backward compatibility / resolution fallback.
    groups = defaultdict(list)
    for idx, req in enumerate(requests_list):
        dims = req.get("dimensions", {})
        key = (
            req.get("fact") or req.get("scenario"),
            req["measure"],
            req["periods"],
            frozenset(dims.keys()),
            req.get("scenario_id", ""),
        )
        groups[key].append((idx, req))

    for (fact_key, measure, periods, dim_names, scenario_id), group_items in groups.items():
        fact = _get_fact(fact=fact_key) or _get_fact_by_scenario(fact_key)
        if not fact:
            for idx, _ in group_items:
                errors[idx] = f"No Fact Table for '{fact_key}'"
            continue

        table = fact.clickhouse_table
        query_measure = measure

        # Reroute measures that live in a different table/column
        if fact.reroute_measure and measure == fact.reroute_measure:
            if fact.reroute_table:
                table = fact.reroute_table
            if fact.reroute_column:
                query_measure = fact.reroute_column

        # Validate identifiers before SQL interpolation. The table name comes
        # from the Fact Table doctype but is interpolated directly into FROM,
        # so it must be validated too (defence against a tampered/typo'd
        # clickhouse_table or reroute_table value).
        if not _SAFE_TABLE_NAME.match(table or ""):
            for idx, _ in group_items:
                errors[idx] = "Invalid table identifier"
            continue
        if not _SAFE_IDENTIFIER.match(query_measure):
            for idx, _ in group_items:
                errors[idx] = "Invalid measure identifier"
            continue

        # Validate dimension names
        dim_names_sorted = sorted(dim_names)
        dim_valid = True
        for dn in dim_names_sorted:
            if not _SAFE_IDENTIFIER.match(dn):
                for idx, _ in group_items:
                    errors[idx] = f"Invalid dimension identifier: {dn}"
                dim_valid = False
                break
        if not dim_valid:
            continue

        select_cols = ["data_area_id", "fiscal_year", "main_account"]
        select_cols.extend(dim_names_sorted)

        # Build IN tuples and params (without fiscal_period)
        in_tuples = []
        params = {}
        for i, (idx, req) in enumerate(group_items):
            dims = req.get("dimensions", {})
            parts = [f"{{e{i}:String}}", f"{{y{i}:Int32}}", f"{{a{i}:String}}"]
            params[f"param_e{i}"] = req["entity"]
            params[f"param_y{i}"] = str(req["year"])
            params[f"param_a{i}"] = req["account"]
            for di, dn in enumerate(dim_names_sorted):
                parts.append(f"{{d{i}_{di}:String}}")
                params[f"param_d{i}_{di}"] = dims.get(dn, "")
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

        # Optional scenario_id filter
        scenario_id_clause = ""
        if scenario_id and fact.has_scenario_id:
            if not _SAFE_SCENARIO_ID.match(scenario_id):
                for idx, _ in group_items:
                    errors[idx] = "Invalid scenario_id format"
                continue
            params["param_sid"] = scenario_id
            scenario_id_clause = " AND scenario_id = {sid:String}"

        sql = (
            f"SELECT {group_by}, coalesce(sum({query_measure}), 0) as val "
            f"FROM {table} "
            f"WHERE {in_cols} IN ({in_values}) "
            f"AND fiscal_period IN ({period_in})"
            f"{scenario_id_clause} "
            f"GROUP BY {group_by}"
        )

        try:
            raw = _clickhouse_query(sql, params, ch_settings)
            result_lookup = {}
            for line in raw.split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                key_parts = tuple(parts[:-1])
                val = float(parts[-1])
                result_lookup[key_parts] = val

            for _, (idx, req) in enumerate(group_items):
                dims = req.get("dimensions", {})
                lookup_key = [req["entity"], str(req["year"]), req["account"]]
                for dn in dim_names_sorted:
                    lookup_key.append(dims.get(dn, ""))
                values[idx] = result_lookup.get(tuple(lookup_key), 0.0)

        except requests.exceptions.Timeout:
            for idx, _ in group_items:
                values[idx] = None
                errors[idx] = "ClickHouse query timeout"
        except requests.exceptions.ConnectionError:
            for idx, _ in group_items:
                values[idx] = None
                errors[idx] = "ClickHouse connection failed"
        except Exception:
            frappe.log_error("ClickHouse query failed", frappe.get_traceback())
            for idx, _ in group_items:
                values[idx] = None
                errors[idx] = "ClickHouse query failed"

    result = {"values": values}
    if any(e is not None for e in errors):
        result["errors"] = errors
    return result


# ---------------------------------------------------------------------------
# Data Retrieval Endpoints
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def health():
    """Health check endpoint with ClickHouse connectivity status.

    Returns:
        status: 'ok' | 'degraded' | 'down'
        app: 'konsol'
        clickhouse: detailed ClickHouse health info
    """
    from konsol.clickhouse import check_health as ch_health

    ch_status = ch_health()
    overall_status = "ok" if ch_status["status"] == "healthy" else ch_status["status"]

    return {
        "status": overall_status,
        "app": "konsol",
        "clickhouse": ch_status,
    }


@frappe.whitelist()
def epm_value(entity, year, period, account, measure="period_net_amount",
              scenario="actuals", fact=None, dimensions=None, scenario_id=""):
    """Single value lookup — returns {"value": <number>}.

    Period accepts: 1-12 (single month), "Q1"-"Q4", "H1"-"H2", "FY".
    Range periods return the sum across constituent months.

    Dimensions are passed as a generic dict (JSON string on this GET endpoint),
    e.g. dimensions={"dim_cost_center":"CC001","dim_project":"P01"}. Keys are
    canonical dimension names, validated against the fact's allowed dimensions.
    `fact` (a Fact registry name) selects the source table and wins over
    `scenario`; if neither pins a fact, `scenario` resolves it via scenario_key.
    """
    _assert_entity_access(entity)

    dims = {k: v for k, v in _parse_dimensions_arg(dimensions).items() if v}

    fact_doc, err = _resolve_and_validate(fact, scenario, measure, dims.keys())
    if err:
        frappe.throw(err, frappe.ValidationError)

    result = _batch_query_clickhouse([{
        "entity": entity, "year": int(year),
        "periods": _resolve_period(period),
        "account": account, "measure": measure,
        "fact": fact_doc.fact_name, "scenario": scenario,
        "dimensions": dims,
        "scenario_id": scenario_id,
    }])
    return {"value": result["values"][0]}


@frappe.whitelist(methods=["POST"])
def epm_batch():
    """Batch value retrieval — accepts JSON array, returns {"values": [...], "errors": [...]}."""
    requests_list = _get_json_body()

    if not isinstance(requests_list, list):
        frappe.throw("Expected a JSON array", frappe.ValidationError)

    if len(requests_list) > MAX_BATCH_SIZE:
        frappe.throw(
            f"Batch size {len(requests_list)} exceeds maximum of {MAX_BATCH_SIZE}",
            frappe.ValidationError,
        )

    n = len(requests_list)
    normalized = [None] * n
    errors_list = [None] * n
    # Resolve the caller's entity allow-list once for the whole batch.
    allowed_entities = _allowed_entities()
    for i, req in enumerate(requests_list):
        scenario = req.get("scenario", "actuals")
        fact_name = req.get("fact")
        measure = req.get("measure", "period_net_amount")

        raw_dims = req.get("dimensions") if isinstance(req.get("dimensions"), dict) else {}
        dimensions = {k: v for k, v in raw_dims.items() if v}

        fact_doc, err = _resolve_and_validate(
            fact_name, scenario, measure, dimensions.keys())
        if err:
            errors_list[i] = err
            continue
        if allowed_entities is not None and req.get("entity", "") not in allowed_entities:
            errors_list[i] = f"Not permitted to access entity '{req.get('entity', '')}'"
            continue

        try:
            periods = _resolve_period(req.get("period", 0))
        except (ValueError, TypeError):
            errors_list[i] = f"Invalid period '{req.get('period')}'"
            continue

        try:
            year = int(req.get("year", 0))
        except (ValueError, TypeError):
            # A non-numeric year (e.g. JSON null from a blank Excel cell)
            # must fail only this row — not raise and 500 the whole batch.
            errors_list[i] = f"Invalid year '{req.get('year')}'"
            continue

        normalized[i] = {
            "entity": req.get("entity", ""),
            "year": year,
            "periods": periods,
            "account": req.get("account", ""),
            "measure": measure,
            "fact": fact_doc.fact_name,
            "scenario": scenario,
            "dimensions": dimensions,
            "scenario_id": req.get("scenario_id", ""),
        }

    valid = [(i, r) for i, r in enumerate(normalized) if r is not None]
    if valid:
        valid_indices, valid_reqs = zip(*valid)
        ch_result = _batch_query_clickhouse(list(valid_reqs))
        values = [None] * n
        for j, orig_idx in enumerate(valid_indices):
            values[orig_idx] = ch_result["values"][j]
            if ch_result.get("errors") and ch_result["errors"][j]:
                errors_list[orig_idx] = ch_result["errors"][j]
    else:
        values = [None] * n

    result = {"values": values}
    if any(e is not None for e in errors_list):
        result["errors"] = errors_list
    return result


# ---------------------------------------------------------------------------
# Budget Write-Back Endpoints
# ---------------------------------------------------------------------------

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
    filters = _budget_filters(data)
    existing = frappe.get_all("Budget Input", filters=filters, limit=1)

    if existing:
        doc = frappe.get_doc("Budget Input", existing[0].name)
        doc.periods = []
    else:
        doc = frappe.new_doc("Budget Input")
        doc.update(filters)

    # Set dynamic dimension fields from data
    budget_dims = frappe.get_all(
        "Dimension",
        filters={"in_budget": 1, "status": "Published"},
        fields=["dimension_name"],
        limit_page_length=0,
    )
    for dim in budget_dims:
        doc.set(dim.dimension_name, data.get(dim.dimension_name, ""))

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
    """Save a single budget line — creates/updates Budget Input doc in Draft."""
    data = _get_json_body()
    _validate_budget_fields(data)
    name = _upsert_budget_input(data)
    return {"name": name}


@frappe.whitelist(methods=["POST"])
def budget_cell_save():
    """Save a single budget cell — upserts one period+layer in a Budget Input doc.

    Designed for EPMSAVE() immediate writes from Excel.
    """
    data = _get_json_body()

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

    filters = _budget_filters(data)
    existing = frappe.get_all("Budget Input", filters=filters, limit=1)

    if existing:
        doc = frappe.get_doc("Budget Input", existing[0].name)
    else:
        doc = frappe.new_doc("Budget Input")
        doc.update(filters)
        # Set dynamic dimension fields
        budget_dims = frappe.get_all(
            "Dimension",
            filters={"in_budget": 1, "status": "Published"},
            fields=["dimension_name"],
            limit_page_length=0,
        )
        for dim in budget_dims:
            doc.set(dim.dimension_name, data.get(dim.dimension_name, ""))

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
    """Save multiple budget lines at once."""
    items = _get_json_body()
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
        except Exception:
            frappe.log_error("Budget save failed", frappe.get_traceback())
            results.append(None)
            errors.append({"index": i, "error": "Save failed — check server logs"})

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
    """
    filters = {}
    if consolidation_group:
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
        for d in docs:
            if d.parent_consolidation_group == doc.name:
                node["children"].append(build_node(d))
        return node

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
    """Approve a Consolidation Adjustment (Pending Approval -> Approved)."""
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

    Creates a reversal doc with negated amounts, links both via reversal_journal_id.
    """
    doc = frappe.get_doc("Consolidation Adjustment", name)
    if doc.status != "Approved":
        frappe.throw(
            f"Cannot reverse: current status is '{doc.status}', expected 'Approved'",
            frappe.ValidationError,
        )

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
    """Create and submit an Allocation Run for a given period."""
    doc = frappe.new_doc("Allocation Run")
    doc.fiscal_year = int(fiscal_year)
    doc.fiscal_period = int(fiscal_period)
    doc.insert()
    doc.submit()

    return {
        "name": doc.name,
        "allocation_run_id": doc.allocation_run_id,
        "status": doc.status,
        "run_by": doc.run_by,
        "run_at": str(doc.run_at),
    }


@frappe.whitelist(methods=["POST"])
def reverse_allocation(name):
    """Reverse an Active Allocation Run. Creates reversal run and cancels original."""
    doc = frappe.get_doc("Allocation Run", name)
    if doc.status != "Active":
        frappe.throw(
            f"Cannot reverse: current status is '{doc.status}', expected 'Active'",
            frappe.ValidationError,
        )

    reversal = frappe.new_doc("Allocation Run")
    reversal.fiscal_year = doc.fiscal_year
    reversal.fiscal_period = doc.fiscal_period
    reversal.reversal_of = doc.name
    reversal.insert()
    reversal.submit()

    doc.cancel()

    return {
        "original": doc.name,
        "reversal": reversal.name,
        "status": "Reversed",
    }


# ---------------------------------------------------------------------------
# Airbyte Sync Webhook
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True, methods=["POST"])
def airbyte_sync_complete():
    """Webhook endpoint called by Airbyte after sync completes.

    Updates EPM Settings with sync timestamp, status, and row count.
    Publishes realtime event for UI refresh.

    Auth: accepts either a logged-in Frappe session OR a shared secret via
    X-Webhook-Secret header (configured in EPM Settings.webhook_secret).
    """
    settings = frappe.get_single("EPM Settings")

    if frappe.session.user == "Guest":
        secret = frappe.request.headers.get("X-Webhook-Secret", "")
        expected = (settings.get_password("webhook_secret", raise_exception=False) or "")
        # Constant-time comparison to avoid leaking the secret via timing.
        if not expected or not hmac.compare_digest(str(secret), str(expected)):
            frappe.throw("Unauthorized", frappe.AuthenticationError)

    data = _get_json_body()
    settings.last_airbyte_sync_at = now_datetime()
    settings.last_airbyte_sync_status = data.get("status", "Success")
    settings.last_airbyte_sync_rows = int(data.get("rows_synced", 0))
    settings.flags.ignore_permissions = True
    settings.save()

    # Per-connector sync status: if the payload names a connection that maps to
    # a registered Connector, update that connector instead of only the global
    # EPM Settings — Build Governance preflight reads per-connector status.
    connection_id = data.get("connection_id") or data.get("connectionId")
    if connection_id and frappe.db.table_exists("Connector"):
        names = frappe.get_all(
            "Connector",
            filters={"airbyte_connection_id": connection_id},
            pluck="name",
            limit_page_length=0,
        )
        for name in names:
            conn = frappe.get_doc("Connector", name)
            conn.last_sync_at = settings.last_airbyte_sync_at
            conn.last_sync_status = settings.last_airbyte_sync_status
            conn.last_sync_rows = settings.last_airbyte_sync_rows
            conn.flags.ignore_permissions = True
            conn.save()

    frappe.db.commit()

    frappe.publish_realtime(
        "airbyte_sync_complete",
        {
            "status": settings.last_airbyte_sync_status,
            "rows": settings.last_airbyte_sync_rows,
            "timestamp": str(settings.last_airbyte_sync_at),
        },
    )

    return {
        "status": "ok",
        "sync_status": settings.last_airbyte_sync_status,
        "rows": settings.last_airbyte_sync_rows,
    }


@frappe.whitelist()
def allocation_history(fiscal_year=None, fiscal_period=None):
    """Return allocation run history with optional filters."""
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


@frappe.whitelist()
def connector_health():
    """Per-connector sync health for the operator dashboard.

    Returns one object per connector with derived status, lag, entity counts,
    and last error. Read-gated by the Connector Health doctype (System Manager /
    EPM Admin / EPM Analyst / EPM User). The rows are maintained by the
    ``refresh_connector_health`` scheduler job.
    """
    if not frappe.has_permission("Connector Health", "read"):
        raise frappe.PermissionError("Not permitted to read Connector Health")
    return frappe.get_all(
        "Connector Health",
        fields=[
            "connector", "erp_source", "last_sync_status", "lag_minutes",
            "entities_loaded", "rows_emitted",
            "last_sync_end", "last_error", "checked_at",
        ],
        order_by="lag_minutes desc",
        limit_page_length=0,
    )
