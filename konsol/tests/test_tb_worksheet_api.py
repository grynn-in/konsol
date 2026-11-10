"""Consolidation worksheet endpoint: konsol/close/tb_worksheet_api.py
``get_worksheet`` (konsol#494 row 6, no dimension).

Loaded against a stub frappe (pattern: test_close_statement_api.py's
``_Site`` / ``_frappe``, copied, not imported). ``konsol.close.ch_read`` and
``konsol.close.signoff_gate`` are stubs (``statement_accounts`` calls the
REAL ``close_policy_model``); ``statement_api``, ``statement_model`` and
``tb_worksheet_model`` are the real modules, loaded by path.
"""
import ast
import importlib.util
import json
import os
import re
import sys
import types
from datetime import date

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")
API_PY = os.path.join(CLOSE_DIR, "tb_worksheet_api.py")
SITE_TZ = "Europe/London"


# --- copied from test_close_statement_api.py (N53): the static
# alias/column-collision check over every ``*_SQL`` constant -------------

def _sql_constants(path=API_PY):
    """``{name: sql}`` for every module-level ``..._SQL = (...)`` string
    constant, read with ``ast.literal_eval`` — no import required."""
    with open(path) as f:
        tree = ast.parse(f.read(), path)
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and "_SQL" in target.id:
            out[target.id] = ast.literal_eval(node.value)
    return out


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SQL_KEYWORDS = {"is", "null", "not", "and", "or", "distinct", "as"}
_AGG_COL_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\((.*)\)(?:\s+AS\s+([A-Za-z_][A-Za-z0-9_]*))?$",
    re.IGNORECASE | re.DOTALL)
_PLAIN_COL_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)(?:\s+AS\s+([A-Za-z_][A-Za-z0-9_]*))?$", re.IGNORECASE)


def _split_select_list(sql):
    upper = sql.upper()
    start = upper.index("SELECT") + len("SELECT")
    end = upper.index(" FROM ")
    parts, depth, current = [], 0, ""
    for ch in sql[start:end]:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current)
    return [p.strip() for p in parts]


def _column_entries(sql):
    entries = []
    for col in _split_select_list(sql):
        m = _AGG_COL_RE.match(col)
        if m:
            func, args, alias = m.group(1), m.group(2), m.group(3)
            idents = {t.lower() for t in _IDENT_RE.findall(args)}
            idents -= _SQL_KEYWORDS
            idents.discard(func.lower())
            entries.append((alias, True, idents))
            continue
        m = _PLAIN_COL_RE.match(col)
        if m:
            name, alias = m.group(1), m.group(2)
            entries.append((alias or name, False, set()))
        else:
            entries.append((None, False, set()))
    return entries


def _alias_collision(sql):
    entries = _column_entries(sql)
    for i, (alias, _is_agg, _idents) in enumerate(entries):
        if not alias:
            continue
        for j, (_alias2, is_agg2, idents2) in enumerate(entries):
            if j != i and is_agg2 and alias.lower() in idents2:
                return alias
    return None


# --- the stub site ------------------------------------------------------------

ANALYST = "zz-analyst@example.com"


def _load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CPM = _load_path("close_policy_model_for_tb_worksheet_api_test",
                 os.path.join(CLOSE_DIR, "close_policy_model.py"))
sys.modules.pop("close_policy_model_for_tb_worksheet_api_test", None)
STATEMENT_MODEL = _load_path("statement_model_for_tb_worksheet_api_test",
                             os.path.join(CLOSE_DIR, "statement_model.py"))
sys.modules.pop("statement_model_for_tb_worksheet_api_test", None)


def _account(code, name, parent, is_group, section, lft, normal_balance=""):
    return {"name": code, "status": "Published", "account_name": name, "parent_account": parent,
            "is_group": is_group, "statement_section": section, "lft": lft,
            "normal_balance": normal_balance}


def _accounts():
    bs, pl = "Balance Sheet", "Profit and Loss"
    return {a["name"]: a for a in (
        _account("1", "ASSETS", None, 1, bs, 1, "Debit"),
        _account("1110", "Cash", "1", 0, bs, 2),
        _account("2", "LIABILITIES", None, 1, bs, 5, "Credit"),
        _account("2100", "Accounts payable", "2", 0, bs, 6),
        _account("3", "EQUITY", None, 1, bs, 9, "Credit"),
        _account("3300", "AOCI - CTA", "3", 0, bs, 11),
        _account("3100", "Retained earnings", "3", 0, bs, 12),
        _account("4", "REVENUE", None, 1, pl, 13),
        _account("4100", "Sales revenue", "4", 0, pl, 14),
    )}


_DECLARED_ROWS = {
    "3300": {"is_group": 0, "status": "Published", "statement_section": "Balance Sheet",
             "account_name": "AOCI - CTA", "parent_account": "3"},
    "3100": {"is_group": 0, "status": "Published", "statement_section": "Balance Sheet",
             "account_name": "Retained earnings", "parent_account": "3"},
}


def _period_row(fy, fp, period_type, status):
    month = min(max(fp, 1), 12)
    return {"fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
            "period_label": "FY%d P%02d" % (fy, fp), "period_type": period_type,
            "start_date": date(fy, month, 1), "end_date": date(fy, month, 28),
            "quarter": "Q%d" % ((month - 1) // 3 + 1), "status": status}


def _calendar(years):
    rows = []
    for fy in years:
        rows.append(_period_row(fy, 0, "Opening", "Closed"))
        for fp in range(1, 13):
            rows.append(_period_row(fy, fp, "Regular", "Closed"))
        rows.append(_period_row(fy, 13, "Closing", "Closed"))
    return rows


def _ch_row(main_account, amount, adjustment_type, data_area_id, currency="USD",
            fy=2025, fp=7, null_rows=0):
    """The worksheet read's row as ClickHouse sends it: the sum under the
    wire alias ``amt`` (never ``amount``, N53), numerics possibly strings."""
    return {"fiscal_year": str(fy), "fiscal_period": str(fp), "data_area_id": data_area_id,
            "main_account": main_account, "adjustment_type": adjustment_type,
            "reporting_currency": currency, "amt": amount, "null_rows": str(null_rows)}


class _Site:
    def __init__(self):
        self.user = ANALYST
        self.roles = {"EPM Analyst"}
        self.periods = _calendar((2024, 2025))
        self.groups = [{"consolidation_group": "G1", "reporting_currency": "USD"}]
        self.accounts = _accounts()
        self.cta_account, self.result_account = "3300", "3100"
        self.declared_rows = dict(_DECLARED_ROWS)
        # Live shape: reporting_currency is '' on ic_elimination / topside /
        # acquisition rows (never filtered on in SQL).
        self.rows = [
            _ch_row("1110", 1000.0, "entity", "ZZA"),
            _ch_row("1110", 500.0, "entity", "ZZB"),
            _ch_row("2100", -600.0, "entity", "ZZA"),
            _ch_row("4100", -900.0, "entity", "ZZB"),
            _ch_row("CTA", -5.07, "cta", "ZZA"),
            _ch_row("2100", 100.0, "ic_elimination", "", currency=""),
            _ch_row("1110", -94.93, "topside", "ZZA", currency=""),
            # Last year's P&L: outside the YTD window, so never in 4100.
            _ch_row("4100", -77.0, "entity", "ZZB", fy=2024, fp=12),
        ]
        self.allowed = None
        self.ch_error = None
        self.ch_calls = []
        self.reads = []
        self.minor_units = {"USD": 2, "JPY": 0}
        self.entity_codes = {"ZZA", "ZZB"}
        self.entity_queries = []
        # Dimension (row 7): what the site declares, and what the warehouse built.
        self.declared_dims = [{"dimension_name": "dim_cost_center", "status": "Published",
                               "in_trial_balance": 1}]
        self.built_dims = ["dim_cost_center"]
        self.columns_calls = []
        self.columns_error = None


def _match_value(value, cond):
    if isinstance(cond, (list, tuple)):
        op, arg = cond[0], cond[1]
        if op == "in":
            return value in arg
        if op == "is":
            return (value not in (None, "")) == (arg == "set")
        raise AssertionError("stub: unsupported operator %r" % (op,))
    return value == cond


def _match(row, filters):
    return all(_match_value(row.get(k), c) for k, c in (filters or {}).items())


def _frappe(site):
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, **k):
        raise (exc or frappe.ValidationError)(msg)

    def only_for(roles, message=False):
        roles = [roles] if isinstance(roles, str) else list(roles)
        if not site.roles.intersection(roles):
            raise frappe.PermissionError("Not permitted")

    def get_all(doctype, filters=None, fields=None, limit_page_length=None, **k):
        site.reads.append(("get_all", doctype))
        if doctype == "Consolidation Group":
            rows = [r for r in site.groups if _match(r, filters)]
        elif doctype == "Entity":
            site.entity_queries.append((filters, k.get("pluck")))
            assert k.get("pluck") == "name"
            return [c for c in sorted(site.entity_codes) if c in filters["name"][1]]
        elif doctype == "Main Account":
            rows = [r for r in site.accounts.values() if _match(r, filters)]
        else:
            raise AssertionError("unexpected get_all on %s" % doctype)
        return [{f: r.get(f) for f in fields} for r in rows]

    def get_value(doctype, name, fieldname, **k):
        assert (doctype, fieldname) == ("ISO Currency", "minor_unit"), (doctype, fieldname)
        site.reads.append(("get_value", doctype, name))
        return site.minor_units.get(name)

    frappe.db = types.SimpleNamespace(get_value=get_value)
    frappe.throw = throw
    frappe.only_for = only_for
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.get_all = get_all
    frappe.get_roles = lambda user=None: sorted(site.roles)
    frappe.session = types.SimpleNamespace(user=site.user)
    frappe.response = {}
    frappe.utils = types.SimpleNamespace(get_system_timezone=lambda: SITE_TZ)
    return frappe


def _ch_read(site):
    ch = types.ModuleType("konsol.close.ch_read")
    error_name = re.compile(r"\(([A-Z][A-Z0-9_]+)\)")

    def rows(sql, params=None):
        if "system.columns" in sql:
            site.columns_calls.append((sql, dict(params or {})))
            if site.columns_error is not None:
                raise site.columns_error
            return [{"name": n} for n in site.built_dims]
        site.ch_calls.append((sql, dict(params or {})))
        if site.ch_error is not None:
            raise site.ch_error
        return [dict(r) for r in site.rows]

    ch.rows = rows
    ch.error_names = lambda e: set(error_name.findall(str(e)))
    ch.not_built = lambda e: bool(ch.error_names(e) & {"UNKNOWN_TABLE", "UNKNOWN_DATABASE"})
    return ch


def _signoff_gate(site):
    sg = types.ModuleType("konsol.close.signoff_gate")
    sg.statement_accounts = lambda: CPM.statement_accounts(
        site.cta_account, site.result_account, site.declared_rows)
    sg.in_scope_entities = lambda fy, fp: []
    return sg


_CLOSE_SIBLINGS = ("statement_model", "timefmt", "drill_model", "statement_export_model",
                   "commentary_model", "statement_api", "tb_worksheet_model")


def _invoke(site, run):
    frappe = _frappe(site)
    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    close = types.ModuleType("konsol.close")
    close.__path__ = []
    fiscal_calendar = types.ModuleType("konsol.fiscal_calendar")
    fiscal_calendar.fiscal_period_rows = lambda: [dict(r) for r in site.periods]
    entity_permissions = types.ModuleType("konsol.entity_permissions")
    entity_permissions.allowed_entity_codes = lambda user=None: site.allowed

    # statement_api imports it since #504 (Basis: Entity); same stub as
    # test_close_statement_api.py.
    def assert_entity_access(code, user=None):
        if site.allowed is not None and code not in site.allowed:
            raise frappe.PermissionError("Not permitted to access entity '%s'" % code)

    entity_permissions.assert_entity_access = assert_entity_access
    tb_dimension = types.ModuleType("konsol.tb_dimension")
    tb_dimension.declared_dimensions = lambda: [dict(r) for r in site.declared_dims]
    konsol.tb_dimension = tb_dimension
    konsol.close = close
    konsol.fiscal_calendar = fiscal_calendar
    konsol.entity_permissions = entity_permissions
    close.ch_read = _ch_read(site)
    close.signoff_gate = _signoff_gate(site)

    names = ["frappe", "konsol", "konsol.close", "konsol.fiscal_calendar",
             "konsol.entity_permissions", "konsol.close.ch_read", "konsol.close.signoff_gate",
             "konsol.tb_dimension", "tb_worksheet_api_under_test"] + ["konsol.close." + n for n in _CLOSE_SIBLINGS]
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({
        "frappe": frappe, "konsol": konsol, "konsol.close": close,
        "konsol.fiscal_calendar": fiscal_calendar,
        "konsol.entity_permissions": entity_permissions, "konsol.tb_dimension": tb_dimension,
        "konsol.close.ch_read": close.ch_read, "konsol.close.signoff_gate": close.signoff_gate,
    })
    try:
        for name in _CLOSE_SIBLINGS:
            setattr(close, name, _load_path("konsol.close." + name,
                                            os.path.join(CLOSE_DIR, name + ".py")))
        api = _load_path("tb_worksheet_api_under_test", API_PY)
        return run(api, frappe)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _call(site, fy=2025, fp=7, group=None, entities=None, dimension=None):
    extra = {} if dimension is None else {"dimension": dimension}
    result = _invoke(site, lambda api, _f: api.get_worksheet(fy, fp, group, entities, **extra))
    json.dumps(result)  # a GET payload must be JSON-safe
    return result


def _call_raises(site, fy=2025, fp=7, group=None, entities=None, dimension=None):
    """``(exception, frappe stub)`` so a test can check the exception class."""
    box = {}

    def run(api, frappe):
        box["frappe"] = frappe
        with pytest.raises(Exception) as info:
            api.get_worksheet(fy, fp, group, entities,
                              **({} if dimension is None else {"dimension": dimension}))
        return info.value

    exc = _invoke(site, run)
    return exc, box["frappe"]


def _line(result, account):
    for line in result["worksheet"]["lines"]:
        if line["account"] == account:
            return line
    raise AssertionError("no line for %r" % account)


# --- ok -----------------------------------------------------------------------

def test_ok_returns_the_worksheet_from_exactly_one_clickhouse_read():
    site = _Site()
    result = _call(site)
    assert result["state"] == "ok", result["message"]
    assert result["message"] is None
    assert result["consolidation_group"] == "G1"
    assert result["reporting_currency"] == "USD"
    assert result["minor_unit"] == 2
    assert len(site.ch_calls) == 1
    sql, params = site.ch_calls[0]
    assert params == {"group": "G1", "fy": 2025}
    assert "gold_fully_consolidated_tb" in sql
    ws = result["worksheet"]
    ids = [c["key"] for c in ws["columns"]]
    assert ids == ["entity:ZZA", "entity:ZZB", "layer:ic_elimination", "layer:topside",
                   "consolidated"]
    cash = _line(result, "1110")
    assert cash["name"] == "Cash"
    assert cash["cells"] == {"entity:ZZA": 1000.0, "entity:ZZB": 500.0,
                             "layer:ic_elimination": 0.0, "layer:topside": -94.93}
    assert cash["consolidated"] == 1405.07


def test_cta_rows_sit_under_their_entity_on_the_declared_cta_account():
    result = _call(_Site())
    cta = _line(result, "3300")
    assert cta["cells"]["entity:ZZA"] == -5.07
    assert cta["consolidated"] == -5.07


def test_pl_is_year_to_date_so_last_years_pl_is_out():
    result = _call(_Site())
    assert _line(result, "4100")["consolidated"] == -900.0


def test_total_cross_foots():
    ws = _call(_Site())["worksheet"]
    assert sum(ws["total"]["cells"].values()) == pytest.approx(ws["total"]["consolidated"])
    assert ws["total"]["consolidated"] == pytest.approx(
        sum(line["consolidated"] for line in ws["lines"]))


def test_sql_never_filters_on_reporting_currency_and_reads_it():
    site = _Site()
    _call(site)
    sql = site.ch_calls[0][0]
    where = sql[sql.upper().index(" WHERE "):]
    assert "reporting_currency" not in where.split("GROUP BY")[0]
    assert "reporting_currency" in sql.split(" FROM ")[0]


def test_a_zero_minor_unit_is_declared_not_missing():
    site = _Site()
    site.groups = [{"consolidation_group": "G1", "reporting_currency": "JPY"}]
    for row in site.rows:
        if row["reporting_currency"]:
            row["reporting_currency"] = "JPY"
    result = _call(site)
    assert result["state"] == "ok", result["message"]
    assert result["minor_unit"] == 0
    assert _line(result, "1110")["consolidated"] == 1405.0


# --- states with no ClickHouse read ---------------------------------------------

def test_several_groups_and_none_named_is_choose_group_with_no_read():
    site = _Site()
    site.groups.append({"consolidation_group": "G2", "reporting_currency": "EUR"})
    result = _call(site)
    assert result["state"] == "choose_group"
    assert result["message"] == "Choose a consolidation group."
    assert result["worksheet"] is None
    assert site.ch_calls == []


def test_no_group_declared_is_choose_group_with_its_own_message():
    site = _Site()
    site.groups = []
    result = _call(site)
    assert result["state"] == "choose_group"
    assert "No consolidation group is declared" in result["message"]
    assert site.ch_calls == []


def test_a_named_unknown_group_is_refused():
    exc, _f = _call_raises(_Site(), group="NOPE")
    assert "NOPE is not a consolidation group" in str(exc)


def test_no_published_chart_is_no_chart_with_no_read():
    site = _Site()
    site.accounts = {}
    result = _call(site)
    assert result["state"] == "no_chart"
    assert result["worksheet"] is None
    assert site.ch_calls == []


def test_an_undeclared_minor_unit_is_setup_gap_never_a_guessed_two():
    site = _Site()
    site.minor_units = {}
    result = _call(site)
    assert result["state"] == "setup_gap"
    assert result["message"].startswith(STATEMENT_MODEL.STATEMENT_CURRENCY_PRECISION_UNDECLARED)
    assert "USD" in result["message"] and "G1" in result["message"]
    assert result["worksheet"] is None
    assert site.ch_calls == []


def test_a_group_with_no_reporting_currency_is_setup_gap():
    site = _Site()
    site.groups = [{"consolidation_group": "G1", "reporting_currency": None}]
    result = _call(site)
    assert result["state"] == "setup_gap"
    assert site.ch_calls == []


def test_an_undeclared_period_is_refused():
    exc, _f = _call_raises(_Site(), fy=2030, fp=1)
    assert "not a declared period" in str(exc)


def test_a_non_numeric_period_is_refused():
    exc, _f = _call_raises(_Site(), fy="x", fp=1)
    assert "not a period" in str(exc)


# --- ClickHouse failure and warehouse defects ------------------------------------

def test_relation_missing_is_not_built_never_zero_rows():
    site = _Site()
    site.ch_error = Exception("Code: 60. DB::Exception: Table missing (UNKNOWN_TABLE)")
    result = _call(site)
    assert result["state"] == "not_built"
    assert "UNKNOWN_TABLE" in result["message"]
    assert result["worksheet"] is None


def test_any_other_clickhouse_failure_is_error():
    site = _Site()
    site.ch_error = ConnectionError("refused")
    result = _call(site)
    assert result["state"] == "error"
    assert result["message"] == "ConnectionError"
    assert result["worksheet"] is None


def test_a_null_amount_is_error_naming_the_account_and_layer():
    site = _Site()
    site.rows.append(_ch_row("2100", None, "topside", "ZZA", currency="", null_rows=1))
    result = _call(site)
    assert result["state"] == "error"
    assert "2100" in result["message"] and "topside" in result["message"]
    assert result["worksheet"] is None


def test_a_row_in_another_currency_is_refused_by_name():
    site = _Site()
    site.rows.append(_ch_row("1110", 12.0, "entity", "ZZB", currency="EUR"))
    result = _call(site)
    assert result["state"] == "error"
    assert "EUR" in result["message"] and "1110" in result["message"]
    assert "USD" in result["message"]
    assert result["worksheet"] is None


def test_a_currency_refusal_never_names_an_entity_outside_scope():
    site = _Site()
    site.allowed = {"ZZA"}
    site.rows.append(_ch_row("1110", 12.0, "entity", "ZZB", currency="EUR"))
    result = _call(site)
    assert result["state"] == "error"
    assert "ZZB" not in json.dumps(result)


# --- entity filter and scope ------------------------------------------------------

def test_entities_filter_moves_the_rest_to_other_and_keeps_consolidated():
    full = _call(_Site())
    site = _Site()
    filtered = _call(site, entities=["ZZA"])
    assert filtered["state"] == "ok"
    assert filtered["entities"] == ["ZZA"]
    ids = [c["key"] for c in filtered["worksheet"]["columns"]]
    assert "entity:ZZB" not in ids
    assert "bucket:other_entities" in ids
    assert _line(filtered, "1110")["cells"]["bucket:other_entities"] == 500.0
    for line in full["worksheet"]["lines"]:
        assert _line(filtered, line["account"])["consolidated"] == line["consolidated"]
    assert len(site.ch_calls) == 1


def test_entities_as_json_text_is_the_same_as_a_list():
    assert _call(_Site(), entities='["ZZA"]') == _call(_Site(), entities=["ZZA"])


def test_no_entities_or_an_empty_list_shows_every_entity():
    for value in (None, "", "[]", []):
        result = _call(_Site(), entities=value)
        assert result["entities"] is None
        assert "entity:ZZB" in [c["key"] for c in result["worksheet"]["columns"]]


def test_entities_that_are_not_a_list_are_refused():
    for value in ("ZZA", '{"a": 1}', "[1]"):
        site = _Site()
        exc, _f = _call_raises(site, entities=value)
        assert "Entities" in str(exc)
        assert site.ch_calls == []


def test_a_scoped_caller_never_sees_an_entity_outside_scope():
    site = _Site()
    site.allowed = {"ZZA"}
    result = _call(site)
    assert result["state"] == "ok"
    dumped = json.dumps(result)
    assert "ZZB" not in dumped
    ws = result["worksheet"]
    assert ws["outside_count"] == 1
    assert "bucket:outside_scope" in [c["key"] for c in ws["columns"]]
    assert _line(result, "1110")["cells"]["bucket:outside_scope"] == 500.0


def test_a_scoped_caller_asking_for_a_hidden_entity_is_refused_before_any_read():
    site = _Site()
    site.allowed = {"ZZA"}
    exc, frappe = _call_raises(site, entities=["ZZA", "ZZB"])
    assert isinstance(exc, frappe.PermissionError)
    assert "ZZB" in str(exc)
    assert site.ch_calls == []


def test_an_unknown_entity_is_refused_by_name_before_any_read():
    site = _Site()
    exc, frappe = _call_raises(site, entities=["ZZNOPE"])
    assert "ZZNOPE is not an entity." in str(exc)
    assert site.ch_calls == []


def test_mixed_known_and_unknown_entities_name_only_the_unknown():
    site = _Site()
    exc, _f = _call_raises(site, entities=["ZZA", "ZZNOPE", "ZZB"])
    assert "ZZNOPE" in str(exc)
    assert "ZZA" not in str(exc) and "ZZB" not in str(exc)
    assert site.ch_calls == []
    assert len(site.entity_queries) == 1


def test_a_scoped_caller_asking_for_a_hidden_unknown_code_gets_the_scope_refusal():
    site = _Site()
    site.allowed = {"ZZA"}
    exc, frappe = _call_raises(site, entities=["ZZA", "ZZNOPE"])
    assert isinstance(exc, frappe.PermissionError)
    assert "You may not see entity ZZNOPE." in str(exc)
    assert "is not an entity" not in str(exc)
    assert site.entity_queries == [] and site.ch_calls == []


def test_no_entity_query_when_entities_is_none_or_empty():
    for value in (None, "", "[]", []):
        site = _Site()
        assert _call(site, entities=value)["state"] == "ok"
        assert site.entity_queries == []


def test_a_role_outside_the_four_is_refused_before_any_read():
    site = _Site()
    site.roles = {"EPM Entity Accountant"}
    exc, frappe = _call_raises(site)
    assert isinstance(exc, frappe.PermissionError)
    assert site.reads == [] and site.ch_calls == []


# --- dimension (konsol#494 row 7) ----------------------------------------------------

def _dimensioned(site):
    for row in site.rows:
        row["dimension_value"] = "CC1" if row["data_area_id"] == "ZZA" else ""


def test_no_dimension_means_no_system_columns_read_and_the_same_sql():
    site = _Site()
    result = _call(site)
    assert result["state"] == "ok"
    assert site.columns_calls == []
    assert result["dimension"] is None
    assert "dimension_value" not in site.ch_calls[0][0]
    assert site.ch_calls[0][0] == _sql_constants()["_WORKSHEET_SQL"]


def test_a_valid_dimension_is_interpolated_once_in_select_and_once_in_group_by():
    site = _Site()
    _dimensioned(site)
    result = _call(site, dimension="dim_cost_center")
    assert result["state"] == "ok", result["message"]
    assert result["dimension"] == "dim_cost_center"
    assert len(site.columns_calls) == 1 and len(site.ch_calls) == 1
    cols_sql = site.columns_calls[0][0]
    assert "epm_gold" in cols_sql and "gold_fully_consolidated_tb" in cols_sql
    assert "system.columns" in cols_sql and "dim\\_%" in cols_sql
    sql = site.ch_calls[0][0]
    assert sql.count("dim_cost_center AS dimension_value") == 1
    select, group_by = sql.split(" GROUP BY ")
    assert select.count("dim_cost_center") == 1
    assert group_by.count("dim_cost_center") == 1
    assert sql.count("dim_cost_center") == 2


def test_a_dimensioned_payload_has_line_ids_with_a_pipe():
    site = _Site()
    _dimensioned(site)
    result = _call(site, dimension="dim_cost_center")
    keys = [line["key"] for line in result["worksheet"]["lines"]]
    assert keys and all("|" in k for k in keys)
    assert any(k.endswith("|CC1") for k in keys)


def test_undeclared_unbuilt_and_illegal_dimensions_are_refused_with_no_worksheet_read():
    for name, tweak in (
        ("dim_other", None),                                    # not declared
        ("dim_cost_center", lambda s: setattr(s, "built_dims", [])),   # not built
        ("dim_cost_center", lambda s: setattr(s, "declared_dims", [
            {"dimension_name": "dim_cost_center", "status": "Draft",
             "in_trial_balance": 1}])),                         # declared, not Published
        ("dim_x; drop table t", None),                          # illegal name
    ):
        site = _Site()
        if tweak:
            tweak(site)
        exc, _f = _call_raises(site, dimension=name)
        assert name.split(";")[0] in str(exc) or "dimension" in str(exc)
        assert site.ch_calls == [], name


def test_no_read_states_with_a_dimension_make_no_clickhouse_read_at_all():
    """choose_group, no_chart and setup_gap return before any read, the
    dimension's system.columns read included (module docstring)."""
    def two_groups(s):
        s.groups = [{"consolidation_group": "G1", "reporting_currency": "USD"},
                    {"consolidation_group": "G2", "reporting_currency": "USD"}]

    def no_groups(s):
        s.groups = []

    def no_chart(s):
        s.accounts = {}

    def setup_gap(s):
        s.minor_units = {}

    for state, tweak in (("choose_group", two_groups), ("choose_group", no_groups),
                         ("no_chart", no_chart), ("setup_gap", setup_gap)):
        site = _Site()
        tweak(site)
        result = _call(site, dimension="dim_cost_center")
        assert result["state"] == state, (state, result["message"])
        assert result["dimension"] == "dim_cost_center"
        assert site.columns_calls == [] and site.ch_calls == [], state


def test_a_failed_system_columns_read_is_an_error_state_not_a_throw():
    site = _Site()
    site.columns_error = RuntimeError("connection refused")
    result = _call(site, dimension="dim_cost_center")
    assert result["state"] == "error" and result["worksheet"] is None
    assert result["message"] and site.ch_calls == []


def test_a_system_columns_read_on_a_missing_database_is_not_built():
    site = _Site()
    site.columns_error = RuntimeError("Code: 81 (UNKNOWN_DATABASE) epm_gold")
    result = _call(site, dimension="dim_cost_center")
    assert result["state"] == "not_built" and result["worksheet"] is None
    assert site.ch_calls == []


def test_refusal_messages_name_the_failed_check():
    site = _Site()
    exc, _f = _call_raises(site, dimension="dim_x; drop table t")
    assert "legal dimension name" in str(exc)
    site = _Site()
    exc, _f = _call_raises(site, dimension="dim_other")
    assert "not declared" in str(exc)
    site = _Site()
    site.built_dims = []
    exc, _f = _call_raises(site, dimension="dim_cost_center")
    assert "not built" in str(exc)


# --- static -------------------------------------------------------------------------

def test_no_sql_constant_has_an_alias_column_collision():
    constants = _sql_constants()
    assert set(constants) == {"_WORKSHEET_SQL"}, (
        "a *_SQL constant was added or removed: update this test's expected set")
    for name, sql in constants.items():
        assert _alias_collision(sql) is None, name
        assert "dim_" not in sql, name


def test_the_endpoint_opens_with_the_literal_only_for():
    with open(API_PY) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_worksheet")
    body = fn.body[1:] if isinstance(fn.body[0].value if isinstance(fn.body[0], ast.Expr) else None,
                                     ast.Constant) else fn.body
    first = ast.unparse(body[0])
    assert first == ("frappe.only_for(('EPM Admin', 'EPM Analyst', 'EPM User', "
                     "'System Manager'))")
