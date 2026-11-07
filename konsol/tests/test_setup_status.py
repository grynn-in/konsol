"""Konsol Setup status endpoint: konsol/setup.py and the konsol-setup Page (konsol#407 phase 4).

``status()`` is loaded against a stub frappe and stub readers, so these tests
pin the wiring: who may call it (EPM Admin, System Manager; anyone else gets
PermissionError before anything is read), that it is GET and writes nothing,
that each step is fed by the existing reader that holds its rule, and that a
failing reader turns its own step "not done" without hiding the others.
The step rules themselves are in test_setup_model.py.
"""
import ast
import importlib.util
import json
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP_PY = os.path.join(APP_DIR, "setup.py")
MODEL_PY = os.path.join(APP_DIR, "setup_model.py")
PAGE_DIR = os.path.join(APP_DIR, "consolidation", "page", "konsol_setup")


class _Write(AssertionError):
    pass


def _refuse(name):
    def fn(*a, **k):
        raise _Write("status() wrote: frappe.%s" % name)
    return fn


class _Site:
    def __init__(self, roles=("EPM Admin",)):
        self.roles = set(roles)
        self.calls = []
        self.allowed = None
        self.raise_in = set()
        self.rate_gate_args = None
        self.counts = {"EPM Fiscal Year": 1, "Entity Fiscal Calendar": 0, "ISO Currency": 150,
                       ("Trial Balance Submission", 1): 3}
        self.records = {
            "Consolidation Group": [
                {"consolidation_group": "G1", "data_area_id": None, "reporting_currency": "EUR",
                 "ic_difference_account": "9999", "nci_account": "2900", "name": "CG-G1-"},
                {"consolidation_group": "G1", "data_area_id": "E1", "reporting_currency": "",
                 "ic_difference_account": "", "nci_account": "", "name": "CG-G1-E1"},
            ],
            "Entity": [{"name": "E1", "functional_currency": "EUR", "is_group": 0, "status": "Active"},
                       {"name": "E2", "functional_currency": "USD", "is_group": 0, "status": "Active"}],
            "Main Account": [{"main_account": "1000", "status": "Published", "is_group": 0,
                              "account_type": "Asset", "statement_section": "Balance Sheet",
                              "fx_method": "closing"}],
            "Dimension": [{"name": "region", "status": "Published", "in_budget": 1, "in_trial_balance": 1,
                           "permission_doctype": None}],
            "Has Role": [{"parent": "admin@zz.test", "role": "EPM Admin", "parenttype": "User"},
                         {"parent": "acc@zz.test", "role": "Entity Accountant", "parenttype": "User"}],
            "User": [{"name": "admin@zz.test", "enabled": 1}, {"name": "acc@zz.test", "enabled": 1}],
            "Trial Balance Submission": [{"fiscal_year": 2025, "fiscal_period": 3, "docstatus": 1}],
        }


def _matches(row, filters):
    for field, cond in (filters or {}).items():
        value = row.get(field)
        if isinstance(cond, list):
            op, arg = cond
            if op == "is":
                if (arg == "set") != bool(value):
                    return False
            elif op == "in":
                if value not in arg:
                    return False
            elif op == "!=":
                if value == arg:
                    return False
            else:
                raise AssertionError(op)
        elif value != cond:
            return False
    return True


def _load(site):
    frappe = types.ModuleType("frappe")
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    whitelisted = {}

    def whitelist(*a, **k):
        def deco(fn):
            whitelisted[fn.__name__] = k
            return fn
        return deco

    def only_for(roles, message=False):
        site.calls.append(("only_for", tuple(roles)))
        if not site.roles & set(roles):
            raise frappe.PermissionError("Not permitted")

    def get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, limit_page_length=None, **k):
        site.calls.append(("get_all", doctype))
        if doctype in site.raise_in:
            raise RuntimeError("%s unreadable" % doctype)
        rows = [r for r in site.records.get(doctype, []) if _matches(r, filters)]
        if pluck:
            return [r.get(pluck) for r in rows]
        if limit_page_length == 1:
            rows = sorted(rows, key=lambda r: (r["fiscal_year"], r["fiscal_period"]), reverse=True)[:1]
        return [types.SimpleNamespace(**r).__dict__ for r in rows]

    def count(doctype, filters=None):
        site.calls.append(("count", doctype))
        if filters:
            return site.counts.get((doctype, filters.get("docstatus")), 0)
        return site.counts.get(doctype, 0)

    frappe.whitelist = whitelist
    frappe.only_for = only_for
    frappe.get_all = get_all
    frappe.db = types.SimpleNamespace(count=count, commit=_refuse("db.commit"),
                                      set_value=_refuse("db.set_value"), sql=_refuse("db.sql"),
                                      delete=_refuse("db.delete"))
    for name in ("get_doc", "new_doc", "delete_doc", "log_error", "enqueue"):
        setattr(frappe, name, _refuse(name))

    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    close = types.ModuleType("konsol.close")
    close.__path__ = []

    spec = importlib.util.spec_from_file_location("konsol.setup_model", MODEL_PY)
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)

    def mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        return m

    def rate_gate(fy, fp, lock=False):
        site.rate_gate_args = (fy, fp, lock)
        return [], None, []

    gca_spec = importlib.util.spec_from_file_location("konsol.group_chart_model",
                                                      os.path.join(APP_DIR, "group_chart_model.py"))
    gcm = importlib.util.module_from_spec(gca_spec)
    gca_spec.loader.exec_module(gcm)
    dvm_spec = importlib.util.spec_from_file_location("konsol.dimension_value_model",
                                                      os.path.join(APP_DIR, "dimension_value_model.py"))
    dvm = importlib.util.module_from_spec(dvm_spec)
    dvm_spec.loader.exec_module(dvm)

    modules = {
        "frappe": frappe,
        "konsol": konsol,
        "konsol.close": close,
        "konsol.setup_model": model,
        "konsol.group_chart_model": gcm,
        "konsol.dimension_value_model": dvm,
        "konsol.clickhouse": mod("konsol.clickhouse",
                                 check_health=lambda: {"clickhouse_reachable": True, "message": ""}),
        "konsol.entity_permissions": mod("konsol.entity_permissions",
                                         allowed_entity_codes=lambda user=None: site.allowed),
        "konsol.fiscal_calendar": mod("konsol.fiscal_calendar",
                                      fiscal_period_rows=lambda: [{"status": "Open"}] * 12),
        "konsol.group_chart": mod("konsol.group_chart", chart_accounts=lambda: {
            "1000": {"main_account": "1000", "is_group": 0, "allow_ic": 0}}),
        "konsol.group_rates": mod("konsol.group_rates", rate_gate=rate_gate),
        "konsol.close.freshness_api": mod("konsol.close.freshness_api", current_freshness=lambda: {
            "state": "fresh", "as_of": "2026-11-07T10:00:00+00:00", "changed_since": [], "pending": 0,
            "last_failed": None}),
        "konsol.close.ic_api": mod("konsol.close.ic_api", published_count=lambda: 0, declared_none=lambda: True,
                                   tolerance_gap=lambda: None, setup_gap=lambda: None),
        "konsol.close.signoff_gate": mod("konsol.close.signoff_gate", statement_gap=lambda: None,
                                         currency_gaps=lambda: {"blocking": None, "reported": None}),
        "konsol.close.period_name": mod("konsol.close.period_name",
                                        period_name=lambda fy, fp: "FY%d P%02d" % (int(fy), int(fp))),
        "konsol.close.mywork_api": mod("konsol.close.mywork_api",
                                       _accountants_without_entities=lambda: ["acc@zz.test"]),
        "konsol.consolidation": mod("konsol.consolidation", __path__=[]),
        "konsol.consolidation.doctype": mod("konsol.consolidation.doctype", __path__=[]),
        "konsol.consolidation.doctype.intercompany_account": mod(
            "konsol.consolidation.doctype.intercompany_account", __path__=[]),
        "konsol.consolidation.doctype.intercompany_account.intercompany_account": mod(
            "konsol.consolidation.doctype.intercompany_account.intercompany_account",
            intercompany_accounts=lambda: set(),
            allow_ic_problem=lambda accounts, flags: ""),
    }
    for name, m in modules.items():
        if "." in name:
            parent, _, child = name.rpartition(".")
            setattr(modules[parent], child, m)
    return modules, whitelisted


def _run(site, fn):
    """Load setup.py under the stubs and call ``fn(setup)`` with them in
    place (status() imports two readers lazily); sys.modules is restored
    afterwards, so no stub leaks into another test file."""
    modules, whitelisted = _load(site)
    saved = {k: sys.modules.get(k) for k in modules}
    sys.modules.update(modules)
    try:
        spec = importlib.util.spec_from_file_location("konsol_setup_under_test", SETUP_PY)
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        setup._whitelisted = whitelisted
        setup._frappe = modules["frappe"]
        setup._model = modules["konsol.setup_model"]
        return fn(setup)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _status(site):
    return _run(site, lambda setup: setup.status())


def test_status_is_whitelisted_for_get_only():
    assert _run(_Site(), lambda setup: setup._whitelisted["status"]) == {"methods": ["GET"]}


@pytest.mark.parametrize("roles", [("EPM User",), ("EPM Analyst",), ("Entity Accountant",), ()])
def test_others_get_permission_error_before_anything_is_read(roles):
    site = _Site(roles)

    def call(setup):
        with pytest.raises(setup._frappe.PermissionError):
            setup.status()
    _run(site, call)
    assert site.calls == [("only_for", ("EPM Admin", "System Manager"))]


@pytest.mark.parametrize("roles", [("EPM Admin",), ("System Manager",)])
def test_admin_and_system_manager_get_every_step(roles):
    out, steps = _run(_Site(roles), lambda setup: (setup.status(), setup._model.STEPS))
    keys = [s["key"] for s in out["steps"]]
    assert keys == [k for k, _, _ in steps]
    assert out["total"] == 11


def test_fixture_site_reads_each_step_from_its_reader():
    site = _Site()
    out = {s["key"]: s for s in _status(site)["steps"]}
    assert out["fiscal_calendar"]["state"] == "done"
    assert out["consolidation_group"]["state"] == "done"
    # E2 is in no group; E1 is.
    assert out["entities"]["state"] == "partial" and "E2" in out["entities"]["detail"]
    assert out["chart_of_accounts"]["state"] == "done" and out["chart_of_accounts"]["count"] == 1
    assert out["dimensions"]["state"] == "done" and "open to every budget user" in out["dimensions"]["detail"]
    assert out["currencies"]["state"] == "done"
    assert out["intercompany"]["state"] == "done"
    assert out["users"]["state"] == "partial" and out["users"]["count"] == 1
    assert out["trial_balance"]["state"] == "done" and "FY2025 P03" in out["trial_balance"]["detail"]
    # The rate gate is asked about the latest period with a submitted TB, unlocked.
    assert site.rate_gate_args == (2025, 3, False)
    assert out["rates"]["state"] == "done"
    assert out["warehouse"]["state"] == "done"


def test_entities_are_cut_to_what_the_caller_may_see():
    site = _Site()
    site.allowed = {"E1"}
    out = {s["key"]: s for s in _status(site)["steps"]}
    assert "E2" not in out["entities"]["detail"]
    assert "1 you cannot see" in out["entities"]["detail"]


def test_a_failing_reader_marks_only_its_own_step():
    site = _Site()
    site.raise_in = {"Dimension"}
    out = {s["key"]: s for s in _status(site)["steps"]}
    assert out["dimensions"]["state"] == "not_done"
    assert "RuntimeError: Dimension unreadable" in out["dimensions"]["detail"]
    assert out["fiscal_calendar"]["state"] == "done"


def test_no_trial_balance_means_rates_are_not_checked():
    site = _Site()
    site.records["Trial Balance Submission"] = []
    site.counts[("Trial Balance Submission", 1)] = 0
    out = {s["key"]: s for s in _status(site)["steps"]}
    assert out["trial_balance"]["state"] == "not_done"
    assert out["rates"]["state"] == "not_done"
    assert site.rate_gate_args is None


def test_setup_source_calls_no_writer():
    with open(SETUP_PY) as f:
        tree = ast.parse(f.read())
    writers = {"insert", "save", "submit", "cancel", "delete", "delete_doc", "set_value", "db_set",
               "commit", "log_error", "enqueue", "new_doc", "get_doc", "sql"}
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            called.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None))
    assert not (called & writers), sorted(called & writers)


def test_page_roles_match_the_endpoint():
    with open(os.path.join(PAGE_DIR, "konsol_setup.json")) as f:
        page = json.load(f)
    assert page["name"] == page["page_name"] == "konsol-setup"
    assert page["title"] == "Konsol Setup" and page["module"] == "Consolidation"
    roles = _run(_Site(), lambda setup: setup.SETUP_ROLES)
    assert {r["role"] for r in page["roles"]} == set(roles)


def test_page_puts_server_text_in_as_text_only():
    with open(os.path.join(PAGE_DIR, "konsol_setup.js")) as f:
        js = f.read()
    assert 'frappe.pages["konsol-setup"]' in js
    assert '"konsol.setup.status"' in js and 'type: "GET"' in js
    # No server value is interpolated into markup: no .html(), no .append of a
    # template string, no innerHTML.
    assert ".html(" not in js and "innerHTML" not in js and "${" not in js
    for field in ("step.label", "step.detail"):
        assert ".text(%s" % field in js
