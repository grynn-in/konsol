"""The entity and group lists the Excel add-in's report pickers show.

The same lists validate the parameters a report is submitted with
(:func:`is_allowed`), so a picker never offers a code the server would refuse.

Both lists are filtered by ``entity_permissions.entity_read_scope``, the rule
``epm_batch`` applies to every read: an unrestricted reader sees everything, a
restricted one sees the codes in ``allowed_entity_codes()``. A group is read
through its root row's entity (``data_area_id``), so it is listed only when
that entity is allowed. No caching: each call reads the current permissions.
"""
import frappe


def _signed_in():
    """Every list is for a signed-in user; a Guest is refused."""
    if frappe.session.user == "Guest":
        frappe.throw("Sign in to read report choices", frappe.PermissionError)


def _allowed():
    _signed_in()
    from konsol.entity_permissions import allowed_entity_codes

    return allowed_entity_codes()


def _readable(code, allowed):
    from konsol.entity_permissions import entity_read_scope

    _, error = entity_read_scope(code, allowed)
    return error is None


def entities():
    """Entities the current user may read, sorted by code."""
    allowed = _allowed()
    rows = frappe.get_all(
        "Entity",
        fields=["entity_code", "entity_name", "is_group", "functional_currency", "status"],
        order_by="entity_code asc",
        limit_page_length=0,
    )
    return [
        {
            "code": r.entity_code,
            "name": r.entity_name,
            "is_group": bool(r.is_group),
            "currency": r.functional_currency,
            "status": r.status,
        }
        for r in rows
        if _readable(r.entity_code, allowed)
    ]


def groups():
    """Distinct Consolidation Group codes the current user may read, sorted.

    Members share their root's code, so each code appears once. The name and
    the entity come from the root row (the one with no parent); a code with no
    root row, which should not exist, falls back to its first row.
    """
    allowed = _allowed()
    rows = frappe.get_all(
        "Consolidation Group",
        fields=["consolidation_group", "entity_name", "data_area_id",
                "parent_consolidation_group"],
        order_by="consolidation_group asc, name asc",
        limit_page_length=0,
    )
    by_code = {}
    for r in rows:
        current = by_code.get(r.consolidation_group)
        is_root = not r.parent_consolidation_group
        if current is None or (is_root and current.get("_root") is False):
            by_code[r.consolidation_group] = {
                "code": r.consolidation_group,
                "name": r.entity_name,
                "entity": r.data_area_id,
                "_root": is_root,
            }
    return [
        {"code": g["code"], "name": g["name"]}
        for _, g in sorted(by_code.items())
        if _readable(g["entity"], allowed)
    ]


def _iso(value):
    return str(value)[:10] if value else None


def _regular_periods(year):
    return frappe.get_all(
        "EPM Fiscal Year Period",
        filters={"parent": str(int(year)), "parenttype": "EPM Fiscal Year",
                 "period_type": "Regular"},
        fields=["fiscal_period", "period_code", "period_label", "quarter",
                "start_date", "end_date"],
        order_by="fiscal_period asc",
        parent_doctype="EPM Fiscal Year",
        limit_page_length=0,
    )


def fiscal_years():
    """Fiscal years with at least one Regular period, ascending.

    Opening, Closing and Adjustment periods are never report columns
    (decided 27 Nov 2026, Deepak Pai, #210 part 3), so a year with only those
    is not offered.
    """
    _signed_in()
    rows = frappe.get_all(
        "EPM Fiscal Year Period",
        filters={"parenttype": "EPM Fiscal Year", "period_type": "Regular"},
        fields=["parent"],
        order_by="parent asc",
        parent_doctype="EPM Fiscal Year",
        limit_page_length=0,
    )
    return sorted({int(r.parent) for r in rows})


def periods(year):
    """The year's declared Regular periods in fiscal_period order. A year with
    none returns an empty list. The label falls back to the code, as the
    EPM Fiscal Year doctype does (``period_label or period_code``)."""
    _signed_in()
    return [
        {
            "number": int(r.fiscal_period),
            "code": r.period_code,
            "label": r.period_label or r.period_code,
            "start_date": _iso(r.start_date),
            "end_date": _iso(r.end_date),
            "quarter": r.quarter or None,
        }
        for r in _regular_periods(year)
    ]


def hierarchies():
    """Published Reporting Hierarchies, by name."""
    _signed_in()
    rows = frappe.get_all(
        "Reporting Hierarchy",
        filters={"status": "Published"},
        fields=["hierarchy_name", "label", "dimension"],
        order_by="hierarchy_name asc",
        limit_page_length=0,
    )
    return [{"name": r.hierarchy_name, "label": r.label or r.hierarchy_name,
             "dimension": r.dimension} for r in rows]


def hierarchy_nodes(hierarchy, year):
    """The hierarchy's nodes flattened in tree order (a parent before its
    children), read as of the end of the year's last Regular period."""
    _signed_in()
    regular = _regular_periods(year)
    if not regular:
        frappe.throw(f"Fiscal year {year} has no Regular period to read the hierarchy at",
                     frappe.ValidationError)
    from konsol.api import get_reporting_hierarchy_tree  # lazy: api imports this module

    tree = get_reporting_hierarchy_tree(hierarchy, as_of=_iso(regular[-1].end_date))
    out = []

    def walk(nodes, depth, parent):
        for n in nodes:
            out.append({"code": n["member_code"], "label": n["member_label"],
                        "depth": depth, "is_group": bool(n["is_group"]),
                        "parent": parent})
            walk(n.get("children") or [], depth + 1, n["member_code"])

    walk(tree["tree"], 0, None)
    return out


def dimension_values(dimension):
    """Values of one declared Dimension, through the Dimension Value list,
    which runs its own read check."""
    _signed_in()
    rows = frappe.get_all(
        "Dimension Value",
        filters={"dimension": dimension},
        fields=["name", "value"],
        order_by="name asc",
        limit_page_length=0,
    )
    return [{"code": r.value, "label": r.value} for r in rows]


def _published_accounts(is_group, section):
    """Published Main Accounts of one chart, in code order.

    Mirrors ``konsol.api._pnl_accounts`` (api.py:1414-1426): status Published,
    ``order_by main_account asc``, and leaves in more than one chart refused
    (api.py:1420-1426). The section filter is optional here (api.py:1416 fixes
    it to Profit and Loss), and ``is_group`` selects leaves or headings.
    """
    filters = {"status": "Published", "is_group": is_group}
    if section:
        filters["statement_section"] = section
    rows = frappe.get_all(
        "Main Account",
        filters=filters,
        fields=["main_account", "account_name", "chart_of_accounts",
                "statement_section", "account_type", "parent_account"],
        order_by="main_account asc",
        limit_page_length=0,
    )
    charts = sorted({r.chart_of_accounts for r in rows if r.chart_of_accounts})
    if len(charts) > 1:
        frappe.throw(
            "Report accounts are read from one chart, and Published accounts exist in "
            f"one chart only: found charts {', '.join(charts)}. Make one chart Inactive or Draft.",
            frappe.ValidationError,
        )
    return [
        {"code": r.main_account, "name": r.account_name or r.main_account,
         "statement_section": r.statement_section, "account_type": r.account_type,
         "parent": r.parent_account or None}
        for r in rows
    ]


def accounts(section=None):
    """Published leaf accounts of the chart, optionally of one statement section."""
    _signed_in()
    return _published_accounts(0, section)


def account_headings(section=None):
    """Published heading (group) accounts of the chart, optionally of one section."""
    _signed_in()
    return _published_accounts(1, section)


def scenarios(year, scenario_type):
    """The scenarios a report column can read for ``year``, with the default.

    ``scenario_type`` is "budget" (the year's active budget scenarios: an
    approved Budget Cycle) or "forecast" (its declared Current Forecasts).
    The lookups and the default rule are hierarchy_query's own, so a picker
    offers what a read accepts and says what a read says: ``default`` is set
    only when the helper picks exactly one; otherwise it is None and ``why``
    is the helper's refusal text. The label is the Scenario's name, else its id.
    """
    _signed_in()
    from konsol import hierarchy_query as hq  # lazy: it imports heavy modules

    year = int(year)
    if scenario_type == "budget":
        ids = list(hq._active_budget_scenarios_by_year().get(year, []))
        default, why = hq.choose_budget_scenario(ids, year)
    elif scenario_type == "forecast":
        ids = list(hq._declared_forecasts_by_year().get(year, []))
        default, why = hq.choose_declared_forecast(None, ids, year)
    else:
        raise ValueError(f"Unknown scenario type '{scenario_type}'")
    names = {}
    if ids:
        names = {r.scenario_id: r.scenario_name for r in frappe.get_all(
            "Scenario", filters={"scenario_id": ["in", ids]},
            fields=["scenario_id", "scenario_name"], order_by="scenario_id asc",
            limit_page_length=0)}
    return {"options": [{"id": i, "label": names.get(i) or i} for i in ids],
            "default": default, "why": why}


def is_allowed_scenario(year, scenario_type, scenario_id):
    """True when ``scenario_id`` is in ``scenarios(year, scenario_type)``.
    A separate function, not an ``is_allowed`` kind: ``is_allowed`` takes one
    code, and a scenario is only valid for a (year, type) pair."""
    try:
        options = scenarios(year, scenario_type)["options"]
    except (TypeError, ValueError):
        if scenario_type not in ("budget", "forecast"):
            raise
        return False
    return any(o["id"] == scenario_id for o in options)


def is_allowed(kind, code):
    """True when ``code`` is in the list for ``kind`` and the current user may
    read it. Kinds: entity, group, fiscal_year, hierarchy, account,
    account_heading, dimension_value (code ``<dimension>:<value>``). An unknown
    kind is a caller bug, so it raises rather than answering False."""
    if kind == "entity":
        return any(i["code"] == code for i in entities())
    if kind == "group":
        return any(i["code"] == code for i in groups())
    if kind == "fiscal_year":
        try:
            return int(code) in fiscal_years()
        except (TypeError, ValueError):
            return False
    if kind == "hierarchy":
        return any(i["name"] == code for i in hierarchies())
    if kind == "account":
        return any(i["code"] == code for i in accounts())
    if kind == "account_heading":
        return any(i["code"] == code for i in account_headings())
    if kind == "dimension_value":
        dimension, sep, value = str(code).partition(":")
        return bool(sep) and any(i["code"] == value for i in dimension_values(dimension))
    raise ValueError(f"Unknown report choice kind '{kind}'")
