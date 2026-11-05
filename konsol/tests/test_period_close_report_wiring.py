"""Period Close Desk report: the wiring a host test can hold.

- Every ``frappe.call`` method in the report JS (Period Close, and the Run
  checks button on Close Assertions) names an existing function that is
  ``@frappe.whitelist(methods=["POST"])``: the buttons write, and a GET
  request rolls back at its end.
- Period Close calls exactly the close app's five action endpoints.
- The report's roles are the roles every read endpoint it calls admits (their
  ``frappe.only_for`` literals), and each of them has report permission on the
  report's ``ref_doctype``: Frappe refuses a report without it, so a listed
  role without it would always be refused.
"""
import ast
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(APP_DIR, "consolidation", "report")
PERIOD_CLOSE = os.path.join(REPORTS, "period_close")
CLOSE_ASSERTIONS = os.path.join(REPORTS, "close_assertions")

ACTIONS = {
    "konsol.close.checks_api.run_checks",
    "konsol.close.signoff_api.sign",
    "konsol.close.signoff_api.reject",
    "konsol.close.signoff_api.close_period",
    "konsol.close.signoff_api.reopen_period",
}
#: The read endpoints execute() calls; the report admits who all of them admit.
READS = (
    ("close/period_api.py", "get_context"),
    ("close/signoff_api.py", "get_signoff"),
    ("close/checks_api.py", "get_checks"),
)


def _read(path):
    with open(path) as f:
        return f.read()


def _methods(js_path):
    """Every ``method: "..."`` string in a report's JS."""
    return set(re.findall(r"""method\s*:\s*["']([\w.]+)["']""", _read(js_path)))


def _function(dotted):
    parts = dotted.split(".")
    assert parts[0] == "konsol", dotted
    path = os.path.join(APP_DIR, *parts[1:-1]) + ".py"
    assert os.path.exists(path), f"{dotted}: no module {path}"
    tree = ast.parse(_read(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == parts[-1]:
            return node
    raise AssertionError(f"{dotted}: no function {parts[-1]} in {path}")


def _whitelist_methods(fn):
    """The ``methods`` of the function's ``@frappe.whitelist(...)``, or None."""
    for dec in fn.decorator_list:
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "whitelist"):
            for kw in dec.keywords:
                if kw.arg == "methods":
                    return tuple(ast.literal_eval(kw.value))
            return ("GET", "POST")  # Frappe's default: any method
    return None


def _only_for(rel, name):
    fn = _function("konsol." + rel[:-3].replace("/", ".") + "." + name)
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "only_for"):
            return set(ast.literal_eval(node.args[0]))
    raise AssertionError(f"{name} has no frappe.only_for literal")


def _assert_post_whitelisted(methods):
    assert methods, "no frappe.call method found"
    for m in methods:
        assert _whitelist_methods(_function(m)) == ("POST",), f"{m} is not whitelisted POST-only"


def test_period_close_buttons_call_the_five_close_actions():
    assert _methods(os.path.join(PERIOD_CLOSE, "period_close.js")) == ACTIONS


def test_period_close_methods_are_whitelisted_post():
    _assert_post_whitelisted(_methods(os.path.join(PERIOD_CLOSE, "period_close.js")))


def test_close_assertions_run_checks_is_whitelisted_post():
    methods = _methods(os.path.join(CLOSE_ASSERTIONS, "close_assertions.js"))
    assert methods == {"konsol.close.checks_api.run_checks"}
    _assert_post_whitelisted(methods)


def test_period_close_posts_every_call():
    """frappe.call defaults to POST, but a ``type: "GET"`` would roll back."""
    js = _read(os.path.join(PERIOD_CLOSE, "period_close.js"))
    assert not re.search(r"""type\s*:\s*["']GET["']""", js)


def _report():
    return json.loads(_read(os.path.join(PERIOD_CLOSE, "period_close.json")))


def test_report_roles_are_who_the_read_endpoints_admit():
    admitted = set.intersection(*[_only_for(rel, name) for rel, name in READS])
    assert {r["role"] for r in _report()["roles"]} == admitted


def test_every_report_role_can_report_on_the_ref_doctype():
    report = _report()
    ref = report["ref_doctype"]
    folder = ref.lower().replace(" ", "_")
    matches = []
    for root, _dirs, files in os.walk(APP_DIR):
        if os.path.basename(root) == folder and f"{folder}.json" in files and "doctype" in root:
            matches.append(os.path.join(root, f"{folder}.json"))
    assert len(matches) == 1, f"ref_doctype {ref}: {matches}"
    perms = json.loads(_read(matches[0]))["permissions"]
    can_report = {p["role"] for p in perms if p.get("report") and not p.get("permlevel")}
    missing = {r["role"] for r in report["roles"]} - can_report
    assert not missing, f"{sorted(missing)} would be refused: no report permission on {ref}"


def test_report_is_a_standard_script_report_in_consolidation():
    report = _report()
    assert (report["name"], report["report_type"], report["module"], report["is_standard"]) == (
        "Period Close", "Script Report", "Consolidation", "Yes")
