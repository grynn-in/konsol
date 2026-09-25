"""Checks API: konsol/close/checks_api.py (konsol#305 A26; stories 7.1-7.4).

`get_checks(fiscal_year, fiscal_period)` (GET) returns the latest Assertion Run
for the period, whether its results are stale against the consolidated
numbers, the failures grouped by domain and cause (A13 `by_cause`), and
whether the caller may run the checks. `run_checks` (POST) delegates to
`trigger_close_run`.

Loaded against a stub frappe (pattern: test_assertion_warn_amber.py `_load`).
`trigger_close_run`, `latest_close_run`, `_manifest_nodes` and
`TERMINAL_STATUSES` are the REAL definitions, compiled out of
assertion_run.py's source against the stub, so the "already in progress" guard
and the role gate under test are the product's, not a copy.
"""
import ast
import importlib.util
import json
import os
import sys
import tempfile
import types
from datetime import datetime

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PY = os.path.join(APP_DIR, "close", "checks_api.py")
MODEL_PY = os.path.join(APP_DIR, "close", "checks_model.py")
ASSERTION_RUN_PY = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run",
                                "assertion_run.py")
REAL_FROM_ASSERTION_RUN = ("trigger_close_run", "latest_close_run", "_manifest_nodes",
                           "TERMINAL_STATUSES")

ALL_CLOSE_ROLES = ("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager")
RUNNERS = ("EPM Admin", "EPM Analyst", "System Manager")
STEP_FIELDS = {"assertion", "dimension", "status", "rows_failed", "severity", "message"}


def _dt(day, hour=0):
    return datetime(2026, 9, day, hour, 0, 0)


def _manifest(nodes):
    """A dbt project dir with target/manifest.json holding `nodes`."""
    path = tempfile.mkdtemp(prefix="zz_a26_")
    os.makedirs(os.path.join(path, "target"))
    with open(os.path.join(path, "target", "manifest.json"), "w") as fh:
        json.dump({"nodes": nodes}, fh)
    return path


def _test_node(name, description=""):
    return {"name": name, "resource_type": "test", "description": description}


class _Site:
    def __init__(self, runs=(), steps=None, roles=("EPM User",), as_of=None,
                 project_path=None):
        self.runs = [dict(r) for r in runs]
        self.steps = steps or {}  # run name -> [step dict incl. sample_rows]
        self.roles = set(roles)
        self.as_of = as_of  # ISO string or None, as current_freshness returns it
        self.project_path = project_path
        self.only_for_calls = []
        self.step_field_calls = []
        self.enqueued = []
        self.commits = 0


def _match(row, filters):
    for key, cond in (filters or {}).items():
        if isinstance(cond, (list, tuple)):
            op, values = cond
            assert op == "in", op
            if row.get(key) not in values:
                return False
        elif row.get(key) != cond:
            return False
    return True


def _order(rows, order_by):
    for part in reversed([p.strip() for p in (order_by or "").split(",") if p.strip()]):
        field, _, direction = part.partition(" ")
        rows = sorted(rows, key=lambda r: (r.get(field) is not None, r.get(field) or datetime.min),
                      reverse=direction.strip().lower() == "desc")
    return rows


def _frappe(site):
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, **k):
        raise (exc or frappe.ValidationError)(msg)

    def only_for(roles, message=False):
        roles = [roles] if isinstance(roles, str) else list(roles)
        site.only_for_calls.append(tuple(roles))
        if not site.roles.intersection(roles):
            raise frappe.PermissionError("Not permitted")

    def whitelist(*a, **k):
        return lambda fn: fn

    def get_all(doctype, filters=None, fields=None, order_by=None, limit=None,
                limit_page_length=None, **k):
        if doctype == "Assertion Step":
            site.step_field_calls.append(list(fields or []))
            assert (filters or {}).get("parenttype") in (None, "Assertion Run"), filters
            rows = site.steps.get((filters or {}).get("parent"), [])
            return [{f: r.get(f) for f in fields} for r in rows]
        assert doctype == "Assertion Run", doctype
        rows = _order([r for r in site.runs if _match(r, filters)], order_by)
        n = limit or limit_page_length
        rows = rows[:n] if n else rows
        return [{f: r.get(f) for f in fields} for r in rows]

    def get_value(doctype, filters, fieldname, **k):
        assert doctype == "Assertion Run", doctype
        rows = [r for r in site.runs if _match(r, filters)]
        return rows[0][fieldname] if rows else None

    class _Doc:
        def __init__(self, data):
            self.__dict__.update(data)
            self.flags = types.SimpleNamespace()

        def insert(self, ignore_permissions=False):
            self.name = f"ZZ-RUN-{len(site.runs) + 1}"
            site.runs.append({"name": self.name, "status": self.status,
                              "fiscal_year": self.fiscal_year,
                              "fiscal_period": self.fiscal_period,
                              "triggered_by": self.triggered_by,
                              "creation": _dt(30), "completed_at": None})
            return self

    def commit():
        site.commits += 1

    def enqueue(method, **kw):
        site.enqueued.append((method, kw))

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.only_for = only_for
    frappe.whitelist = whitelist
    frappe.get_all = get_all
    frappe.get_doc = lambda data: _Doc(data)
    frappe.get_roles = lambda user=None: sorted(site.roles)
    frappe.get_single = lambda dt: types.SimpleNamespace(dbt_project_path=site.project_path)
    frappe.enqueue = enqueue
    frappe.db = types.SimpleNamespace(get_value=get_value, commit=commit)
    frappe.session = types.SimpleNamespace(user="zz-analyst@example.com")
    frappe.utils = types.SimpleNamespace(now=lambda: "2026-09-30 00:00:00")
    return frappe


def _assertion_run_module(frappe):
    """The real definitions named in REAL_FROM_ASSERTION_RUN, compiled from
    assertion_run.py's source against the stub frappe."""
    with open(ASSERTION_RUN_PY) as fh:
        tree = ast.parse(fh.read())
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in REAL_FROM_ASSERTION_RUN:
            body.append(node)
        elif isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) in REAL_FROM_ASSERTION_RUN for t in node.targets):
            body.append(node)
    module = types.ModuleType("konsol.consolidation.doctype.assertion_run.assertion_run")
    module.__dict__.update({"frappe": frappe, "os": os, "json": json})
    exec(compile(ast.Module(body=body, type_ignores=[]), ASSERTION_RUN_PY, "exec"), module.__dict__)
    missing = [n for n in REAL_FROM_ASSERTION_RUN if n not in module.__dict__]
    assert not missing, f"assertion_run.py no longer defines {missing}"
    return module


def _call(site, fn, *args):
    frappe = _frappe(site)
    spec = importlib.util.spec_from_file_location("konsol.close.checks_model", MODEL_PY)
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    freshness_api = types.ModuleType("konsol.close.freshness_api")
    freshness_api.current_freshness = lambda: {"state": "fresh", "as_of": site.as_of,
                                               "pending": 0, "changed_since": [],
                                               "last_failed": None}
    assertion_run = _assertion_run_module(frappe)
    names = ["konsol", "konsol.close", "konsol.consolidation", "konsol.consolidation.doctype",
             "konsol.consolidation.doctype.assertion_run"]
    mods = {n: types.ModuleType(n) for n in names}
    mods.update({
        "frappe": frappe,
        "konsol.close.checks_model": model,
        "konsol.close.freshness_api": freshness_api,
        "konsol.consolidation.doctype.assertion_run.assertion_run": assertion_run,
    })
    mods["konsol.close"].checks_model = model
    mods["konsol.close"].freshness_api = freshness_api
    mods["konsol.consolidation.doctype.assertion_run"].assertion_run = assertion_run
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("close_checks_api_under_test", API_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return getattr(module, fn)(*args), frappe
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _run(name, status, creation, completed_at=None, year=2026, period=9):
    return {"name": name, "status": status, "fiscal_year": year, "fiscal_period": period,
            "creation": creation, "completed_at": completed_at}


def _step(assertion, dimension, status, rows_failed=0):
    return {"assertion": assertion, "dimension": dimension, "status": status,
            "rows_failed": rows_failed, "severity": "error", "message": f"{assertion} said so",
            "failures_table": "zz_tbl", "sample_rows": "SECRET permlevel-1 rows"}


def _raises(fn, exc_name):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        assert type(e).__name__ == exc_name, f"{type(e).__name__}: {e}"
        return str(e)
    raise AssertionError(f"expected {exc_name}")


# --- get_checks ----------------------------------------------------------------

STEPS = [
    _step("assert_fx_rate_present", "FX", "Fail", 3),
    _step("assert_ownership_sums", "Ownership", "Pass"),
    _step("assert_ic_balances", "Consolidation", "Warn", 2),
    _step("assert_null_accounts", "Data Quality", "Error"),
]


def test_failures_are_grouped_by_domain_and_cause():
    path = _manifest({
        "test.open_epm.assert_fx_rate_present.1": _test_node("assert_fx_rate_present",
                                                              "Every entity currency has a rate."),
        "test.open_epm.assert_null_accounts.2": _test_node("assert_null_accounts", "  "),
    })
    site = _Site(runs=[_run("RUN-1", "Red", _dt(10), _dt(10, 1))], steps={"RUN-1": STEPS},
                 roles=("EPM Analyst",), as_of=_dt(9).isoformat(), project_path=path)
    out, _ = _call(site, "get_checks", 2026, 9)
    assert out["latest"] == {"name": "RUN-1", "status": "Red",
                             "completed_at": _dt(10, 1).isoformat()}
    assert out["staleness"] == "current"
    assert out["results_run"] == "RUN-1"
    assert [d["domain"] for d in out["domains"]] == ["FX", "Consolidation", "Data Quality"]
    fx = out["domains"][0]["failures"][0]
    assert fx["assertion"] == "assert_fx_rate_present"
    assert fx["rows_failed"] == 3
    assert fx["description"] == "Every entity currency has a rate."
    assert fx["description_missing"] is False
    dq = out["domains"][2]["failures"][0]
    assert dq["description"] is None and dq["description_missing"] is True
    ic = out["domains"][1]
    assert ic["count"] == 0 and ic["warn_count"] == 1
    assert out["failures"] == 2 and out["warnings"] == 1
    assert out["can_run"] is True
    json.dumps(out)  # JSON-safe


def test_sample_rows_is_never_requested():
    site = _Site(runs=[_run("RUN-1", "Red", _dt(10), _dt(10, 1))], steps={"RUN-1": STEPS},
                 project_path=_manifest({}))
    out, _ = _call(site, "get_checks", 2026, 9)
    assert site.step_field_calls, "the steps were not read"
    for fields in site.step_field_calls:
        assert set(fields) == STEP_FIELDS, fields
        assert "sample_rows" not in fields and "failures_table" not in fields
    assert "SECRET" not in json.dumps(out)


def test_a_run_that_completed_before_the_numbers_were_built_is_stale():
    site = _Site(runs=[_run("RUN-1", "Green", _dt(10), _dt(10, 1))], steps={"RUN-1": []},
                 as_of=_dt(11).isoformat(), project_path=_manifest({}))
    out, _ = _call(site, "get_checks", 2026, 9)
    assert out["staleness"] == "stale"
    assert out["as_of"] == _dt(11).isoformat()


def test_the_latest_run_is_the_newest_by_creation_whatever_its_status():
    site = _Site(
        runs=[_run("RUN-1", "Red", _dt(10), _dt(10, 1)),
              _run("RUN-2", "Running", _dt(11)),
              _run("RUN-X", "Green", _dt(12), _dt(12, 1), period=8)],
        steps={"RUN-1": STEPS, "RUN-2": [_step("assert_other", "Other", "Fail")]},
        project_path=_manifest({}))
    out, _ = _call(site, "get_checks", 2026, 9)
    assert out["latest"] == {"name": "RUN-2", "status": "Running", "completed_at": None}
    assert out["staleness"] == "running"
    # Steps come from the latest terminal run of this period, not the one in flight.
    assert out["results_run"] == "RUN-1"
    assert [d["domain"] for d in out["domains"]] == ["FX", "Consolidation", "Data Quality"]


def test_a_viewer_reads_the_checks_but_may_not_run_them():
    for role in ("EPM User", "Entity Accountant"):
        site = _Site(runs=[_run("RUN-1", "Green", _dt(10), _dt(10, 1))], steps={"RUN-1": []},
                     roles=(role,), project_path=_manifest({}))
        out, _ = _call(site, "get_checks", 2026, 9)
        assert out["can_run"] is False, role
        assert set(site.only_for_calls[0]) == set(ALL_CLOSE_ROLES)
    for role in RUNNERS:
        site = _Site(roles=(role,), project_path=_manifest({}))
        out, _ = _call(site, "get_checks", 2026, 9)
        assert out["can_run"] is True, role


def test_a_role_outside_the_close_is_refused():
    site = _Site(roles=("Guest",), project_path=_manifest({}))
    _raises(lambda: _call(site, "get_checks", 2026, 9), "PermissionError")


def test_no_run_at_all_is_not_run():
    site = _Site(project_path=_manifest({}))
    out, _ = _call(site, "get_checks", 2026, 9)
    assert out["latest"] is None
    assert out["staleness"] == "not_run"
    assert out["results_run"] is None
    assert out["domains"] == []
    assert site.step_field_calls == []


def test_period_arguments_from_a_query_string_are_read_as_integers():
    site = _Site(runs=[_run("RUN-1", "Green", _dt(10), _dt(10, 1))], steps={"RUN-1": []},
                 project_path=_manifest({}))
    out, _ = _call(site, "get_checks", "2026", "9")
    assert out["latest"]["name"] == "RUN-1"


# --- run_checks ----------------------------------------------------------------

def test_the_analyst_runs_the_checks_through_trigger_close_run():
    site = _Site(roles=("EPM Analyst",))
    name, _ = _call(site, "run_checks", 2026, 9)
    assert name == "ZZ-RUN-1"
    assert site.runs[-1]["status"] == "Queued"
    assert site.runs[-1]["triggered_by"] == "zz-analyst@example.com"
    assert site.enqueued and site.enqueued[0][1]["close_run"] == "ZZ-RUN-1"
    assert set(site.only_for_calls[0]) == set(RUNNERS)


def test_run_checks_while_another_run_is_active_propagates_already_in_progress():
    site = _Site(runs=[_run("RUN-1", "Running", _dt(10))], roles=("EPM Admin",))
    msg = _raises(lambda: _call(site, "run_checks", 2026, 9), "ValidationError")
    assert "already in progress" in msg and "RUN-1" in msg
    assert len(site.runs) == 1 and site.enqueued == []


def test_run_checks_as_a_viewer_or_entity_accountant_is_refused():
    for role in ("EPM User", "Entity Accountant"):
        site = _Site(roles=(role,))
        _raises(lambda: _call(site, "run_checks", 2026, 9), "PermissionError")
        assert site.runs == [] and site.enqueued == [] and site.commits == 0, role
        # Refused by run_checks' own gate, before trigger_close_run is reached.
        assert site.only_for_calls == [RUNNERS], site.only_for_calls
