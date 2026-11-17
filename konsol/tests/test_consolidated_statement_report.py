"""Consolidated Statement report: the group filter (review of konsol#362).

Every member row of a Consolidation Group carries its root's code, so a
member's document name (typed in, or passed as a route option the Link
field accepts) used to resolve to the root's code and silently return the
whole root group's statement. A member row must be refused by name.
"""
import ast
import importlib.util
import os
import re
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(APP_DIR, "consolidation", "report", "consolidated_statement", "consolidated_statement.py")
#: The one doc-name -> code resolution lives in statement_api (konsol#495 row 11b).
API_PATH = os.path.join(APP_DIR, "close", "statement_api.py")


class Refused(Exception):
    pass


def _group_code_with(rows):
    """Exec only statement_api.group_code against a stub frappe whose get_value reads ``rows``."""
    tree = ast.parse(open(API_PATH).read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "group_code")

    def get_value(doctype, name, fields, as_dict=False):
        row = rows.get(name)
        if row is None:
            return None
        if isinstance(fields, (list, tuple)):
            return types.SimpleNamespace(**row) if as_dict else tuple(row[f] for f in fields)
        return row[fields]

    def throw(msg, *a, **k):
        raise Refused(msg)

    frappe = types.SimpleNamespace(db=types.SimpleNamespace(get_value=get_value), throw=throw, _=lambda s: s)
    env = {"frappe": frappe}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), API_PATH, "exec"), env)
    return env["group_code"]


def test_the_report_imports_the_one_resolution_and_keeps_its_name_importable():
    tree = ast.parse(open(PATH).read())
    assert not any(isinstance(n, ast.FunctionDef) and n.name == "_group_code" for n in tree.body)
    assert any(
        isinstance(n, ast.ImportFrom) and any(a.name == "group_code" and a.asname == "_group_code" for a in n.names)
        for n in tree.body
    )


ROWS = {
    "CG-ZZG-": {"consolidation_group": "ZZG", "data_area_id": None},
    "CG-ZZG-ZZA": {"consolidation_group": "ZZG", "data_area_id": "ZZA"},
}


def test_root_document_name_resolves_to_its_code():
    assert _group_code_with(ROWS)("CG-ZZG-") == "ZZG"


def test_bare_code_is_passed_on():
    assert _group_code_with(ROWS)("ZZG") == "ZZG"


def test_blank_is_none():
    assert _group_code_with(ROWS)(None) is None


def test_member_row_is_refused_not_resolved_to_its_root():
    try:
        _group_code_with(ROWS)("CG-ZZG-ZZA")
    except Refused as e:
        assert "CG-ZZG-ZZA" in str(e) and "root" in str(e)
    else:
        raise AssertionError("a member row resolved to its root's statement")


# --- Prior year: execute() reads the prior year and never fails on it -----

class _ValidationError(Exception):
    pass


_spec = importlib.util.spec_from_file_location(
    "statement_report_model_for_report_test", os.path.join(APP_DIR, "close", "statement_report_model.py"))
REAL_MODEL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(REAL_MODEL)


def _tb(fy, fp):
    return {"fiscal_year": fy, "fiscal_period": fp, "main_account": "4100",
            "adjustment_type": "entity", "amount": 1.0, "null_rows": 0}


def _ok(fy, tb_rows):
    payload = {"state": "ok", "message": None, "statement": {"sections": [], "minor_unit": 2},
               "period": {"fiscal_year": fy, "fiscal_period": 12}}
    return payload, {"tb_rows": tb_rows}


_BUDGET_OK = {"state": "ok", "message": "Approved budget for ZZG FY2024: Budget Cycle B.", "statement": {"x": 1},
              "meta": {"cycle": "B", "layers": ["base"], "rate_set": "RS", "scenario_id": "S", "declaration": "APB"}}


def _execute_with(statement, entity_statement=None, budget=None):
    """Exec the module's functions against stub frappe/statement_api
    (``statement(fy, fp, group)`` -> ``(payload, reads)``, as
    ``statement_api._statement``; ``budget(fy, fp, group)`` -> the budget
    payload, as ``statement_api.budget_payload``, S5 K7) and a row model that
    records the prior and budget payloads it was handed (``prior_with_rows``
    is the real one)."""
    tree = ast.parse(open(PATH).read())
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    seen = {"calls": [], "budget_calls": []}

    def _statement(fy, fp, group):
        seen["calls"].append((fy, fp, group))
        return statement(fy, fp, group)

    def budget_payload(fy, fp, group):
        seen["budget_calls"].append((fy, fp, group))
        return (budget or (lambda *a: dict(_BUDGET_OK)))(fy, fp, group)

    class Model:
        prior_with_rows = staticmethod(REAL_MODEL.prior_with_rows)

        @staticmethod
        def columns(minor_unit=None, budget=None):
            seen["columns_budget"] = budget
            return ["cols"]

        @staticmethod
        def rows(payload, prior=None, drill_group=None, budget=None):
            if payload.get("state") != "ok":   # the real model refuses a non-ok payload
                raise ValueError(payload.get("message"))
            seen["rows"] = prior
            seen["drill_group"] = drill_group
            seen["budget"] = budget
            return ["rows"]

        @staticmethod
        def message(payload, prior=None, budget=None):
            seen["message"] = prior
            seen["message_budget"] = budget
            return "msg"

    def throw(msg, *a, **k):
        raise Refused(msg)

    class _Dict(dict):
        __getattr__ = dict.get

    seen["error_logs"] = []

    def log_error(title=None, message=None, **kwargs):
        seen["error_logs"].append({"title": title, "message": message, **kwargs})

    frappe = types.SimpleNamespace(
        _dict=_Dict, throw=throw, ValidationError=_ValidationError,
        clear_last_message=lambda: None,
        log_error=log_error, get_traceback=lambda *a, **k: "Traceback (stub)\nKeyError: 'input_fingerprint'",
        db=types.SimpleNamespace(get_value=lambda *a, **k: None))
    def _entity_statement(fy, fp, entity):
        seen["entity_calls"].append((fy, fp, entity))
        return entity_statement(fy, fp, entity)

    seen["entity_calls"] = []

    def get_statement(*a):
        seen["group_called"] = True
        return _statement(*a)[0]

    api = types.SimpleNamespace(
        get_statement=get_statement, _statement=_statement, budget_payload=budget_payload,
        get_entity_statement=lambda *a: _entity_statement(*a)[0], _entity_statement=_entity_statement)
    env = {"frappe": frappe, "statement_api": api, "statement_report_model": Model,
           "_group_code": lambda value: value or None}  # imported from statement_api (row 11b)
    exec(compile(ast.Module(body=fns, type_ignores=[]), PATH, "exec"), env)
    return env["execute"], seen


def test_execute_reads_the_prior_year_for_the_same_period_and_group():
    execute, seen = _execute_with(lambda fy, fp, group: _ok(int(fy), [_tb(int(fy), 12)]))
    result = execute({"fiscal_year": "2024", "fiscal_period": 12, "consolidation_group": "ZZG"})
    assert seen["calls"] == [("2024", 12, "ZZG"), (2023, 12, "ZZG")]
    assert seen["rows"] is seen["message"]
    assert seen["rows"]["state"] == "ok" and seen["rows"]["period"]["fiscal_year"] == 2023
    assert result == (["cols"], ["rows"], "msg")


def test_execute_still_renders_when_the_prior_year_read_is_refused():
    """The prior year is not a declared period: the read throws; the report
    renders this year with the refusal as the prior payload's message."""
    def statement(fy, fp, group):
        if fy == 2023:
            raise _ValidationError("FY2023 P12 is not a declared period.")
        return _ok(int(fy), [_tb(int(fy), 12)])

    execute, seen = _execute_with(statement)
    assert execute({"fiscal_year": 2024, "fiscal_period": 12, "consolidation_group": "ZZG"}) \
        == (["cols"], ["rows"], "msg")
    assert seen["rows"]["state"] == "error"
    assert seen["rows"]["message"] == "FY2023 P12 is not a declared period."


def test_an_ok_prior_year_with_no_warehouse_rows_is_not_shown_as_zero():
    """Measured live 5 Nov 2026: FY2022 P12 for ECL_GROUP is state ok with
    no rows at all, every line 0.00. The prior year must be blank with the
    reason, not a column of zeros."""
    execute, seen = _execute_with(
        lambda fy, fp, group: _ok(int(fy), [_tb(int(fy), 12)] if int(fy) == 2023 else []))
    execute({"fiscal_year": 2023, "fiscal_period": 12, "consolidation_group": "ZZG"})
    assert seen["rows"]["state"] != "ok"
    assert "No rows in the warehouse for FY2022" in seen["rows"]["message"]


def test_execute_hands_the_chosen_group_filter_to_the_heading_drill_links():
    execute, seen = _execute_with(lambda fy, fp, group: _ok(int(fy), [_tb(int(fy), 12)]))
    execute({"fiscal_year": 2024, "fiscal_period": 12, "consolidation_group": "CG-ZZG-"})
    assert seen["drill_group"] == "CG-ZZG-"


# --- Budget columns (konsol#348 S5 K7): the Group basis asks for the budget --

def _ok_group(fy, tb_rows, group="ZZG"):
    payload, reads = _ok(fy, tb_rows)
    payload["consolidation_group"] = group
    return payload, reads


def test_group_basis_reads_the_budget_for_the_statements_own_group_and_hands_it_to_rows_and_message():
    """The group may be blank in the filter (the only group): the budget is
    read for the group the statement chose, once, after the statement."""
    execute, seen = _execute_with(lambda fy, fp, group: _ok_group(int(fy), [_tb(int(fy), 12)]))
    result = execute({"fiscal_year": "2024", "fiscal_period": 12})
    assert seen["budget_calls"] == [("2024", 12, "ZZG")]
    assert seen["budget"] == _BUDGET_OK and seen["message_budget"] is seen["budget"]
    # BUD-4-2 (PR #554 review D1): the columns depend on the budget's state,
    # so the model's columns() takes the budget payload too.
    assert seen["columns_budget"] is seen["budget"]
    assert result == (["cols"], ["rows"], "msg")


def test_a_refused_budget_read_never_fails_the_report():
    """budget_payload throws (a refusal) or raises (a fault): the report
    renders the actuals, and the budget payload carries the reason for the
    legend's "Budget columns not shown: ..." line."""
    def refused(fy, fp, group):
        raise _ValidationError("Not permitted to read Budget Cycle B.")

    execute, seen = _execute_with(lambda fy, fp, group: _ok_group(int(fy), [_tb(int(fy), 12)]), budget=refused)
    assert execute({"fiscal_year": 2024, "fiscal_period": 12, "consolidation_group": "ZZG"}) \
        == (["cols"], ["rows"], "msg")
    assert seen["budget"]["state"] == "error" and seen["budget"]["statement"] is None
    assert seen["budget"]["message"] == "Not permitted to read Budget Cycle B."
    assert seen["budget"]["meta"] == {}

    def faulty(fy, fp, group):
        raise KeyError("input_fingerprint")

    execute, seen = _execute_with(lambda fy, fp, group: _ok_group(int(fy), [_tb(int(fy), 12)]), budget=faulty)
    execute({"fiscal_year": 2024, "fiscal_period": 12, "consolidation_group": "ZZG"})
    assert seen["budget"]["state"] == "error"
    assert "KeyError" in seen["budget"]["message"] and "input_fingerprint" in seen["budget"]["message"]


def test_a_faulty_budget_read_is_logged_as_well_as_shown():
    """S5 G2: a fault in the budget read (anything but a ValidationError) is
    a bug. The legend still shows it (above), and one Error Log records it
    with the traceback, so it is found without a user reporting the legend.
    A refusal (frappe.throw) is the user's to read, so it is not logged.
    Nothing commits here: the request does."""
    def faulty(fy, fp, group):
        raise KeyError("input_fingerprint")

    execute, seen = _execute_with(lambda fy, fp, group: _ok_group(int(fy), [_tb(int(fy), 12)]), budget=faulty)
    execute({"fiscal_year": 2024, "fiscal_period": 12, "consolidation_group": "ZZG"})
    assert seen["budget"]["state"] == "error"
    assert seen["error_logs"] == [{"title": "Consolidated Statement budget read",
                                   "message": "Traceback (stub)\nKeyError: 'input_fingerprint'"}]

    def refused(fy, fp, group):
        raise _ValidationError("Not permitted to read Budget Cycle B.")

    execute, seen = _execute_with(lambda fy, fp, group: _ok_group(int(fy), [_tb(int(fy), 12)]), budget=refused)
    execute({"fiscal_year": 2024, "fiscal_period": 12, "consolidation_group": "ZZG"})
    assert seen["budget"]["state"] == "error" and seen["error_logs"] == []

    source = open(PATH).read()
    assert "frappe.db.commit" not in source, "never commit inside a request's report"


def test_a_non_ok_statement_reads_no_budget():
    def refused(fy, fp, group):
        payload, reads = _ok_group(int(fy), [])
        payload.update(state="choose_group", message="Choose a consolidation group.", statement=None,
                       consolidation_group=None)
        return payload, None

    execute, seen = _execute_with(refused)
    try:
        execute({"fiscal_year": 2024, "fiscal_period": 12})
    except Refused as e:
        assert "Choose a consolidation group" in str(e)
    else:
        raise AssertionError("a non-ok statement was not refused")
    assert seen["budget_calls"] == []


def test_entity_basis_reads_no_budget():
    """The entity budget is S6: the Entity basis asks for none, so the
    legend carries no budget line."""
    execute, seen = _execute_with(lambda *a: _ok(2024, []), lambda fy, fp, entity: _ok(int(fy), [_tb(int(fy), 12)]))
    execute({"fiscal_year": 2024, "fiscal_period": 12, "basis": "Entity", "entity": "ZZA"})
    assert seen["budget_calls"] == [] and seen["budget"] is None and seen["message_budget"] is None




# --- Basis: Entity (konsol#495 row 4) --------------------------------------

def test_entity_basis_reads_the_entity_statement_and_its_prior_year():
    execute, seen = _execute_with(
        lambda *a: (_ for _ in ()).throw(AssertionError("group statement read on entity basis")),
        lambda fy, fp, entity: _ok(int(fy), [_tb(int(fy), 12)]))
    result = execute({"fiscal_year": "2024", "fiscal_period": 12, "basis": "Entity", "entity": "ZZA"})
    assert seen["entity_calls"] == [("2024", 12, "ZZA"), (2023, 12, "ZZA")]
    assert seen["calls"] == [] and not seen.get("group_called")
    assert seen["drill_group"] is None
    assert seen["rows"]["state"] == "ok" and seen["rows"]["period"]["fiscal_year"] == 2023
    assert result == (["cols"], ["rows"], "msg")


def test_entity_basis_without_an_entity_is_refused():
    execute, seen = _execute_with(lambda *a: _ok(2024, []), lambda *a: _ok(2024, []))
    try:
        execute({"fiscal_year": 2024, "fiscal_period": 12, "basis": "Entity"})
    except Refused as e:
        assert "entity" in str(e).lower()
    else:
        raise AssertionError("entity basis without an entity was not refused")
    assert seen["entity_calls"] == [] and seen["calls"] == []


def test_entity_basis_prior_year_refusal_does_not_fail_the_report():
    def entity_statement(fy, fp, entity):
        if fy == 2023:
            raise _ValidationError("FY2023 P12 is not a declared period.")
        return _ok(int(fy), [_tb(int(fy), 12)])

    execute, seen = _execute_with(lambda *a: _ok(2024, []), entity_statement)
    execute({"fiscal_year": 2024, "fiscal_period": 12, "basis": "Entity", "entity": "ZZA"})
    assert seen["rows"]["state"] == "error"


def _js():
    return open(PATH.replace(".py", ".js")).read()


def _filter(fieldname):
    source = _js()
    start = source.index('fieldname: "%s"' % fieldname)
    return source[start:source.index("\t\t},", start)]


def test_js_basis_and_entity_filters():
    basis = _filter("basis")
    assert "Group\\nEntity" in basis and 'default: "Group"' in basis
    entity = _filter("entity")
    assert 'options: "Entity"' in entity
    assert 'depends_on: \'eval:doc.basis=="Entity"\'' in entity
    # Frappe's query report ignores mandatory_depends_on (it checks only
    # reqd); the Basis filter's on_change makes the entity required.
    assert "mandatory_depends_on" not in entity
    assert "is_group: 0" in entity


def _basis_change(start, new):
    from konsol.tests.test_desk_report_js_calls import run_basis_change
    return run_basis_change("consolidated_statement/consolidated_statement.js",
                            "Consolidated Statement", start, new)


def test_js_no_filter_uses_mandatory_depends_on():
    assert "mandatory_depends_on" not in _js()


def test_js_switching_basis_to_entity_makes_the_entity_required_then_refreshes():
    got = _basis_change("Group", "Entity")
    assert not got["initial"]["entity"]
    assert got["after"]["entity"], "entity must be reqd on the entity basis"
    assert got["calls"][-1] == "refresh", got["calls"]


def test_js_switching_basis_back_to_group_drops_the_entity_requirement():
    got = _basis_change("Entity", "Group")
    assert not got["after"]["entity"]
    assert not got["after"]["consolidation_group"], "the group stays optional (blank = the only group)"
    assert got["calls"][-1] == "refresh", got["calls"]


def test_js_opened_on_the_entity_basis_requires_the_entity_before_the_first_run():
    # A link (?basis=Entity) sets the route filter, then onload, then Frappe's
    # own refresh: no Basis on_change has run yet.
    from konsol.tests.test_desk_report_js_calls import run_onload
    got = run_onload("consolidated_statement/consolidated_statement.js",
                     "Consolidated Statement", "Entity")
    assert got["hasOnload"]
    assert got["after"]["entity"]
    assert "refresh" not in got["calls"], "onload must not refresh: Frappe does"


def test_js_opened_on_the_group_basis_leaves_the_entity_optional():
    from konsol.tests.test_desk_report_js_calls import run_onload
    got = run_onload("consolidated_statement/consolidated_statement.js",
                     "Consolidated Statement", "Group")
    assert got["hasOnload"] and not got["after"]["entity"]


def test_js_consolidation_group_is_hidden_on_the_entity_basis():
    group = _filter("consolidation_group")
    assert 'depends_on: \'eval:doc.basis!="Entity"\'' in group


def test_js_drill_url_uses_the_entity_when_the_row_carries_one():
    source = _js()
    start = source.index("function drill_url")
    body = source[start:source.index("\n}\n", start)]
    assert "data.drill_entity" in body
    assert 'params.set("basis", "Entity")' in body
    assert 'params.set("entity", data.drill_entity)' in body
    assert 'params.set("consolidation_group", data.drill_group || "")' in body
    assert "heading: data.code" in body


def test_js_heading_link_shows_for_an_entity_row_too():
    source = _js()
    assert "data.code && (data.drill_group || data.drill_entity)" in source


def test_js_budget_cell_opens_the_budget_drill_when_the_row_is_marked():
    """S5 K10b: the Budget cell of a marked row (drill_budget) links Statement
    Drill with basis Budget, the row's group and its code (a heading, or
    UNBALANCED for the plug's row). The label link stays the actuals' drill."""
    source = _js()
    start = source.index("function budget_drill_url")
    body = source[start:source.index("\n}\n", start)]
    assert 'params.set("basis", "Budget")' in body or 'basis: "Budget"' in body
    assert "heading: data.code" in body
    assert "data.drill_group" in body
    assert "drill_entity" not in body, "the entity basis has no budget (S6)"
    formatter = source[source.index("formatter(value, row, column, data, default_formatter)"):]
    assert 'column.fieldname === "budget" && data.drill_budget && data.code' in formatter
    assert "budget_drill_url(data)" in formatter


def _export_body():
    source = _js()
    start = source.index("function export_to_excel")
    return source[start:source.index("\n}\n", start)]


def test_js_export_button_posts_all_five_filters():
    body = _export_body()
    # Posted to the whitelisted method's own URL: a "cmd" posted to "/" is
    # deprecated in Frappe v16 and removed in v17. new_window (true) keeps a
    # refusal's JSON off the Desk tab.
    assert re.search(r'open_url_post\(\s*"/api/method/konsol\.close\.statement_api\.export_statement",\s*\{', body)
    assert "cmd:" not in body and "frappe.request.url" not in body
    assert re.search(r"\},\s*true,?\s*\);", body), "new_window must be true"
    assert "get_filter_value(name)" in body
    for name in ("fiscal_year", "fiscal_period", "basis", "consolidation_group", "entity"):
        assert '%s: value("%s")' % (name, name) in body, name


def test_js_export_button_is_added_and_removed_in_after_datatable_render():
    source = _js()
    hook = source[source.index("after_datatable_render"):]
    assert "page.remove_inner_button(EXPORT_TO_EXCEL)" in hook
    assert "page.add_inner_button(EXPORT_TO_EXCEL, export_to_excel)" in hook
    assert "has_statement()" in hook


# --- the formatter's links, run in node (PR #554 review m3, m4) ----------------

_FORMAT_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const [, , script, report, rowsJson] = process.argv;
global.__ = (s) => s;
global.frappe = {
  query_reports: {},
  router: { make_url: (parts) => "/app/" + parts.map((p) => p.toLowerCase().replace(/ /g, "-")).join("/") },
  utils: { escape_html: (s) => s },
  ui: {},
  query_report: { data: [], get_filter_value: (name) => ({ fiscal_year: 2024, fiscal_period: 12 })[name] || "" },
};
global.konsol = { period_reports: { setup: () => Promise.resolve() } };
vm.runInThisContext(fs.readFileSync(script, "utf8"));
const def = frappe.query_reports[report];
const out = [];
for (const data of JSON.parse(rowsJson)) {
  const cell = (fieldname) => def.formatter(data[fieldname], {}, { fieldname }, data, (v) => String(v));
  out.push({ label: cell("label"), budget: cell("budget") });
}
console.log(JSON.stringify(out));
"""


def _format_rows(rows):
    import json
    import shutil
    import subprocess

    assert shutil.which("node"), "node is needed to run the report script"
    out = subprocess.run(["node", "-", PATH.replace(".py", ".js"), "Consolidated Statement", json.dumps(rows)],
                         input=_FORMAT_HARNESS, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


_HEADING_ROW = {"label": "REVENUE", "code": "4", "drill_group": "CG-ZZG-", "consolidation_group": "ZZG",
                "budget": 100.0, "drill_budget": 1}
_PLUG_ROW = {"label": "Unbalanced budget (no balancing entry)", "code": "UNBALANCED", "drill_group": "CG-ZZG-",
             "budget": 700.0, "drill_budget": 1}
_ENTITY_ROW = {"label": "REVENUE", "code": "4", "drill_entity": "ZZA", "budget": None, "drill_budget": 0}


def _href(cell):
    import re
    found = re.search(r'href="([^"]+)"', cell)
    return found.group(1) if found else None


def test_js_the_plug_rows_label_is_no_actuals_drill_link_but_its_budget_cell_is():
    """PR #554 review m3: the "Unbalanced budget" row has no actuals line
    behind it (statement_report_model.UNBALANCED_CODE is no chart heading),
    so its label must not open the actuals' drill; its Budget cell keeps the
    budget drill (basis Budget, heading UNBALANCED). A heading row keeps both."""
    heading, plug = _format_rows([_HEADING_ROW, _PLUG_ROW])
    assert "<a " not in plug["label"], plug["label"]
    assert "<a " in plug["budget"] and "basis=Budget" in _href(plug["budget"]) \
        and "heading=UNBALANCED" in _href(plug["budget"]), plug["budget"]
    assert "<a " in heading["label"] and "heading=4" in _href(heading["label"])
    assert "<a " in heading["budget"] and "basis=Budget" in _href(heading["budget"])


def test_js_drill_url_sets_the_group_basis_explicitly():
    """PR #554 review m4: the heading's actuals link names basis=Group, so the
    drill opens on the Group basis whatever the user's remembered Basis
    filter; the entity row's link names basis=Entity as before."""
    heading, entity = _format_rows([_HEADING_ROW, _ENTITY_ROW])
    assert "basis=Group" in _href(heading["label"]) and "consolidation_group=CG-ZZG-" in _href(heading["label"])
    assert "basis=Entity" in _href(entity["label"]) and "entity=ZZA" in _href(entity["label"])
    assert "basis=Group" not in _href(entity["label"])
    assert "<a " not in entity["budget"], "a blank budget cell is no link"
