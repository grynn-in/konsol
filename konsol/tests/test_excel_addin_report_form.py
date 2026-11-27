"""The Reports panel's template list and generated form (konsol#548 v1 T14).

The pane no longer hard-codes its templates or their fields. It reads
``konsol.api.report_catalog`` and builds one widget per declared param, fills
each picker from ``konsol.api.report_choices``, refetches a picker when a
param it depends on changes, and applies a formulas template through
``konsolReport.apply`` (report.js, T13). The Apply button reads "Update this
report" when the active sheet is the add-in's own report of the selected
template, else "Apply to new sheet" (decided by Deepak Pai, 27 Nov 2026,
konsol#548 Q3: a sheet painted before v1 is not adopted).

report_form.js runs under node in ``excel_addin_report_form_harness.mjs`` with
a fake DOM and stub endpoints; the wiring in index.html is checked as source.
"""
import copy
import json
import os
import re
import shutil
import subprocess

TESTS = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(TESTS, "excel_addin_report_form_harness.mjs")
ADDIN = os.path.join(os.path.dirname(TESTS), "public", "excel-addin")


def _read(name):
    with open(os.path.join(ADDIN, name), encoding="utf-8") as f:
        return f.read()


def _p(name, ptype, label=None, required=False, default=None, depends_on=(), **extra):
    out = {"name": name, "type": ptype, "label": label or name.replace("_", " ").capitalize(),
           "required": required, "default": default, "depends_on": list(depends_on)}
    out.update(extra)
    return out


PNL = {
    "id": "pnl_monthly", "version": 1, "title": "Monthly P&L", "description": "12-month income statement",
    "mode": "formulas", "sheet_name": "P&L", "available": True, "why": None,
    "params": [
        _p("entity", "entity", "Entity", required=True),
        _p("year", "fiscal_year", "Year", required=True),
        _p("scenario_id", "text", default="actuals", echo_only=True),
    ],
}

VARIANCE = {
    "id": "variance", "version": 1, "title": "Actual vs budget", "description": "Variance by account",
    "mode": "formulas", "sheet_name": "Variance", "available": True, "why": None,
    "params": [
        _p("year", "fiscal_year", "Year", required=True),
        _p("period", "period", "Period", required=True, depends_on=["year"]),
        _p("budget", "scenario", "Budget", default="rule", depends_on=["year"], scenario_type="budget"),
        _p("hierarchy", "hierarchy", "Hierarchy"),
        _p("scope", "scope", "Scope", required=True, depends_on=["hierarchy", "year"],
           kinds=["entity", "group", "node"]),
        _p("accounts", "account_list", "Accounts"),
        _p("basis", "choice", "Basis", default="month", choices=["ytd", "month"]),
        _p("rows", "int", "Rows", default=5),
        _p("scenario_id", "text", default="actuals", echo_only=True),
    ],
}

FORECAST = {
    "id": "forecast", "version": 1, "title": "Forecast", "description": "Forecast by month",
    "mode": "formulas", "sheet_name": "FC", "available": False,
    "why": "No Published Dataset has scenario key 'fc' (read by series 'fc'), so Forecast cannot be read on this site.",
    "params": [_p("entity", "entity", "Entity", required=True)],
}

TB = {
    "id": "trial_balance_long", "version": 1, "title": "Entity TB", "description": "One row per account",
    "mode": "snapshot", "sheet_name": "TB", "available": True, "why": None,
    "params": [
        _p("entity", "entity", "Entity", required=True),
        _p("year", "fiscal_year", "Year", required=True),
        _p("period_from", "int", default=1),
        _p("period_to", "int", default=12),
    ],
}

CATALOG = {"templates": [PNL, VARIANCE, FORECAST, TB]}

#: 30 entities: long enough for a searchable list.
ENTITIES = [{"code": "ZZ%02d" % i, "name": "Entity %d" % i, "is_group": False,
             "currency": "USD", "status": "Active"} for i in range(1, 31)]
FEW_ENTITIES = ENTITIES[:3]
YEARS = [2023, 2024]
PERIODS = [{"number": n, "code": "P%02d" % n, "label": "M%d" % n, "start_date": None,
            "end_date": None, "quarter": None} for n in (1, 2, 3)]
ONE_BUDGET = {"options": [{"id": "B24", "label": "Budget 2024"}], "default": "B24", "why": None}
TWO_BUDGETS = {"options": [{"id": "B24", "label": "Budget 2024"}, {"id": "B24R", "label": "Revised"}],
               "default": None, "why": "FY2024 has two active budget scenarios (B24, B24R): pick one."}
HIERARCHIES = [{"name": "MGMT", "label": "Management", "dimension": "Region"}]
NODES = [{"code": "ALL", "label": "All", "depth": 0, "is_group": True, "parent": None},
         {"code": "EU", "label": "Europe", "depth": 1, "is_group": False, "parent": "ALL"}]
GROUPS = [{"code": "ZZG", "name": "ZZ Group"}]
ACCOUNTS = [{"code": "4000", "name": "Revenue", "statement_section": "Profit and Loss",
             "account_type": None, "parent": None},
            {"code": "5000", "name": "Cost of sales", "statement_section": "Profit and Loss",
             "account_type": None, "parent": None}]


def _choices(budgets=ONE_BUDGET, entities=ENTITIES):
    return [
        ["entities", {}, entities],
        ["groups", {}, GROUPS],
        ["fiscal_years", {}, YEARS],
        ["periods", {"year": "2024"}, PERIODS],
        ["scenarios", {"year": "2024", "scenario_type": "budget"}, budgets],
        ["hierarchies", {}, HIERARCHIES],
        ["hierarchy_nodes", {"hierarchy": "MGMT", "year": "2024"}, NODES],
        ["accounts", {}, ACCOUNTS],
    ]


NOT_OWN = {"own": False, "copied": False, "report_id": None, "entry": None, "area": None,
           "sheet": "Sheet1", "reason": "Sheet1 is not a report the add-in applied"}
OWN_PNL = {"own": True, "copied": False, "report_id": "r-1", "area": "A1:M5", "sheet": "P&L",
           "reason": "", "entry": {"template_id": "pnl_monthly", "template_version": "1",
                                   "params": {"entity": "ZZ02", "year": 2024, "scenario_id": "actuals"},
                                   "area": "A1:M5", "server": "https://pane.example",
                                   "worksheet_id": "{ws}", "applied_at": "2026-11-27T00:00:00Z"}}


def _run(ops, catalog=CATALOG, choices=None, inspect=None, **extra):
    node = shutil.which("node")
    # Not a skip: a host without node would report green while testing nothing.
    assert node, "node is required to run the Excel add-in tests"
    scenario = {"catalog": catalog, "choices": _choices() if choices is None else choices,
                "inspect": inspect or [NOT_OWN], "ops": ops}
    scenario.update(extra)
    proc = subprocess.run([node, HARNESS, json.dumps(scenario)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


# -- the template list -------------------------------------------------------------

def test_the_template_list_is_the_catalog_in_its_order():
    snap = _run([["load"], ["snap"]])["snaps"][0]
    assert [t["id"] for t in snap["templates"]] == ["pnl_monthly", "variance", "forecast", "trial_balance_long"]
    assert snap["template"] == "pnl_monthly"
    assert snap["hint"] == "12-month income statement"


def test_an_unavailable_template_is_disabled_and_says_why():
    snap = _run([["load"], ["snap"]])["snaps"][0]
    by_id = {t["id"]: t for t in snap["templates"]}
    assert by_id["forecast"]["disabled"] and not by_id["pnl_monthly"]["disabled"]
    assert FORECAST["why"] in snap["templateWhy"], snap["templateWhy"]


def test_a_failed_catalog_shows_the_error_and_no_fallback_list():
    out = _run([["load"], ["snap"]], catalog=None, catalogError="HTTP 403 — PermissionError")
    snap = out["snaps"][0]
    assert snap["templates"] == [], snap["templates"]
    assert "HTTP 403 — PermissionError" in snap["error"], snap
    assert snap["apply"]["disabled"] and snap["snapshot"]["hidden"]
    assert any(level == "err" and "HTTP 403" in m for level, m in out["logs"]), out["logs"]


# -- types to widgets --------------------------------------------------------------

def test_each_param_type_gets_its_widget():
    out = _run([["load"], ["select", "variance"], ["snap"]])
    f = out["snaps"][0]["fields"]
    assert f["year"]["tag"] == "select" and f["year"]["options"] == ["", "2023", "2024"], f["year"]
    assert f["period"]["tag"] == "select"
    assert f["budget"]["tag"] == "select"
    assert f["hierarchy"]["tag"] == "select" and f["hierarchy"]["options"] == ["", "MGMT"]
    # The scope's default kind is entity: 30 of them, so a searchable input.
    assert f["scope"]["tag"] == "input" and f["scope"]["list"], f["scope"]
    assert f["scope"]["kinds"]["options"] == ["entity", "group", "node"], f["scope"]
    assert f["accounts"]["tag"] == "select" and f["accounts"]["multiple"], f["accounts"]
    assert f["accounts"]["options"] == ["4000", "5000"]
    assert f["basis"]["tag"] == "select" and f["basis"]["options"] == ["", "ytd", "month"]
    assert f["basis"]["value"] == "month"
    assert f["rows"]["tag"] == "input" and f["rows"]["type"] == "number" and f["rows"]["value"] == "5"
    # An echo_only text param is not shown, and is still sent.
    assert "scenario_id" not in f
    assert out["snaps"][0]["values"]["scenario_id"] == "actuals"
    assert f["year"]["label"].startswith("Year")


def test_a_long_list_is_a_searchable_input():
    long_ = _run([["load"], ["snap"]])["snaps"][0]["fields"]["entity"]
    assert long_["tag"] == "input" and long_["list"], long_
    assert long_["options"] == [e["code"] for e in ENTITIES]
    short = _run([["load"], ["snap"]], choices=_choices(entities=FEW_ENTITIES))["snaps"][0]["fields"]["entity"]
    assert short["tag"] == "select" and short["options"] == ["", "ZZ01", "ZZ02", "ZZ03"], short


def test_a_scope_toggle_offers_only_the_params_kinds_and_refetches():
    cat = copy.deepcopy(CATALOG)
    cat["templates"][1]["params"][4]["kinds"] = ["entity", "group"]
    out = _run([["load"], ["select", "variance"], ["snap"], ["setKind", "scope", "group"], ["snap"]],
               catalog=cat)
    first, second = out["snaps"]
    assert first["fields"]["scope"]["kinds"]["options"] == ["entity", "group"]
    assert second["fields"]["scope"]["options"] == ["", "ZZG"], second["fields"]["scope"]
    assert {"kind": "groups", "args": {}} in out["requests"]


# -- depends_on --------------------------------------------------------------------

def test_a_dependent_waits_for_its_parent_and_refetches_when_it_changes():
    out = _run([["load"], ["select", "variance"], ["snap"], ["set", "year", "2024"], ["snap"]])
    before, after = out["snaps"]
    assert before["fields"]["period"]["why"] == "Choose Year first.", before["fields"]["period"]
    assert before["fields"]["period"]["options"] == [""]
    assert {"kind": "periods", "args": {"year": "2024"}} in out["requests"]
    assert {"kind": "scenarios", "args": {"year": "2024", "scenario_type": "budget"}} in out["requests"]
    assert after["fields"]["period"]["options"] == ["", "1", "2", "3"]
    assert after["fields"]["period"]["why"] == ""
    # No request was sent for a picker whose parent was unset.
    assert not any(r["kind"] == "periods" and not r["args"] for r in out["requests"]), out["requests"]


def test_a_hierarchy_node_scope_needs_hierarchy_and_year():
    out = _run([["load"], ["select", "variance"], ["setKind", "scope", "node"], ["snap"],
                ["set", "year", "2024"], ["set", "hierarchy", "MGMT"], ["snap"]])
    before, after = out["snaps"]
    assert before["fields"]["scope"]["why"].startswith("Choose "), before["fields"]["scope"]
    assert after["fields"]["scope"]["options"] == ["", "ALL", "EU"], after["fields"]["scope"]
    assert {"kind": "hierarchy_nodes", "args": {"hierarchy": "MGMT", "year": "2024"}} in out["requests"]


# -- the scenario rule default -----------------------------------------------------

def test_a_rule_default_is_preselected_when_the_server_names_one():
    snap = _run([["load"], ["select", "variance"], ["set", "year", "2024"], ["snap"]])["snaps"][0]
    assert snap["fields"]["budget"]["value"] == "B24", snap["fields"]["budget"]
    assert snap["fields"]["budget"]["why"] == ""


def test_a_rule_without_a_default_leaves_the_field_blank_and_says_why():
    snap = _run([["load"], ["select", "variance"], ["set", "year", "2024"], ["snap"]],
                choices=_choices(budgets=TWO_BUDGETS))["snaps"][0]
    assert snap["fields"]["budget"]["value"] == ""
    assert snap["fields"]["budget"]["why"] == TWO_BUDGETS["why"], snap["fields"]["budget"]
    assert "budget" not in snap["values"], snap["values"]


# -- required fields block Apply ---------------------------------------------------

def test_required_fields_block_apply_and_are_named():
    out = _run([["load"], ["select", "variance"], ["snap"],
                ["set", "year", "2024"], ["set", "period", "2"], ["snap"],
                ["set", "scope", "ZZ03"], ["snap"]])
    empty, partial, full = out["snaps"]
    assert empty["apply"]["disabled"]
    assert "Year" in empty["blocked"] and "Period" in empty["blocked"] and "Scope" in empty["blocked"], empty
    assert partial["apply"]["disabled"] and "Scope" in partial["blocked"] and "Year" not in partial["blocked"]
    assert not full["apply"]["disabled"] and full["blocked"] == "", full
    assert full["values"] == {"year": 2024, "period": 2, "budget": "B24",
                              "scope": {"kind": "entity", "code": "ZZ03"},
                              "basis": "month", "rows": 5, "scenario_id": "actuals"}, full["values"]


def test_an_account_list_sends_the_selected_codes():
    out = _run([["load"], ["select", "variance"], ["setMany", "accounts", ["4000", "5000"]], ["values"]])
    assert out["results"][0]["accounts"] == ["4000", "5000"]


def test_an_unavailable_template_blocks_apply_with_its_why():
    snap = _run([["load"], ["select", "forecast"], ["snap"]])["snaps"][0]
    assert snap["apply"]["disabled"] and FORECAST["why"] in snap["blocked"], snap


# -- the button label and prefill --------------------------------------------------

def test_apply_reads_apply_to_new_sheet_on_a_sheet_that_is_not_a_report():
    snap = _run([["load"], ["snap"]])["snaps"][0]
    assert snap["apply"]["text"] == "Apply to new sheet", snap["apply"]
    assert not snap["apply"]["hidden"] and snap["snapshot"]["hidden"]


def test_an_own_report_of_the_template_reads_update_and_prefills_its_params():
    out = _run([["load"], ["snap"]], inspect=[OWN_PNL])
    snap = out["snaps"][0]
    assert snap["apply"]["text"] == "Update this report", snap["apply"]
    assert snap["fields"]["entity"]["value"] == "ZZ02"
    assert snap["fields"]["year"]["value"] == "2024"
    assert snap["values"] == {"entity": "ZZ02", "year": 2024, "scenario_id": "actuals"}


def test_another_templates_report_reads_apply_to_new_sheet():
    snap = _run([["load"], ["select", "variance"], ["snap"]], inspect=[OWN_PNL])["snaps"][0]
    assert snap["apply"]["text"] == "Apply to new sheet"


def test_the_label_is_rechecked_when_a_sheet_is_activated():
    out = _run([["load"], ["snap"], ["activate"], ["snap"], ["activate"], ["snap"]],
               inspect=[NOT_OWN, OWN_PNL, NOT_OWN])
    assert out["watched"], "the form must listen to worksheets.onActivated"
    labels = [s["apply"]["text"] for s in out["snaps"]]
    assert labels == ["Apply to new sheet", "Update this report", "Apply to new sheet"], labels
    assert out["inspects"] >= 3


def test_the_label_is_rechecked_when_the_template_changes():
    out = _run([["load"], ["select", "variance"], ["select", "pnl_monthly"], ["snap"]],
               inspect=[OWN_PNL])
    assert out["snaps"][0]["apply"]["text"] == "Update this report"
    assert out["inspects"] >= 3


def test_a_snapshot_template_shows_the_snapshot_button():
    snap = _run([["load"], ["select", "trial_balance_long"], ["snap"]])["snaps"][0]
    assert snap["apply"]["hidden"] and not snap["snapshot"]["hidden"], snap
    assert snap["fields"]["period_from"]["value"] == "1" and snap["fields"]["period_to"]["value"] == "12"


# -- window.konsolidatApplyReport's two shapes -------------------------------------

def test_normalise_request_accepts_legacy_and_params_shapes():
    out = _run([["normalise", {"template_id": "pnl_monthly", "entity": "ZZ01", "year": 2024}],
                ["normalise", {"template_id": "pnl_monthly", "params": {"entity": "ZZ01", "year": 2024}}]])
    legacy, new = out["results"]
    assert legacy == {"template_id": "pnl_monthly", "params": {"entity": "ZZ01", "year": 2024}}, legacy
    assert new == legacy, new


# -- the wiring in index.html ------------------------------------------------------

def _between(src, start, end="\n      function "):
    i = src.index(start)
    return src[i:src.index(end, i + 1)]


def test_the_page_loads_report_js_and_the_form_after_ribbon_js_and_server_js():
    src = _read("index.html")
    server = src.index('<script src="server.js"></script>')
    ribbon = src.index('<script src="ribbon.js"></script>')
    report = src.index('<script src="report.js"></script>')
    form = src.index('<script src="report_form.js"></script>')
    assert server < ribbon < report < form, (server, ribbon, report, form)


def test_the_page_has_no_hard_coded_template_list_or_fields():
    src = _read("index.html")
    assert not re.search(r'<option value="(pnl_monthly|trial_balance_long)"', src)
    assert 'id="report-entity"' not in src and 'id="report-year"' not in src
    assert 'id="report-fields"' in src


def test_the_page_reads_the_catalog_and_choices_through_api():
    src = _read("index.html")
    body = _between(src, "function getJson(")
    assert "api(" in body and "parseApiError(" in body, body
    assert '"/api/method/konsol.api.report_catalog"' in src
    assert '"/api/method/konsol.api.report_choices"' in src
    assert "konsolReportForm.mount(" in src


def test_apply_builds_from_params_and_applies_through_report_js():
    src = _read("index.html")
    build = _between(src, "function buildCellMap(")
    assert "params" in build and "scenario_id" not in build, build
    apply_ = _between(src, "function applyReport(")
    assert "konsolReport.apply(" in apply_ and 'target: "active"' in apply_, apply_
    assert ".missing()" in apply_, "Apply must refuse while a required field is empty"
    assert "checkSheet()" in apply_, "the label must be rechecked after an apply"


def test_window_apply_report_takes_both_shapes():
    src = _read("index.html")
    m = re.search(r"window\.konsolidatApplyReport\s*=\s*function\s*\(params\)\s*\{(.*?)\n      \};", src, re.S)
    assert m, "window.konsolidatApplyReport is gone"
    assert "normaliseRequest(params)" in m.group(1) and "konsolReport.apply(" in m.group(1), m.group(1)


def test_the_catalog_is_loaded_after_sign_in_and_the_form_watches_activation():
    src = _read("index.html")
    assert ".load()" in _between(src, "function showLoggedIn(")
    assert ".watchActivation()" in _between(src, "function start() {", "\n      if (typeof Office")
    form = _read("report_form.js")
    assert "worksheets.onActivated.add(" in form


def test_the_snapshot_reads_the_generated_form():
    src = _read("index.html")
    snap = _between(src, "function snapshotReport(")
    assert "values()" in snap and "snapshotAndRemember(" in snap, snap
    assert 'getElementById("period-from")' not in snap


# -- review findings (PR #626) ------------------------------------------------------

def test_every_scope_kind_of_the_model_has_a_form_mapping():
    """Finding 2: the server's kinds are report_template_model.SCOPE_KINDS
    (entity, group, node); the form keyed node as hierarchy_node, so a node
    scope fetched nothing."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_rtm_kinds", os.path.join(os.path.dirname(TESTS), "report_template_model.py"))
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    out = _run([["api", "KIND_OF_SCOPE"], ["api", "SCOPE_LABEL"]])
    kind_of_scope, labels = out["results"]
    assert set(kind_of_scope) == set(model.SCOPE_KINDS), kind_of_scope
    assert set(labels) == set(model.SCOPE_KINDS), labels
    assert kind_of_scope["node"] == "hierarchy_nodes"


def test_a_node_scope_reads_the_nodes_and_sends_kind_node():
    out = _run([["load"], ["select", "variance"], ["setKind", "scope", "node"],
                ["set", "year", "2024"], ["set", "hierarchy", "MGMT"], ["set", "period", "1"],
                ["set", "scope", "EU"], ["snap"]])
    snap = out["snaps"][0]
    assert {"kind": "hierarchy_nodes", "args": {"hierarchy": "MGMT", "year": "2024"}} in out["requests"]
    assert snap["fields"]["scope"]["options"] == ["", "ALL", "EU"], snap["fields"]["scope"]
    assert snap["values"]["scope"] == {"kind": "node", "code": "EU"}, snap["values"]
    assert snap["blocked"] == "", snap["blocked"]


def _per_kind(template, available, why):
    t = copy.deepcopy(template)
    t["available_kinds"] = available
    t["unavailable_kinds"] = why
    return t


def test_the_form_offers_only_the_available_kinds_and_says_why_for_the_others():
    """Finding 4: the catalog names the kinds a template can be read for on
    this site; the toggle offers those only and shows each other kind's why."""
    why = "No Published Dataset has scenario key 'consolidated_budget' (read by series 'bud'), " \
          "so Actual vs budget cannot be read for a group on this site."
    cat = copy.deepcopy(CATALOG)
    cat["templates"][1] = _per_kind(VARIANCE, ["entity", "node"], {"group": why})
    snap = _run([["load"], ["select", "variance"], ["snap"]], catalog=cat)["snaps"][0]
    scope = snap["fields"]["scope"]
    assert scope["kinds"]["options"] == ["entity", "node"], scope
    assert why in snap["kindsWhy"]["scope"], snap["kindsWhy"]
    # One kind left: no toggle, the kind is sent, the why still shows.
    cat["templates"][1] = _per_kind(VARIANCE, ["entity"], {"group": why, "node": "no node word"})
    out = _run([["load"], ["select", "variance"], ["set", "scope", "ZZ03"], ["snap"]], catalog=cat)
    snap = out["snaps"][0]
    assert "kinds" not in snap["fields"]["scope"], snap["fields"]["scope"]
    assert snap["values"]["scope"] == {"kind": "entity", "code": "ZZ03"}
    assert why in snap["kindsWhy"]["scope"] and "no node word" in snap["kindsWhy"]["scope"]
    assert not any(r["kind"] in ("groups", "hierarchy_nodes") for r in out["requests"]), out["requests"]


def test_a_catalog_without_per_kind_availability_offers_the_declared_kinds():
    snap = _run([["load"], ["select", "variance"], ["snap"]])["snaps"][0]
    assert snap["fields"]["scope"]["kinds"]["options"] == ["entity", "group", "node"]
    assert snap["kindsWhy"]["scope"] == ""


BUDGET_ONLY = {
    "id": "budget_only", "version": 1, "title": "Budget", "description": "A budget by year",
    "mode": "formulas", "sheet_name": "B", "available": True, "why": None,
    "params": [
        _p("year", "fiscal_year", "Year", required=True),
        _p("budget", "scenario", "Budget", default="rule", depends_on=["year"], scenario_type="budget"),
    ],
}
BUD23 = {"options": [{"id": "B23", "label": "Budget 2023"}], "default": "B23", "why": None}


def _held_run(ops, **extra):
    choices = _choices() + [["scenarios", {"year": "2023", "scenario_type": "budget"}, BUD23]]
    return _run([["load"], ["select", "budget_only"]] + ops,
                catalog={"templates": [BUDGET_ONLY]}, choices=choices, hold=["scenarios"], **extra)


def test_apply_waits_while_a_dependent_picker_is_loading():
    """Finding 5: the year changed, its budgets not yet in: Apply would send
    the new year with the old year's budget. It is blocked until the reply."""
    out = _held_run([["set", "year", "2023"], ["release", 0], ["snap"],
                     ["set", "year", "2024"], ["snap"], ["missing"], ["release", 1], ["snap"]])
    ready23, loading, ready24 = out["snaps"]
    assert ready23["values"] == {"year": 2023, "budget": "B23"}, ready23["values"]
    assert not ready23["apply"]["disabled"], ready23
    assert loading["apply"]["disabled"] and loading["blocked"] == "Loading choices…", loading
    assert out["results"] == ["Loading choices…"]
    assert not ready24["apply"]["disabled"] and ready24["blocked"] == "", ready24
    assert ready24["values"] == {"year": 2024, "budget": "B24"}, ready24["values"]


def test_a_stale_reply_that_arrives_late_is_dropped():
    out = _held_run([["set", "year", "2023"], ["set", "year", "2024"], ["snap"],
                     ["release", 1], ["snap"], ["release", 0], ["snap"]])
    both, new_in, old_late = out["snaps"]
    assert both["apply"]["disabled"] and both["blocked"] == "Loading choices…", both
    assert new_in["values"] == {"year": 2024, "budget": "B24"}, new_in["values"]
    assert not new_in["apply"]["disabled"], new_in
    assert old_late["values"] == {"year": 2024, "budget": "B24"}, old_late["values"]
    assert old_late["fields"]["budget"]["options"] == ["", "B24"], old_late["fields"]["budget"]
    assert not old_late["apply"]["disabled"] and old_late["blocked"] == "", old_late


def test_a_stale_reply_first_keeps_apply_blocked_until_the_new_one():
    out = _held_run([["set", "year", "2023"], ["set", "year", "2024"],
                     ["release", 0], ["snap"], ["release", 1], ["snap"]])
    old_first, new_in = out["snaps"]
    assert old_first["apply"]["disabled"] and old_first["blocked"] == "Loading choices…", old_first
    assert old_first["values"].get("budget") != "B23", old_first["values"]
    assert new_in["values"] == {"year": 2024, "budget": "B24"} and not new_in["apply"]["disabled"]


def test_a_choices_request_that_never_answers_times_out_and_clears_loading():
    """A hung request must not leave the picker loading forever: after 30 s the
    request is aborted, the field says how to recover, and Apply unblocks."""
    out = _held_run([["set", "year", "2023"], ["snap"], ["advance", 29000], ["snap"],
                     ["advance", 2000], ["snap"]], fakeTimers=True)
    waiting, still, timed_out = out["snaps"]
    assert waiting["blocked"] == "Loading choices\u2026" and still["blocked"] == "Loading choices\u2026"
    assert out["aborted"] == 1, out["aborted"]
    why = timed_out["fields"]["budget"]["why"]
    assert "re-select the template to retry" in why, why
    assert timed_out["blocked"] != "Loading choices\u2026", timed_out["blocked"]
    assert any("re-select the template" in m for _, m in out["logs"]), out["logs"]


def test_a_reply_inside_the_timeout_cancels_it():
    out = _held_run([["set", "year", "2023"], ["release", 0], ["advance", 60000], ["snap"]],
                    fakeTimers=True)
    assert out["aborted"] == 0 and out["snaps"][0]["fields"]["budget"]["why"] == ""


def test_a_stored_report_of_an_unavailable_kind_shows_why_not_a_blank_pick():
    """The sheet was built for a group; this site no longer offers the group
    kind for the template. The field says why instead of 'not a choice now'."""
    why = "No Published Dataset has scenario key 'consolidated_budget' (read by series 'bud'), " \
          "so Actual vs budget cannot be read for a group on this site."
    cat = copy.deepcopy(CATALOG)
    cat["templates"][1] = _per_kind(VARIANCE, ["entity"], {"group": why})
    own = copy.deepcopy(OWN_PNL)
    own["entry"]["template_id"] = "variance"
    own["entry"]["params"] = {"scope": {"kind": "group", "code": "ZZ03"}, "year": 2024}
    out = _run([["load"], ["select", "variance"], ["activate"], ["snap"]], catalog=cat,
               inspect=[NOT_OWN, own])
    f = out["snaps"][0]["fields"]["scope"]
    assert why in f["why"], f
    assert "not a choice now" not in f["why"], f
