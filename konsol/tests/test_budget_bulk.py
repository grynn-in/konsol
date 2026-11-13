"""S2-U1: konsol/budget_bulk.py, the budget check endpoint (konsol#348).

check_file / check_upload read the attached file, hand the table to
budget_intake.plan, and write the counts and the report on the Budget Upload.
They write no Budget Entry. budget_bulk imports frappe, so it is loaded
against stubs; the plan is a stub that returns a canned report.
"""
import importlib.util
import json
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_PATH = os.path.join(APP_DIR, "budget_bulk.py")


class Denied(Exception):
    pass


class Thrown(Exception):
    pass


def _item(entity, layer, ok=True, errors=()):
    return {"scenario": "BUD", "fiscal_year": 2027, "entity": entity, "layer": layer,
            "fiscal_period": 1, "rows": 2, "errors": list(errors), "ok": ok and not errors}


class FakeUpload:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.name = "BU-0001"
        self.owner = "ann@example.com"
        self.report = None
        self.saved = 0

    def insert(self):
        self.log.append(("insert", "Budget Upload"))
        return self

    def save(self):
        self.saved += 1
        return self

    def check_permission(self, ptype):
        self.log.append(("check_permission", ptype))


@pytest.fixture
def env(monkeypatch):
    log = []
    state = types.SimpleNamespace(can_create=True, table=[["h"], ["r"]], plan_report=None,
                                  plan_raises=None, plan_calls=[], doc=None)

    def get_doc(arg, name=None):
        if isinstance(arg, dict):
            doc = FakeUpload(**{k: v for k, v in arg.items()})
        else:
            doc = state.doc
        doc.log = log
        state.doc = doc
        return doc

    frappe = types.ModuleType("frappe")

    def throw(msg, exc=None, *a, **k):
        raise (exc or Thrown)(msg)

    frappe.throw = throw
    frappe.PermissionError = Denied
    frappe.whitelist = lambda **kw: (lambda fn: setattr(fn, "_whitelist", kw) or fn)
    frappe.get_doc = get_doc
    frappe.has_permission = lambda doctype, ptype=None, **k: state.can_create

    tb = types.ModuleType("konsol.tb_bulk")
    tb._read_table = lambda url: state.table

    intake = types.ModuleType("konsol.budget_intake")

    def plan(table, *, form_basis):
        state.plan_calls.append((table, form_basis))
        if state.plan_raises:
            raise state.plan_raises
        report = state.plan_report
        return {"groups": {}, "report": report, "ok": all(r["ok"] for r in report)}
    intake.plan = plan

    for name, mod in {"frappe": frappe, "konsol.tb_bulk": tb, "konsol.budget_intake": intake}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("_budget_bulk_under_test", PY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return types.SimpleNamespace(mod=mod, state=state, log=log)


def test_the_endpoints_are_post_only(env):
    assert env.mod.check_file._whitelist == {"methods": ["POST"]}
    assert env.mod.check_upload._whitelist == {"methods": ["POST"]}


def test_check_file_writes_counts_and_report_and_names_the_refusal(env):
    refusal = "Layer board takes rows only from holders of the Budget Approver role"
    env.state.plan_report = [_item("E1", "base"), _item("E2", "board", errors=[refusal]),
                             _item("E1", "board")]
    out = env.mod.check_file("/files/b.csv", "Period movement")
    doc = env.state.doc
    assert (doc.status, doc.group_count, doc.valid_count, doc.total_rows, doc.error) == \
        ("Checked", 3, 2, 6, "")
    assert doc.failed_count == 0 and doc.loaded_count == 0
    stored = json.loads(doc.report)
    assert [r["ok"] for r in stored] == [True, False, True]
    assert stored[1]["errors"] == [refusal]
    assert out["report"] == stored and out["valid_count"] == 2 and out["status"] == "Checked"
    assert env.state.plan_calls == [(env.state.table, "Period movement")]


def test_check_file_writes_no_budget_entry(env):
    env.state.plan_report = [_item("E1", "base")]
    env.mod.check_file("/files/b.csv")
    assert env.log == [("insert", "Budget Upload")]
    assert env.state.plan_calls[0][1] == ""


def test_a_structural_problem_fails_the_upload_with_the_message(env):
    env.state.plan_raises = ValueError("Missing column: scenario")
    env.mod.check_file("/files/b.csv")
    doc = env.state.doc
    assert (doc.status, doc.error, doc.report) == ("Failed", "Missing column: scenario", "[]")
    assert doc.group_count == doc.valid_count == doc.total_rows == 0


def test_a_user_who_may_not_create_uploads_is_refused(env):
    env.state.can_create = False
    with pytest.raises(Denied):
        env.mod.check_file("/files/b.csv")
    assert env.log == []


def test_check_upload_checks_a_saved_draft_in_place(env):
    env.state.plan_report = [_item("E1", "base")]
    env.state.doc = FakeUpload(status="Draft", upload_file="/files/b.csv", amount_basis="")
    env.mod.check_upload("BU-0001", amount_basis="Year-to-date movement")
    assert env.state.doc.amount_basis == "Year-to-date movement"
    assert env.state.doc.status == "Checked"
    assert ("check_permission", "write") in env.log
    assert env.state.plan_calls[0][1] == "Year-to-date movement"


def test_check_upload_refuses_a_loading_or_loaded_upload(env):
    env.state.doc = FakeUpload(status="Loaded", upload_file="/files/b.csv", amount_basis="")
    with pytest.raises(Thrown):
        env.mod.check_upload("BU-0001")
