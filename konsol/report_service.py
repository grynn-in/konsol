"""Report templates, frappe side (konsol#548 v1, T9).

The endpoints in api.py parse the request body and call this module. This
module:

1. loads the template files (``konsol/report_templates/*.json``, validated by
   report_template_model) once per process;
2. validates every submitted param through report_choices, the same lists the
   add-in's pickers show. A value that is not a choice is refused by name;
   nothing falls back to another value. A value of the wrong type (a dict for
   an entity code) is refused by name before any check. An entity or a group
   is checked for access first, with the rule K.EPM applies to its entity
   argument (``_assert_entity_access``: entity_read_scope on the code);
3. resolves the site context the pure interpreter needs: the year's Regular
   periods (report_choices.periods); per ``accounts`` block the chart's
   Published leaves, per ``account_tree`` block the Published tree (both with
   the charts they came from), per ``account_list`` block the chosen accounts,
   per ``hierarchy_nodes`` block the hierarchy's nodes; and the scope's name;
4. calls the interpreter (compile_formulas / compile_snapshot).

Request bodies, both accepted by build_cell_map and build_snapshot:

- legacy: ``{template_id, entity, year, scenario_id}`` (snapshot:
  ``{template_id, entity, year, period_from, period_to}``). The keys the
  template declares become params.
- new: ``{template_id, params: {...}}``, the params passed as they are.
"""
import frappe

from konsol import report_choices
from konsol.report_compiler import _TEMPLATE_DIR, TB_LONG_MAX_ROWS
from konsol.report_template_model import (
    SCENARIO_TYPES, block_key, compile_formulas, compile_snapshot, description, load_templates,
    render, scope_kinds,
)

#: The body keys of today's add-in requests, read as params when the body
#: has no ``params`` object.
LEGACY_BUILD_KEYS = ("entity", "year", "scenario_id")
LEGACY_SNAPSHOT_KEYS = ("entity", "year", "period_from", "period_to")
_LEGACY_REQUIRED = ("template_id", "entity", "year")

_templates = None


def templates():
    """Every template file, validated, in ``order`` (cached per process)."""
    global _templates
    if _templates is None:
        _templates = load_templates(_TEMPLATE_DIR)
    return _templates


def template(template_id):
    """One template, or None."""
    return templates().get(template_id)


def _refuse(msg):
    frappe.throw(msg, frappe.ValidationError)


def _text(value):
    return value.strip() if isinstance(value, str) else value


# ---------------------------------------------------------------------------
# Request body
# ---------------------------------------------------------------------------

def request(body, legacy_keys):
    """``(template_id, params)`` from a legacy or a new request body."""
    if not isinstance(body, dict):
        _refuse("The request body must be a JSON object")
    template_id = _text(body.get("template_id") or "")
    if "params" in body:
        params = body["params"]
        if not isinstance(params, dict):
            _refuse("params must be an object of param name to value")
        if not template_id:
            _refuse("template_id is required")
        return template_id, dict(params)
    if not template_id or not _text(body.get("entity") or "") or body.get("year") is None:
        _refuse("template_id, entity, and year are required")
    t = template(template_id)
    declared = {p["name"] for p in t["params"]} if t else set(legacy_keys)
    params = {}
    for key in legacy_keys:
        if key in declared and key in body:
            value = _text(body[key])
            if value is not None and value != "":
                params[key] = value
    return template_id, params


# ---------------------------------------------------------------------------
# Params
# ---------------------------------------------------------------------------

def _assert_entity_access(entity):
    """api._assert_entity_access: PermissionError when the user may not read
    ``entity``. These reads go straight to ClickHouse, where Frappe's
    permission layer never runs."""
    from konsol.entity_permissions import assert_entity_access

    assert_entity_access(entity)


def _check_entity(tid, name, code):
    _assert_entity_access(code)
    if not report_choices.is_allowed("entity", code):
        _refuse(f"Report template {tid!r}: {name} {code!r} is not an entity you can report on.")


def _check_group(tid, name, code):
    """A group is read by K.EPM with the group code as its entity, and
    epm_batch checks entity_read_scope(<group code>, allowed): the same rule
    on the same argument, before any read (PR #626 review finding 1)."""
    _assert_entity_access(code)
    if not report_choices.is_allowed("group", code):
        _not_a_choice(tid, name, code, "a consolidation group you can report on")


def _node_reader():
    """report_choices.hierarchy_nodes, read once per (hierarchy, year): one
    build checks the scope, fills a hierarchy_nodes block and names the
    scope from one read of the tree (PR #626 review finding 8d)."""
    memo = {}

    def read(hierarchy, year):
        key = (hierarchy, int(year))
        if key not in memo:
            memo[key] = report_choices.hierarchy_nodes(hierarchy, year)
        return memo[key]

    return read


def _not_a_choice(tid, name, value, what):
    _refuse(f"Report template {tid!r}: {name} {value!r} is not {what}.")


def _as_int(tid, name, value):
    try:
        return int(value)
    except (TypeError, ValueError):
        _refuse(f"Report template {tid!r}: {name} {value!r} is not a whole number.")


def _year_for(tid, spec, by_name, values):
    """The fiscal year a dependent param is read in: its depends_on year, else
    the template's one fiscal_year param."""
    names = [d for d in spec.get("depends_on") or [] if by_name[d]["type"] == "fiscal_year"]
    if not names:
        names = [n for n, p in by_name.items() if p["type"] == "fiscal_year"]
    if len(names) != 1:
        _refuse(f"Report template {tid!r}: {spec['name']} needs one fiscal_year param to read in.")
    year = values.get(names[0])
    if year is None:
        _refuse(f"Report template {tid!r}: {spec['name']} depends on {names[0]}, which is not set.")
    return year


def _parent(tid, spec, by_name, values, ptype):
    """The value of the depends_on param of type ``ptype`` (refused when unset)."""
    names = [d for d in spec.get("depends_on") or [] if by_name[d]["type"] == ptype]
    if not names:
        names = [n for n, p in by_name.items() if p["type"] == ptype]
    if len(names) != 1:
        _refuse(f"Report template {tid!r}: {spec['name']} needs one {ptype} param.")
    value = values.get(names[0])
    if value is None or value == "":
        _refuse(f"Report template {tid!r}: {spec['name']} depends on {names[0]}, which is not set.")
    return value


#: Types validated in a second pass: they read another param's value.
_DEPENDENT = ("period", "scenario", "hierarchy_node", "dimension_value", "scope")

#: Param types whose value is a code or text: a str (a dict or list is refused).
_TEXT_TYPES = ("entity", "group", "hierarchy", "hierarchy_node", "dimension_value",
               "account", "account_heading", "scenario", "choice", "text")
#: Param types whose value is a whole number: an int, or its text.
_NUMBER_TYPES = ("fiscal_year", "int", "period")


def _check_type(tid, name, ptype, value):
    """Refuse, by name, a value whose JSON type the param cannot hold, before
    it reaches a check that would raise on it (PR #626 review finding 8a)."""
    if ptype in _TEXT_TYPES:
        ok = isinstance(value, str)
        what = "text"
    elif ptype in _NUMBER_TYPES:
        ok = isinstance(value, (str, int)) and not isinstance(value, bool)
        what = "a whole number"
    elif ptype == "account_list":
        ok = isinstance(value, list) and all(isinstance(c, str) for c in value)
        what = "a list of account codes"
    elif ptype == "scope":
        ok = (isinstance(value, dict) and isinstance(value.get("kind"), str)
              and isinstance(value.get("code"), str))
        what = 'a scope ({"kind": ..., "code": ...})'
    else:
        ok, what = True, ""
    if not ok:
        _refuse(f"Report template {tid!r}: {name} {value!r} is not {what}.")


def validate_params(tmpl, params, nodes=None):
    """The template's params, each checked against report_choices; refused by name.

    Returns ``{name: value}`` for every declared param in declared order:
    defaults applied, a fiscal_year or int as an int, a ``"rule"`` scenario
    resolved by the site's scenario rule. An entity or a group is
    access-checked first. ``nodes`` reads a hierarchy's nodes (default: one
    read per call, :func:`_node_reader`).
    """
    nodes = nodes or _node_reader()
    tid = tmpl["id"]
    declared = [p["name"] for p in tmpl["params"]]
    unknown = sorted(set(params or {}) - set(declared))
    if unknown:
        _refuse(f"Report template {tid!r} has no param "
                f"{', '.join(repr(u) for u in unknown)} (declared: {', '.join(declared) or 'none'}).")
    by_name = {p["name"]: p for p in tmpl["params"]}
    raw = {}
    for p in tmpl["params"]:
        value = _text((params or {}).get(p["name"]))
        if (value is None or value == "") and p.get("default") != "rule":
            value = p.get("default")
        if value is None or value == "":
            value = None
            if p.get("required"):
                _refuse(f"Report template {tid!r}: {p['name']} is required.")
        raw[p["name"]] = value

    values = {}
    for dependent in (False, True):
        for p in tmpl["params"]:
            if (p["type"] in _DEPENDENT) != dependent:
                continue
            values[p["name"]] = _check_param(tid, p, raw[p["name"]], by_name, values, nodes)
    return {name: values[name] for name in declared}


def _check_param(tid, p, value, by_name, values, nodes):
    name, ptype = p["name"], p["type"]
    if ptype == "scenario" and value is None and p.get("default") == "rule":
        year = _year_for(tid, p, by_name, values)
        rule = report_choices.scenarios(year, p["scenario_type"])
        if rule.get("default") is None:
            _refuse(f"Report template {tid!r}: {name} has no default {p['scenario_type']} "
                    f"scenario for FY{year}: {rule.get('why')}")
        return rule["default"]
    if value is None:
        return None
    _check_type(tid, name, ptype, value)
    if ptype == "entity":
        _check_entity(tid, name, value)
    elif ptype == "group":
        _check_group(tid, name, value)
    elif ptype == "fiscal_year":
        year = _as_int(tid, name, value)
        if year not in report_choices.fiscal_years():
            _refuse(f"Report template {tid!r}: {name} {value!r} is not a report year: FY{year} "
                    f"has no Regular period declared. Declare the year's Regular periods on "
                    f"EPM Fiscal Year {year}.")
        return year
    elif ptype == "period":
        year = _year_for(tid, p, by_name, values)
        if not any(str(value) in (str(q["number"]), q["code"])
                   for q in report_choices.periods(year)):
            _not_a_choice(tid, name, value, f"a Regular period of FY{year}")
    elif ptype == "scenario":
        year = _year_for(tid, p, by_name, values)
        if not report_choices.is_allowed_scenario(year, p["scenario_type"], value):
            _not_a_choice(tid, name, value, f"a {p['scenario_type']} scenario of FY{year}")
    elif ptype == "hierarchy":
        if not report_choices.is_allowed("hierarchy", value):
            _not_a_choice(tid, name, value, "a Published Reporting Hierarchy")
    elif ptype == "hierarchy_node":
        hierarchy = _parent(tid, p, by_name, values, "hierarchy")
        year = _year_for(tid, p, by_name, values)
        if not any(n["code"] == value for n in nodes(hierarchy, year)):
            _not_a_choice(tid, name, value, f"a node of hierarchy {hierarchy!r}")
    elif ptype == "dimension_value":
        deps = p.get("depends_on") or []
        if deps:
            dimension = values.get(deps[0])
            if dimension is None or dimension == "":
                _refuse(f"Report template {tid!r}: {name} depends on {deps[0]}, which is not set.")
            code = f"{dimension}:{value}"
        else:
            code = str(value)
        if not report_choices.is_allowed("dimension_value", code):
            _not_a_choice(tid, name, value, "a declared dimension value")
    elif ptype == "scope":
        return _check_scope(tid, p, value, by_name, values, nodes)
    elif ptype in ("account", "account_heading"):
        if not report_choices.is_allowed(ptype, value):
            _not_a_choice(tid, name, value, "a Published "
                          + ("leaf account" if ptype == "account" else "heading account"))
    elif ptype == "account_list":
        if not isinstance(value, list) or not value:
            _refuse(f"Report template {tid!r}: {name} {value!r} is not a list of account codes.")
        codes = {a["code"] for a in report_choices.accounts()}
        missing = [c for c in value if c not in codes]
        if missing:
            _not_a_choice(tid, name, value, "a list of Published leaf accounts (not: "
                          + ", ".join(str(m) for m in missing) + ")")
        return list(value)
    elif ptype == "choice":
        if value not in p["choices"]:
            _not_a_choice(tid, name, value, "one of " + ", ".join(p["choices"]))
    elif ptype == "int":
        return _as_int(tid, name, value)
    elif ptype == "text":
        if not isinstance(value, str):
            _refuse(f"Report template {tid!r}: {name} {value!r} is not text.")
    else:
        _refuse(f"Report template {tid!r}: {name} has unknown type {ptype!r}.")
    return value


def _check_scope(tid, p, value, by_name, values, nodes):
    """A scope is ``{kind, code}`` with ``kind`` one of the param's kinds."""
    name = p["name"]
    if not isinstance(value, dict) or set(value) != {"kind", "code"}:
        _refuse(f"Report template {tid!r}: {name} {value!r} is not a scope "
                "({\"kind\": ..., \"code\": ...}).")
    kind, code = value["kind"], _text(value["code"])
    if kind not in p["kinds"]:
        _refuse(f"Report template {tid!r}: {name} kind {kind!r} is not one of "
                f"{', '.join(p['kinds'])}.")
    if kind == "entity":
        _check_entity(tid, name, code)
    elif kind == "group":
        _check_group(tid, name, code)
    else:
        hierarchy = _parent(tid, p, by_name, values, "hierarchy")
        year = _year_for(tid, p, by_name, values)
        if not any(n["code"] == code for n in nodes(hierarchy, year)):
            _not_a_choice(tid, name, code, f"a node of hierarchy {hierarchy!r}")
        return {"kind": kind, "code": code, "hierarchy": hierarchy}
    return {"kind": kind, "code": code}


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

def block_accounts(block):
    """An ``accounts`` block's Published leaves of the group chart as
    ``([(code, caption), ...], [chart names])``, in code order; the caption
    falls back to the code. The filters are api._pnl_accounts' (status
    Published, leaves, the block's statement section) plus the block's
    account_type and sub_section when it declares them."""
    want = block.get("filter") or {}
    filters = {"status": "Published", "is_group": 0}
    if want.get("statement_section"):
        filters["statement_section"] = want["statement_section"]
    if want.get("account_type"):
        filters["account_type"] = ["in", list(want["account_type"])]
    if want.get("sub_section"):
        filters["sub_section"] = want["sub_section"]
    rows = frappe.get_all(
        "Main Account",
        filters=filters,
        fields=["main_account", "account_name", "chart_of_accounts"],
        order_by="main_account asc",
    )
    charts = sorted({r.chart_of_accounts for r in rows if r.chart_of_accounts})
    return [(r.main_account, r.account_name or r.main_account) for r in rows], charts


def _published_chart():
    """Every Published Main Account (headings and leaves), in code order."""
    return frappe.get_all(
        "Main Account",
        filters={"status": "Published"},
        fields=["main_account", "account_name", "chart_of_accounts", "parent_account",
                "is_group", "statement_section"],
        order_by="main_account asc",
        limit_page_length=0,
    )


def block_tree(tmpl, block, params):
    """An ``account_tree`` block's rows as ``([{code, caption, depth, is_group,
    parent}], [chart names])`` in DFS order, children in code order.

    The tree is the Published Main Accounts nested by ``parent_account``:

    - ``root`` naming an account_heading param: that heading (depth 0) and
      every account under it;
    - ``root`` naming a statement section: its accounts, with the headings
      that carry no section of their own and hold one of them (the chart
      allows a heading without a section); a row whose parent is not in the
      set is a root;
    - no ``root``: the whole chart.

    ``depth`` keeps depths ``0 .. depth-1``. A heading left with no child row
    (its children cut, or none Published) is dropped with its subtree, so a
    heading is never written without the rows it sums. The caption falls
    back to the code.
    """
    tid = tmpl["id"]
    rows = _published_chart()
    by_code = {r.main_account: r for r in rows}
    root = block.get("root")
    heading_params = {p["name"] for p in tmpl["params"] if p["type"] == "account_heading"}
    if root in heading_params:
        code = params.get(root)
        if code is None or code == "":
            _refuse(f"Report template {tid!r}: an account tree starts at the heading in param "
                    f"{root!r}, and none is given.")
        if code not in by_code or not by_code[code].is_group:
            _refuse(f"Report template {tid!r}: {root} {code!r} is not a Published heading account.")
        members = {code}
        frontier = [code]
        while frontier:
            parent = frontier.pop()
            for r in rows:
                if r.parent_account == parent and r.main_account not in members:
                    members.add(r.main_account)
                    frontier.append(r.main_account)
        starts = [code]
    else:
        if root:
            members = {r.main_account for r in rows if r.statement_section == root}
            for code in list(members):
                seen = set()
                parent = by_code[code].parent_account
                while parent in by_code and parent not in seen:
                    seen.add(parent)
                    up = by_code[parent]
                    if up.statement_section and up.statement_section != root:
                        break
                    members.add(parent)
                    parent = up.parent_account
        else:
            members = set(by_code)
        starts = [r.main_account for r in rows
                  if r.main_account in members and r.parent_account not in members]
    children = {}
    for r in rows:
        if r.main_account in members and r.parent_account in members and r.main_account not in starts:
            children.setdefault(r.parent_account, []).append(r.main_account)

    limit = block.get("depth")
    visited = set()

    def walk(code, depth, parent):
        """The kept rows of ``code``'s subtree, or [] when it is dropped."""
        if code in visited or (limit is not None and depth >= int(limit)):
            return []
        visited.add(code)
        r = by_code[code]
        below = []
        for child in children.get(code, ()):
            below.extend(walk(child, depth + 1, code))
        if r.is_group and not below:
            return []
        return [{"code": code, "caption": r.account_name or code, "depth": depth,
                 "is_group": bool(r.is_group), "parent": parent}] + below

    lines = []
    for code in starts:
        lines.extend(walk(code, 0, None))
    charts = sorted({by_code[n["code"]].chart_of_accounts for n in lines
                     if by_code[n["code"]].chart_of_accounts})
    return lines, charts


def block_account_list(tmpl, block, params):
    """An ``account_list`` block's ``[(code, caption)]`` in the param's order,
    captions from report_choices.accounts (the Published leaves)."""
    codes = params.get(block["param"]) or []
    names = {a["code"]: a.get("name") for a in report_choices.accounts()}
    return [(code, names.get(code) or code) for code in codes]


def _year_value(tmpl, params):
    years = [p["name"] for p in tmpl["params"] if p["type"] == "fiscal_year"]
    if len(years) == 1:
        return params.get(years[0])
    return None


def _hierarchy_value(tmpl, params):
    names = [p["name"] for p in tmpl["params"] if p["type"] == "hierarchy"]
    return params.get(names[0]) if len(names) == 1 else None


def block_nodes(tmpl, block, params, year, nodes=None):
    """A ``hierarchy_nodes`` block's nodes in tree order (report_choices.
    hierarchy_nodes for the year); under its ``node`` param, that node and
    its descendants only."""
    tid = tmpl["id"]
    hierarchy = params.get(block["hierarchy"])
    if hierarchy is None or hierarchy == "" or year is None:
        _refuse(f"Report template {tid!r}: a hierarchy_nodes block needs the hierarchy in "
                f"param {block['hierarchy']!r} and the fiscal year.")
    listed = (nodes or _node_reader())(hierarchy, year)
    start = params.get(block["node"]) if block.get("node") else None
    if not start:
        return listed
    out, inside = [], None
    for n in listed:
        if inside is None:
            if n["code"] == start:
                inside = int(n["depth"])
                out.append(n)
        elif int(n["depth"]) > inside:
            out.append(n)
        else:
            break
    return out


def scope_name(tmpl, params, nodes=None):
    """The display name of the template's scope: the entity's name, the
    group's root name, or the node's label; None when the site declares none."""
    found = [p for p in tmpl["params"] if p["type"] in ("entity", "group", "scope")]
    if len(found) != 1:
        return None
    p = found[0]
    value = params.get(p["name"])
    if value is None:
        return None
    if p["type"] in ("entity", "group"):
        kind, code, hierarchy = p["type"], value, None
    elif isinstance(value, dict):
        kind, code = value.get("kind"), value.get("code")
        hierarchy = value.get("hierarchy") or _hierarchy_value(tmpl, params)
    else:
        return None
    if kind == "entity":
        items, key = report_choices.entities(), "name"
    elif kind == "group":
        items, key = report_choices.groups(), "name"
    else:
        year = _year_value(tmpl, params)
        if not hierarchy or year is None:
            return None
        items, key = (nodes or _node_reader())(hierarchy, year), "label"
    return next((i.get(key) for i in items if i["code"] == code), None)


def resolve_context(tmpl, params, nodes=None):
    """ctx for compile_formulas (its contract is in report_template_model):
    the year's Regular periods; per block, its accounts (and charts), tree,
    account list or hierarchy nodes; and the scope's display name. ``nodes``
    as for :func:`validate_params`."""
    nodes = nodes or _node_reader()
    year = _year_value(tmpl, params)
    ctx = {"accounts": {}, "charts": {}, "trees": {}, "account_lists": {}, "nodes": {}}
    if year is not None:
        ctx["periods"] = report_choices.periods(year)
    for i, block in enumerate(tmpl["rows"]["blocks"]):
        key = block_key(block, i)
        btype = block["type"]
        if btype == "accounts":
            ctx["accounts"][key], ctx["charts"][key] = block_accounts(block)
        elif btype == "account_tree":
            ctx["trees"][key], ctx["charts"][key] = block_tree(tmpl, block, params)
        elif btype == "account_list":
            ctx["account_lists"][key] = block_account_list(tmpl, block, params)
        elif btype == "hierarchy_nodes":
            ctx["nodes"][key] = block_nodes(tmpl, block, params, year, nodes)
    name = scope_name(tmpl, params, nodes)
    if name is not None:
        ctx["scope_name"] = name
    return ctx


def _pnl_accounts():
    """The monthly P&L's accounts, as api._pnl_accounts returned them: the
    group chart's Published Profit and Loss leaves as (code, caption), in
    code order. Leaves spread over several charts are refused with the
    template's several_charts text."""
    tmpl = template("pnl_monthly")
    block = next(b for b in tmpl["rows"]["blocks"] if b["type"] == "accounts")
    lines, charts = block_accounts(block)
    if len(charts) > 1:
        _refuse(render(tmpl["errors"]["several_charts"],
                       {"title": tmpl["title"], "charts": ", ".join(charts)}))
    return lines


# ---------------------------------------------------------------------------
# Build and snapshot
# ---------------------------------------------------------------------------

def build(template_id, params):
    """A formulas template + params -> the cell map."""
    tmpl = template(template_id)
    if tmpl is None or tmpl["mode"] != "formulas":
        _refuse(f"Unknown template_id for formulas: {template_id!r}")
    nodes = _node_reader()
    values = validate_params(tmpl, params, nodes)
    why = _kind_why(tmpl, _build_kind(tmpl, values), _published_keys())
    if why is not None:
        _refuse(why)
    ctx = resolve_context(tmpl, values, nodes)
    try:
        return compile_formulas(tmpl, values, ctx)
    except ValueError as exc:
        _refuse(str(exc))


def _build_kind(tmpl, values):
    """The scope kind a formulas build reads: the scope param's, else the
    entity or group param's type."""
    for p in tmpl["params"]:
        if p["type"] == "scope" and isinstance(values.get(p["name"]), dict):
            return values[p["name"]]["kind"]
    for p in tmpl["params"]:
        if p["type"] in ("entity", "group"):
            return p["type"]
    return "entity"


def snapshot(template_id, params):
    """A snapshot template + params -> the values-only cell map."""
    tmpl = template(template_id)
    if tmpl is None or tmpl["mode"] != "snapshot":
        _refuse(f"Snapshot not supported for template {template_id!r}")
    _check_snapshot_periods(params)
    values = validate_params(tmpl, params)
    rows = _read_snapshot_source(tmpl, values)
    try:
        return compile_snapshot(tmpl, values, rows)
    except ValueError as exc:
        _refuse(str(exc))


def _check_snapshot_periods(params):
    """api.build_snapshot's integer check, with its text, before the generic one."""
    try:
        for key in ("period_from", "period_to"):
            if (params or {}).get(key) not in (None, ""):
                int(params[key])
    except (TypeError, ValueError):
        _refuse("period_from and period_to must be integers")


def _read_snapshot_source(tmpl, values):
    readers = {"entity_trial_balance": _read_entity_trial_balance}
    reader = readers.get(tmpl["source"])
    if reader is None:
        _refuse(f"Report template {tmpl['id']!r}: no reader for source {tmpl['source']!r}")
    return reader(values)


def _read_entity_trial_balance(values):
    """The entity's own TB rows. Periods stay 1..12 as api.build_snapshot
    validated them (follow-up konsol#210: the declared calendar)."""
    period_from, period_to = values["period_from"], values["period_to"]
    if period_from < 1 or period_to > 12 or period_from > period_to:
        _refuse("period_from and period_to must be between 1 and 12")
    return _fetch_trial_balance_rows(values["entity"], int(values["year"]), period_from, period_to)


def _fetch_trial_balance_rows(entity, year, period_from, period_to):
    """Load trial balance rows from gold_trial_balance for snapshot export."""
    from konsol.api import _clickhouse_query, _get_ch_connection

    ch_settings = _get_ch_connection()
    sql = (
        "SELECT data_area_id, fiscal_year, fiscal_period, main_account, "
        "any(account_name) AS account_name, any(account_type_name) AS account_type_name, "
        "any(multiIf(is_pnl = 1, 'P&L', is_balance_sheet = 1, 'BS', '')) AS bs_pnl, "
        "sum(period_debit) AS debit, sum(period_credit) AS credit, "
        "sum(period_debit) - sum(period_credit) AS net "
        "FROM epm_gold.gold_trial_balance "
        "WHERE data_area_id = {entity:String} "
        "AND fiscal_year = {year:Int32} "
        "AND fiscal_period >= {pfrom:Int32} AND fiscal_period <= {pto:Int32} "
        "GROUP BY data_area_id, fiscal_year, fiscal_period, main_account "
        "ORDER BY main_account, fiscal_period "
        f"LIMIT {int(TB_LONG_MAX_ROWS)}"
    )
    params = {
        "param_entity": entity,
        "param_year": str(int(year)),
        "param_pfrom": str(int(period_from)),
        "param_pto": str(int(period_to)),
    }
    raw = _clickhouse_query(sql, params, ch_settings)
    rows = []
    for line in raw.split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 10:
            continue
        rows.append({
            "entity": parts[0],
            "year": int(parts[1]),
            "period": int(parts[2]),
            "account": parts[3],
            "account_name": parts[4],
            "account_type": parts[5],
            "bs_pnl": parts[6],
            "debit": float(parts[7]),
            "credit": float(parts[8]),
            "net": float(parts[9]),
        })
    return rows


# ---------------------------------------------------------------------------
# Catalog and choices
# ---------------------------------------------------------------------------

def list_templates():
    """The four-key list api.list_report_templates has always returned."""
    return [{"id": t["id"], "title": t["title"], "description": description(t),
             "mode": t["mode"]} for t in templates().values()]


_PARAM_EXTRA = ("choices", "scenario_type", "kinds", "echo_only")


def _param_spec(p):
    out = {
        "name": p["name"],
        "type": p["type"],
        "label": p.get("label") or p["name"].replace("_", " ").capitalize(),
        "required": bool(p.get("required")),
        "default": p.get("default"),
        "depends_on": list(p.get("depends_on") or []),
    }
    for key in _PARAM_EXTRA:
        if key in p:
            out[key] = p[key]
    return out


_KIND_TEXT = {"entity": "an entity", "group": "a group", "node": "a hierarchy node"}


def _kind_why(t, kind, published):
    """None when every read series has a Published Dataset for ``kind``'s
    word; else the refusal, naming each missing word and its series."""
    missing = {}
    for name, series in (t.get("series") or {}).items():
        read = series.get("read")
        if read is None:
            continue  # a formula series reads nothing
        word = read.get(kind)
        if word not in published:
            missing.setdefault(word, []).append(name)
    if not missing:
        return None
    return "; ".join(
        f"No Published Dataset has scenario key {w!r} (read by series "
        f"{', '.join(repr(x) for x in s)}), so {t['title']} cannot be read for "
        f"{_KIND_TEXT.get(kind, kind)} on this site."
        for w, s in sorted(missing.items(), key=lambda i: str(i[0])))


def _published_keys():
    """The scenario keys of the site's Published Datasets."""
    return {r.scenario_key for r in frappe.get_all(
        "Dataset", filters={"status": "Published"}, fields=["scenario_key"],
        order_by="scenario_key asc", limit_page_length=0) if r.scenario_key}


def catalog():
    """Every template's spec, in ``order``, with its availability per scope kind.

    A product rule (PR #626 review finding 4): a scope kind (entity, group,
    node) is offered when every series the template reads has a Published
    Dataset (``scenario_key``) for that kind's word. ``available_kinds`` lists
    the offered kinds in declared order; ``unavailable_kinds`` maps each
    other kind to its ``why``. The template is ``available`` when at least
    one kind is; else ``why`` joins every kind's why. A snapshot template
    reads its registered source, so every kind it declares is available.
    """
    published = _published_keys()
    out = []
    for t in templates().values():
        kinds = scope_kinds(t)
        whys = {} if t["mode"] == "snapshot" else {k: _kind_why(t, k, published) for k in kinds}
        unavailable = {k: w for k, w in whys.items() if w is not None}
        available = [k for k in kinds if k not in unavailable]
        why = None if available else "; ".join(unavailable[k] for k in kinds)
        out.append({
            "id": t["id"],
            "version": t["version"],
            "title": t["title"],
            "description": description(t),
            "mode": t["mode"],
            "sheet_name": t["sheet_name"],
            "params": [_param_spec(p) for p in t["params"]],
            "available": bool(available),
            "why": why,
            "available_kinds": available,
            "unavailable_kinds": unavailable,
        })
    return {"templates": out}


#: kind -> the report_choices function and the request args it takes, in order.
CHOICE_KINDS = {
    "entities": ("entities", ()),
    "groups": ("groups", ()),
    "fiscal_years": ("fiscal_years", ()),
    "periods": ("periods", ("year",)),
    "hierarchies": ("hierarchies", ()),
    "hierarchy_nodes": ("hierarchy_nodes", ("hierarchy", "year")),
    "dimension_values": ("dimension_values", ("dimension",)),
    "accounts": ("accounts", ("section?",)),
    "account_headings": ("account_headings", ("section?",)),
    "scenarios": ("scenarios", ("year", "scenario_type")),
}


def choices(kind, **args):
    """``{kind, choices}``: report_choices.<kind> for the given args. An
    unknown kind, or a required arg missing, is refused by name."""
    if kind not in CHOICE_KINDS:
        _refuse(f"Unknown report choice kind {kind!r} (known: {', '.join(CHOICE_KINDS)}).")
    fn, wanted = CHOICE_KINDS[kind]
    call = []
    for arg in wanted:
        optional = arg.endswith("?")
        arg = arg.rstrip("?")
        value = _text(args.get(arg))
        if value is None or value == "":
            if optional:
                call.append(None)
                continue
            _refuse(f"Report choices {kind!r} need {arg}.")
        if arg == "scenario_type" and value not in SCENARIO_TYPES:
            _refuse(f"Report choices {kind!r}: scenario_type {value!r} is not one of "
                    f"{', '.join(SCENARIO_TYPES)}.")
        if arg == "year":
            try:
                value = int(value)
            except (TypeError, ValueError):
                _refuse(f"Report choices {kind!r}: year {value!r} is not a whole number.")
        call.append(value)
    return {"kind": kind, "choices": getattr(report_choices, fn)(*call)}
