"""The Konsol home's seven charts and the "Konsol Overview" dashboard (konsol#407 phase 2).

Every chart is a Dashboard Chart of type Custom with its own Dashboard Chart
Source, whose method (konsol/close/charts_api.py) checks the caller's roles
and read permission and reads through the permission-scoped readers on every
call. None is a group-by chart: Frappe caches group-by data under the chart
name alone (frappe/utils/dashboard.py ``chart-data:{chart_name}``), so a user
could be served counts computed under another user's permissions.

Three layers, no site:

- the pure layout (charts_model.py) on fixtures: shapes, counts, zeros for
  an empty category, a reader disagreement raised rather than guessed;
- charts_api.py against a stub frappe: each method's chart from stubbed
  readers, the caller's entities only, a refusal for a role without read
  (table-driven), filters refused, a reader's ValueError turned into the
  chart's own note (a ValidationError, which the chart widget shows inside
  the card: its xcall is ``silent``) rather than a 500;
- the shipped files: seven Custom charts with a source each, the dashboard
  in ``consolidation_dashboard/`` (the only folder Frappe syncs a Dashboard
  from), the workspace listing all seven below the checklist, ``modified``
  bumped so migrate re-imports them.
"""
import ast
import datetime
import importlib.util
import json
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_DIR = os.path.join(APP_DIR, "consolidation")
API = os.path.join(APP_DIR, "close", "charts_api.py")
WORKSPACE = os.path.join(MODULE_DIR, "workspace", "konsol", "konsol.json")
DASHBOARD = os.path.join(MODULE_DIR, "consolidation_dashboard", "konsol_overview",
                         "konsol_overview.json")
ROLES = {"EPM Admin", "EPM Analyst", "EPM User", "System Manager"}

#: chart name -> (chart type, source, method)
CHARTS = {
    "TB Submission Progress": ("Bar", "Konsol TB Submission Progress", "tb_submission_progress"),
    "Net Sales and Net Result": ("Line", "Konsol Net Sales and Net Result", "net_sales_and_result"),
    "Top IC Differences": ("Bar", "Konsol Top IC Differences", "top_ic_differences"),
    "Approvals Backlog": ("Bar", "Konsol Approvals Backlog", "approvals_backlog"),
    "Closing vs Average Rate": ("Line", "Konsol Closing vs Average Rate", "closing_vs_average_rate"),
    "Entities by Status": ("Donut", "Konsol Entities by Status", "entities_by_status"),
    "Days to Close": ("Bar", "Konsol Days to Close", "days_to_close"),
}


def _read(path):
    with open(path) as f:
        return f.read()


def _scrub(name):
    return name.lower().replace(" ", "_")


def _load(name, rel, modules=None):
    modules = modules or {}
    saved = {k: sys.modules.get(k) for k in modules}
    sys.modules.update(modules)
    try:
        spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, rel))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _model():
    return _load("charts_model_under_test", os.path.join("close", "charts_model.py"))


def _values(chart):
    return {d["name"]: d["values"] for d in chart["datasets"]}


# --- charts_model -----------------------------------------------------------------

P1, P2, P3 = (2025, 1), (2025, 2), (2025, 3)


def test_tb_progress_counts_each_expected_entity_once_and_keeps_empty_periods():
    m = _model()
    progress = {P1: {"expected": ["A", "B", "C"], "missing": ["C"]},
                P2: {"expected": ["A", "B", "C"], "missing": ["A", "B", "C"]},
                P3: {"expected": [], "missing": []}}
    submitted = {P1: {"A", "B"}}  # B also declared an exception: the TB counts
    exceptions = {P1: {"B"}, P2: set()}
    chart = m.tb_progress([P1, P2, P3], progress, submitted, exceptions, None)
    assert chart["labels"] == ["FY2025 P01", "FY2025 P02", "FY2025 P03"]
    assert _values(chart) == {"Submitted": [2, 0, 0], "Declared exception": [0, 0, 0],
                              "Missing": [1, 3, 0]}


def test_tb_progress_is_cut_to_the_callers_entities():
    m = _model()
    progress = {P1: {"expected": ["A", "B", "C", "D"], "missing": ["C", "D"]}}
    chart = m.tb_progress([P1], progress, {P1: {"A"}}, {P1: {"B"}}, {"B", "D"})
    assert _values(chart) == {"Submitted": [0], "Declared exception": [1], "Missing": [1]}


def test_tb_progress_raises_when_the_reads_disagree_with_the_gate():
    """An entity the gate counts as in but the caller's reads show neither a
    TB nor an exception for is never counted as either: it raises."""
    m = _model()
    progress = {P1: {"expected": ["A"], "missing": []}}
    with pytest.raises(ValueError, match="A"):
        m.tb_progress([P1], progress, {}, {}, None)


def _statement(net, sales_amount, heading="4"):
    lines = [{"kind": "heading", "heading": heading, "current": sales_amount},
             {"kind": "heading", "heading": "5", "current": -1},
             {"kind": "net_result", "label": "Net result", "current": net}]
    return {"sections": [{"section": "Profit and Loss", "lines": lines},
                         {"section": "Balance Sheet", "lines": []}]}


def test_pl_trend_reads_net_result_and_the_declared_heading():
    m = _model()
    statements = {P1: _statement(10, 100), P2: _statement(-5, 0)}
    chart = m.pl_trend([P1, P2], statements, "4", "USD")
    assert chart["labels"] == ["FY2025 P01", "FY2025 P02"]
    assert _values(chart) == {"Net sales (USD)": [100.0, 0.0], "Net result (USD)": [10.0, -5.0]}


def test_pl_trend_never_sends_negative_zero():
    """The statement flips the P&L sign, so an empty period comes back as
    Decimal('-0.00'); measured live (7 Nov), the chart got -0.0."""
    from decimal import Decimal
    m = _model()
    chart = m.pl_trend([P1], {P1: _statement(Decimal("-0.00"), Decimal("-0.00"))}, "4", "USD")
    for values in _values(chart).values():
        assert [str(v) for v in values] == ["0.0"]


def test_pl_trend_names_an_undeclared_heading_rather_than_guessing_one():
    m = _model()
    chart = m.pl_trend([P1], {P1: _statement(10, 100)}, None, "USD")
    names = [d["name"] for d in chart["datasets"]]
    assert names[0].startswith("Net sales: declare") and "Close Settings" in names[0]
    assert chart["datasets"][0]["values"] == [0]
    assert _values(chart)["Net result (USD)"] == [10.0]


def test_pl_trend_raises_for_a_heading_the_statement_does_not_carry():
    m = _model()
    with pytest.raises(ValueError, match="99"):
        m.pl_trend([P1], {P1: _statement(10, 100)}, "99", "USD")


def _pair(a, b, diff, group="G1", aa="1", ab="2"):
    return {"entity_a": a, "entity_b": b, "account_a": aa, "account_b": ab, "difference": diff,
            "consolidation_group": group}


def test_ic_top_ranks_by_absolute_difference_and_keeps_ten():
    m = _model()
    pairs = [_pair("A%02d" % i, "B", float(i) * (-1 if i % 2 else 1)) for i in range(1, 13)]
    chart = m.ic_top([{"consolidation_group": "G1", "pairs": pairs}])
    assert len(chart["labels"]) == 10
    assert chart["labels"][0] == "A12 ↔ B · 1/2"
    assert chart["datasets"][0]["values"][:3] == [12.0, 11.0, 10.0]


def test_ic_top_names_the_group_only_when_there_are_several():
    m = _model()
    chart = m.ic_top([{"consolidation_group": "G1", "pairs": [_pair("A", "B", 5)]},
                      {"consolidation_group": "G2", "pairs": [_pair("A", "B", 3, group="G2")]}])
    assert chart["labels"] == ["A ↔ B · 1/2 (G1)", "A ↔ B · 1/2 (G2)"]


def test_ic_top_refuses_a_pair_without_a_difference():
    m = _model()
    with pytest.raises(ValueError):
        m.ic_top([{"consolidation_group": "G1", "pairs": [_pair("A", "B", None)]}])


def test_approvals_lists_every_doctype_with_zeros():
    m = _model()
    items = [{"doctype": "Group Exchange Rate"}, {"doctype": "Group Exchange Rate"},
             {"doctype": "IC Balance"}]
    chart = m.approvals(items, ("Consolidation Journal", "Group Exchange Rate", "IC Balance"))
    assert chart["labels"] == ["Consolidation Journal", "Group Exchange Rate", "IC Balance"]
    assert chart["datasets"][0]["values"] == [0, 2, 1]


def test_entities_by_status_keeps_every_option_and_names_the_rest():
    m = _model()
    chart = m.entities_by_status(["Active", "Active", None, "Retired"],
                                 ["Active", "Dormant", "Disposed"])
    assert chart["labels"] == ["Active", "Dormant", "Disposed", "Not set", "Retired"]
    assert chart["datasets"][0]["values"] == [2, 0, 0, 1, 1]


def test_top_currencies_by_entity_count_excluding_the_reporting_currency():
    m = _model()
    rows = ["EUR", "EUR", "CAD", "USD", "USD", "USD", "", None, "GBP", "CAD", "JPY"]
    assert m.top_currencies(rows, "USD", 3) == ["CAD", "EUR", "GBP"]


def test_rates_lines_per_currency_and_type_and_a_gap_is_named_never_silent():
    """Review of #411 (M1): a period with no approved rate at all is left
    out (live: P01-P06 had none, and the lines fell to 0); a period missing
    one rate names it in its label."""
    m = _model()
    rows = [{"from_currency": "EUR", "rate_type": "Closing", "fiscal_year": 2025,
             "fiscal_period": 2, "rate": 1.1},
            {"from_currency": "EUR", "rate_type": "Average", "fiscal_year": 2025,
             "fiscal_period": 2, "rate": 1.05},
            {"from_currency": "EUR", "rate_type": "Closing", "fiscal_year": 2025,
             "fiscal_period": 3, "rate": 1.2}]
    chart = m.rates([P1, P2, P3], ["EUR"], rows)
    assert chart["labels"] == ["FY2025 P02", "FY2025 P03 (no EUR average)"]
    assert _values(chart) == {"EUR closing": [1.1, 1.2], "EUR average": [1.05, 0]}


def test_rates_refuses_two_approved_rates_for_one_key():
    m = _model()
    row = {"from_currency": "EUR", "rate_type": "Closing", "fiscal_year": 2025,
           "fiscal_period": 1, "rate": 1.1}
    with pytest.raises(ValueError, match="EUR"):
        m.rates([P1], ["EUR"], [row, dict(row, rate=1.2)])


def test_days_to_close_shows_an_unsigned_period_as_zero_with_a_label():
    m = _model()
    states = [{"key": [2025, 1], "end_date": "2025-01-31"},
              {"key": [2025, 2], "end_date": datetime.date(2025, 2, 28)},
              {"key": [2025, 3], "end_date": "2025-03-31"}]
    signed = {(2025, 1): {"signed_off_at": datetime.datetime(2025, 2, 10, 17, 0)},
              (2025, 2): {"signed_off_at": None}}
    chart = m.days_to_close(states, signed)
    assert chart["labels"] == ["FY2025 P01", "FY2025 P02 (sign-off date not recorded)",
                               "FY2025 P03 (not signed off)"]
    assert chart["datasets"][0]["values"] == [10, 0, 0]


# --- charts_api against a stub frappe ---------------------------------------------

def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


class _Site:
    """What the stub readers answer."""

    def __init__(self, **kw):
        self.roles = kw.get("roles", ["EPM User"])
        self.readable = kw.get("readable")  # None: every doctype
        self.allowed = kw.get("allowed")
        self.lists = kw.get("lists", {})
        self.landing = kw.get("landing", {"period": [2025, 2]})
        self.periods = kw.get("periods", [
            {"key": [2024, 12], "end_date": "2024-12-31"},
            {"key": [2025, 1], "end_date": "2025-01-31"},
            {"key": [2025, 2], "end_date": "2025-02-28"},
            {"key": [2025, 3], "end_date": "2025-03-31"}])  # after the landing
        self.progress = kw.get("progress", {})
        self.statement = kw.get("statement")
        self.ic = kw.get("ic")
        self.queue = kw.get("queue", {"items": []})
        self.signed = kw.get("signed", {})
        self.groups = kw.get("groups", [{"consolidation_group": "G", "reporting_currency": "USD"}])
        self.single = kw.get("single", {})
        self.calls = []


def _stub(site):
    f = _module("frappe")
    f.ValidationError = type("ValidationError", (Exception,), {})
    f.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, **k):
        raise (exc or f.ValidationError)(msg)

    def only_for(roles):
        if not set(site.roles) & set(roles):
            raise f.PermissionError("only for %s" % (roles,))

    def has_permission(doctype, ptype="read", throw=False, **k):
        ok = site.readable is None or doctype in site.readable
        if throw and not ok:
            raise f.PermissionError("no %s on %s" % (ptype, doctype))
        return ok

    def get_list(doctype, filters=None, fields=None, pluck=None, **k):
        site.calls.append((doctype, filters))
        rows = site.lists.get(doctype, [])
        return [r[pluck] for r in rows] if pluck else [dict(r) for r in rows]

    meta = types.SimpleNamespace(get_field=lambda fn: types.SimpleNamespace(
        options="Active\nDormant\nDisposed"))
    f.throw = throw
    f.only_for = only_for
    f.has_permission = has_permission
    f.get_list = get_list
    f.get_meta = lambda dt: meta
    f.get_roles = lambda user=None: list(site.roles)
    f.session = types.SimpleNamespace(user="u@example.com")
    f.whitelist = lambda *a, **k: (lambda fn: fn)
    f.parse_json = lambda v: json.loads(v) if isinstance(v, str) else v
    f.db = types.SimpleNamespace(get_single_value=lambda dt, field: site.single.get(field))
    return f


def _api(site):
    frappe = _stub(site)
    model = _load("charts_model_for_api", os.path.join("close", "charts_model.py"))
    overview_model = _load("overview_model_for_charts", os.path.join("close", "overview_model.py"))
    policy = _load("close_policy_model_for_charts", os.path.join("close", "close_policy_model.py"))

    def statement_for(rows, accounts, period_rows, key, declared, minor_unit):
        return site.statement[tuple(key)]

    def _statement(fy, fp, group):
        if site.statement is None:
            return {"state": "not_built", "message": "The warehouse is not built yet."}, None
        return ({"state": "ok", "reporting_currency": "USD", "consolidation_group": "G",
                 "statement": {"minor_unit": 2}},
                {"tb_rows": [], "accounts": {}, "period_rows": [], "declared": {}})

    def choose_group(groups, consolidation_group=None):
        if len(groups) == 1:
            return groups[0]["consolidation_group"], None, "ok", None
        return None, None, "choose_group", "Choose a consolidation group."

    def get_ic(fy, fp):
        return site.ic

    close = _module(
        "konsol.close", charts_model=model, overview_model=overview_model,
        close_policy_model=policy,
        period_api=_module("p", get_context=lambda: {"landing": site.landing,
                                                     "periods": site.periods}),
        signoff_gate=_module(
            "s", trial_balance_progress=lambda fy, fp: site.progress[(fy, fp)],
            latest_signed_runs=lambda fields=(): site.signed),
        statement_api=_module("sa", _statement=_statement, root_groups=lambda: site.groups,
                              choose_group=choose_group),
        statement_model=_module("sm", statement=statement_for),
        ic_api=_module("i", get_ic=get_ic),
        approvals_api=_module("a", queue_for=lambda user, roles: site.queue),
    )
    modules = {
        "frappe": frappe,
        "konsol": _module("konsol", group_rates=_module(
            "gr", true_rate=lambda q, per: float(q) / int(per))),
        "konsol.close": close,
        "konsol.entity_permissions": _module("e", allowed_entity_codes=lambda: site.allowed),
    }
    return _load("charts_api_under_test", os.path.join("close", "charts_api.py"), modules), frappe


def test_tb_submission_progress_reads_the_gate_and_the_callers_lists():
    site = _Site(
        allowed={"A", "C"},
        progress={(2025, 1): {"expected": ["A", "B", "C"], "missing": ["C"]},
                  (2025, 2): {"expected": ["A", "B", "C"], "missing": ["A", "C"]}},
        lists={"Trial Balance Submission": [{"data_area_id": "A", "fiscal_period": 1}],
               "TB Exception": [{"data_area_id": "B", "fiscal_period": 1}]})
    api, _ = _api(site)
    chart = api.tb_submission_progress(filters="[]")
    assert chart["labels"] == ["FY2025 P01", "FY2025 P02"]  # the current year only
    assert _values(chart) == {"Submitted": [1, 0], "Declared exception": [0, 0],
                              "Missing": [1, 2]}
    assert ("Trial Balance Submission", {"fiscal_year": 2025, "docstatus": 1}) in site.calls


def test_net_sales_and_result_uses_the_statement_for_every_period():
    site = _Site(single={"statement_net_sales_heading": "4"},
                 statement={(2025, 1): _statement(1, 10), (2025, 2): _statement(2, 20)})
    api, _ = _api(site)
    chart = api.net_sales_and_result()
    assert _values(chart) == {"Net sales (USD)": [10.0, 20.0], "Net result (USD)": [1.0, 2.0]}


def test_a_statement_that_is_not_ok_is_the_charts_note():
    api, frappe = _api(_Site(statement=None))
    with pytest.raises(frappe.ValidationError, match="Net Sales and Net Result.*not built"):
        api.net_sales_and_result()


def test_top_ic_differences_reads_get_ic_and_a_state_is_its_note():
    ic = {"state": "checked", "message": None,
          "groups": [{"consolidation_group": "G", "pairs": [_pair("A", "B", -7)]}]}
    api, _ = _api(_Site(ic=ic))
    chart = api.top_ic_differences()
    assert chart["labels"] == ["A ↔ B · 1/2"] and chart["datasets"][0]["values"] == [7.0]
    api, frappe = _api(_Site(ic={"state": "not_configured", "message": "Not configured."}))
    with pytest.raises(frappe.ValidationError, match="Not configured"):
        api.top_ic_differences()


def test_top_ic_differences_with_no_intercompany_declared_is_an_empty_chart():
    """Review of #411 (L5): "None in this group" is a declaration, not a
    failure; the chart's empty state shows, not a permanent note."""
    api, _ = _api(_Site(ic={"state": "not_applicable", "message": "None declared.",
                            "groups": []}))
    chart = api.top_ic_differences()
    assert chart["labels"] == [] and chart["datasets"][0]["values"] == []


def test_approvals_backlog_counts_the_queue_items():
    api, _ = _api(_Site(queue={"items": [{"doctype": "IC Balance"}], "sent_back": [{}]}))
    chart = api.approvals_backlog()
    assert len(chart["labels"]) == 7 and sum(chart["datasets"][0]["values"]) == 1


def test_closing_vs_average_rate_reads_submitted_rates_into_the_group_currency():
    site = _Site(lists={
        "Entity": [{"functional_currency": "EUR"}, {"functional_currency": "USD"}],
        "Group Exchange Rate": [
            {"from_currency": "EUR", "rate_type": "Closing", "fiscal_year": 2025,
             "fiscal_period": 1, "quote": 110, "quoted_per": "100"}]})
    api, _ = _api(site)
    chart = api.closing_vs_average_rate()
    assert chart["labels"] == ["FY2025 P01 (no EUR average)"]
    assert _values(chart) == {"EUR closing": [1.1], "EUR average": [0]}
    (filters,) = [f for dt, f in site.calls if dt == "Group Exchange Rate"]
    assert filters["docstatus"] == 1 and filters["to_currency"] == "USD"
    assert filters["fiscal_year"] == 2025


def test_closing_vs_average_rate_with_no_foreign_currency_in_view_is_a_note():
    """Measured live (7 Nov): an EPM User scoped to one USD entity got an
    empty chart, which the widget shows as "no rate approved". The note says
    why instead."""
    site = _Site(lists={"Entity": [{"functional_currency": "USD"}]})
    api, frappe = _api(site)
    with pytest.raises(frappe.ValidationError, match="other than USD"):
        api.closing_vs_average_rate()
    assert not [dt for dt, _f in site.calls if dt == "Group Exchange Rate"]


def test_closing_vs_average_rate_with_several_groups_is_a_note():
    api, frappe = _api(_Site(groups=[{"consolidation_group": "G1"}, {"consolidation_group": "G2"}]))
    with pytest.raises(frappe.ValidationError, match="Choose a consolidation group"):
        api.closing_vs_average_rate()


def test_entities_by_status_reads_through_get_list():
    site = _Site(lists={"Entity": [{"status": "Active"}, {"status": "Disposed"}]})
    api, _ = _api(site)
    chart = api.entities_by_status()
    assert chart["labels"] == ["Active", "Dormant", "Disposed"]
    assert chart["datasets"][0]["values"] == [1, 0, 1]
    assert ("Entity", {"is_group": 0}) in site.calls


def test_days_to_close_reads_the_signed_runs():
    site = _Site(signed={(2025, 1): {"signed_off_at": datetime.datetime(2025, 2, 5)}})
    api, _ = _api(site)
    chart = api.days_to_close()
    assert chart["labels"] == ["FY2025 P01", "FY2025 P02 (not signed off)"]
    assert chart["datasets"][0]["values"] == [5, 0]


def test_no_current_period_is_the_charts_note():
    api, frappe = _api(_Site(landing={"period": None, "reason": "No fiscal year is declared."}))
    with pytest.raises(frappe.ValidationError, match="No fiscal year is declared"):
        api.days_to_close()


@pytest.mark.parametrize("method", sorted(m for _t, _s, m in CHARTS.values()))
def test_a_role_outside_the_set_is_refused(method):
    api, frappe = _api(_Site(roles=["Entity Accountant"]))
    with pytest.raises(frappe.PermissionError):
        getattr(api, method)()


#: method -> the doctype whose read it needs (None: only the role gate, as the
#: reader it calls gates no doctype of its own)
NEEDS_READ = {
    "tb_submission_progress": "Trial Balance Submission",
    "closing_vs_average_rate": "Group Exchange Rate",
    "entities_by_status": "Entity",
    "days_to_close": "Assertion Run",
}


@pytest.mark.parametrize("method,doctype", sorted(NEEDS_READ.items()))
def test_a_caller_without_read_on_the_doctype_is_refused(method, doctype):
    everything = {"Trial Balance Submission", "TB Exception", "Group Exchange Rate", "Entity",
                  "Assertion Run"}
    site = _Site(readable=everything - {doctype})
    api, frappe = _api(site)
    with pytest.raises(frappe.PermissionError, match=doctype):
        getattr(api, method)()
    assert site.calls == []  # refused before anything is read


@pytest.mark.parametrize("method", sorted(m for _t, _s, m in CHARTS.values()))
def test_every_method_refuses_filters(method):
    api, frappe = _api(_Site())
    with pytest.raises(frappe.ValidationError, match="no filters"):
        getattr(api, method)(filters='[["Entity", "status", "=", "Active"]]')


def test_a_readers_value_error_is_the_charts_note_not_a_500():
    site = _Site(progress={(2025, 1): {"expected": ["A"], "missing": []},
                           (2025, 2): {"expected": [], "missing": []}})
    api, frappe = _api(site)
    with pytest.raises(frappe.ValidationError, match="TB Submission Progress cannot be shown: .*A"):
        api.tb_submission_progress()


# --- the shipped files ------------------------------------------------------------

def _whitelisted():
    out = {}
    for fn in ast.parse(_read(API)).body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for dec in fn.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "whitelist"):
                out[fn.name] = next(ast.literal_eval(k.value) for k in dec.keywords
                                    if k.arg == "methods")
    return out


def _chart_doc(name):
    return json.loads(_read(os.path.join(MODULE_DIR, "dashboard_chart", _scrub(name),
                                         _scrub(name) + ".json")))


def test_every_chart_is_custom_with_its_own_source_and_roles():
    methods = _whitelisted()
    for name, (kind, source, method) in CHARTS.items():
        doc = _chart_doc(name)
        assert doc["name"] == doc["chart_name"] == name
        assert doc["chart_type"] == "Custom", name  # never a cached group-by chart
        assert doc["type"] == kind and doc["source"] == source, name
        assert doc["is_standard"] == 1 and doc["module"] == "Consolidation", name
        assert {r["role"] for r in doc["roles"]} == ROLES, name
        assert json.loads(doc["filters_json"]) == [], name
        assert doc["modified"] >= "2026-11-07", name
        assert doc.get("empty_state_message"), name
        folder = os.path.join(MODULE_DIR, "dashboard_chart_source", _scrub(source))
        src = json.loads(_read(os.path.join(folder, _scrub(source) + ".json")))
        assert src["name"] == src["source_name"] == source and src["module"] == "Consolidation"
        assert os.path.isfile(os.path.join(folder, "__init__.py"))
        js = _read(os.path.join(folder, _scrub(source) + ".js"))
        assert 'frappe.dashboards.chart_sources["%s"]' % source in js, source
        assert 'method: "konsol.close.charts_api.%s"' % method in js, source
        assert methods.get(method) == ["POST"], method  # the widget's xcall POSTs
    assert os.path.isfile(os.path.join(MODULE_DIR, "dashboard_chart_source", "__init__.py"))


def test_no_other_dashboard_chart_ships_in_the_module():
    shipped = set(os.listdir(os.path.join(MODULE_DIR, "dashboard_chart"))) - {"__init__.py",
                                                                              "__pycache__"}
    assert shipped == {_scrub(n) for n in CHARTS}


def test_the_stacked_chart_says_so():
    options = json.loads(_chart_doc("TB Submission Progress")["custom_options"])
    assert options["barOptions"]["stacked"] == 1


def test_the_dashboard_lists_all_seven():
    doc = json.loads(_read(DASHBOARD))
    assert doc["doctype"] == "Dashboard" and doc["name"] == doc["dashboard_name"] == "Konsol Overview"
    assert doc["is_standard"] == 1 and doc["module"] == "Consolidation"
    assert [c["chart"] for c in doc["charts"]] == list(CHARTS)
    assert doc["modified"] >= "2026-11-07"


def test_the_workspace_lists_all_seven_for_the_tabs_block():
    ws = json.loads(_read(WORKSPACE))
    assert [c["chart_name"] for c in ws["charts"]] == list(CHARTS)
    content = json.loads(ws["content"])
    assert not [b for b in content if b["type"] == "chart"]  # the Trends tab reads the child table
    assert [b["data"]["custom_block_name"] for b in content] == ["Konsol Home Tabs"]
    ids = [b["id"] for b in content]
    assert len(ids) == len(set(ids))
    assert ws["modified"] > "2026-11-07 23:55:00.000000"


def test_charts_api_never_bypasses_permissions():
    source = _read(API)
    assert "ignore_permissions" not in source
    assert "frappe.get_all(" not in source
    assert "frappe.cache" not in source  # every call reads afresh
    for fn in ast.parse(source).body:
        if isinstance(fn, ast.FunctionDef) and fn.name in _whitelisted():
            gate = fn.body[1]  # after the docstring
            assert ast.unparse(gate.value.func) == "frappe.only_for", fn.name
            assert set(ast.literal_eval(gate.value.args[0])) == ROLES, fn.name
            assert "_start(filters" in ast.unparse(fn), fn.name


# --- the declared net sales heading -----------------------------------------------

def test_close_settings_declares_the_net_sales_heading():
    doc = json.loads(_read(os.path.join(MODULE_DIR, "doctype", "close_settings",
                                        "close_settings.json")))
    field = next(f for f in doc["fields"] if f["fieldname"] == "statement_net_sales_heading")
    assert field["fieldtype"] == "Link" and field["options"] == "Main Account"
    assert not field.get("reqd") and field.get("description")
    order = doc["field_order"]
    assert order.index("statement_net_sales_heading") > order.index("statements_section")
    assert order.index("statement_net_sales_heading") < order.index("deadlines_tab")  # konsol#510


def test_net_sales_heading_problem():
    policy = _load("close_policy_model_for_heading", os.path.join("close", "close_policy_model.py"))
    ok = {"name": "4", "is_group": 1, "status": "Published", "statement_section": "Profit and Loss"}
    assert policy.net_sales_heading_problem(None, None) is None
    assert policy.net_sales_heading_problem("4", ok) is None
    assert "not found" in policy.net_sales_heading_problem("4", None)
    for change in ({"is_group": 0}, {"status": "Draft"}, {"statement_section": "Balance Sheet"}):
        assert "4" in policy.net_sales_heading_problem("4", dict(ok, **change)), change
