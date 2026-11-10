"""Desk report scripts call only whitelisted POST functions.

A GET request rolls back at the end (CLAUDE.md), so every server call a
Desk report script makes to write must name a function that exists, is
``@frappe.whitelist(methods=["POST"])``, and is sent as a POST. This reads
each report script's ``frappe.call`` / ``frappe.xcall`` and checks the
named function's source.
"""
import ast
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(APP_DIR, "consolidation", "report")

#: The report scripts this test covers, and how many server calls each must
#: make at least (so a renamed call cannot leave the test checking nothing).
SCRIPTS = {
    os.path.join("consolidated_statement", "consolidated_statement.js"): 1,
    os.path.join("statement_drill", "statement_drill.js"): 0,
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

