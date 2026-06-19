"""Orchestration tests for D365 write-back — push_budget_input / require_enabled
/ _set_status. ``frappe`` is faked via sys.modules so these run in the standalone
suite (the module imports frappe lazily inside each site-bound function).
"""
import sys
import types

import pytest
import requests

from konsol import d365_writeback as wb


class _FakeMeta:
    def __init__(self, fields):
        self._fields = set(fields)

    def has_field(self, f):
        return f in self._fields


class _FakeDoc:
    def __init__(self, **kw):
        self.name = kw.get("name", "BUD-0001")
        self.data_area_id = kw.get("data_area_id", "USMF")
        self.fiscal_year = kw.get("fiscal_year", 2024)
        self.main_account = kw.get("main_account", "401100")
        self.dim_cost_center = kw.get("dim_cost_center", "CC001")
        self.dim_department = kw.get("dim_department", "")
        self.periods = kw.get("periods", [types.SimpleNamespace(fiscal_period=1, amount=100.0)])
        self._vals = {"d365_writeback_status": kw.get("status")}
        self.meta = _FakeMeta(kw.get("fields", ["d365_writeback_status", "d365_writeback_error"]))
        self.set_calls = []

    def get(self, k, default=None):
        if k in self._vals:
            return self._vals[k]
        return getattr(self, k, default)

    def db_set(self, field, value, update_modified=True):
        self._vals[field] = value
        self.set_calls.append((field, value))


class _FakeThrow(Exception):
    pass


def _install_fake_frappe(monkeypatch, budget_doc):
    fr = types.ModuleType("frappe")

    def throw(msg, exc=None):
        raise _FakeThrow(msg)

    fr.throw = throw
    fr.get_doc = lambda dt, name: budget_doc
    fr.log_error = lambda **kw: None
    fr.get_traceback = lambda: "traceback"
    monkeypatch.setitem(sys.modules, "frappe", fr)
    return fr


# --- require_enabled ---

def test_require_enabled_raises_when_disabled(monkeypatch):
    _install_fake_frappe(monkeypatch, _FakeDoc())
    with pytest.raises(_FakeThrow):
        wb.require_enabled({"enabled": False})


def test_require_enabled_raises_on_missing_config(monkeypatch):
    _install_fake_frappe(monkeypatch, _FakeDoc())
    with pytest.raises(_FakeThrow):
        wb.require_enabled({"enabled": True, "resource_url": "", "tenant_id": "t",
                            "client_id": "c", "client_secret": "s"})


def test_require_enabled_passes_when_complete(monkeypatch):
    _install_fake_frappe(monkeypatch, _FakeDoc())
    wb.require_enabled({"enabled": True, "resource_url": "u", "tenant_id": "t",
                        "client_id": "c", "client_secret": "s"})  # no raise


# --- _set_status ---

def test_set_status_writes_when_field_exists():
    doc = _FakeDoc()
    wb._set_status(doc, "Pushed", "")
    assert doc._vals["d365_writeback_status"] == "Pushed"


def test_set_status_truncates_error_to_140():
    doc = _FakeDoc()
    wb._set_status(doc, "Failed", "x" * 500)
    assert len(doc._vals["d365_writeback_error"]) == 140


def test_set_status_noop_when_field_absent():
    doc = _FakeDoc(fields=[])           # meta.has_field -> False
    wb._set_status(doc, "Pushed", "")
    assert doc.set_calls == []


# --- push_budget_input ---

def test_push_happy_path_records_pushed(monkeypatch):
    doc = _FakeDoc(periods=[types.SimpleNamespace(fiscal_period=1, amount=100.0)])
    _install_fake_frappe(monkeypatch, doc)
    monkeypatch.setattr(wb, "get_config", lambda entity_id=None: {"enabled": True})
    monkeypatch.setattr(wb, "require_enabled", lambda cfg: None)
    monkeypatch.setattr(wb, "get_token", lambda cfg: "TOK")
    pushed = {}
    monkeypatch.setattr(
        wb, "push_replace_batch",
        lambda cfg, tok, mid, entries: pushed.setdefault("n", len(entries)),
    )
    out = wb.push_budget_input("BUD-0001")
    assert out["status"] == "Pushed" and out["entries"] == 1
    assert out["budget_model_id"] == "EPM-BUD-0001"
    assert doc._vals["d365_writeback_status"] == "Pushed"
    assert pushed["n"] == 1


def test_push_skips_when_already_pushed(monkeypatch):
    doc = _FakeDoc(status="Pushed")
    _install_fake_frappe(monkeypatch, doc)
    monkeypatch.setattr(wb, "get_config", lambda entity_id=None: {"enabled": True})
    monkeypatch.setattr(wb, "require_enabled", lambda cfg: None)
    called = {"replace": False}
    monkeypatch.setattr(wb, "push_replace_batch", lambda *a: called.update(replace=True))
    out = wb.push_budget_input("BUD-0001")            # no force
    assert out["status"] == "Skipped" and called["replace"] is False


def test_push_force_repushes_when_already_pushed(monkeypatch):
    doc = _FakeDoc(status="Pushed")
    _install_fake_frappe(monkeypatch, doc)
    monkeypatch.setattr(wb, "get_config", lambda entity_id=None: {"enabled": True})
    monkeypatch.setattr(wb, "require_enabled", lambda cfg: None)
    monkeypatch.setattr(wb, "get_token", lambda cfg: "TOK")
    monkeypatch.setattr(wb, "push_replace_batch", lambda *a: None)
    out = wb.push_budget_input("BUD-0001", force=True)
    assert out["status"] == "Pushed"


def test_push_error_path_records_failed_and_reraises(monkeypatch):
    doc = _FakeDoc()
    logged = {}
    fr = _install_fake_frappe(monkeypatch, doc)
    fr.log_error = lambda **kw: logged.update(kw)
    monkeypatch.setattr(wb, "get_config", lambda entity_id=None: {"enabled": True})
    monkeypatch.setattr(wb, "require_enabled", lambda cfg: None)

    resp = requests.models.Response()
    resp.status_code = 400
    resp._content = b"period closed"

    def boom(cfg):
        raise requests.exceptions.HTTPError(response=resp)

    monkeypatch.setattr(wb, "get_token", boom)
    with pytest.raises(requests.exceptions.HTTPError):
        wb.push_budget_input("BUD-0001")
    assert doc._vals["d365_writeback_status"] == "Failed"
    assert "HTTP 400" in doc._vals["d365_writeback_error"]
    # server-side log captured the D365 response body for operability
    assert "period closed" in logged["message"]


def test_push_resolves_config_by_entity(monkeypatch):
    doc = _FakeDoc(data_area_id="USMF")
    _install_fake_frappe(monkeypatch, doc)
    seen = {}

    def fake_get_config(entity_id=None):
        seen["entity_id"] = entity_id
        return {"enabled": True}

    monkeypatch.setattr(wb, "get_config", fake_get_config)
    monkeypatch.setattr(wb, "require_enabled", lambda cfg: None)
    monkeypatch.setattr(wb, "get_token", lambda cfg: "TOK")
    monkeypatch.setattr(wb, "push_replace_batch", lambda *a: None)

    wb.push_budget_input("BUD-0001")

    assert seen["entity_id"] == "USMF"
