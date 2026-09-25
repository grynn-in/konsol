"""The endpoint contract for `konsol/close/*_api.py` (konsol#305 A01).

Every close-app endpoint is source-checked, the same way
`test_assertion_run_access.py` checks `trigger_close_run`: `ast`-parsed, no
frappe and no site needed. The three rules (from the close conventions in
tasks.md):

  - every `@frappe.whitelist` function declares exactly `methods=["GET"]` or
    `methods=["POST"]`;
  - a function a GET can reach (no `methods=` kwarg, or `"GET"` in it) never
    calls a write — `.insert(`, `.save(`, `.submit(`, `.cancel(`,
    `db.set_value`, `db.commit`, `frappe.enqueue`, `delete_doc`;
  - its first real statement (after the docstring and any lazy imports) is
    `frappe.only_for(...)`.

`konsol/close/*.py` also never imports the doctypes the close app replaces:
`home_api`, `control_api`, `home_model`.
"""
import ast
import glob
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")

#: Calls that only ever belong on a POST. Checked as source substrings inside
#: a single function's body, mirroring the plain-language grep the row asks
#: for rather than a full write-effect analysis.
FORBIDDEN_WRITES = (
    ".insert(",
    ".save(",
    ".submit(",
    ".cancel(",
    "db.set_value",
    "db.commit",
    "frappe.enqueue",
    "delete_doc",
)

#: Modules the close app must never import from (copy, do not import: R01/R02
#: delete the doctypes and APIs these name).
FORBIDDEN_IMPORTS = ("home_api", "control_api", "home_model")


def _is_whitelist_decorator(dec):
    return (
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr == "whitelist"
        and isinstance(dec.func.value, ast.Name)
        and dec.func.value.id == "frappe"
    )


def _whitelisted_functions(tree):
    """Every top-level function decorated with `@frappe.whitelist(...)`."""
    return [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and any(_is_whitelist_decorator(d) for d in n.decorator_list)
    ]


def _whitelist_methods(fn):
    """The `methods=` of the function's frappe.whitelist decorator, or None."""
    for dec in fn.decorator_list:
        if _is_whitelist_decorator(dec):
            for kw in dec.keywords:
                if kw.arg == "methods":
                    return ast.literal_eval(kw.value)
    return None


def _first_statement(fn):
    """The first real statement of the body — the docstring and the lazy
    `import frappe` some modules open with (they must import on the host, with
    no bench) are not statements a caller can be refused by."""
    body = fn.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    while body and isinstance(body[0], (ast.Import, ast.ImportFrom)):
        body = body[1:]
    assert body, f"{fn.name} has no body"
    return body[0]


def _only_for_roles(stmt):
    """The roles named by a `frappe.only_for(...)` statement, else None."""
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return None
    call = stmt.value
    if not (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "only_for"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "frappe"
    ):
        return None
    (arg,) = call.args
    roles = ast.literal_eval(arg)
    return {roles} if isinstance(roles, str) else set(roles)


def _is_get_capable(methods):
    """No explicit `methods=` kwarg, or `"GET"` named in it, means a GET
    request reaches the function."""
    return methods is None or "GET" in methods


def check_source(source, filename="<source>"):
    """Every contract problem in `source`, one entry per `@frappe.whitelist`
    function found. Each entry is `{"function", "code", "message"}`; `code` is
    one of `bad_methods`, `get_writes`, `no_gate`."""
    tree = ast.parse(source, filename=filename)
    problems = []
    for fn in _whitelisted_functions(tree):
        methods = _whitelist_methods(fn)
        if methods not in (["GET"], ["POST"]):
            problems.append(
                {
                    "function": fn.name,
                    "code": "bad_methods",
                    "message": (
                        f'{filename}:{fn.name} must declare methods=["GET"] or '
                        f"methods=[\"POST\"], not {methods!r}"
                    ),
                }
            )
        if _is_get_capable(methods):
            segment = ast.get_source_segment(source, fn) or ""
            hit = next((w for w in FORBIDDEN_WRITES if w in segment), None)
            if hit:
                problems.append(
                    {
                        "function": fn.name,
                        "code": "get_writes",
                        "message": (
                            f"{filename}:{fn.name} can be reached by GET and calls "
                            f"{hit} — writes must be behind methods=[\"POST\"]"
                        ),
                    }
                )
        stmt = _first_statement(fn)
        if _only_for_roles(stmt) is None:
            problems.append(
                {
                    "function": fn.name,
                    "code": "no_gate",
                    "message": f"{filename}:{fn.name} does not open with frappe.only_for(...)",
                }
            )
    return problems


def _api_files():
    return sorted(glob.glob(os.path.join(CLOSE_DIR, "*_api.py")))


def _close_files():
    return sorted(glob.glob(os.path.join(CLOSE_DIR, "*.py")))


def _problems_of(code):
    bad = []
    for path in _api_files():
        with open(path) as f:
            source = f.read()
        rel = os.path.relpath(path, APP_DIR)
        bad += [p["message"] for p in check_source(source, rel) if p["code"] == code]
    return bad


def test_the_close_package_exists():
    assert os.path.isfile(os.path.join(CLOSE_DIR, "__init__.py")), "konsol/close/__init__.py is missing"


def test_every_endpoint_declares_one_method():
    bad = _problems_of("bad_methods")
    assert not bad, "\n".join(bad)


def test_get_endpoints_do_not_write():
    bad = _problems_of("get_writes")
    assert not bad, "\n".join(bad)


def test_every_endpoint_opens_with_only_for():
    bad = _problems_of("no_gate")
    assert not bad, "\n".join(bad)


def test_no_close_module_imports_the_old_apis():
    bad = []
    for path in _close_files():
        with open(path) as f:
            source = f.read()
        rel = os.path.relpath(path, APP_DIR)
        for name in FORBIDDEN_IMPORTS:
            if name in source:
                bad.append(f"{rel} mentions {name}")
    assert not bad, "\n".join(bad)


def test_the_checker_catches_a_bad_endpoint():
    """Failure path: a bare `@frappe.whitelist()` (no declared method) whose
    body writes with `.insert(` while nothing rules out GET, and whose first
    statement is not a gate. Exactly those 3 problems must come back — no
    more, no fewer."""
    source = '''
import frappe


@frappe.whitelist()
def bad_endpoint():
    frappe.get_doc("Test Doctype").insert()
'''
    problems = check_source(source, "bad.py")
    codes = sorted(p["code"] for p in problems)
    assert codes == ["bad_methods", "get_writes", "no_gate"], problems
