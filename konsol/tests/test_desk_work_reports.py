"""The My Work Desk report: structure (no frappe needed).

The Approvals report was removed on 5 Nov 2026: approvals are done in each
record's Actions menu (test_desk_approval_actions.py).

- Every ``frappe.call`` method the reports' JS names is a function that
  exists and is ``@frappe.whitelist(methods=["POST"])``: a GET would roll
  the approve back (CLAUDE.md), and a misspelt path fails only at a click.
- Each report's roles are the roles its API admits (``frappe.only_for``),
  and each of them holds report permission on the report's ref_doctype
  (Frappe refuses the report otherwise): a listed role that is always
  refused is a bug.
"""
import ast
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(APP_DIR, "consolidation", "report")
REPORTS = {
    # report folder: (module file of the API, the API function the report reads)
    "my_work": (os.path.join("close", "mywork_api.py"), "get_my_work"),
}


def _read(path):
    with open(path) as f:
        return f.read()


def _js(folder):
    return _read(os.path.join(REPORT_DIR, folder, folder + ".js"))


def _report_json(folder):
    return json.loads(_read(os.path.join(REPORT_DIR, folder, folder + ".json")))


def _methods(js):
    return re.findall(r"""method:\s*["']([\w.]+)["']""", js)


def _function(dotted):
    """``(FunctionDef, module path)`` for a dotted ``konsol.x.y.fn``, or ``(None, path)``."""
    parts = dotted.split(".")
    assert parts[0] == "konsol", dotted
    path = os.path.join(APP_DIR, *parts[1:-1]) + ".py"
    if not os.path.exists(path):
        return None, path
    tree = ast.parse(_read(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == parts[-1]:
            return node, path
    return None, path


def _whitelist_methods(fn):
    """The ``methods`` list of ``fn``'s ``@frappe.whitelist(...)``, or None if not whitelisted."""
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Call) and ast.unparse(dec.func) == "frappe.whitelist":
            for kw in dec.keywords:
                if kw.arg == "methods":
                    return [e.value for e in kw.value.elts]
            return []
        if ast.unparse(dec) == "frappe.whitelist":
            return []
    return None


def _only_for_roles(module_file, fn_name):
    tree = ast.parse(_read(os.path.join(APP_DIR, module_file)))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == fn_name)
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "frappe.only_for":
            return {e.value for e in node.args[0].elts}
    raise AssertionError("%s.%s has no frappe.only_for" % (module_file, fn_name))


def _doctype_json(name):
    folder = name.lower().replace(" ", "_")
    for module in os.listdir(APP_DIR):
        path = os.path.join(APP_DIR, module, "doctype", folder, folder + ".json")
        if os.path.exists(path):
            return json.loads(_read(path))
    raise AssertionError("no doctype JSON for %s" % name)


def test_every_frappe_call_method_is_a_whitelisted_post_function():
    for folder in REPORTS:
        for method in _methods(_js(folder)):
            fn, path = _function(method)
            assert fn is not None, "%s.js calls %s, which does not exist (%s)" % (folder, method, path)
            methods = _whitelist_methods(fn)
            assert methods is not None, "%s is not whitelisted" % method
            assert methods == ["POST"], "%s must be POST-only, is %r" % (method, methods)


def test_every_frappe_call_in_the_reports_says_post():
    for folder in REPORTS:
        js = _js(folder)
        calls = js.count("frappe.call(")
        assert calls == len(_methods(js))
        assert js.count('type: "POST"') == calls


def test_my_work_writes_nothing():
    assert _methods(_js("my_work")) == []


def test_report_roles_are_the_apis_and_can_report_on_ref_doctype():
    for folder, (module_file, fn_name) in REPORTS.items():
        report = _report_json(folder)
        assert report["report_type"] == "Script Report"
        assert report["module"] == "Consolidation"
        roles = {r["role"] for r in report["roles"]}
        assert roles == _only_for_roles(module_file, fn_name), folder
        doctype = _doctype_json(report["ref_doctype"])
        can_report = {p["role"] for p in doctype["permissions"]
                      if not p.get("permlevel") and p.get("read") and p.get("report")}
        assert roles <= can_report, "%s: %s cannot report on %s" % (
            folder, sorted(roles - can_report), report["ref_doctype"])


def test_reports_read_the_api_and_never_the_close_app():
    for folder, (module_file, fn_name) in REPORTS.items():
        py = _read(os.path.join(REPORT_DIR, folder, folder + ".py"))
        assert fn_name + "(" in py, folder
        assert "/close" not in py and "/close" not in _js(folder), folder
