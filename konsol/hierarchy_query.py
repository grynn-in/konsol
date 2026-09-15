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
        # One set of rows per active budget scenario (konsol#214): a read that
        # did not filter on it would add budget scenarios together, so every
        # variance read names one (the request's, else the single active one).
        "has_scenario_id": True,
        "scenario_column": "budget_scenario_id",
        "needs_budget_scenario": True,
    },
}


def _normalize_scenario(scenario):
    return (scenario or "actuals").strip().lower()


def _clickhouse_query(sql, params, ch_settings):
    """The flat path's api._clickhouse_query, so both paths report a failed
    query the same way: ClickHouse's reply logged, its code and exception
    name raised as api.ClickHouseQueryError (konsol#214 row K8). Imported
    here, not at load: api imports this module in its functions."""
    from konsol.api import _clickhouse_query as run

    return run(sql, params, ch_settings)


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


def _tranche_start(m):
    return str(m.get("effective_from") or "1900-01-01")


def covering_tranche(members, day):
    """The row of ONE code whose window covers ``day``, else None (konsol#220).

    A blank ``effective_from`` means 1900-01-01, a blank ``effective_to``
    means still open; both bounds are inclusive.
    """
    day = str(day)
    for m in members or []:
        end = m.get("effective_to")
        if _tranche_start(m) <= day and (not end or day <= str(end)):
            return m
    return None


def current_tranche(members, today):
    """The tranche of ONE code that applies on ``today`` (konsol#220).

    A renamed or moved node is several dated rows of one code. The row whose
    window covers ``today`` is the answer (a blank ``effective_from`` means
    1900-01-01, a blank ``effective_to`` means still open); when none does,
    the row with the latest ``effective_from``. None for no rows.
    """
    if not members:
        return None
    return covering_tranche(members, today) or max(members, key=_tranche_start)


def resolve_hierarchy_name(hierarchy_name, node_code):
    """The tree to read: the one named, else the only published tree holding
    the node. A node in several published trees is an error that names them."""
    import frappe

    node_code = (node_code or "").strip()
    if not node_code:
        return None, "hierarchy_node is required for hierarchy mode"

    hierarchy_name = (hierarchy_name or "").strip()
    if hierarchy_name:
        # MariaDB matches 'mgmt_2026' to MGMT_2026; ClickHouse would not, so
        # pass the stored spelling on.
        stored = frappe.db.get_value(
            "Reporting Hierarchy",
            {"hierarchy_name": hierarchy_name, "status": "Published"},
            "hierarchy_name",
        )
        if not stored:
            return None, f"Reporting Hierarchy '{hierarchy_name}' not found or not published"
        return stored, None

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


def get_hierarchy_member(hierarchy_name, node_code, on=None, on_label=None):
    """Return member info dict or error string.

    Without ``on`` (reads) the tranche of today, or the latest one. With
    ``on`` (an ISO date) only the tranche covering that day; none covering it
    is an error naming the day and ``on_label``.
    """
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

    # Every row here shares the code; the dated tranches of one node are not
    # duplicates, so only the one of today goes on (konsol#220).
    rows = frappe.get_all(
        "Reporting Hierarchy Member",
        filters={"reporting_hierarchy": header.name, "member_code": node_code},
        fields=["member_code", "member_label", "is_group", "effective_from", "effective_to"],
        order_by="name asc",
    )
    if on is None:
        tranche = current_tranche(rows, frappe.utils.today())
    else:
        tranche = covering_tranche(rows, on)
        if rows and tranche is None:
            when = f"{on} ({on_label})" if on_label else str(on)
            return None, f"Node '{node_code}' is not in hierarchy '{hierarchy_name}' on {when}."
    member, err = choose_member(node_code, hierarchy_name, [tranche] if tranche else [])
    if err:
        return None, err
    return {
        "hierarchy_name": hierarchy_name,
        "dimension": header.dimension,
        "member_code": member.member_code,
        "member_label": member.member_label,
        "is_group": bool(member.is_group),
    }, None


def validate_hierarchy_read(hierarchy_name, node_code, scenario, on=None, on_label=None):
    """Validate hierarchy + node for a read (group nodes allowed). ``on`` /
    ``on_label`` pick the tranche of one day (see get_hierarchy_member)."""
    hname, err = resolve_hierarchy_name(hierarchy_name, node_code)
    if err:
        return None, err
    info, err = get_hierarchy_member(hname, node_code, on=on, on_label=on_label)
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


def validate_hierarchy_write(hierarchy_name, node_code, *, fiscal_year, fiscal_period):
    """Budget write-back only at leaf nodes, as the node is in the period
    written (konsol#220): the tranche covering the period's end date in the
    declared calendar. An undeclared period raises from period_dates."""
    from konsol.period_status import period_dates

    end = period_dates(fiscal_year, fiscal_period)[1]
    on = end.isoformat() if hasattr(end, "isoformat") else str(end)
    info, err = validate_hierarchy_read(
        hierarchy_name, node_code, "budget",
        on=on, on_label=f"FY{fiscal_year} P{fiscal_period}",
    )
    if err:
        return None, err
    if info["is_group"]:
        return None, (
            f"Node '{node_code}' is a group — budget write-back is only allowed at leaf nodes."
        )
    return info, None


def _active_budget_scenarios():
    """The scenario_ids of the active budget scenarios, sorted."""
    import frappe

    return frappe.get_all(
        "Scenario",
        filters={"scenario_type": "budget", "is_active": 1},
        pluck="scenario_id",
        order_by="scenario_id asc",
    )


def _active_budget_scenarios_by_year():
    """{fiscal_year: [scenario_id, ...]} of the active budget scenarios.

    A budget scenario belongs to the years of its Budget Cycles. A cancelled
    cycle (docstatus 2) has withdrawn its sheets, so it does not count. Two
    lookups, however many years a call reads.
    """
    import frappe

    active = _active_budget_scenarios()
    if not active:
        return {}
    by_year = defaultdict(set)
    for cycle in frappe.get_all(
        "Budget Cycle",
        filters={"scenario_id": ["in", active], "docstatus": ["<", 2]},
        fields=["scenario_id", "fiscal_year"],
    ):
        by_year[int(cycle["fiscal_year"])].add(cycle["scenario_id"])
    return {year: sorted(ids) for year, ids in by_year.items()}


def choose_budget_scenario(active, fiscal_year):
    """The budget scenario a variance read uses when none is named.

    ``active`` is the active budget scenarios already narrowed to
    ``fiscal_year``. Exactly one is the answer. None, or several, is an
    error that says so, never a guess (konsol#214).
    """
    if len(active) == 1:
        return active[0], None
    if not active:
        return None, (
            f"No active budget scenario belongs to FY{fiscal_year}, "
            "so there is no variance to show."
        )
    return None, (
        f"Several active budget scenarios belong to FY{fiscal_year} "
        f"({', '.join(active)}); choose one."
    )


def named_budget_scenario_error(scenario_id, active):
    """Why a variance read cannot use the named ``scenario_id``, or None.

    ``active`` is the active budget scenarios. The warehouse variance models
    keep only those, so any other id (an actuals scenario, an inactive or a
    misspelled one) would filter to nothing and read 0.0 (konsol#214).
    """
    if scenario_id in active:
        return None
    return f"{scenario_id} is not an active budget scenario, so there is no variance for it."


def batch_query_hierarchy(requests_list, *, allowed_entities):
    """Execute hierarchy-mode batch queries. Returns {values, errors}.

    ``allowed_entities`` is the reader's allowed_entity_codes() (None when
    unrestricted) and is required: a forgotten argument must not read every
    entity. Callers refuse named entities outside it before calling; here a
    wildcard read is limited to it (entity_permissions.entity_read_scope).
    """
    from konsol.clickhouse import get_connection as _get_ch_connection
    from konsol.entity_permissions import entity_read_scope

    ch_settings = _get_ch_connection()
    n = len(requests_list)
    values = [None] * n
    errors = [None] * n

    groups = defaultdict(list)
    budgets_by_year = None  # looked up once per call, when first needed
    active_budgets = None  # likewise, for named variance scenarios
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
        scenario_id = req.get("scenario_id", "")
        if cfg.get("needs_budget_scenario") and not scenario_id:
            # No scenario named: the one active budget of the request's year.
            if budgets_by_year is None:
                budgets_by_year = _active_budget_scenarios_by_year()
            year = int(req["year"])
            scenario_id, err = choose_budget_scenario(
                budgets_by_year.get(year, []), fiscal_year=year)
            if err:
                errors[idx] = err
                continue
        elif cfg.get("needs_budget_scenario") and _SAFE_SCENARIO_ID.match(scenario_id):
            # A named scenario must be one the warehouse keeps variance for.
            # (An unsafe id is refused by its format below.)
            if active_budgets is None:
                active_budgets = set(_active_budget_scenarios())
            err = named_budget_scenario_error(scenario_id, active_budgets)
            if err:
                errors[idx] = err
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
            scenario_id,
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
        scope, denied = (entity_read_scope(None, allowed_entities, wildcard=True)
                         if wildcard else (None, None))
        if denied:
            for idx, _ in group_items:
                errors[idx] = denied
            continue
        if scope is not None:
            ent_ph = []
            for ei, e in enumerate(scope):
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
            column = cfg.get("scenario_column", "scenario_id")
            scenario_id_clause = f" AND {column} = {{sid:String}}"

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
        except Exception as exc:
            from konsol.api import ClickHouseQueryError

            if isinstance(exc, ClickHouseQueryError):
                # already logged with ClickHouse's reply; the message is its
                # code and exception name only (row K7)
                message = str(exc)
            else:
                import frappe
                frappe.log_error("Hierarchy ClickHouse query failed", frappe.get_traceback())
                message = "ClickHouse query failed"
            for idx, _ in group_items:
                values[idx] = None
                errors[idx] = message

    result = {"values": values}
    if any(e is not None for e in errors):
        result["errors"] = errors
    return result