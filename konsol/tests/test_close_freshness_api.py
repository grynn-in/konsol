"""Freshness API: konsol/close/freshness_api.py (konsol#305 A16; story 0.2).

`get_freshness()` reads Build Approval rows and the latest change per build
trigger doctype, and passes them through the A05 model. Loaded against a stub
frappe (pattern: test_assertion_warn_amber.py `_load`); the stub site answers
`db.sql` from per-doctype rows and honours the `docstatus IN (1,2)` filter, so
a draft-only change is really excluded by the query, not by the stub.
"""
import ast
import importlib.util
import os
import re
import sys
import types
from datetime import datetime

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PY = os.path.join(APP_DIR, "close", "freshness_api.py")
MODEL_PY = os.path.join(APP_DIR, "close", "freshness_model.py")

ALL_CLOSE_ROLES = {"EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager"}

TRIGGERS = [
    "Consolidation Group", "Consolidation Adjustment", "Ownership Period",
    "Historical Equity Rate", "IC Elimination Rule", "IC Balance",
    "Trial Balance Submission", "Group Exchange Rate",
]
SUBMITTABLE = {
    "Consolidation Adjustment", "Ownership Period", "Historical Equity Rate",
    "IC Balance", "Trial Balance Submission", "Group Exchange Rate",
}
BUILD_MAP = {dt: {"scope": "staging", "risk": "low"} for dt in TRIGGERS}
BUILD_MAP.update({
    "Entity": {"scope": "consolidation", "risk": "high"},
    "Trial Balance Submission": {"scope": "consolidation", "risk": "high"},
    "Group Exchange Rate": {"scope": "consolidation", "risk": "high"},
})
FLAGGED = ("Draft", "Pending Review", "Approved", "Running")


def _dt(day, hour=0):
    return datetime(2026, 9, day, hour, 0, 0)


class _Site:
    """Stub site: Build Approval rows for get_all, per-doctype records for db.sql."""

    def __init__(self, builds=(), records=None, roles=("EPM User",),
                 triggers=None, build_map=None):
        self.builds = list(builds)
        self.records = records or {}  # doctype -> [(docstatus, modified)]
        self.roles = set(roles)
        self.triggers = list(TRIGGERS if triggers is None else triggers)
        self.build_map = dict(BUILD_MAP if build_map is None else build_map)
        self.only_for_calls = []
        self.sql_calls = []
        self.whitelisted = set()


def _load(site):
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
        def deco(fn):
            site.whitelisted.add(fn.__name__)
            return fn
        return deco

    def get_all(doctype, filters=None, fields=None, **k):
        assert doctype == "Build Approval", doctype
        rows = site.builds
        state = (filters or {}).get("workflow_state")
        if state:
            op, values = state
            assert op == "in"
            rows = [b for b in rows if b["workflow_state"] in values]
        return [dict(b) for b in rows]

    def sql(query, values=None, as_dict=False, **k):
        site.sql_calls.append(query)
        m = re.search(r"`tab([^`]+)`", query)
        assert m, query
        dt = m.group(1)
        rows = site.records.get(dt, [])
        if re.search(r"docstatus\s+in\s*\(\s*1\s*,\s*2\s*\)", query, re.I):
            rows = [r for r in rows if r[0] in (1, 2)]
        latest = max((r[1] for r in rows), default=None)
        return [[latest]]

    frappe.throw = throw
    frappe.only_for = only_for
    frappe.whitelist = whitelist
    frappe.get_all = get_all
    frappe.db = types.SimpleNamespace(sql=sql)
    frappe.get_meta = lambda dt: types.SimpleNamespace(is_submittable=int(dt in SUBMITTABLE))
    frappe.session = types.SimpleNamespace(user="zz@example.com")

    konsol = types.ModuleType("konsol")
    close = types.ModuleType("konsol.close")
    spec = importlib.util.spec_from_file_location("konsol.close.freshness_model", MODEL_PY)
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    hooks = types.ModuleType("konsol.hooks")
    hooks._dbt_trigger_doctypes = site.triggers
    tasks = types.ModuleType("konsol.tasks")
    tasks.DOCTYPE_BUILD_MAP = site.build_map
    build_lock = types.ModuleType("konsol.build_lock")
    build_lock.FLAGGED_STATES = FLAGGED
    konsol.hooks, konsol.tasks, konsol.build_lock, konsol.close = hooks, tasks, build_lock, close
    close.freshness_model = model

    mods = {"frappe": frappe, "konsol": konsol, "konsol.close": close,
            "konsol.close.freshness_model": model, "konsol.hooks": hooks,
            "konsol.tasks": tasks, "konsol.build_lock": build_lock}
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("close_freshness_api_under_test", API_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return module, frappe


def _call(site, fn="get_freshness"):
    """Run with the stub modules installed (the API imports lazily)."""
    module, frappe = _load(site)
    mods = {"frappe": frappe}
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        return getattr(module, fn)()
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _build(name, scope, state, at=None, error=None):
    return {"name": name, "build_scope": scope, "workflow_state": state,
            "completed_at": at, "error_message": error}


# --- pass-through ------------------------------------------------------------

def test_a_tb_submitted_after_the_last_consolidation_build_is_stale():
    site = _Site(
        builds=[_build("BA-1", "consolidation", "Completed", _dt(10))],
        records={"Trial Balance Submission": [(1, _dt(11))],
                 "Consolidation Group": [(0, _dt(9))]},
    )
    out = _call(site)
    assert out["state"] == "stale"
    assert out["changed_since"] == ["Trial Balance Submission"]
    assert out["as_of"] == _dt(10).isoformat()
    assert out["pending"] == 0
    assert out["last_failed"] is None


def test_every_change_covered_is_fresh():
    site = _Site(
        builds=[_build("BA-1", "full", "Completed", _dt(12))],
        records={"Trial Balance Submission": [(1, _dt(11))], "Entity": [(0, _dt(3))]},
    )
    out = _call(site)
    assert out == {"state": "fresh", "as_of": _dt(12).isoformat(), "pending": 0,
                   "changed_since": [], "last_failed": None}


def test_pending_builds_are_counted():
    site = _Site(builds=[
        _build("BA-1", "consolidation", "Completed", _dt(10)),
        _build("BA-2", "staging", "Pending Review"),
        _build("BA-3", "consolidation", "Pending Review"),
    ])
    out = _call(site)
    assert out["state"] == "pending"
    assert out["pending"] == 2


def test_a_failed_build_is_reported_with_its_reason_as_json_safe_values():
    site = _Site(builds=[
        _build("BA-1", "consolidation", "Completed", _dt(10)),
        _build("BA-2", "consolidation", "Failed", _dt(11), "dbt exit 2"),
    ])
    out = _call(site)
    assert out["state"] == "failed"
    assert out["last_failed"] == {"name": "BA-2", "at": _dt(11).isoformat(),
                                  "reason": "dbt exit 2"}


def test_cancelled_builds_are_not_read():
    site = _Site(builds=[_build("BA-1", "consolidation", "Cancelled", None)])
    out = _call(site)  # a Cancelled row with no completed_at must not raise
    assert out["state"] == "never_built"
    assert out["as_of"] is None


# --- which changes count -----------------------------------------------------

def test_draft_only_tb_changes_do_not_count():
    site = _Site(
        builds=[_build("BA-1", "consolidation", "Completed", _dt(10))],
        records={"Trial Balance Submission": [(1, _dt(9)), (0, _dt(15))]},
    )
    out = _call(site)
    assert out["state"] == "fresh", out
    assert out["changed_since"] == []


def test_a_cancelled_tb_counts_as_a_change():
    site = _Site(
        builds=[_build("BA-1", "consolidation", "Completed", _dt(10))],
        records={"Trial Balance Submission": [(2, _dt(15))]},
    )
    assert _call(site)["changed_since"] == ["Trial Balance Submission"]


def test_a_non_submittable_doctype_counts_every_save():
    site = _Site(
        builds=[_build("BA-1", "consolidation", "Completed", _dt(10))],
        records={"Consolidation Group": [(0, _dt(15))]},
    )
    assert _call(site)["changed_since"] == ["Consolidation Group"]


def test_entity_is_read_as_well_as_the_hook_triggers():
    site = _Site(
        builds=[_build("BA-1", "consolidation", "Completed", _dt(10))],
        records={"Entity": [(0, _dt(15))]},
    )
    assert _call(site)["changed_since"] == ["Entity"]
    queried = {re.search(r"`tab([^`]+)`", q).group(1) for q in site.sql_calls}
    assert queried == set(TRIGGERS) | {"Entity"}


def test_an_undeclared_trigger_doctype_raises_not_skipped():
    site = _Site(
        builds=[_build("BA-1", "consolidation", "Completed", _dt(10))],
        triggers=TRIGGERS + ["ZZ Unmapped Doctype"],
    )
    with pytest.raises(ValueError, match="ZZ Unmapped Doctype"):
        _call(site)


def test_an_undeclared_trigger_raises_even_with_no_records():
    # the check is on the declaration, not on whether the doctype has rows
    site = _Site(triggers=TRIGGERS + ["ZZ Unmapped Doctype"])
    with pytest.raises(ValueError, match="ZZ Unmapped Doctype"):
        _call(site)


# --- the real declarations agree ---------------------------------------------

def _literal_assignment(path, name):
    tree = ast.parse(open(path).read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


def test_every_real_trigger_doctype_is_in_the_build_map():
    triggers = _literal_assignment(os.path.join(APP_DIR, "hooks.py"), "_dbt_trigger_doctypes")
    build_map = _literal_assignment(os.path.join(APP_DIR, "tasks.py"), "DOCTYPE_BUILD_MAP")
    missing = [dt for dt in list(triggers) + ["Entity"] if dt not in build_map]
    assert missing == []


# --- access and shape --------------------------------------------------------

def test_the_endpoint_is_gated_on_every_close_role():
    site = _Site()
    _call(site)
    assert site.only_for_calls and set(site.only_for_calls[0]) == ALL_CLOSE_ROLES


def test_a_user_with_no_close_role_is_refused():
    site = _Site(roles=("Guest",))
    module, frappe = _load(site)
    with pytest.raises(frappe.PermissionError):
        module.get_freshness()


def test_current_freshness_is_a_helper_not_an_endpoint():
    site = _Site(builds=[_build("BA-1", "full", "Completed", _dt(12))])
    out = _call(site, "current_freshness")
    assert out["state"] == "fresh"
    assert "get_freshness" in site.whitelisted
    assert "current_freshness" not in site.whitelisted
