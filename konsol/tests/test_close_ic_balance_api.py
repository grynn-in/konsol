"""IC Balance API: konsol/close/ic_balance_api.py (konsol#305 story 5.4,
decision #305-W5-4, Deepak 6 Oct 2026).

- ``get_ic_balances(fiscal_year, fiscal_period)`` (GET): the period's draft
  and approved IC Balances, each with the margin of the unrealised-profit
  rule that matches its pair (read-only: the rule stays in Desk), the
  missing-rule gap naming the pairs, entity scope and ``can_draft``.
- ``save_ic_balance(...)`` (POST): an Analyst (or System Manager) drafts or
  edits a draft. Never an Admin (R2: the Admin approves in Approvals), never
  a submit: a forged status or docstatus never reaches it.
- ``rule_gap(fy, fp)`` / ``open_rule_gap()``: the setup gap for the sign-off
  gate and My work, produced by the real ``ic_balance_model``.

Loaded against a stub frappe (pattern: test_close_ic_api.py ``_Site`` /
``_frappe`` / ``_invoke``, copied, not imported). The real
``ic_balance_model.py`` is loaded by path under its dotted name.
"""
import importlib.util
import inspect
import json
import os
import sys
import types
from datetime import date

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")
API_PY = os.path.join(CLOSE_DIR, "ic_balance_api.py")
MODEL_PY = os.path.join(CLOSE_DIR, "ic_balance_model.py")

ANALYST = "zz-analyst@example.com"

SAVE_PARAMS = ["fiscal_year", "fiscal_period", "selling_entity", "buying_entity",
               "ic_sales_amount", "ending_inventory_from_ic", "name"]


def _load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _period(fy, fp, status="Open"):
    return {"fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
            "period_type": "Regular", "start_date": date(fy, fp, 1),
            "end_date": date(fy, fp, 28), "status": status}


def _bal(name, sell, buy, fy=2025, fp=7, docstatus=0, sales=1000.0, inventory=250.0):
    return {"name": name, "selling_entity": sell, "buying_entity": buy, "fiscal_year": fy,
            "fiscal_period": fp, "ic_sales_amount": sales,
            "ending_inventory_from_ic": inventory, "docstatus": docstatus}


def _rule(rule_id, debit="*", credit="*", margin=20.0, rule_type="unrealized_profit"):
    return {"name": rule_id, "rule_id": rule_id, "rule_name": rule_id, "rule_type": rule_type,
            "margin_pct": margin, "debit_entity_pattern": debit,
            "credit_entity_pattern": credit}


class _Site:
    def __init__(self):
        self.user = ANALYST
        self.roles = {"EPM Analyst"}
        self.periods = [_period(2025, 6, "Closed"), _period(2025, 7, "Open"),
                        _period(2025, 8, "Open")]
        self.balances = [_bal("ICB-UK01-DE01-2025-P7", "UK01", "DE01"),
                         _bal("ICB-FR01-DE01-2025-P7", "FR01", "DE01", docstatus=1),
                         _bal("ICB-FR01-ES01-2025-P7", "FR01", "ES01", docstatus=2),
                         _bal("ICB-UK01-FR01-2025-P6", "UK01", "FR01", fp=6)]
        self.rules = [_rule("R-UK", debit="UK01", credit="*", margin=12.5)]
        self.entities = [
            {"name": "UK01", "is_group": 0, "status": "Active"},
            {"name": "DE01", "is_group": 0, "status": "Active"},
            {"name": "FR01", "is_group": 0, "status": "Active"},
            {"name": "ES01", "is_group": 0, "status": "Active"},
            {"name": "OLD1", "is_group": 0, "status": "Disposed"},
            {"name": "GRP", "is_group": 1, "status": "Active"},
        ]
        self.allowed = None
        self.reads = []
        self.inserted = []
        self.saved = []


def _match_value(value, cond):
    if isinstance(cond, (list, tuple)):
        op, arg = cond[0], cond[1]
        if op == "in":
            return value in arg
        raise AssertionError("stub: unsupported operator %r" % (op,))
    return value == cond


def _match(row, filters):
    return all(_match_value(row.get(key), cond) for key, cond in (filters or {}).items())


class _Doc:
    def __init__(self, site, data, existing=False):
        object.__setattr__(self, "_site", site)
        object.__setattr__(self, "_data", dict(data))
        object.__setattr__(self, "_existing", existing)

    def __getattr__(self, key):
        try:
            return self._data[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        self._data[key] = value

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def insert(self):
        d = self._data
        d.setdefault("name", "ICB-%s-%s-%s-P%s" % (d["selling_entity"], d["buying_entity"],
                                                    d["fiscal_year"], d["fiscal_period"]))
        d.setdefault("docstatus", 0)
        self._site.inserted.append(dict(d))
        return self

    def save(self):
        self._site.saved.append(dict(self._data))
        return self


def _frappe(site):
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, **k):
        raise (exc or frappe.ValidationError)(msg)

    def only_for(roles, message=False):
        roles = [roles] if isinstance(roles, str) else list(roles)
        if not site.roles.intersection(roles):
            raise frappe.PermissionError("Not permitted")

    def whitelist(*a, **k):
        return lambda fn: fn

    def get_all(doctype, filters=None, fields=None, order_by=None, limit=None,
                limit_page_length=None, pluck=None, **k):
        site.reads.append(("get_all", doctype, json.dumps(filters, sort_keys=True, default=str)))
        if doctype == "IC Balance":
            rows = [r for r in site.balances if _match(r, filters)]
        elif doctype == "IC Elimination Rule":
            rows = [r for r in site.rules if _match(r, filters)]
        elif doctype == "Entity":
            rows = [r for r in site.entities if _match(r, filters)]
        else:
            raise AssertionError("unexpected get_all on %s" % doctype)
        if pluck:
            return [r.get(pluck) for r in rows]
        return [{f: r.get(f) for f in fields} for r in rows]

    def get_doc(arg, name=None):
        if isinstance(arg, dict):
            assert arg.get("doctype") == "IC Balance", arg
            return _Doc(site, arg)
        assert arg == "IC Balance", arg
        for row in site.balances:
            if row["name"] == name:
                return _Doc(site, dict(row, doctype="IC Balance"), existing=True)
        raise frappe.ValidationError("IC Balance %s not found" % name)

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.only_for = only_for
    frappe.whitelist = whitelist
    frappe.get_all = get_all
    frappe.get_doc = get_doc
    frappe.get_roles = lambda user=None: sorted(site.roles)
    frappe.session = types.SimpleNamespace(user=site.user)
    return frappe


def _invoke(site, run):
    frappe = _frappe(site)
    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    close = types.ModuleType("konsol.close")
    close.__path__ = []
    fc = types.ModuleType("konsol.fiscal_calendar")
    fc.fiscal_period_rows = lambda: [dict(r) for r in site.periods]
    ep = types.ModuleType("konsol.entity_permissions")
    ep.allowed_entity_codes = lambda user=None: site.allowed
    konsol.close, konsol.fiscal_calendar, konsol.entity_permissions = close, fc, ep
    names = ["frappe", "konsol", "konsol.close", "konsol.fiscal_calendar",
             "konsol.entity_permissions", "konsol.close.ic_balance_model",
             "close_ic_balance_api_under_test"]
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({"frappe": frappe, "konsol": konsol, "konsol.close": close,
                        "konsol.fiscal_calendar": fc, "konsol.entity_permissions": ep})
    try:
        close.ic_balance_model = _load_path("konsol.close.ic_balance_model", MODEL_PY)
        api = _load_path("close_ic_balance_api_under_test", API_PY)
        return run(api)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _get(site, fy=2025, fp=7):
    out = _invoke(site, lambda api: api.get_ic_balances(fy, fp))
    json.dumps(out)
    return out


def _request(fn, request):
    """Frappe's dispatcher (frappe.handler -> get_newargs): a request key the
    signature does not name is dropped, unless the function takes **kwargs."""
    params = inspect.signature(fn).parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(**request)
    return fn(**{k: v for k, v in request.items() if k in params})


def _save(site, **overrides):
    request = {"fiscal_year": 2025, "fiscal_period": 8, "selling_entity": "UK01",
               "buying_entity": "DE01", "ic_sales_amount": "1000",
               "ending_inventory_from_ic": "250"}
    request.update(overrides)
    return _invoke(site, lambda api: _request(api.save_ic_balance, request))


def _save_refused(site, **overrides):
    with pytest.raises(Exception) as info:
        _save(site, **overrides)
    assert site.inserted == [] and site.saved == []
    return info.value


# --- get_ic_balances ------------------------------------------------------------

def test_get_lists_draft_and_approved_of_the_period_with_margin():
    out = _get(_Site())
    names = [b["name"] for b in out["balances"]]
    assert names == ["ICB-FR01-DE01-2025-P7", "ICB-UK01-DE01-2025-P7"]  # no cancelled, no P6
    fr, uk = out["balances"]
    assert fr["status"] == "Approved" and fr["missing_rule"] is True and fr["rules"] == []
    assert uk["status"] == "Draft" and uk["rules"] == [
        {"rule_id": "R-UK", "rule_name": "R-UK", "margin_pct": 12.5}]
    assert out["period"]["fiscal_year"] == 2025 and out["period"]["status"] == "Open"
    assert out["rules_desk"] == "/app/ic-elimination-rule"


def test_get_gap_names_the_uncovered_pair():
    out = _get(_Site())
    assert out["gap"]["code"] == "ic_unrealized_profit_rule_undeclared"
    assert out["gap"]["pairs"] == [{"selling_entity": "FR01", "buying_entity": "DE01"}]
    assert "FR01 → DE01" in out["gap"]["message"]


def test_get_no_gap_when_every_pair_has_a_rule():
    site = _Site()
    site.rules.append(_rule("R-ALL"))
    assert _get(site)["gap"] is None


def test_get_can_draft_only_for_draft_roles_in_an_open_period():
    for roles, expected in (({"EPM Analyst"}, True), ({"System Manager"}, True),
                            ({"EPM Admin"}, False), ({"EPM User"}, False)):
        site = _Site()
        site.roles = roles
        assert _get(site)["can_draft"] is expected, roles
    site = _Site()
    assert _get(site, fp=6)["can_draft"] is False  # Closed


def test_get_refuses_entity_accountant():
    site = _Site()
    site.roles = {"Entity Accountant"}
    with pytest.raises(Exception, match="Not permitted"):
        _get(site)


def test_get_entities_are_active_leaves():
    assert _get(_Site())["entities"] == ["DE01", "ES01", "FR01", "UK01"]


def test_get_scope_hides_pairs_with_neither_entity():
    site = _Site()
    site.allowed = {"UK01"}
    out = _get(site)
    assert [b["name"] for b in out["balances"]] == ["ICB-UK01-DE01-2025-P7"]
    assert out["hidden"] == 1
    # the gap shown is the visible one: FR01 → DE01 is not named to this caller
    assert out["gap"] is None
    assert "FR01" not in json.dumps(out["balances"])


def test_get_undeclared_period_refused():
    with pytest.raises(Exception, match="not declared"):
        _get(_Site(), fp=11)


# --- save_ic_balance --------------------------------------------------------------

def test_save_inserts_a_draft_with_exactly_its_fields():
    site = _Site()
    out = _save(site)
    assert out == {"name": "ICB-UK01-DE01-2025-P8", "docstatus": 0}
    assert len(site.inserted) == 1
    doc = site.inserted[0]
    assert set(doc) == {"doctype", "selling_entity", "buying_entity", "fiscal_year",
                        "fiscal_period", "ic_sales_amount", "ending_inventory_from_ic",
                        "name", "docstatus"}
    assert doc["docstatus"] == 0
    assert (doc["fiscal_year"], doc["fiscal_period"]) == (2025, 8)
    assert doc["ic_sales_amount"] == 1000.0 and doc["ending_inventory_from_ic"] == 250.0


def test_save_allowed_without_a_rule():
    """W5-4: no rule is a gap, never a refusal at save (rejected option)."""
    site = _Site()
    site.rules = []
    assert _save(site)["docstatus"] == 0


def test_save_refused_for_non_draft_roles():
    for roles in ({"EPM Admin"}, {"EPM User"}, {"Entity Accountant"}, set()):
        site = _Site()
        site.roles = roles
        e = _save_refused(site)
        assert "Not permitted" in str(e), roles


def test_save_forged_status_and_docstatus_never_reach_the_document():
    site = _Site()
    _save(site, docstatus=1, status="Approved", workflow_state="Approved", owner="x@y",
          approved_by="x@y")
    doc = site.inserted[0]
    assert doc["docstatus"] == 0
    for key in ("status", "workflow_state", "owner", "approved_by"):
        assert key not in doc


def test_save_signature_takes_no_forged_keys():
    fn = _invoke(_Site(), lambda api: api.save_ic_balance)
    params = inspect.signature(fn).parameters
    assert not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    assert list(params) == SAVE_PARAMS


def test_save_refused_in_a_closed_period():
    e = _save_refused(_Site(), fiscal_period=6)
    assert "Closed" in str(e)


def test_save_refused_for_unknown_or_same_entity_or_bad_amount():
    assert "not an entity" in str(_save_refused(_Site(), buying_entity="XX99"))
    assert "not an entity" in str(_save_refused(_Site(), buying_entity="GRP"))
    assert "same entity" in str(_save_refused(_Site(), buying_entity="UK01"))
    assert "negative" in str(_save_refused(_Site(), ic_sales_amount="-1"))


def test_save_scope_refuses_a_pair_with_neither_entity_allowed():
    site = _Site()
    site.allowed = {"ES01"}
    e = _save_refused(site)
    assert "neither entity" in str(e)


def test_save_scope_allows_a_pair_with_one_entity_allowed():
    site = _Site()
    site.allowed = {"DE01"}
    assert _save(site)["name"] == "ICB-UK01-DE01-2025-P8"


def test_save_new_refused_when_a_draft_or_approved_or_cancelled_exists():
    e = _save_refused(_Site(), fiscal_period=7)
    assert "already a draft" in str(e)
    e = _save_refused(_Site(), fiscal_period=7, selling_entity="FR01")
    assert "approved" in str(e)
    e = _save_refused(_Site(), fiscal_period=7, selling_entity="FR01", buying_entity="ES01")
    assert "cancelled" in str(e)


def test_save_edits_a_draft_amounts_only():
    site = _Site()
    out = _save(site, fiscal_period=7, ic_sales_amount="2000", ending_inventory_from_ic="400",
                name="ICB-UK01-DE01-2025-P7")
    assert out == {"name": "ICB-UK01-DE01-2025-P7", "docstatus": 0}
    assert site.inserted == []
    saved = site.saved[0]
    assert saved["ic_sales_amount"] == 2000.0 and saved["ending_inventory_from_ic"] == 400.0
    assert saved["docstatus"] == 0


def test_save_edit_refuses_an_approved_balance():
    e = _save_refused(_Site(), fiscal_period=7, selling_entity="FR01",
                      name="ICB-FR01-DE01-2025-P7")
    assert "not a draft" in str(e)


def test_save_edit_refuses_a_changed_key():
    e = _save_refused(_Site(), fiscal_period=7, buying_entity="FR01",
                      name="ICB-UK01-DE01-2025-P7")
    assert "save it under its own key" in str(e)


def test_save_edit_scope_checks_the_stored_pair():
    site = _Site()
    site.allowed = {"ES01"}
    e = _save_refused(site, fiscal_period=7, name="ICB-UK01-DE01-2025-P7")
    assert "neither entity" in str(e)


# --- the gap readers (the real producer's output) ----------------------------------

def test_rule_gap_reads_the_period_draft_and_approved_balances():
    site = _Site()
    gap = _invoke(site, lambda api: api.rule_gap(2025, 7))
    assert gap["code"] == "ic_unrealized_profit_rule_undeclared"
    assert gap["pairs"] == [{"selling_entity": "FR01", "buying_entity": "DE01"}]
    assert gap["entities"] == ["DE01", "FR01"]


def test_rule_gap_none_and_no_rule_read_without_balances():
    site = _Site()
    assert _invoke(site, lambda api: api.rule_gap(2025, 9)) is None
    assert [r for r in site.reads if r[1] == "IC Elimination Rule"] == []


def test_open_rule_gap_covers_open_periods_only():
    site = _Site()
    # P6 (Closed) UK01 → FR01 is covered by R-UK anyway; make it uncovered
    site.rules = []
    gap = _invoke(site, lambda api: api.open_rule_gap())
    pairs = [(p["selling_entity"], p["buying_entity"]) for p in gap["pairs"]]
    assert pairs == [("FR01", "DE01"), ("UK01", "DE01")]  # not UK01 → FR01 (P6 Closed)


def test_open_rule_gap_none_when_covered():
    site = _Site()
    site.rules.append(_rule("R-FR", debit="FR01", credit="DE01"))
    assert _invoke(site, lambda api: api.open_rule_gap()) is None
