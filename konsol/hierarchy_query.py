"""Reporting hierarchy resolution and ClickHouse batch queries for =K.EPM() v2."""
from __future__ import annotations

import re
from collections import defaultdict

import requests

_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_SAFE_SCENARIO_ID = re.compile(r"^[A-Za-z0-9_]+$")
_ENTITY_WILDCARD = {"", "*", "ALL"}

HIERARCHY_SCENARIO_CONFIG = {
    "actuals": {
        "table": "epm_gold.gold_tb_at_hierarchy_node",
        "default_measure": "period_net_amount",
        "measures": {
            "period_net_amount", "period_debit", "period_credit", "transaction_count",
        },
        "has_scenario_id": False,
    },
    "budget": {
        "table": "epm_gold.gold_budget_at_hierarchy_node",
        "default_measure": "period_amount",
        "measures": {"period_amount", "annual_amount"},
        "has_scenario_id": True,
        "has_layer": True,
    },
    "forecast": {
        "table": "epm_gold.gold_budget_at_hierarchy_node",
        "default_measure": "period_amount",
        "measures": {"period_amount", "annual_amount"},
        "has_scenario_id": True,
        "has_layer": True,
    },
    "variance": {
        "table": "epm_gold.gold_variance_at_hierarchy_node",
        "default_measure": "variance_abs",
        "measures": {"variance_abs", "actual_amount", "budget_amount"},
        "has_scenario_id": False,
    },
}


def _normalize_scenario(scenario):
    return (scenario or "actuals").strip().lower()


def _clickhouse_query(sql, params, ch_settings):
    from konsol.clickhouse import connection_url as _ch_url

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


def entity_is_wildcard(entity):
    return (entity or "").strip().upper() in _ENTITY_WILDCARD


def choose_hierarchy(node_code, trees):
    """Pick the tree for a node when the formula names none.

    ``trees`` lists the published hierarchies that hold the node's code.
    Exactly one is an answer. None, or several, is an error, never a guess:
    the resolver used to take whichever tree was edited last, so a formula's
    value could change when someone edited a different tree.
    """
    if len(trees) == 1:
        return trees[0], None
    if not trees:
        return None, f"Node '{node_code}' is not in any published Reporting Hierarchy."
    return None, (
        f"Node '{node_code}' is in {len(trees)} published hierarchies "
        f"({', '.join(trees)}). Pass the hierarchy name to choose one."
    )


def choose_member(node_code, hierarchy_name, members):
    """The one member of a tree carrying ``node_code``, or an error.

    Two members sharing a code can't be told apart by a formula or by the
    warehouse rollup, which joins on the code.
    """
    if not members:
        return None, f"Node '{node_code}' not found in hierarchy '{hierarchy_name}'"
    if len(members) > 1:
        labels = ", ".join(str(m.get("member_label") or "?") for m in members)
        return None, (
            f"Node '{node_code}' is used by {len(members)} members of hierarchy "
            f"'{hierarchy_name}' ({labels}). Give each member its own Member Code."
        )
    return members[0], None


def resolve_hierarchy_name(hierarchy_name, node_code):
    """The tree to read: the one named, else the only published tree holding
    the node. A node in several published trees is an error that names them."""
    import frappe

    node_code = (node_code or "").strip()
    if not node_code:
        return None, "hierarchy_node is required for hierarchy mode"

    hierarchy_name = (hierarchy_name or "").strip()
    if hierarchy_name:
        if not frappe.db.exists(
            "Reporting Hierarchy",
            {"hierarchy_name": hierarchy_name, "status": "Published"},
        ):
            return None, f"Reporting Hierarchy '{hierarchy_name}' not found or not published"
        return hierarchy_name, None

    holders = sorted(set(frappe.get_all(
        "Reporting Hierarchy Member",
        filters={"member_code": node_code},
        pluck="reporting_hierarchy",
    )))
    trees = frappe.get_all(
        "Reporting Hierarchy",
        filters={"name": ["in", holders], "status": "Published"},
        pluck="hierarchy_name",
        order_by="hierarchy_name asc",
    ) if holders else []
    return choose_hierarchy(node_code, trees)


def get_hierarchy_member(hierarchy_name, node_code):
    """Return member info dict or error string."""
    import frappe

    header = frappe.db.get_value(
        "Reporting Hierarchy",
        {"hierarchy_name": hierarchy_name},
        ["name", "dimension", "status"],
        as_dict=True,
    )
    if not header:
        return None, f"Reporting Hierarchy '{hierarchy_name}' not found"
    if header.status != "Published":
        return None, f"Reporting Hierarchy '{hierarchy_name}' is not published"

    member, err = choose_member(node_code, hierarchy_name, frappe.get_all(
        "Reporting Hierarchy Member",
        filters={"reporting_hierarchy": header.name, "member_code": node_code},
        fields=["member_code", "member_label", "is_group"],
        order_by="name asc",
    ))
    if err:
        return None, err
    return {
        "hierarchy_name": hierarchy_name,
        "dimension": header.dimension,
        "member_code": member.member_code,
        "member_label": member.member_label,
        "is_group": bool(member.is_group),
    }, None


def validate_hierarchy_read(hierarchy_name, node_code, scenario):
    """Validate hierarchy + node for a read (group nodes allowed)."""
    hname, err = resolve_hierarchy_name(hierarchy_name, node_code)
    if err:
        return None, err
    info, err = get_hierarchy_member(hname, node_code)
    if err:
        return None, err

    sc = _normalize_scenario(scenario)
    if sc not in HIERARCHY_SCENARIO_CONFIG:
        allowed = ", ".join(sorted(HIERARCHY_SCENARIO_CONFIG))
        return None, f"Invalid scenario '{scenario}' for hierarchy mode. Allowed: {allowed}"

    if sc in ("budget", "forecast", "variance"):
        from konsol.epm.budget_grain import budget_dimension_names
        if info["dimension"] not in budget_dimension_names():
            return None, (
                f"Hierarchy axis '{info['dimension']}' is not supported for scenario "
                f"'{sc}' (requires in_budget dimensions: "
                f"{', '.join(budget_dimension_names()) or 'none'}). "
                "Use actuals for this hierarchy, or rebuild the hierarchy on a budget dimension."
            )
    return {**info, "hierarchy_name": hname}, None


def validate_hierarchy_write(hierarchy_name, node_code):
    """Budget write-back only at leaf nodes."""
    info, err = validate_hierarchy_read(hierarchy_name, node_code, "budget")
    if err:
        return None, err
    if info["is_group"]:
        return None, (
            f"Node '{node_code}' is a group — budget write-back is only allowed at leaf nodes."
        )
    return info, None


def batch_query_hierarchy(requests_list, allowed_entities=None):
    """Execute hierarchy-mode batch queries. Returns {values, errors}."""
    from konsol.clickhouse import get_connection as _get_ch_connection

    ch_settings = _get_ch_connection()
    n = len(requests_list)
    values = [None] * n
    errors = [None] * n

    groups = defaultdict(list)
    for idx, req in enumerate(requests_list):
        sc = _normalize_scenario(req.get("scenario", "actuals"))
        cfg = HIERARCHY_SCENARIO_CONFIG.get(sc)
        if not cfg:
            errors[idx] = f"Unsupported hierarchy scenario '{sc}'"
            continue
        measure = req.get("measure") or cfg["default_measure"]
        if measure not in cfg["measures"]:
            errors[idx] = (
                f"Invalid measure '{measure}' for hierarchy scenario '{sc}'. "
                f"Allowed: {', '.join(sorted(cfg['measures']))}"
            )
            continue
        dims = frozenset(req.get("dimensions", {}).keys())
        wildcard = entity_is_wildcard(req.get("entity", ""))
        key = (
            sc,
            measure,
            req["hierarchy_name"],
            req["hierarchy_node"],
            req["periods"],
            dims,
            req.get("scenario_id", ""),
            req.get("layer", ""),
            wildcard,
            "" if wildcard else req.get("entity", ""),
        )
        groups[key].append((idx, req))

    for key, group_items in groups.items():
        sc, measure, hname, node, periods, dim_names, scenario_id, layer, wildcard, _entity_key = key
        cfg = HIERARCHY_SCENARIO_CONFIG[sc]
        table = cfg["table"]

        if not _SAFE_IDENTIFIER.match(measure):
            for idx, _ in group_items:
                errors[idx] = "Invalid measure identifier"
            continue

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

        if wildcard:
            select_cols = ["fiscal_year", "main_account"]
        else:
            select_cols = ["data_area_id", "fiscal_year", "main_account"]
        select_cols.extend(dim_names_sorted)

        params = {"param_hname": hname, "param_node": node}
        in_tuples = []
        for i, (idx, req) in enumerate(group_items):
            ent = req.get("entity", "")
            if wildcard:
                parts = [f"{{y{i}:Int32}}", f"{{a{i}:String}}"]
                params[f"param_y{i}"] = str(req["year"])
                params[f"param_a{i}"] = req["account"]
            else:
                parts = [f"{{e{i}:String}}", f"{{y{i}:Int32}}", f"{{a{i}:String}}"]
                params[f"param_e{i}"] = ent
                params[f"param_y{i}"] = str(req["year"])
                params[f"param_a{i}"] = req["account"]
            dims = req.get("dimensions", {})
            for di, dn in enumerate(dim_names_sorted):
                parts.append(f"{{d{i}_{di}:String}}")
                params[f"param_d{i}_{di}"] = dims.get(dn, "")
            in_tuples.append(f"({', '.join(parts)})")

        period_placeholders = []
        for pi, p in enumerate(periods):
            pkey = f"fp{pi}"
            period_placeholders.append(f"{{{pkey}:Int32}}")
            params[f"param_{pkey}"] = str(p)

        entity_clause = ""
        if wildcard and allowed_entities is not None:
            if not allowed_entities:
                for idx, _ in group_items:
                    errors[idx] = "Not permitted to access any entity"
                continue
            ent_list = sorted(allowed_entities)
            ent_ph = []
            for ei, e in enumerate(ent_list):
                ek = f"ent{ei}"
                ent_ph.append(f"{{{ek}:String}}")
                params[f"param_{ek}"] = e
            entity_clause = f" AND data_area_id IN ({', '.join(ent_ph)})"

        scenario_id_clause = ""
        if cfg.get("has_scenario_id") and scenario_id:
            if not _SAFE_SCENARIO_ID.match(scenario_id):
                for idx, _ in group_items:
                    errors[idx] = "Invalid scenario_id format"
                continue
            params["param_sid"] = scenario_id
            scenario_id_clause = " AND scenario_id = {sid:String}"

        # Optional budget layer filter — mirrors the flat path in api.py. Omitted
        # → sum across all layers (the final budget); supplied → restrict to one
        # layer. Only budget/forecast configs are layered (has_layer). Not
        # selected/grouped, just narrowed (grynn-in/konsol#63).
        layer_clause = ""
        if cfg.get("has_layer") and layer:
            if not _SAFE_IDENTIFIER.match(layer):
                for idx, _ in group_items:
                    errors[idx] = "Invalid layer format"
                continue
            params["param_layer"] = layer
            layer_clause = " AND layer = {layer:String}"

        group_by = ", ".join(select_cols)
        in_cols = f"({group_by})"
        in_values = ", ".join(in_tuples)
        period_in = ", ".join(period_placeholders)

        sql = (
            f"SELECT {group_by}, coalesce(sum({measure}), 0) as val "
            f"FROM {table} "
            f"WHERE hierarchy_name = {{hname:String}} "
            f"AND hierarchy_member_code = {{node:String}} "
            f"AND {in_cols} IN ({in_values}) "
            f"AND fiscal_period IN ({period_in})"
            f"{entity_clause}"
            f"{scenario_id_clause}"
            f"{layer_clause} "
            f"GROUP BY {group_by}"
        )

        try:
            raw = _clickhouse_query(sql, params, ch_settings)
            result_lookup = {}
            for line in raw.split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                result_lookup[tuple(parts[:-1])] = float(parts[-1])

            for _, (idx, req) in enumerate(group_items):
                dims = req.get("dimensions", {})
                if wildcard:
                    lookup_key = [str(req["year"]), req["account"]]
                else:
                    lookup_key = [req.get("entity", ""), str(req["year"]), req["account"]]
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
            import frappe
            frappe.log_error("Hierarchy ClickHouse query failed", frappe.get_traceback())
            for idx, _ in group_items:
                values[idx] = None
                errors[idx] = "ClickHouse query failed"

    result = {"values": values}
    if any(e is not None for e in errors):
        result["errors"] = errors
    return result