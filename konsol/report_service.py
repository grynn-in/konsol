"""Report templates, frappe side (konsol#548 v1, T9).

The endpoints in api.py parse the request body and call this module. This
module:

1. loads the template files (``konsol/report_templates/*.json``, validated by
   report_template_model) once per process;
2. validates every submitted param through report_choices, the same lists the
   add-in's pickers show. A value that is not a choice is refused by name;
   nothing falls back to another value. An entity is checked for access first,
   as the endpoints always did (``_assert_entity_access``);
3. resolves the site context the pure interpreter needs: the year's Regular
   periods (report_choices.periods) and, per ``accounts`` block, the chart's
   Published accounts and the charts they came from;
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
    block_key, compile_formulas, compile_snapshot, load_templates, render,
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


def validate_params(tmpl, params):
    """The template's params, each checked against report_choices; refused by name.

    Returns ``{name: value}`` for every declared param in declared order:
    defaults applied, a fiscal_year or int as an int, a ``"rule"`` scenario
    resolved by the site's scenario rule. An entity is access-checked first.
    """
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
            values[p["name"]] = _check_param(tid, p, raw[p["name"]], by_name, values)
    return {name: values[name] for name in declared}


def _check_param(tid, p, value, by_name, values):
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
    if ptype == "entity":
        _check_entity(tid, name, value)
    elif ptype == "group":
        if not report_choices.is_allowed("group", value):
            _not_a_choice(tid, name, value, "a consolidation group you can report on")
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
        if not any(n["code"] == value for n in report_choices.hierarchy_nodes(hierarchy, year)):
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
        return _check_scope(tid, p, value, by_name, values)
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


def _check_scope(tid, p, value, by_name, values):
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
        if not report_choices.is_allowed("group", code):
            _not_a_choice(tid, name, code, "a consolidation group you can report on")
    else:
        hierarchy = _parent(tid, p, by_name, values, "hierarchy")
        year = _year_for(tid, p, by_name, values)
        if not any(n["code"] == code for n in report_choices.hierarchy_nodes(hierarchy, year)):
            _not_a_choice(tid, name, code, f"a node of hierarchy {hierarchy!r}")
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


def resolve_context(tmpl, params):
    """ctx for compile_formulas (its contract is in report_template_model):
    the year's Regular periods and each accounts block's lines and charts."""
    years = [p["name"] for p in tmpl["params"] if p["type"] == "fiscal_year"]
    ctx = {"accounts": {}, "charts": {}}
    if len(years) == 1 and params.get(years[0]) is not None:
        ctx["periods"] = report_choices.periods(params[years[0]])
    for i, block in enumerate(tmpl["rows"]["blocks"]):
        if block["type"] == "accounts":
            key = block_key(block, i)
            ctx["accounts"][key], ctx["charts"][key] = block_accounts(block)
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
    values = validate_params(tmpl, params)
    ctx = resolve_context(tmpl, values)
    try:
        return compile_formulas(tmpl, values, ctx)
    except ValueError as exc:
        _refuse(str(exc))


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

def _description(t):
    return t.get("description", "").replace(
        "{sign.sentence}", t.get("sign", {}).get("sentence", ""))


def list_templates():
    """The four-key list api.list_report_templates has always returned."""
    return [{"id": t["id"], "title": t["title"], "description": _description(t),
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


def _dataset_words(t):
    """{word: [series names]} for every dataset word the template's series read."""
    words = {}
    for name, series in (t.get("series") or {}).items():
        for word in (series.get("read") or {}).values():
            words.setdefault(word, []).append(name)
    return words


def catalog():
    """Every template's spec, in ``order``, with ``available`` / ``why``.

    A template is unavailable when a dataset word its series read has no
    Published Dataset (``scenario_key``) on this site; ``why`` names the word
    and the series. A snapshot template reads its registered source, so it
    is always available.
    """
    published = {r.scenario_key for r in frappe.get_all(
        "Dataset", filters={"status": "Published"}, fields=["scenario_key"],
        order_by="scenario_key asc", limit_page_length=0) if r.scenario_key}
    out = []
    for t in templates().values():
        missing = {w: s for w, s in _dataset_words(t).items() if w not in published}
        why = None
        if missing:
            why = "; ".join(
                f"No Published Dataset has scenario key {w!r} (read by series "
                f"{', '.join(repr(x) for x in s)}), so {t['title']} cannot be read on this site."
                for w, s in sorted(missing.items()))
        out.append({
            "id": t["id"],
            "version": t["version"],
            "title": t["title"],
            "description": _description(t),
            "mode": t["mode"],
            "sheet_name": t["sheet_name"],
            "params": [_param_spec(p) for p in t["params"]],
            "available": not missing,
            "why": why,
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
        if arg == "year":
            try:
                value = int(value)
            except (TypeError, ValueError):
                _refuse(f"Report choices {kind!r}: year {value!r} is not a whole number.")
        call.append(value)
    return {"kind": kind, "choices": getattr(report_choices, fn)(*call)}
