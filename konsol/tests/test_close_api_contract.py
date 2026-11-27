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

The second rule (a GET never writes) also covers the orchestrator and the
legacy pipeline trigger (konsol#473): `konsol/orchestrator/api.py` and
`konsol/pipeline/doctype/pipeline_run/pipeline_run.py`. The orchestrator
module imports on the host without frappe, so it decorates with its own
`whitelist` alias (`@whitelist(...)`), which the checker reads like
`@frappe.whitelist(...)`. The other two rules stay close-only: those modules
gate with `check_epm_admin()`, not `frappe.only_for`, and their reads declare
no method.
"""
import ast
import glob
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")

#: Modules outside konsol/close whose writes are also held to "a GET never
#: writes" (konsol#473): a GET request is rolled back at the end, so a write
#: reached by GET enqueues work and loses the record.
GET_NEVER_WRITES_FILES = (
    os.path.join(APP_DIR, "orchestrator", "api.py"),
    os.path.join(APP_DIR, "pipeline", "doctype", "pipeline_run", "pipeline_run.py"),
)

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
    """`@frappe.whitelist(...)` or a bare `@frappe.whitelist` (A01b: the bare
    form allows GET and declares no method, so it must not be skipped), and
    the orchestrator's module alias `@whitelist(...)` / `@whitelist`
    (konsol#473: `whitelist = frappe.whitelist` so the module imports on the
    host without frappe)."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Name):
        return target.id == "whitelist"
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "whitelist"
        and isinstance(target.value, ast.Name)
        and target.value.id == "frappe"
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
        if _is_whitelist_decorator(dec) and isinstance(dec, ast.Call):
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


#: Endpoints gated by the Frappe permission of the Desk reports they serve, not
#: by a fixed role list (Deepak Pai, 10 Nov 2026: a site may grant the three
#: period reports to other roles, who must not get a "Not permitted" dialog).
#: Each opens with ``if not <check>(): raise frappe.PermissionError``.
REPORT_PERMISSION_GATED = {("period_strip_api.py", "period_strip_info")}


def _problems_of(code, files=None):
    bad = []
    for path in files if files is not None else _api_files():
        with open(path) as f:
            source = f.read()
        rel = os.path.relpath(path, APP_DIR)
        for p in check_source(source, rel):
            if p["code"] != code:
                continue
            if code == "no_gate" and (os.path.basename(path), p["function"]) in REPORT_PERMISSION_GATED:
                continue
            bad.append(p["message"])
    return bad


def test_report_permission_gated_endpoints_open_with_the_raise():
    for filename, function in sorted(REPORT_PERMISSION_GATED):
        with open(os.path.join(CLOSE_DIR, filename)) as f:
            tree = ast.parse(f.read())
        fn = next(n for n in _whitelisted_functions(tree) if n.name == function)
        stmt = _first_statement(fn)
        assert _opens_with_the_report_check(stmt), (filename, function)


def _opens_with_the_report_check(stmt):
    """``if not _may_open_a_period_report(): raise frappe.PermissionError``."""
    if not (isinstance(stmt, ast.If) and isinstance(stmt.body[0], ast.Raise)):
        return False
    test = stmt.test
    named = (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
             and isinstance(test.operand, ast.Call) and isinstance(test.operand.func, ast.Name)
             and test.operand.func.id == "_may_open_a_period_report")
    return named and "PermissionError" in ast.unparse(stmt.body[0])


def test_the_exemption_check_rejects_a_gate_that_cannot_fire():
    def first(src):
        return ast.parse(src).body[0].body[0]
    good = "def f():\n    if not _may_open_a_period_report():\n        raise frappe.PermissionError\n"
    assert _opens_with_the_report_check(first(good))
    for bad in (good.replace("not _may_open_a_period_report()", "False"),
                good.replace("not _may_open_a_period_report()", "_may_open_a_period_report()"),
                good.replace("_may_open_a_period_report", "_other_check"),
                good.replace("PermissionError", "ValueError")):
        assert not _opens_with_the_report_check(first(bad)), bad


def test_the_close_package_exists():
    assert os.path.isfile(os.path.join(CLOSE_DIR, "__init__.py")), "konsol/close/__init__.py is missing"


def test_every_endpoint_declares_one_method():
    bad = _problems_of("bad_methods")
    assert not bad, "\n".join(bad)


def test_get_endpoints_do_not_write():
    bad = _problems_of("get_writes")
    assert not bad, "\n".join(bad)


def test_the_orchestrator_and_pipeline_trigger_files_exist():
    """If either module moves, the rule below would pass over nothing."""
    for path in GET_NEVER_WRITES_FILES:
        assert os.path.isfile(path), f"{os.path.relpath(path, APP_DIR)} is missing"


def test_orchestrator_and_pipeline_get_endpoints_do_not_write():
    """konsol#473: `start_run`, `retry_step`, `resume_run`, `cancel_run` and
    `trigger_pipeline` insert or save a Pipeline Run, commit and enqueue the
    worker, so each must declare `methods=["POST"]`; `get_run` and
    `launch_options` read only, so they may stay reachable by GET."""
    bad = _problems_of("get_writes", GET_NEVER_WRITES_FILES)
    assert not bad, "\n".join(bad)


def test_the_orchestrator_files_declare_endpoints():
    """The alias is read: the checker sees the orchestrator's `@whitelist`
    functions, so the rule above is checking something."""
    seen = {}
    for path in GET_NEVER_WRITES_FILES:
        with open(path) as f:
            tree = ast.parse(f.read())
        seen[os.path.basename(path)] = {fn.name for fn in _whitelisted_functions(tree)}
    assert {"start_run", "retry_step", "resume_run", "cancel_run", "get_run", "launch_options"} <= seen["api.py"], seen
    assert "trigger_pipeline" in seen["pipeline_run.py"], seen


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


def test_the_checker_catches_a_bare_decorator():
    """Failure path (A01b): `@frappe.whitelist` with no call allows GET and
    declares no method. It must be found, not skipped."""
    source = '''
import frappe


@frappe.whitelist
def bare_endpoint():
    frappe.get_doc("Test Doctype").insert()
'''
    codes = sorted(p["code"] for p in check_source(source, "bare.py"))
    assert codes == ["bad_methods", "get_writes", "no_gate"], codes


def test_the_checker_catches_the_whitelist_alias():
    """Failure path (konsol#473): a module that aliases `whitelist =
    frappe.whitelist` and decorates with `@whitelist()` must be read like
    `@frappe.whitelist()`, so a GET-reachable write behind the alias is found,
    not skipped — and `@whitelist(methods=["POST"])` clears it."""
    source = '''
import frappe

whitelist = frappe.whitelist


@whitelist()
def aliased_endpoint():
    frappe.get_doc("Test Doctype").insert()


@whitelist
def bare_aliased_endpoint():
    frappe.db.commit()


@whitelist(methods=["POST"])
def posted_endpoint():
    frappe.only_for("EPM Admin")
    frappe.get_doc("Test Doctype").insert()
'''
    problems = check_source(source, "alias.py")
    by_function = {}
    for p in problems:
        by_function.setdefault(p["function"], []).append(p["code"])
    assert sorted(by_function.get("aliased_endpoint", [])) == ["bad_methods", "get_writes", "no_gate"], problems
    assert sorted(by_function.get("bare_aliased_endpoint", [])) == ["bad_methods", "get_writes", "no_gate"], problems
    assert "posted_endpoint" not in by_function, problems


def test_no_endpoint_hides_outside_an_api_module():
    """A01b: every `@frappe.whitelist` in konsol/close lives in a `*_api.py`
    module, so the three rules above see every endpoint."""
    bad = []
    for path in _close_files():
        if path.endswith("_api.py"):
            continue
        with open(path) as f:
            tree = ast.parse(f.read())
        rel = os.path.relpath(path, APP_DIR)
        bad += [f"{rel}:{fn.name} is whitelisted outside a *_api.py module" for fn in _whitelisted_functions(tree)]
    assert not bad, "\n".join(bad)
