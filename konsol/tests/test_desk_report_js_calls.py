"""Desk report scripts call only whitelisted POST functions.

A GET request rolls back at the end (CLAUDE.md), so every server call a
Desk report script makes to write must name a function that exists, is
``@frappe.whitelist(methods=["POST"])``, and is sent as a POST. This reads
each report script's ``frappe.call`` / ``frappe.xcall`` and checks the
named function's source.
"""
import ast
import json
import os
import re
import shutil
import subprocess

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(APP_DIR, "consolidation", "report")

#: The report scripts this test covers, and how many server calls each must
#: make at least (so a renamed call cannot leave the test checking nothing).
SCRIPTS = {
    os.path.join("consolidated_statement", "consolidated_statement.js"): 1,
    os.path.join("statement_drill", "statement_drill.js"): 0,
    os.path.join("consolidation_worksheet", "consolidation_worksheet.js"): 0,
}

_CALL = re.compile(r"frappe\.(?:call|xcall)\s*\(")
_METHOD = re.compile(r"""method\s*:\s*["']([\w.]+)["']""")
_OPEN_URL_POST = re.compile(r"open_url_post\s*\(")
_CMD = re.compile(r"""cmd\s*:\s*["']([\w.]+)["']""")
# open_url_post names its function either as a cmd parameter (POST to "/",
# deprecated in Frappe v16) or in the URL it posts to (/api/method/<dotted>).
_URL_METHOD = re.compile(r"""["'`]/api/method/([\w.]+)["'`]""")
_TYPE = re.compile(r"""type\s*:\s*["'](\w+)["']""")


def _call_bodies(source):
    """The argument text of each ``frappe.call(`` / ``frappe.xcall(``,
    up to its matching close parenthesis."""
    for match in _CALL.finditer(source):
        depth, i = 1, match.end()
        while depth and i < len(source):
            depth += {"(": 1, ")": -1}.get(source[i], 0)
            i += 1
        yield source[match.end():i - 1]


def _whitelist_methods(dotted):
    """The ``methods`` of ``@frappe.whitelist(...)`` on the function
    ``dotted`` names (``konsol.close.commentary_api.save_commentary``), or
    ``None`` when the function is missing or not whitelisted."""
    parts = dotted.split(".")
    assert parts[0] == "konsol", dotted
    path = os.path.join(APP_DIR, *parts[1:-1]) + ".py"
    assert os.path.exists(path), f"{dotted}: no module {path}"
    tree = ast.parse(open(path).read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == parts[-1]:
            for deco in node.decorator_list:
                if (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)
                        and deco.func.attr == "whitelist"):
                    for kw in deco.keywords:
                        if kw.arg == "methods":
                            return ast.literal_eval(kw.value)
                    return ["GET", "POST", "PUT", "DELETE"]  # frappe's default: any
            return None
    return None


def test_every_report_script_exists():
    for script in SCRIPTS:
        assert os.path.exists(os.path.join(REPORT_DIR, script)), script


def test_every_server_call_names_a_whitelisted_post_function_sent_as_post():
    for script, at_least in SCRIPTS.items():
        source = open(os.path.join(REPORT_DIR, script)).read()
        bodies = list(_call_bodies(source))
        assert len(bodies) >= at_least, f"{script}: expected at least {at_least} server call(s)"
        for body in bodies:
            method = _METHOD.search(body)
            assert method, f"{script}: a frappe.call without a literal method: {body[:80]!r}"
            methods = _whitelist_methods(method.group(1))
            assert methods is not None, f"{script}: {method.group(1)} is not a whitelisted function"
            assert list(methods) == ["POST"], f"{script}: {method.group(1)} allows {methods}"
            sent = _TYPE.search(body)
            assert sent and sent.group(1) == "POST", f"{script}: {method.group(1)} is not sent as POST"


def test_the_commentary_call_is_commentary_api_save_commentary():
    source = open(os.path.join(REPORT_DIR, "consolidated_statement", "consolidated_statement.js")).read()
    methods = [_METHOD.search(b).group(1) for b in _call_bodies(source)]
    assert "konsol.close.commentary_api.save_commentary" in methods


def _open_url_post_bodies(source):
    """The argument text of each ``open_url_post(`` (a form POST that
    downloads a file), up to its matching close parenthesis."""
    for match in _OPEN_URL_POST.finditer(source):
        depth, i = 1, match.end()
        while depth and i < len(source):
            depth += {"(": 1, ")": -1}.get(source[i], 0)
            i += 1
        yield source[match.end():i - 1]


def _open_url_post_method(body):
    """The dotted function an ``open_url_post`` body names: the URL form
    (``"/api/method/<dotted>"``, first argument) or a literal ``cmd``."""
    found = _URL_METHOD.search(body) or _CMD.search(body)
    return found.group(1) if found else None


def test_every_open_url_post_names_a_whitelisted_post_function():
    """``open_url_post`` is a POST by construction, so the function it names
    (in the URL or a ``cmd``) must accept exactly POST (a GET-only function
    would refuse it; the default any-method one is not pinned)."""
    for script in SCRIPTS:
        source = open(os.path.join(REPORT_DIR, script)).read()
        for body in _open_url_post_bodies(source):
            method = _open_url_post_method(body)
            assert method, f"{script}: an open_url_post without a literal method: {body[:80]!r}"
            methods = _whitelist_methods(method)
            assert methods is not None, f"{script}: {method} is not a whitelisted function"
            assert list(methods) == ["POST"], f"{script}: {method} allows {methods}"


def test_open_url_post_helper_reads_the_url_form_and_the_cmd_form():
    assert _open_url_post_method('"/api/method/konsol.a.b", {x: 1}') == "konsol.a.b"
    assert _open_url_post_method('frappe.request.url, {cmd: "konsol.c.d"}') == "konsol.c.d"
    assert _open_url_post_method("frappe.request.url, {x: 1}") is None


def test_the_statement_export_button_posts_export_statement():
    source = open(os.path.join(REPORT_DIR, "consolidated_statement", "consolidated_statement.js")).read()
    methods = [_open_url_post_method(b) for b in _open_url_post_bodies(source)]
    assert "konsol.close.statement_api.export_statement" in methods


# --- Behaviour of the filters' on_change, run in node with a stubbed Desk ---

_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const [, , script, report, startBasis, newBasis] = process.argv;
const calls = [];
const values = { basis: startBasis };
const dfs = {};
global.__ = (s) => s;
global.frappe = {
  query_reports: {},
  router: { make_url: () => "" },
  utils: { escape_html: (s) => s },
  ui: {},
};
frappe.query_report = {
  data: [],
  get_filter_value: (name) => values[name] || "",
  get_filter: (name) => ({ df: dfs[name], refresh: () => calls.push("refresh_" + name) }),
  refresh: () => calls.push("refresh"),
};
vm.runInThisContext(fs.readFileSync(script, "utf8"));
const def = frappe.query_reports[report];
for (const df of def.filters) dfs[df.fieldname] = df;
const initial = Object.fromEntries(Object.entries(dfs).map(([k, df]) => [k, df.reqd || 0]));
values.basis = newBasis;
dfs.basis.on_change(frappe.query_report);
const after = Object.fromEntries(Object.entries(dfs).map(([k, df]) => [k, df.reqd || 0]));
console.log(JSON.stringify({ initial, after, calls }));
"""

def run_basis_change(script, report, start, new):
    """Load a report script in node, set Basis to ``new`` and call the
    Basis filter's ``on_change`` as Frappe does. Returns the filters' ``reqd``
    before and after, and the report calls made."""
    assert shutil.which("node"), "node is needed to run the report scripts"
    path = os.path.join(REPORT_DIR, script)
    out = subprocess.run(["node", "-", path, report, start, new], input=_HARNESS,
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


# Opening a report from a link: Frappe sets the route filters, then calls the
# report's onload, then refreshes (query_report.js refresh_report). The first
# run therefore happens before any Basis on_change.
_ONLOAD_HARNESS = _HARNESS.split("const initial")[0] + r"""
values.basis = newBasis;
const hasOnload = typeof def.onload === "function";
if (hasOnload) def.onload(frappe.query_report);
const after = Object.fromEntries(Object.entries(dfs).map(([k, df]) => [k, df.reqd || 0]));
console.log(JSON.stringify({ hasOnload, after, calls }));
"""


def run_onload(script, report, basis, entity=""):
    """Load a report script in node, preset the Basis filter to ``basis`` (as
    ``set_route_filters`` does) and call the report's ``onload``. Returns
    whether an onload exists, the filters' ``reqd`` after it, and the report
    calls made (a refresh here would be a double run: Frappe refreshes next)."""
    assert shutil.which("node"), "node is needed to run the report scripts"
    path = os.path.join(REPORT_DIR, script)
    out = subprocess.run(["node", "-", path, report, "", basis], input=_ONLOAD_HARNESS,
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


# Opening a report from a link, modelled on Frappe v16 query_report.js
# refresh_report: _no_refresh = true; set_route_filters sets each route value
# in filter order, and a changed value runs the filter's onchange, which calls
# the filter's on_change (if any) instead of Frappe's own refresh; then
# onload; _no_refresh = false; refresh. The steps run in frappe.run_serially,
# so a throw (refresh() throws "Filter missing" when a reqd filter is blank,
# get_filter_values(true)) skips every later step: the later route values
# are never set.
_ROUTE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const [, , script, report, routeJson] = process.argv;
const route = JSON.parse(routeJson);
const values = {};
const dfs = {};
const runs = [];
global.__ = (s) => s;
global.frappe = {
  query_reports: {},
  router: { make_url: () => "" },
  utils: { escape_html: (s) => s },
  ui: {},
};
const qr = {
  data: [],
  _no_refresh: false,
  get_filter_value: (name) => values[name] || "",
  get_filter: (name) => ({ df: dfs[name], refresh: () => {} }),
  refresh: () => {
    const missing = Object.values(dfs).filter((df) => df.reqd && !values[df.fieldname])
      .map((df) => df.fieldname);
    runs.push(missing.length ? { missing } : { ran: { ...values } });
    if (missing.length) throw "Filter missing";
  },
};
frappe.query_report = qr;
vm.runInThisContext(fs.readFileSync(script, "utf8"));
const def = frappe.query_reports[report];
for (const df of def.filters) {
  dfs[df.fieldname] = df;
  if (df.default !== undefined) values[df.fieldname] = df.default;
}
let error = null;
try {
  qr._no_refresh = true;
  for (const df of def.filters) {
    if (!(df.fieldname in route) || values[df.fieldname] === route[df.fieldname]) continue;
    values[df.fieldname] = route[df.fieldname];
    if (df.on_change) df.on_change(qr);
  }
  if (def.onload) def.onload(qr);
  qr._no_refresh = false;
  qr.refresh();
} catch (e) {
  error = String(e);
}
console.log(JSON.stringify({ values, runs, error }));
"""


def run_route(script, report, route):
    """Open a report script in node from a link carrying ``route`` (filter
    name -> value), as Frappe's refresh_report does. Returns the final filter
    values, every refresh (``{"ran": values}`` or ``{"missing": [names]}``)
    and the error that stopped the sequence, if any."""
    assert shutil.which("node"), "node is needed to run the report scripts"
    path = os.path.join(REPORT_DIR, script)
    out = subprocess.run(["node", "-", path, report, json.dumps(route)], input=_ROUTE_HARNESS,
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


_DRILL = ("statement_drill/statement_drill.js", "Statement Drill")
_STATEMENT = ("consolidated_statement/consolidated_statement.js", "Consolidated Statement")


def test_drill_link_on_the_entity_basis_sets_every_filter_then_runs_once():
    # Consolidated Statement's entity heading link. A refresh from the Basis
    # on_change during route application threw "Filter missing" (entity not
    # set yet) and aborted it: entity and heading were never set.
    route = {"fiscal_year": 2024, "fiscal_period": 12, "basis": "Entity",
             "entity": "ZZOP", "heading": "4"}
    got = run_route(*_DRILL, route)
    assert got["error"] is None, got
    assert got["values"]["entity"] == "ZZOP" and got["values"]["heading"] == "4", got
    assert got["runs"] == [{"ran": got["values"]}], got


def test_drill_link_on_the_group_basis_runs_once():
    route = {"fiscal_year": 2024, "fiscal_period": 12, "consolidation_group": "CG-ZZ",
             "heading": "4"}
    got = run_route(*_DRILL, route)
    assert got["error"] is None, got
    assert got["values"]["consolidation_group"] == "CG-ZZ", got
    assert got["runs"] == [{"ran": got["values"]}], got


def test_statement_link_on_the_entity_basis_without_an_entity_asks_for_it():
    # Frappe's inline "Please set filters" (the final refresh finds entity
    # blank), reached at the end of the sequence, not in the middle of it.
    got = run_route(*_STATEMENT, {"basis": "Entity"})
    assert got["runs"] == [{"missing": ["entity"]}], got
    assert got["error"] == "Filter missing", got


def test_statement_link_on_the_entity_basis_with_an_entity_runs_once():
    got = run_route(*_STATEMENT, {"basis": "Entity", "entity": "ZZOP"})
    assert got["error"] is None, got
    assert got["values"]["entity"] == "ZZOP", got
    assert got["runs"] == [{"ran": got["values"]}], got
