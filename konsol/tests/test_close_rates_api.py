"""Rates API: konsol/close/rates_api.py ``get_rates`` (konsol#305 E403;
stories 4.1, 4.3; #305-W2-3, W2-10, W2-14; D2-9).

``get_rates(fiscal_year, fiscal_period)`` (GET) returns the period's rate
grid: the required pairs from the warehouse, the drafts and approved rates,
the previous approved rate per key, the move flags under the declared
threshold, the approve mode on each awaiting cell (with the draft's
preparers), the policy gaps, and whether the caller may enter or approve.

Loaded against a stub frappe (pattern: test_close_checks_api.py ``_Site`` /
``_call``, copied, not imported). ``konsol.group_rates`` and
``konsol.fiscal_calendar`` are stub modules: ``translation_needs`` is how the
ClickHouse read is stubbed, so no ClickHouse client is imported. The real
``close_policy_model.py``, ``rates_model.py`` and ``self_approval.py`` are
loaded by path under their dotted names, so the approve rule and the preparer
rule under test are the product's.
"""
import decimal
import importlib.util
import json
import os
import re
import sys
import types
from datetime import date, datetime

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")
API_PY = os.path.join(CLOSE_DIR, "rates_api.py")
TIMEFMT_PY = os.path.join(CLOSE_DIR, "timefmt.py")
OWNERSHIP_CHANGE_PY = os.path.join(CLOSE_DIR, "ownership_change.py")
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
#: The stub site's system time zone (A55, L01e): BST (+01:00) in July 2025.
SITE_TZ = "Europe/London"

RATES_ROLES = ("EPM Admin", "EPM Analyst", "EPM User", "System Manager")
LEAD = "zz-lead@example.com"
ANALYST = "zz-analyst@example.com"
VIEWER = "zz-viewer@example.com"


def _period(fy, fp, status="Open"):
    return {"fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
            "period_label": "P%02d" % fp, "period_type": "Regular",
            "start_date": date(fy, fp, 1), "end_date": date(fy, fp, 28),
            "quarter": "Q%d" % ((fp - 1) // 3 + 1), "status": status}


def _ger(name, frm, to, rate_type, quote, docstatus, owner=ANALYST, quoted_per="1",
         erp_quote=None, modified=None, change_reason=None, source="Manual"):
    return {"name": name, "from_currency": frm, "to_currency": to, "rate_type": rate_type,
            "quote": quote, "quoted_per": quoted_per, "erp_quote": erp_quote,
            "docstatus": docstatus, "owner": owner,
            "modified": modified or datetime(2025, 7, 31, 10, 0, 0),
            "change_reason": change_reason, "source": source,
            "fiscal_year": 2025, "fiscal_period": 7}


def _earlier(name, frm, to, rate_type, quote, fy, fp, quoted_per="1"):
    return {"name": name, "from_currency": frm, "to_currency": to, "rate_type": rate_type,
            "quote": quote, "quoted_per": quoted_per, "fiscal_year": fy, "fiscal_period": fp,
            "docstatus": 1}


class _Site:
    def __init__(self):
        self.user = LEAD
        self.roles = {"EPM Admin"}
        self.periods = [_period(2025, 6, "Closed"), _period(2025, 7, "Open")]
        self.needs = ({("EUR", "GBP"), ("USD", "GBP")}, [])
        self.needs_error = None
        self.threshold = 0.5
        self.policy = "Blocked"
        self.workflow_state_field = None
        self.docs = [
            _ger("GER-1", "EUR", "GBP", "Closing", 0.85, 1),
            _ger("GER-2", "EUR", "GBP", "Average", 0.84, 0, owner=ANALYST),
        ]
        self.earlier = [
            _earlier("GER-0", "EUR", "GBP", "Closing", 0.80, 2025, 6),
            _earlier("GER-00", "EUR", "GBP", "Average", 0.81, 2025, 6),
        ]
        self.versions = []  # {"ref_doctype", "docname", "owner", "data"}
        self.her = []  # Historical Equity Rate drafts (E405)
        self.ops = []  # Ownership Period rows: drafts (E405) and submitted (E406 scope query)
        self.entities = []  # Active leaf entities (E406): {"name"}
        self.tbs = []  # Trial Balance Submission rows (E406): {"data_area_id", "fiscal_year", "fiscal_period", "docstatus"}
        self.can_record = True  # frappe.has_permission("Ownership Period", "create") (E406)
        self.allowed = None  # allowed_entity_codes(): None = unrestricted (E405, W2-10)
        self.quoted_per_options = "1\n10\n100\n1000\n10000"  # E409b: Group Exchange Rate meta
        self.move_answer = None
        self.move_calls = []
        self.needs_calls = []
        self.only_for_calls = []
        self.reads = []  # one entry per MariaDB read
        self.named = {}  # name -> _FakeDoc, for get_doc(doctype, name)
        self.get_doc_calls = []  # what get_doc received
        self.new_docs = []  # every _FakeDoc built from a dict
        self.signed = []  # signoff_gate.latest_signed_runs() keys (O55)


def _her(name, data_area_id="ZZA", group="CG1", account="4000", rate_date=None,
         historical_rate=1.2, owner=ANALYST, creation=None, docstatus=0):
    return {"name": name, "consolidation_group": group, "data_area_id": data_area_id,
            "main_account": account, "rate_date": rate_date or date(2024, 1, 1),
            "historical_rate": historical_rate, "owner": owner,
            "creation": creation or datetime(2025, 7, 1, 9, 0, 0), "docstatus": docstatus}


def _op(name, data_area_id="ZZA", group="CG1", effective_date=None, end_date=None,
        ownership_pct=60.0, consolidation_method="Equity", owner=ANALYST, creation=None,
        docstatus=0):
    return {"name": name, "consolidation_group": group, "data_area_id": data_area_id,
            "effective_date": effective_date or date(2025, 1, 1), "end_date": end_date,
            "ownership_pct": ownership_pct, "consolidation_method": consolidation_method,
            "owner": owner, "creation": creation or datetime(2025, 7, 2, 9, 0, 0),
            "docstatus": docstatus}


def _entity(name, status="Active", is_group=0):
    return {"name": name, "status": status, "is_group": is_group}


def _tb(data_area_id, fy=2025, fp=7, docstatus=1):
    return {"data_area_id": data_area_id, "fiscal_year": fy, "fiscal_period": fp,
            "docstatus": docstatus}


def _match_value(value, cond):
    if isinstance(cond, (list, tuple)):
        op, arg = cond[0], cond[1]
        if op == "in":
            return value in arg
        if op == "is":
            assert arg in ("set", "not set"), cond
            return (value not in (None, "")) == (arg == "set")
        if op == "<=":
            return value is not None and value <= arg
        raise AssertionError("stub: unsupported operator %r" % (op,))
    return value == cond


def _match(row, filters):
    return all(_match_value(row.get(key), cond) for key, cond in (filters or {}).items())


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
                limit_page_length=None, pluck=None, **k):
        site.reads.append(("get_all", doctype))
        if doctype == "Group Exchange Rate":
            rows = [r for r in site.docs if _match(r, filters)]
        elif doctype == "Version":
            rows = [r for r in site.versions if _match(r, filters)]
        elif doctype == "Historical Equity Rate":
            rows = [r for r in site.her if _match(r, filters)]
        elif doctype == "Ownership Period":
            rows = [r for r in site.ops if _match(r, filters)]
        elif doctype == "Entity":
            rows = [r for r in site.entities if _match(r, filters)]
        elif doctype == "Trial Balance Submission":
            rows = [r for r in site.tbs if _match(r, filters)]
        else:
            raise AssertionError("unexpected get_all on %s" % doctype)
        if pluck:
            return [r.get(pluck) for r in rows]
        return [{f: r.get(f) for f in fields} for r in rows]

    def sql(query, values=None, as_dict=False, **k):
        site.reads.append(("sql", query))
        assert "tabGroup Exchange Rate" in query, query
        assert "docstatus = 1" in query, query
        assert as_dict, "the earlier approved rates are read as_dict"
        fy, fy2, fp = values
        assert fy == fy2
        return [dict(r) for r in site.earlier
                if r["docstatus"] == 1 and (r["fiscal_year"] < fy
                                            or (r["fiscal_year"] == fy and r["fiscal_period"] < fp))]

    def get_single_value(doctype, field, **k):
        site.reads.append(("single", doctype, field))
        assert (doctype, field) == ("Close Settings", "self_approval"), (doctype, field)
        return site.policy

    def get_value(doctype, filters, fieldname=None, **k):
        site.reads.append(("get_value", doctype))
        assert doctype == "Workflow", doctype
        return site.workflow_state_field

    def get_doc(arg, name=None, **k):
        site.get_doc_calls.append(dict(arg) if isinstance(arg, dict) else (arg, name))
        if isinstance(arg, dict):
            doc = _FakeDoc(dict(arg), new=True)
            site.new_docs.append(doc)
            return doc
        site.reads.append(("get_doc", arg))
        assert arg in ("Group Exchange Rate", "Ownership Period"), arg
        if name not in site.named:
            raise frappe.ValidationError("%s %s not found" % (arg, name))
        return site.named[name]

    def has_permission(doctype, ptype="read", *a, **k):
        assert (doctype, ptype) == ("Ownership Period", "create"), (doctype, ptype)
        return site.can_record

    def get_meta(doctype):
        site.reads.append(("get_meta", doctype))
        assert doctype == "Group Exchange Rate", doctype
        fields = {"quoted_per": types.SimpleNamespace(options=site.quoted_per_options)}
        return types.SimpleNamespace(get_field=lambda f: fields[f])

    frappe.get_doc = get_doc
    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.only_for = only_for
    frappe.whitelist = whitelist
    frappe.get_all = get_all
    frappe.get_meta = get_meta
    frappe.has_permission = has_permission
    frappe.get_roles = lambda user=None: sorted(site.roles)
    frappe.flags = {}
    frappe.db = types.SimpleNamespace(sql=sql, get_single_value=get_single_value,
                                      get_value=get_value)
    frappe.session = types.SimpleNamespace(user=site.user)
    frappe.utils = types.SimpleNamespace(get_system_timezone=lambda: SITE_TZ)
    return frappe


class _FakeDoc:
    """A recording Group Exchange Rate: insert()/save() are counted, and run
    what the controller would set (source Manual, a label) without deciding
    anything for the endpoint."""

    def __init__(self, data, new=False):
        self.__dict__["_data"] = dict(data)
        self.__dict__["flags"] = types.SimpleNamespace()
        self.__dict__["calls"] = []
        self.__dict__["_new"] = new

    def __getattr__(self, key):
        try:
            return self.__dict__["_data"][key]
        except KeyError:
            raise AttributeError(key) from None

    def __setattr__(self, key, value):
        self.__dict__["_data"][key] = value

    def get(self, key, default=None):
        return self._data.get(key, default)

    def insert(self, *a, **k):
        self.calls.append(("insert", a, k))
        self._data.setdefault(
            "name", "OP-NEW" if self._data.get("doctype") == "Ownership Period" else "GER-NEW")
        self._data.setdefault("docstatus", 0)
        if not self._data.get("source"):
            self._data["source"] = "Manual"
        self._data["quote_label"] = "%s %s per %s %s" % (
            self._data.get("quote"), self._data.get("to_currency"),
            self._data.get("quoted_per"), self._data.get("from_currency"))
        return self

    def save(self, *a, **k):
        self.calls.append(("save", a, k))
        self._data["quote_label"] = "%s %s per %s %s" % (
            self._data.get("quote"), self._data.get("to_currency"),
            self._data.get("quoted_per"), self._data.get("from_currency"))
        return self

    def submit(self, *a, **k):
        raise AssertionError("save_rate must never submit")


def _group_rates(site):
    gr = types.ModuleType("konsol.group_rates")
    gr.RATE_TYPES = ("Closing", "Average")
    gr.PREFILL_ROLES = ("EPM Analyst", "EPM Admin", "System Manager")
    gr.true_rate = lambda q, p: float(decimal.Decimal(str(q or 0)) / int(p or 1))
    gr.ch_error_names = lambda e: set()

    def translation_needs(fy, fp):
        site.needs_calls.append((fy, fp))
        if site.needs_error is not None:
            raise site.needs_error
        return site.needs

    def move_threshold():
        site.reads.append(("single", "Close Settings", "rate_move_threshold"))
        return site.threshold

    def move_problem(rate, previous=None, erp_rate=None, unit="", threshold=None):
        site.move_calls.append({"rate": rate, "previous": previous, "erp_rate": erp_rate,
                                "unit": unit, "threshold": threshold})
        return site.move_answer

    gr.translation_needs = translation_needs
    gr.move_threshold = move_threshold
    gr.move_problem = move_problem
    return gr


def _fiscal_calendar(site):
    fc = types.ModuleType("konsol.fiscal_calendar")

    def fiscal_period_rows():
        site.reads.append(("sql", "fiscal_period_rows"))
        return [dict(r) for r in site.periods]

    fc.fiscal_period_rows = fiscal_period_rows
    return fc


def _load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _call(site, fy=2025, fp=7):
    return _invoke(site, lambda api: api.get_rates(fy, fp))


def _load_rates_api(site):
    """The loaded ``rates_api`` module itself (for unit-testing ``_iso``
    directly), not a JSON-safe endpoint result."""
    return _invoke(site, lambda api: api, require_json_safe=False)


def _invoke(site, run, require_json_safe=True):
    frappe = _frappe(site)
    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    close = types.ModuleType("konsol.close")
    close.__path__ = []
    group_rates = _group_rates(site)
    fiscal_calendar = _fiscal_calendar(site)
    entity_permissions = types.ModuleType("konsol.entity_permissions")
    entity_permissions.allowed_entity_codes = lambda user=None: site.allowed
    konsol.close = close
    konsol.group_rates = group_rates
    konsol.fiscal_calendar = fiscal_calendar
    konsol.entity_permissions = entity_permissions
    names = ["frappe", "konsol", "konsol.close", "konsol.group_rates", "konsol.fiscal_calendar",
             "konsol.entity_permissions", "konsol.close.close_policy_model",
             "konsol.close.rates_model", "konsol.close.self_approval", "konsol.close.scope_model",
             "konsol.close.timefmt", "konsol.close.signoff_gate",
             "konsol.close.ownership_change", "close_rates_api_under_test"]
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({"frappe": frappe, "konsol": konsol, "konsol.close": close,
                        "konsol.group_rates": group_rates,
                        "konsol.fiscal_calendar": fiscal_calendar,
                        "konsol.entity_permissions": entity_permissions})
    try:
        close.close_policy_model = _load_path(
            "konsol.close.close_policy_model", os.path.join(CLOSE_DIR, "close_policy_model.py"))
        close.rates_model = _load_path(
            "konsol.close.rates_model", os.path.join(CLOSE_DIR, "rates_model.py"))
        close.self_approval = _load_path(
            "konsol.close.self_approval", os.path.join(CLOSE_DIR, "self_approval.py"))
        close.scope_model = _load_path(
            "konsol.close.scope_model", os.path.join(CLOSE_DIR, "scope_model.py"))
        close.timefmt = _load_path("konsol.close.timefmt", TIMEFMT_PY)
        # O55: the REAL ownership_change.py (and through it the real
        # ownership_change_model.py), reading this stub site; only
        # signoff_gate's signed runs are stubbed.
        signoff_gate = types.ModuleType("konsol.close.signoff_gate")
        signoff_gate.latest_signed_runs = lambda: {k: "ZZ-RUN" for k in site.signed}
        sys.modules["konsol.close.signoff_gate"] = signoff_gate
        close.signoff_gate = signoff_gate
        close.ownership_change = _load_path(
            "konsol.close.ownership_change", OWNERSHIP_CHANGE_PY)
        api = _load_path("close_rates_api_under_test", API_PY)
        site.frappe = frappe
        result = run(api)
        if require_json_safe:
            json.dumps(result)  # JSON-safe
        return result
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _call_raises(site, fy=2025, fp=7):
    with pytest.raises(Exception) as info:
        _call(site, fy, fp)
    return info.value


def _call_pending(site):
    return _invoke(site, lambda api: api.get_pending())


def _call_pending_raises(site):
    with pytest.raises(Exception) as info:
        _call_pending(site)
    return info.value


def _call_ownership(site, fy=2025, fp=7):
    return _invoke(site, lambda api: api.get_ownership(fy, fp))


def _call_ownership_raises(site, fy=2025, fp=7):
    with pytest.raises(Exception) as info:
        _call_ownership(site, fy, fp)
    return info.value


def _row(result, frm, to="GBP"):
    rows = [r for r in result["rows"] if (r["from_currency"], r["to_currency"]) == (frm, to)]
    assert len(rows) == 1, result["rows"]
    return rows[0]


def _awaiting(result):
    cells = []
    for row in result["rows"] + result["unrequired"]:
        for key in ("closing", "average"):
            if row[key]["status"] == "awaiting_approval":
                cells.append(row[key])
    return cells


# --- the grid -------------------------------------------------------------------

def test_the_close_lead_sees_two_rows_and_approves_another_users_draft_directly():
    site = _Site()
    result = _call(site)
    assert site.only_for_calls == [RATES_ROLES]
    assert len(result["rows"]) == 2
    eur = _row(result, "EUR")
    assert eur["required"] is True
    assert eur["closing"]["status"] == "approved"
    assert eur["average"]["status"] == "awaiting_approval"
    assert eur["average"]["owner"] == ANALYST
    assert eur["average"]["approve"]["mode"] == "direct"
    assert eur["average"]["edited_by"] == []
    usd = _row(result, "USD")
    assert usd["closing"]["status"] == "missing" and usd["average"]["status"] == "missing"
    assert result["summary"] == {"missing": 2, "awaiting_approval": 1, "approved": 1}
    assert result["can_approve"] is True and result["can_enter"] is True
    assert result["period"] == {"fiscal_year": 2025, "fiscal_period": 7, "period_code": "P07",
                                "status": "Open"}
    assert result["pairs_error"] is None and result["blockers"] == []
    assert result["threshold_pct"] == 50.0
    assert result["self_approval"] == "Blocked"
    assert result["policy_gaps"] == []
    assert site.needs_calls == [(2025, 7)]


def test_the_previous_approved_rate_and_the_true_rate_reach_the_cell():
    site = _Site()
    site.docs = [_ger("GER-1", "EUR", "GBP", "Closing", 88, 1, quoted_per="100",
                      erp_quote=87)]
    site.earlier = [_earlier("GER-A", "EUR", "GBP", "Closing", 0.80, 2025, 6),
                    _earlier("GER-B", "EUR", "GBP", "Closing", 0.70, 2025, 3),
                    _earlier("GER-C", "EUR", "GBP", "Closing", 0.60, 2024, 12)]
    result = _call(site)
    cell = _row(result, "EUR")["closing"]
    assert cell["rate"] == pytest.approx(0.88)
    assert cell["previous"]["name"] == "GER-A"
    assert cell["previous"]["label"] == "FY2025 P6 (GER-A)"
    assert cell["delta"] == pytest.approx(0.88 / 0.80 - 1, abs=1e-12)
    closing_calls = [c for c in site.move_calls if c["rate"] == pytest.approx(0.88)]
    assert closing_calls[0]["erp_rate"] == pytest.approx(0.87)
    assert closing_calls[0]["unit"] == "GBP per EUR"
    assert closing_calls[0]["threshold"] == 0.5


def test_failure_path_self_approval_under_blocked_is_refused():
    site = _Site()
    site.docs = [_ger("GER-2", "EUR", "GBP", "Average", 0.84, 0, owner=LEAD)]
    result = _call(site)
    cell = _row(result, "EUR")["average"]
    assert cell["approve"]["mode"] == "refused"
    assert "blocks self-approval" in cell["approve"]["message"]


def test_failure_path_approve_by_an_analyst_is_refused():
    site = _Site()
    site.user = ANALYST
    site.roles = {"EPM Analyst"}
    result = _call(site)
    cell = _row(result, "EUR")["average"]
    assert cell["approve"]["mode"] == "not_approver"
    assert result["can_approve"] is False
    assert result["can_enter"] is True


def test_failure_path_undeclared_threshold_is_a_gap_never_a_default():
    site = _Site()
    site.threshold = None
    result = _call(site)
    assert result["threshold_pct"] is None
    assert "rate_move_undeclared" in [g["code"] for g in result["policy_gaps"]]
    assert site.move_calls, "move_problem was never called"
    assert all(c["threshold"] is None for c in site.move_calls), site.move_calls


def test_failure_path_undeclared_self_approval_is_a_gap():
    site = _Site()
    site.policy = None
    result = _call(site)
    assert result["self_approval"] is None
    assert "self_approval_undeclared" in [g["code"] for g in result["policy_gaps"]]


def test_failure_path_clickhouse_down_lists_the_docs_with_required_none():
    site = _Site()
    site.needs_error = RuntimeError("connection refused")
    site.docs.append(_ger("GER-9", "JPY", "GBP", "Closing", 0.005, 0))
    result = _call(site)
    assert result["pairs_error"].startswith("RuntimeError")
    pairs = {(r["from_currency"], r["to_currency"]): r["required"] for r in result["rows"]}
    assert pairs == {("EUR", "GBP"): None, ("JPY", "GBP"): None}
    assert result["unrequired"] == []
    assert result["blockers"] == []


def test_a_group_with_no_reporting_currency_is_a_blocker():
    site = _Site()
    site.needs = ({("EUR", "GBP")}, ["ZZGRP"])
    result = _call(site)
    assert result["blockers"] == ["Consolidation Group ZZGRP has no reporting currency"]


def test_a_rate_for_an_unrequired_pair_is_listed_apart():
    site = _Site()
    site.docs.append(_ger("GER-9", "JPY", "GBP", "Closing", 0.005, 0))
    result = _call(site)
    assert [(r["from_currency"], r["required"]) for r in result["unrequired"]] == [("JPY", False)]
    assert result["unrequired"][0]["closing"]["approve"]["mode"] == "direct"


def test_failure_path_an_undeclared_period_is_refused():
    site = _Site()
    err = _call_raises(site, 2031, 9)
    assert "not declared" in str(err), err
    assert "FY2031 P09" in str(err), err
    assert site.needs_calls == []


def test_a_closed_period_cannot_be_entered():
    site = _Site()
    result = _call(site, 2025, 6)
    assert result["period"]["status"] == "Closed"
    assert result["can_enter"] is False
    assert result["can_approve"] is True


def test_string_arguments_are_cast_to_int():
    site = _Site()
    result = _call(site, "2025", "7")
    assert result["period"]["fiscal_year"] == 2025 and result["period"]["fiscal_period"] == 7


# --- roles ------------------------------------------------------------------------

def test_failure_path_the_entity_accountant_is_refused():
    site = _Site()
    site.roles = {"Entity Accountant"}
    err = _call_raises(site)
    assert type(err).__name__ == "PermissionError", err
    assert site.only_for_calls == [RATES_ROLES]
    assert site.reads == [] and site.needs_calls == []


def test_a_viewer_reads_but_can_neither_enter_nor_approve():
    site = _Site()
    site.user = VIEWER
    site.roles = {"EPM User"}
    result = _call(site)
    assert result["can_enter"] is False
    assert result["can_approve"] is False
    cells = _awaiting(result)
    assert cells and all(c["approve"]["mode"] == "not_approver" for c in cells)


# --- preparers (#305-W2-14) ---------------------------------------------------------

def test_failure_path_a_lead_who_edited_the_analysts_draft_is_refused_under_blocked():
    site = _Site()
    site.versions = [{"ref_doctype": "Group Exchange Rate", "docname": "GER-2", "owner": LEAD,
                      "data": json.dumps({"changed": [["quote", 0.83, 0.84]]})}]
    result = _call(site)
    cell = _row(result, "EUR")["average"]
    assert cell["owner"] == ANALYST
    assert cell["approve"]["mode"] == "refused"
    assert cell["edited_by"] == [LEAD]


def test_the_same_edit_under_allowed_with_reason_needs_a_reason():
    site = _Site()
    site.policy = "Allowed with reason"
    site.versions = [{"ref_doctype": "Group Exchange Rate", "docname": "GER-2", "owner": LEAD,
                      "data": json.dumps({"changed": [["quote", 0.83, 0.84]]})}]
    result = _call(site)
    cell = _row(result, "EUR")["average"]
    assert cell["approve"]["mode"] == "reason"
    assert "approval_api.approve" in cell["approve"]["message"]


def test_a_version_of_another_document_does_not_make_a_preparer():
    site = _Site()
    site.versions = [{"ref_doctype": "Group Exchange Rate", "docname": "GER-OTHER", "owner": LEAD,
                      "data": json.dumps({"changed": [["quote", 0.83, 0.84]]})}]
    result = _call(site)
    cell = _row(result, "EUR")["average"]
    assert cell["approve"]["mode"] == "direct"
    assert cell["edited_by"] == []


def test_an_approved_or_missing_cell_carries_no_approve_mode():
    site = _Site()
    result = _call(site)
    eur = _row(result, "EUR")
    assert eur["closing"]["approve"] is None and eur["closing"]["edited_by"] is None
    usd = _row(result, "USD")
    assert usd["closing"]["approve"] is None


# --- query count ----------------------------------------------------------------------

def _pairs_site(n):
    currencies = ["EUR", "USD", "JPY", "CHF", "SEK", "NOK"][:n]
    site = _Site()
    site.needs = ({(c, "GBP") for c in currencies}, [])
    site.docs = []
    site.earlier = []
    for i, c in enumerate(currencies):
        site.docs.append(_ger("GER-C%d" % i, c, "GBP", "Closing", 1.1, 0))
        site.docs.append(_ger("GER-A%d" % i, c, "GBP", "Average", 1.0, 1))
        site.earlier.append(_earlier("GER-E%d" % i, c, "GBP", "Closing", 1.0, 2025, 6))
    return site


def test_the_query_count_is_constant_in_the_number_of_pairs():
    one, six = _pairs_site(1), _pairs_site(6)
    r1, r6 = _call(one), _call(six)
    assert len(r1["rows"]) == 1 and len(r6["rows"]) == 6
    assert len(one.reads) == 8, one.reads  # 7 + 1 get_meta for quoted_per_options (E409b)
    assert len(six.reads) == 8, six.reads
    assert one.needs_calls == [(2025, 7)] and six.needs_calls == [(2025, 7)]
    versions = [r for r in six.reads if r == ("get_all", "Version")]
    assert len(versions) == 1


# --- quoted_per_options (E409b): the one source of truth is the doctype meta ---------

def test_get_rates_sends_quoted_per_options_from_the_group_exchange_rate_meta():
    site = _Site()
    result = _call(site)
    assert result["quoted_per_options"] == ["1", "10", "100", "1000", "10000"]


def test_a_changed_meta_changes_the_quoted_per_options_payload():
    site = _Site()
    site.quoted_per_options = "5\n\n50\n"
    result = _call(site)
    assert result["quoted_per_options"] == ["5", "50"]


# --- save_rate (E404): the Analyst saves a draft; nothing else can be set -----------

SAVE_PARAMS = ["fiscal_year", "fiscal_period", "from_currency", "to_currency", "rate_type",
               "quote", "quoted_per", "change_reason", "name"]
NEW_KEYS = {"doctype", "to_currency", "from_currency", "rate_type", "fiscal_year",
            "fiscal_period", "quote", "quoted_per", "change_reason"}
FORGED = ("docstatus", "source", "erp_quote", "source_note", "owner", "amended_from")


def _analyst_site():
    site = _Site()
    site.user = ANALYST
    site.roles = {"EPM Analyst"}
    return site


def _save(site, **kw):
    args = {"fiscal_year": 2025, "fiscal_period": 7, "from_currency": "USD",
            "to_currency": "GBP", "rate_type": "Closing", "quote": 0.79, "quoted_per": "1"}
    args.update(kw)
    return _invoke(site, lambda api: api.save_rate(**args))


def _save_raises(site, **kw):
    with pytest.raises(Exception) as info:
        _save(site, **kw)
    return info.value


def _no_ignore_flags(site, doc):
    assert not [k for k in vars(doc.flags) if k.startswith("ignore")], vars(doc.flags)
    assert not [k for k in site.frappe.flags if str(k).startswith("ignore")], site.frappe.flags


def test_save_rate_creates_a_new_draft_through_insert_with_only_the_nine_keys():
    site = _analyst_site()
    result = _save(site, change_reason="  RBI reference, 30 Sep  ")
    assert site.only_for_calls == [("EPM Analyst", "EPM Admin", "System Manager")]
    dicts = [c for c in site.get_doc_calls if isinstance(c, dict)]
    assert len(dicts) == 1
    assert set(dicts[0]) == NEW_KEYS, dicts[0]
    assert dicts[0]["doctype"] == "Group Exchange Rate"
    assert dicts[0]["change_reason"] == "RBI reference, 30 Sep"
    assert (dicts[0]["fiscal_year"], dicts[0]["fiscal_period"]) == (2025, 7)
    doc = site.new_docs[0]
    assert [c[0] for c in doc.calls] == ["insert"]
    _no_ignore_flags(site, doc)
    assert result == {"name": "GER-NEW", "docstatus": 0, "quote_label": doc.quote_label,
                      "source": "Manual"}


def test_save_rate_reads_the_period_and_the_grain_once_each():
    site = _analyst_site()
    _save(site)
    assert site.reads == [("sql", "fiscal_period_rows"), ("get_all", "Group Exchange Rate")], \
        site.reads


def test_forge_save_rate_takes_no_kwargs_and_names_only_the_nine_parameters():
    import inspect

    site = _analyst_site()
    sig = _invoke(site, lambda api: {"sig": [(p.name, p.kind) for p in
                                              inspect.signature(api.save_rate).parameters.values()]})
    names = [n for n, _ in sig["sig"]]
    assert names == SAVE_PARAMS, names
    kinds = {k for _, k in sig["sig"]}
    assert inspect.Parameter.VAR_KEYWORD not in kinds
    assert inspect.Parameter.VAR_POSITIONAL not in kinds
    for forged in FORGED:
        assert forged not in names, forged


def test_forge_a_forged_status_field_cannot_be_passed_at_all():
    site = _analyst_site()
    for forged in FORGED:
        err = _save_raises(site, **{forged: "ZZ forged"})
        assert isinstance(err, TypeError), (forged, err)
    assert site.new_docs == []


def test_failure_path_an_approved_key_with_no_name_is_refused_naming_the_amendment():
    site = _analyst_site()
    site.docs = [_ger("GER-1", "EUR", "GBP", "Closing", 0.85, 1)]
    err = _save_raises(site, from_currency="EUR")
    assert "GER-1" in str(err) and "amendment" in str(err), err
    assert "already the approved Closing rate" in str(err), err
    assert site.new_docs == []


def test_failure_path_a_draft_key_with_no_name_is_refused_naming_the_draft():
    site = _analyst_site()
    err = _save_raises(site, from_currency="EUR", rate_type="Average")
    assert "GER-2 is already a draft for this rate" in str(err), err
    assert site.new_docs == []


def test_a_cancelled_rate_does_not_block_a_new_draft():
    site = _analyst_site()
    site.docs = [_ger("GER-1", "EUR", "GBP", "Closing", 0.85, 2)]
    result = _save(site, from_currency="EUR")
    assert result["name"] == "GER-NEW"


def _named(site, name="GER-2", **kw):
    data = {"doctype": "Group Exchange Rate", "name": name, "from_currency": "EUR",
            "to_currency": "GBP", "rate_type": "Average", "fiscal_year": 2025,
            "fiscal_period": 7, "quote": 0.84, "quoted_per": "1", "change_reason": "old",
            "docstatus": 0, "owner": ANALYST, "source": "ERP pre-fill", "erp_quote": 0.83,
            "source_note": None, "amended_from": None, "quote_label": "x"}
    data.update(kw)
    doc = _FakeDoc(data)
    site.named[name] = doc
    return doc


def test_update_by_name_changes_only_quote_quoted_per_and_change_reason():
    site = _analyst_site()
    doc = _named(site)
    before = dict(doc._data)
    result = _save(site, name="GER-2", from_currency="EUR", rate_type="Average",
                   quote=8.5, quoted_per="10", change_reason="  RBI  ")
    assert [c[0] for c in doc.calls] == ["save"]
    changed = {k for k in set(before) | set(doc._data) if before.get(k) != doc._data.get(k)}
    assert changed == {"quote", "quoted_per", "change_reason", "quote_label"}, changed
    assert (doc.quote, doc.quoted_per, doc.change_reason) == (8.5, "10", "RBI")
    assert site.new_docs == []
    _no_ignore_flags(site, doc)
    assert result["name"] == "GER-2" and result["docstatus"] == 0
    assert site.reads == [("sql", "fiscal_period_rows"), ("get_doc", "Group Exchange Rate")]


def test_update_by_name_with_a_blank_reason_clears_it():
    site = _analyst_site()
    doc = _named(site)
    _save(site, name="GER-2", from_currency="EUR", rate_type="Average", change_reason="   ")
    assert not doc.change_reason


def test_failure_path_a_named_approved_rate_is_refused():
    site = _analyst_site()
    doc = _named(site, docstatus=1)
    err = _save_raises(site, name="GER-2", from_currency="EUR", rate_type="Average")
    assert "GER-2" in str(err) and "amend" in str(err), err
    assert doc.calls == []


def test_failure_path_a_named_doc_with_a_different_grain_is_refused():
    site = _analyst_site()
    doc = _named(site)
    for kw in ({"from_currency": "USD", "rate_type": "Average"},
               {"from_currency": "EUR", "rate_type": "Closing"},
               {"from_currency": "EUR", "rate_type": "Average", "fiscal_period": 6}):
        if kw.get("fiscal_period") == 6:
            site.periods = [_period(2025, 6, "Open"), _period(2025, 7, "Open")]
        err = _save_raises(site, name="GER-2", **kw)
        assert "GER-2" in str(err), err
        assert "EUR" in str(err) and "Average" in str(err), err
    assert doc.calls == []


def test_failure_path_a_closed_period_is_refused_before_any_get_doc():
    site = _analyst_site()
    err = _save_raises(site, fiscal_period=6)
    assert "is Closed" in str(err) and "reopen" in str(err), err
    assert "FY2025 P06" in str(err), err
    assert site.get_doc_calls == []
    assert ("get_all", "Group Exchange Rate") not in site.reads


def test_failure_path_an_undeclared_period_is_refused():
    site = _analyst_site()
    err = _save_raises(site, fiscal_year=2031, fiscal_period=9)
    assert "not declared" in str(err), err
    assert site.get_doc_calls == []


def test_failure_path_rate_type_spot_is_refused_before_any_read():
    site = _analyst_site()
    err = _save_raises(site, rate_type="Spot")
    assert "Spot" in str(err) and "Closing" in str(err), err
    assert site.reads == [] and site.get_doc_calls == []


def test_failure_path_the_entity_accountant_and_the_viewer_are_refused():
    for role in ("Entity Accountant", "EPM User"):
        site = _analyst_site()
        site.roles = {role}
        err = _save_raises(site)
        assert type(err).__name__ == "PermissionError", (role, err)
        assert site.reads == [] and site.get_doc_calls == []


def test_the_close_lead_may_save_the_analysts_draft_and_the_owner_is_untouched():
    site = _Site()
    doc = _named(site)
    _save(site, name="GER-2", from_currency="EUR", rate_type="Average", quote=0.845)
    assert doc.owner == ANALYST
    assert [c[0] for c in doc.calls] == ["save"]


# --- get_pending (E405): HER and OP drafts awaiting approval -----------------------

def test_get_pending_lists_her_direct_and_refuses_the_leads_own_op_under_blocked():
    site = _Site()
    site.her = [_her("HER-1", owner=ANALYST)]
    site.ops = [_op("OP-1", owner=LEAD)]
    site.user = LEAD
    site.roles = {"EPM Admin"}
    site.policy = "Blocked"
    result = _call_pending(site)
    json.dumps(result)
    her_item = next(i for i in result["items"] if i["doctype"] == "Historical Equity Rate")
    op_item = next(i for i in result["items"] if i["doctype"] == "Ownership Period")
    assert her_item["approve"]["mode"] == "direct"
    # failure path: self-approval under Blocked is refused
    assert op_item["approve"]["mode"] == "refused"
    assert result["counts"]["Historical Equity Rate"] == 1
    assert result["counts"]["Ownership Period"] == 1
    assert isinstance(her_item["created"], str) and isinstance(op_item["created"], str)


def test__iso_attaches_the_system_zone_to_a_naive_datetime():
    """L01e: ``rates_api._iso`` is the same ``timefmt.zoned_iso`` wrapper
    every sibling endpoint's ``_iso`` uses (tb_read_api.py, checks_api.py,
    trail_api.py, freshness_api.py, signoff_api.py) — not a naive
    ``isoformat()``."""
    api = _load_rates_api(_Site())
    # BST (+01:00) in July.
    assert api._iso(datetime(2025, 7, 1, 9, 0, 0)) == "2025-07-01T09:00:00+01:00"
    # GMT in January: +00:00, not the +01:00 of the summer.
    assert api._iso(datetime(2025, 1, 10, 8, 0, 0)) == "2025-01-10T08:00:00+00:00"


def test__iso_leaves_a_plain_date_alone_and_blank_stays_none():
    api = _load_rates_api(_Site())
    assert api._iso(date(2025, 10, 3)) == "2025-10-03"
    assert api._iso(None) is None and api._iso("") is None


# --- A55: every datetime get_pending sends carries the site's time zone -----------

_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
_OFFSET = re.compile(r"(Z|[+-]\d{2}:\d{2})$")


def _strings(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, str):
        yield value


def test_get_pending_payload_carries_no_naive_datetime():
    # Found live 2 Oct via L01d: get_pending's created showed raw, e.g.
    # '2026-09-16T18:13:05.213749' (no zone).
    site = _Site()
    site.her = [_her("HER-1", rate_date=date(2024, 1, 1),
                      creation=datetime(2025, 7, 1, 9, 0, 0))]
    site.ops = [_op("OP-1", effective_date=date(2025, 1, 1), end_date=date(2025, 12, 31),
                     creation=datetime(2025, 7, 2, 9, 0, 0))]
    result = _call_pending(site)
    stamps = [s for s in _strings(result) if _DATETIME.match(s)]
    naive = [s for s in stamps if not _OFFSET.search(s)]
    assert not naive, "datetimes sent without a time zone: %r" % naive
    assert sorted(stamps) == ["2025-07-01T09:00:00+01:00", "2025-07-02T09:00:00+01:00"], stamps


def test_failure_path_seen_by_the_analyst_every_item_is_not_approver():
    site = _Site()
    site.her = [_her("HER-1", owner=ANALYST)]
    site.ops = [_op("OP-1", owner=LEAD)]
    site.user = ANALYST
    site.roles = {"EPM Analyst"}
    result = _call_pending(site)
    assert result["items"] and all(i["approve"]["mode"] == "not_approver" for i in result["items"])
    assert result["can_approve"] is False


def test_under_allowed_with_reason_the_leads_own_op_needs_a_reason():
    site = _Site()
    site.ops = [_op("OP-1", owner=LEAD)]
    site.user = LEAD
    site.roles = {"EPM Admin"}
    site.policy = "Allowed with reason"
    result = _call_pending(site)
    op_item = next(i for i in result["items"] if i["doctype"] == "Ownership Period")
    assert op_item["approve"]["mode"] == "reason"
    assert "approval_api.approve" in op_item["approve"]["message"]


def test_no_drafts_gives_empty_items_and_zero_counts_never_missing_keys():
    site = _Site()
    result = _call_pending(site)
    assert result["items"] == []
    assert result["counts"]["Historical Equity Rate"] == 0
    assert result["counts"]["Ownership Period"] == 0


def test_failure_path_the_entity_accountant_is_refused_from_pending():
    site = _Site()
    site.her = [_her("HER-1")]
    site.roles = {"Entity Accountant"}
    err = _call_pending_raises(site)
    assert type(err).__name__ == "PermissionError", err
    assert site.reads == []


def test_pending_query_count_is_constant_in_the_number_of_drafts():
    one = _Site()
    one.her = [_her("HER-1")]
    one.ops = [_op("OP-1")]
    ten = _Site()
    ten.her = [_her("HER-%d" % i) for i in range(5)]
    ten.ops = [_op("OP-%d" % i) for i in range(5)]
    r1, r10 = _call_pending(one), _call_pending(ten)
    assert len(r1["items"]) == 2 and len(r10["items"]) == 10
    assert len(one.reads) == 7, one.reads
    assert len(ten.reads) == 7, ten.reads


# --- get_pending: Viewer and entity scope (#305-W2-10, W2-14) ----------------------

def test_a_viewer_reads_pending_but_every_item_is_not_approver():
    site = _Site()
    site.her = [_her("HER-1")]
    site.ops = [_op("OP-1")]
    site.user = VIEWER
    site.roles = {"EPM User"}
    result = _call_pending(site)
    assert result["items"] and all(i["approve"]["mode"] == "not_approver" for i in result["items"])
    assert result["can_approve"] is False


def test_failure_path_a_leak_items_are_cut_to_the_callers_allowed_entities():
    site = _Site()
    site.her = [_her("HER-A", data_area_id="ZZA"), _her("HER-X", data_area_id="ZZX")]
    site.allowed = {"ZZA"}
    result = _call_pending(site)
    assert len(result["items"]) == 1
    assert result["counts"]["hidden"] == 1
    dumped = json.dumps(result)
    assert "ZZX" not in dumped, dumped


def test_the_w2_14_case_an_op_edited_by_the_lead_is_refused_under_blocked():
    site = _Site()
    site.ops = [_op("OP-1", owner=ANALYST)]
    site.versions = [{"ref_doctype": "Ownership Period", "docname": "OP-1", "owner": LEAD,
                      "data": json.dumps({"changed": [["ownership_pct", 50, 60]]})}]
    site.user = LEAD
    site.roles = {"EPM Admin"}
    site.policy = "Blocked"
    result = _call_pending(site)
    op_item = result["items"][0]
    assert op_item["approve"]["mode"] == "refused"
    assert op_item["edited_by"] == [LEAD]


# --- get_ownership (E406): ownership gaps for the period, from scope_model ---------

def test_failure_path_a_tb_with_no_ownership_is_blocking_and_a_covered_entity_is_not():
    site = _Site()
    site.entities = [_entity("ZZA"), _entity("ZZB"), _entity("ZZC")]
    site.ops = [_op("OP-A", data_area_id="ZZA", effective_date=date(2025, 1, 1), docstatus=1)]
    site.tbs = [_tb("ZZA"), _tb("ZZB")]
    result = _call_ownership(site)
    assert result["blocking"] == [{
        "entity": "ZZB",
        "message": "ZZB has a submitted trial balance for FY2025 P07 but no approved ownership "
                   "period covering 2025-07-01: it is not consolidated. Record its ownership, or "
                   "cancel the trial balance (#305-W2-2).",
        "desk": "/app/ownership-period/new?data_area_id=ZZB",
    }]
    # ZZA is covered and has a TB: not listed anywhere.
    # ZZC is an Active leaf with no OP and no TB: out_of_scope only, never blocking.
    assert result["out_of_scope"] == ["ZZC"]
    assert result["in_scope_count"] == 1
    assert result["start_date"] == "2025-07-01"
    assert result["period"] == {"fiscal_year": 2025, "fiscal_period": 7}
    assert result["can_record"] is True
    assert result["hidden"] == 0


def test_an_ownership_period_whose_end_date_is_before_start_does_not_cover():
    site = _Site()
    site.entities = [_entity("ZZA")]
    site.ops = [_op("OP-A", data_area_id="ZZA", effective_date=date(2025, 1, 1),
                    end_date=date(2025, 6, 30), docstatus=1)]
    site.tbs = [_tb("ZZA")]
    result = _call_ownership(site)
    assert [b["entity"] for b in result["blocking"]] == ["ZZA"]
    assert result["out_of_scope"] == []
    assert result["in_scope_count"] == 0


def test_failure_path_an_undeclared_period_is_refused_from_get_ownership():
    site = _Site()
    err = _call_ownership_raises(site, 2031, 9)
    assert "not declared" in str(err), err
    assert "FY2031 P09" in str(err), err


def test_failure_path_the_entity_accountant_is_refused_from_get_ownership():
    site = _Site()
    site.roles = {"Entity Accountant"}
    err = _call_ownership_raises(site)
    assert type(err).__name__ == "PermissionError", err
    assert site.only_for_calls == [RATES_ROLES]
    assert site.reads == []


def test_ownership_query_count_is_constant_in_the_number_of_leaves():
    # O63: a caller who may record also reads the submitted nodes for the
    # change form (one more read; no node has an entity here, so no Entity
    # name read). A Viewer's count stays 4 (test_o63_a_viewer_gets_change_none).
    small = _Site()
    small.entities = [_entity("ZZA"), _entity("ZZB"), _entity("ZZC")]
    big = _Site()
    big.entities = [_entity("ZZ%03d" % i) for i in range(30)]
    _call_ownership(small)
    _call_ownership(big)
    assert len(small.reads) == 5, small.reads
    assert len(big.reads) == 5, big.reads


def test_an_epm_user_reads_ownership_with_can_record_false():
    site = _Site()
    site.entities = [_entity("ZZA")]
    site.user = VIEWER
    site.roles = {"EPM User"}
    site.can_record = False
    result = _call_ownership(site)
    assert site.only_for_calls == [RATES_ROLES]
    assert result["can_record"] is False


def test_failure_path_a_leak_blocking_is_cut_to_the_callers_allowed_entities():
    site = _Site()
    site.entities = [_entity("ZZA"), _entity("ZZB")]
    site.tbs = [_tb("ZZA"), _tb("ZZB")]
    site.allowed = {"ZZA"}
    result = _call_ownership(site)
    assert [b["entity"] for b in result["blocking"]] == ["ZZA"]
    assert result["hidden"] == 1
    assert result["blocking_hidden"] == 1
    dumped = json.dumps(result)
    assert "ZZB" not in dumped, dumped


def test_blocking_hidden_counts_only_hidden_blocking_entities_not_out_of_scope():
    # ZZA, ZZB: submitted TB, no ownership -> blocking. ZZC: no TB, no ownership
    # -> out_of_scope. allowed = {ZZA}: ZZB is a hidden blocker, ZZC is a hidden
    # out-of-scope entity. blocking_hidden must count only ZZB, while the
    # existing combined `hidden` field keeps counting both (N1).
    site = _Site()
    site.entities = [_entity("ZZA"), _entity("ZZB"), _entity("ZZC")]
    site.tbs = [_tb("ZZA"), _tb("ZZB")]
    site.allowed = {"ZZA"}
    result = _call_ownership(site)
    assert [b["entity"] for b in result["blocking"]] == ["ZZA"]
    assert result["out_of_scope"] == []
    assert result["blocking_hidden"] == 1
    assert result["hidden"] == 2
    dumped = json.dumps(result)
    assert "ZZB" not in dumped and "ZZC" not in dumped, dumped


def test_in_scope_count_is_cut_to_the_callers_allowed_entities_not_total_scope():
    # Three entities are all covered by ownership (all in scope). allowed
    # narrows to two of them: in_scope_count must be 2 (len(scope & allowed)),
    # never 3 (len(scope)) — the cut a mutation to plain len(scope) would miss
    # (N2).
    site = _Site()
    site.entities = [_entity("ZZA"), _entity("ZZB"), _entity("ZZC")]
    site.ops = [
        _op("OP-A", data_area_id="ZZA", effective_date=date(2025, 1, 1), docstatus=1),
        _op("OP-B", data_area_id="ZZB", effective_date=date(2025, 1, 1), docstatus=1),
        _op("OP-C", data_area_id="ZZC", effective_date=date(2025, 1, 1), docstatus=1),
    ]
    site.allowed = {"ZZA", "ZZB"}
    result = _call_ownership(site)
    assert result["in_scope_count"] == 2


def test_a_non_regular_period_is_refused_from_get_ownership():
    site = _Site()
    site.periods.append({
        "fiscal_year": 2025, "fiscal_period": 13, "period_code": "P13",
        "period_label": "Closing", "period_type": "Closing",
        "start_date": date(2025, 12, 31), "end_date": date(2025, 12, 31),
        "quarter": "", "status": "Open",
    })
    err = _call_ownership_raises(site, 2025, 13)
    assert type(err).__name__ == "ValidationError", err
    assert "Regular" in str(err), err
    assert "FY2025 P13" in str(err), err


# --- O55: preview_ownership_change (GET): refusals and effect, no write -----
# Decisions: #305-4.2-1 (structural effect only), #305-Q1-1 (a change ends its
# predecessor on approval), wireframe-4.2.md as drawn. The context and effect
# come from the REAL ownership_change.py / ownership_change_model.py.

PREVIEW_PARAMS = ["fiscal_year", "fiscal_period", "consolidation_group", "entity",
                  "ownership_pct", "consolidation_method"]
O55_GROUP = "ECL_GROUP"
O55_LEAF = "ZZ5B1"
O55_GOLDEN = os.path.join(FIXTURES, "close_ownership_preview_payload.json")
O55_GOLDEN_REFUSED = os.path.join(FIXTURES, "close_ownership_preview_refused.json")
O55_MODEL = None


def _o55_model():
    global O55_MODEL
    if O55_MODEL is None:
        spec = importlib.util.spec_from_file_location(
            "ownership_change_model_for_o55_test",
            os.path.join(CLOSE_DIR, "ownership_change_model.py"))
        O55_MODEL = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(O55_MODEL)
    return O55_MODEL


def _o55_calendar():
    """FY2025 as live declares it (measured 7 Oct): P00 Opening on 1 Jan,
    P01-P12 Regular months, P13 Closing on 31 Dec."""
    rows = [{"fiscal_year": 2025, "fiscal_period": 0, "period_code": "P00",
             "period_label": "Opening", "period_type": "Opening",
             "start_date": date(2025, 1, 1), "end_date": date(2025, 1, 1),
             "quarter": "", "status": "Open"}]
    for m in range(1, 13):
        end = (date(2025 + m // 12, m % 12 + 1, 1) - (date(2025, 1, 2) - date(2025, 1, 1)))
        rows.append({"fiscal_year": 2025, "fiscal_period": m, "period_code": "P%02d" % m,
                     "period_label": "P%02d" % m, "period_type": "Regular",
                     "start_date": date(2025, m, 1), "end_date": end,
                     "quarter": "Q%d" % ((m - 1) // 3 + 1), "status": "Open"})
    rows.append({"fiscal_year": 2025, "fiscal_period": 13, "period_code": "P13",
                 "period_label": "Closing", "period_type": "Closing",
                 "start_date": date(2025, 12, 31), "end_date": date(2025, 12, 31),
                 "quarter": "", "status": "Open"})
    return rows


def _o55_site(roles=("EPM Analyst",), user=ANALYST):
    site = _Site()
    site.user = user
    site.roles = set(roles)
    site.periods = _o55_calendar()
    site.ops = [_op("OP-ZZ5B1-1", data_area_id=O55_LEAF, group=O55_GROUP,
                    effective_date=date(2025, 1, 1), end_date=None, ownership_pct=100.0,
                    consolidation_method="full", owner=LEAD, docstatus=1)]
    # P09 is before the change (not re-signed); P11 and the Closing P13 are after (O54a).
    site.signed = [(2025, 9), (2025, 11), (2025, 13)]
    return site


def _preview(site, fy=2025, fp=10, group=O55_GROUP, entity=O55_LEAF, pct="80", method="full"):
    return _invoke(site, lambda api: api.preview_ownership_change(fy, fp, group, entity, pct,
                                                                  method))


def _preview_raises(site, **k):
    with pytest.raises(Exception) as info:
        _preview(site, **k)
    return info.value


def _golden(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _no_write(site):
    assert site.new_docs == [], site.new_docs
    assert [c for c in site.get_doc_calls] == [], site.get_doc_calls
    assert not [r for r in site.reads if r[0] == "sql" and r[1] != "fiscal_period_rows"], site.reads


def test_o55_preview_happy_path_matches_the_golden_payload():
    site = _o55_site()
    result = _preview(site)
    assert site.only_for_calls == [RATES_ROLES], site.only_for_calls
    assert result == _golden(O55_GOLDEN), json.dumps(result, indent=1)
    assert set(result) == {"problems", "effect", "current"}
    assert result["problems"] == []
    assert result["effect"]["resign"] == ["FY2025 P11", "FY2025 P13"]
    assert result["effect"]["current_ends"] == "2025-09-30"
    assert result["current"]["name"] == "OP-ZZ5B1-1"
    _no_write(site)


def test_o55_the_effect_is_the_real_models_for_the_periods_start():
    site = _o55_site()
    result = _preview(site)
    model = _o55_model()
    current = result["current"]
    expected = model.effect({"entity": O55_LEAF, "effective_date": "2025-10-01",
                             "ownership_pct": "80", "consolidation_method": "full"},
                            current, _o55_calendar(), sorted(site.signed))
    assert result["effect"] == expected
    assert "not previewed" in result["effect"]["not_shown"]


def test_o55_the_golden_payload_carries_no_amount():
    golden = _golden(O55_GOLDEN)
    assert golden["effect"]["not_shown"] == _o55_model().NOT_SHOWN
    for banned in ("goodwill_amount", "nci_amount", "amount", "result"):
        assert banned not in golden["effect"], banned


def test_o55_a_viewer_may_preview():
    site = _o55_site(roles=("EPM User",), user=VIEWER)
    result = _preview(site)
    assert result["problems"] == []
    assert result["effect"]["first_period"] == "FY2025 P10"
    _no_write(site)


def test_o55_refused_change_matches_the_golden_refused_payload_and_has_no_effect():
    site = _o55_site()
    site.ops.append(_op("OP-ZZ5B1-2", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 11, 1), ownership_pct=70.0,
                        consolidation_method="full", docstatus=0))
    result = _preview(site, pct="120")
    assert result == _golden(O55_GOLDEN_REFUSED), json.dumps(result, indent=1)
    assert result["problems"] == [
        "Ownership % must be a number from 0 to 100.",
        "A change for ZZ5B1 is already awaiting approval (OP-ZZ5B1-2): edit that draft.",
    ]
    assert result["effect"] is None
    assert result["current"]["name"] == "OP-ZZ5B1-1"
    _no_write(site)


def test_o55_failure_path_nan_pct_is_a_problem_never_an_effect():
    site = _o55_site()
    result = _preview(site, pct="nan")
    assert result["problems"] == ["Ownership % must be a number from 0 to 100."]
    assert result["effect"] is None


def test_o55_failure_path_no_current_ownership_is_the_desk_sentence():
    site = _o55_site()
    site.ops = []
    result = _preview(site)
    assert result["current"] is None
    assert result["effect"] is None
    assert result["problems"][0] == (
        "ZZ5B1 has no ownership for FY2025 P10: record its first ownership in Desk "
        "(an acquisition is a Business Combination).")


def test_o55_failure_path_a_hidden_entity_throws_before_any_read():
    site = _o55_site(roles=("EPM Analyst",))
    site.allowed = {"ZZOTHER"}
    err = _preview_raises(site)
    assert type(err).__name__ == "ValidationError", err
    assert "ZZ5B1" in str(err), err
    assert site.reads == [], site.reads
    _no_write(site)


def test_o55_failure_path_a_blank_entity_is_refused_for_a_scoped_caller():
    site = _o55_site()
    site.allowed = {"ZZ5B1"}
    err = _preview_raises(site, entity="")
    assert type(err).__name__ == "ValidationError", err
    assert site.reads == [], site.reads


def test_o55_a_visible_entity_previews_for_a_scoped_caller():
    site = _o55_site()
    site.allowed = {"ZZ5B1"}
    assert _preview(site)["problems"] == []


def test_o55_failure_path_a_closing_period_is_the_period_problem():
    site = _o55_site()
    result = _preview(site, fp=13)
    assert result["effect"] is None
    assert result["problems"][0] == (
        "FY2025 P13 is the Closing period, not a Regular one: an ownership change starts "
        "on the first day of a Regular period; pick a Regular period.")
    # The model's own first-day sentence follows: 31 Dec is inside P12.
    assert any(p.startswith("Ownership changes take effect on the first day of a period")
               for p in result["problems"]), result["problems"]
    _no_write(site)


def test_o55_failure_path_an_opening_period_is_never_silently_p01():
    # P00 starts on P01's first day, so the model alone sees a valid first
    # day: the endpoint must refuse the non-Regular period itself.
    site = _o55_site()
    result = _preview(site, fp=0)
    assert result["effect"] is None
    assert result["problems"][0] == (
        "FY2025 P00 is the Opening period, not a Regular one: an ownership change starts "
        "on the first day of a Regular period; pick a Regular period.")
    _no_write(site)


def test_o55_failure_path_an_undeclared_period_throws_naming_it():
    site = _o55_site()
    err = _preview_raises(site, fp=20)
    assert type(err).__name__ == "ValidationError", err
    assert "FY2025 P20" in str(err), err


def test_o55_failure_path_corrupt_overlap_throws_a_frappe_error_not_a_value_error():
    site = _o55_site()
    site.ops.append(_op("OP-ZZ5B1-X", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 6, 1), ownership_pct=60.0,
                        consolidation_method="full", docstatus=1))
    err = _preview_raises(site)
    assert type(err).__name__ == "ValidationError", err
    assert "corrupt" in str(err), err


def test_o55_failure_path_the_entity_accountant_is_refused():
    site = _o55_site(roles=("Entity Accountant",), user="zz-ea@example.com")
    err = _preview_raises(site)
    assert type(err).__name__ == "PermissionError", err
    assert site.reads == []


def test_o55_preview_names_only_its_parameters_and_takes_no_kwargs():
    # O66 adds ``name`` (the draft being edited), the only optional one.
    import inspect

    site = _o55_site()
    sig = _invoke(site, lambda api: {"sig": [
        (p.name, p.kind, p.default is None)
        for p in inspect.signature(api.preview_ownership_change).parameters.values()]})
    assert [n for n, _, _ in sig["sig"]] == PREVIEW_PARAMS + ["name"]
    kinds = {k for _, k, _ in sig["sig"]}
    assert inspect.Parameter.VAR_KEYWORD not in kinds
    assert inspect.Parameter.VAR_POSITIONAL not in kinds
    assert [n for n, _, none in sig["sig"] if none] == ["name"]


def test_o55_preview_is_a_get_with_a_literal_role_tuple():
    import ast

    with open(API_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "preview_ownership_change")
    deco = fn.decorator_list[0]
    assert ast.unparse(deco) == "frappe.whitelist(methods=['GET'])", ast.unparse(deco)
    first = fn.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        first = fn.body[1]
    call = first.value
    assert ast.unparse(call.func) == "frappe.only_for"
    assert isinstance(call.args[0], ast.Tuple), ast.unparse(call)
    assert tuple(e.value for e in call.args[0].elts) == RATES_ROLES


# --- O56: save_ownership_change (POST): the Analyst's draft ------------------
# Decisions: #305-4.2-1, #305-Q1-1, wireframe-4.2.md as drawn; R2 (the
# Analyst drafts, the Close Lead approves). Every refusal of the preview is
# thrown before any write; the controller (O53) decides the rest, so the
# insert and the save carry no ignore flag.

O56_SAVE_PARAMS = PREVIEW_PARAMS + ["name"]
O56_SAVE_ROLES = ("EPM Analyst", "EPM Admin", "System Manager")


def _o56_save(site, fy=2025, fp=10, group=O55_GROUP, entity=O55_LEAF, pct="80", method="full",
          name=None):
    return _invoke(site, lambda api: api.save_ownership_change(
        fy, fp, group, entity, pct, method, name=name))


def _o56_save_raises(site, **k):
    with pytest.raises(Exception) as info:
        _o56_save(site, **k)
    return info.value


def _no_save(site):
    """Nothing inserted and nothing saved: every refusal comes before the write."""
    assert site.new_docs == [], [d._data for d in site.new_docs]
    for doc in site.named.values():
        assert doc.calls == [], doc.calls


def _o56_draft(name="OP-ZZ5B1-D", entity=O55_LEAF, group=O55_GROUP,
               effective_date=date(2025, 10, 1), pct=70.0, docstatus=0, owner=ANALYST):
    row = _op(name, data_area_id=entity, group=group, effective_date=effective_date,
              ownership_pct=pct, consolidation_method="full", owner=owner,
              docstatus=docstatus)
    doc = _FakeDoc(dict(row, doctype="Ownership Period", supersedes="OP-ZZ5B1-1",
                        superseded_end_date=None))
    return row, doc


def test_o56_happy_path_inserts_one_draft_superseding_the_current_period():
    site = _o55_site()
    result = _o56_save(site)
    assert site.only_for_calls == [O56_SAVE_ROLES], site.only_for_calls
    assert result == {"name": "OP-NEW", "docstatus": 0}
    assert len(site.new_docs) == 1
    doc = site.new_docs[0]
    assert [c[0] for c in doc.calls] == ["insert"]
    assert doc.calls[0][1:] == ((), {}), "insert() with no ignore flag: the controller decides"
    assert doc._data == {
        "doctype": "Ownership Period", "consolidation_group": O55_GROUP,
        "data_area_id": O55_LEAF, "effective_date": "2025-10-01", "end_date": None,
        "ownership_pct": 80.0, "consolidation_method": "full",
        "supersedes": "OP-ZZ5B1-1", "superseded_end_date": None,
        "name": "OP-NEW", "docstatus": 0, "source": "Manual",
        "quote_label": doc._data["quote_label"]}


def test_o56_the_draft_ends_where_the_current_period_ends_as_the_effect_says():
    # The preview's effect gives after.to = the current period's end; the
    # saved draft must be that change, not an open-ended one.
    site = _o55_site()
    site.ops[0]["end_date"] = date(2025, 12, 31)
    _o56_save(site)
    doc = site.new_docs[0]
    assert doc._data["end_date"] == "2025-12-31"
    assert doc._data["superseded_end_date"] == "2025-12-31"


def test_o56_forged_keys_never_reach_the_doc_the_signature_is_pinned():
    import inspect

    site = _o55_site()
    sig = _invoke(site, lambda api: {"sig": [
        (p.name, p.kind, p.default is None)
        for p in inspect.signature(api.save_ownership_change).parameters.values()]})
    assert [n for n, _, _ in sig["sig"]] == O56_SAVE_PARAMS
    kinds = {k for _, k, _ in sig["sig"]}
    assert inspect.Parameter.VAR_KEYWORD not in kinds
    assert inspect.Parameter.VAR_POSITIONAL not in kinds
    for forged in ("docstatus", "supersedes", "superseded_end_date", "end_date",
                   "effective_date", "owner", "amended_from", "acquisition_price",
                   "is_disposal"):
        assert forged not in O56_SAVE_PARAMS, forged
    # The only optional parameter is ``name`` (default None).
    assert [n for n, _, none in sig["sig"] if none] == ["name"]


def test_o56_is_a_post_with_a_literal_role_tuple_and_never_commits():
    import ast

    with open(API_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "save_ownership_change")
    deco = fn.decorator_list[0]
    assert ast.unparse(deco) == "frappe.whitelist(methods=['POST'])", ast.unparse(deco)
    first = fn.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        first = fn.body[1]
    call = first.value
    assert ast.unparse(call.func) == "frappe.only_for"
    assert isinstance(call.args[0], ast.Tuple), ast.unparse(call)
    assert tuple(e.value for e in call.args[0].elts) == O56_SAVE_ROLES
    # Code only (the docstring may say what the endpoint does not do).
    used = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.keyword) and node.arg:
            used.add(node.arg)
    for banned in ("commit", "ignore_permissions", "ignore_validate", "ignore_mandatory",
                   "ignore_links", "submit"):
        assert banned not in used, banned


def test_o56_failure_path_a_viewer_is_refused_by_only_for_before_any_read():
    site = _o55_site(roles=("EPM User",), user=VIEWER)
    err = _o56_save_raises(site)
    assert type(err).__name__ == "PermissionError", err
    assert site.reads == [], site.reads
    _no_save(site)


def test_o56_failure_path_the_entity_accountant_is_refused():
    site = _o55_site(roles=("Entity Accountant",), user="zz-ea@example.com")
    err = _o56_save_raises(site)
    assert type(err).__name__ == "PermissionError", err
    assert site.reads == []
    _no_save(site)


def test_o56_failure_path_a_hidden_entity_throws_before_any_read():
    site = _o55_site()
    site.allowed = {"ZZOTHER"}
    err = _o56_save_raises(site)
    assert type(err).__name__ == "ValidationError", err
    assert "ZZ5B1" in str(err), err
    assert site.reads == [], site.reads
    _no_save(site)


def test_o56_failure_path_a_hidden_entity_with_a_name_throws_before_reading_the_draft():
    site = _o55_site()
    site.allowed = {"ZZOTHER"}
    row, doc = _o56_draft()
    site.ops.append(row)
    site.named[row["name"]] = doc
    err = _o56_save_raises(site, name=row["name"])
    assert type(err).__name__ == "ValidationError", err
    assert site.reads == [], site.reads
    assert site.get_doc_calls == [], site.get_doc_calls
    _no_save(site)


def _o56_refusal_cases():
    """(label, site mutator, save kwargs, the sentence the model writes)."""
    def no_current(site):
        site.ops = []

    def closed(site):
        for r in site.periods:
            if r["fiscal_period"] == 10:
                r["status"] = "Closed"

    def started_same_day(site):
        site.ops[0]["effective_date"] = date(2025, 10, 1)

    def later(site):
        site.ops[0]["end_date"] = date(2025, 10, 31)
        site.ops.append(_op("OP-ZZ5B1-L", data_area_id=O55_LEAF, group=O55_GROUP,
                            effective_date=date(2025, 11, 1), ownership_pct=90.0,
                            consolidation_method="full", docstatus=1))

    def pending(site):
        site.ops.append(_op("OP-ZZ5B1-P", data_area_id=O55_LEAF, group=O55_GROUP,
                            effective_date=date(2025, 11, 1), ownership_pct=70.0,
                            consolidation_method="full", docstatus=0))

    def nothing(site):
        pass

    return [
        ("no current", no_current, {},
         "ZZ5B1 has no ownership for FY2025 P10: record its first ownership in Desk "
         "(an acquisition is a Business Combination)."),
        ("non-Regular period", nothing, {"fp": 13},
         "FY2025 P13 is the Closing period, not a Regular one: an ownership change starts "
         "on the first day of a Regular period; pick a Regular period."),
        ("first day", nothing, {"fp": 13},
         "Ownership changes take effect on the first day of a period: the warehouse reads "
         "ownership on each period's first day. 2025-12-31 is inside FY2025 P12;"),
        ("Opening period", nothing, {"fp": 0},
         "FY2025 P00 is the Opening period, not a Regular one"),
        ("closed period", closed, {},
         "FY2025 P10 is Closed: an ownership change must start in an Open period."),
        ("pct 120", nothing, {"pct": "120"}, "Ownership % must be a number from 0 to 100."),
        ("pct nan", nothing, {"pct": "nan"}, "Ownership % must be a number from 0 to 100."),
        ("pct inf", nothing, {"pct": "inf"}, "Ownership % must be a number from 0 to 100."),
        ("method", nothing, {"method": "bogus"},
         "Method must be one of full, proportional, equity, none."),
        ("nothing changes", nothing, {"pct": "100"},
         "Nothing changes: ZZ5B1 is already 100 % full from 2025-01-01."),
        ("starts same day", started_same_day, {},
         "The change must start after the current period's start (2025-10-01)."),
        ("later exists", later, {},
         "ZZ5B1 already has an ownership period from 2025-11-01: change or cancel that one "
         "first."),
        ("pending exists", pending, {},
         "A change for ZZ5B1 is already awaiting approval (OP-ZZ5B1-P): edit that draft."),
    ]


def test_o56_failure_path_every_refusal_throws_its_sentence_with_no_insert():
    for label, mutate, kwargs, sentence in _o56_refusal_cases():
        site = _o55_site()
        mutate(site)
        err = _o56_save_raises(site, **kwargs)
        assert type(err).__name__ == "ValidationError", (label, err)
        assert sentence in str(err), (label, str(err))
        _no_save(site)


def test_o56_failure_path_every_problem_is_in_the_one_refusal():
    site = _o55_site()
    site.ops.append(_op("OP-ZZ5B1-P", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 11, 1), ownership_pct=70.0,
                        consolidation_method="full", docstatus=0))
    err = _o56_save_raises(site, pct="120")
    assert "Ownership % must be a number from 0 to 100." in str(err), err
    assert "already awaiting approval (OP-ZZ5B1-P)" in str(err), err
    _no_save(site)


def test_o56_failure_path_corrupt_overlap_throws_with_no_insert():
    site = _o55_site()
    site.ops.append(_op("OP-ZZ5B1-X", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 6, 1), ownership_pct=60.0,
                        consolidation_method="full", docstatus=1))
    err = _o56_save_raises(site)
    assert type(err).__name__ == "ValidationError", err
    assert "corrupt" in str(err), err
    _no_save(site)


def test_o56_edit_saves_the_named_draft_leaving_it_out_of_the_pending_check():
    # ``ownership_change.context`` reports every draft on the node as pending;
    # the draft being edited is excluded by its exact name.
    site = _o55_site()
    row, doc = _o56_draft()
    site.ops.append(row)
    site.named[row["name"]] = doc
    result = _o56_save(site, pct="80", method="equity", name=row["name"])
    assert result == {"name": "OP-ZZ5B1-D", "docstatus": 0}
    assert site.new_docs == []
    assert [c[0] for c in doc.calls] == ["save"]
    assert doc.calls[0][1:] == ((), {}), "save() with no ignore flag: the controller decides"
    assert doc._data["ownership_pct"] == 80.0
    assert doc._data["consolidation_method"] == "equity"
    assert doc._data["supersedes"] == "OP-ZZ5B1-1"
    assert doc._data["superseded_end_date"] is None
    assert doc._data["effective_date"] == date(2025, 10, 1), "the period is unchanged"


def test_o56_failure_path_edit_still_refuses_another_pending_draft_on_the_node():
    site = _o55_site()
    row, doc = _o56_draft()
    site.ops.append(row)
    site.named[row["name"]] = doc
    site.ops.append(_op("OP-ZZ5B1-E", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 11, 1), ownership_pct=75.0,
                        consolidation_method="full", docstatus=0))
    err = _o56_save_raises(site, name=row["name"])
    assert ("A change for ZZ5B1 is already awaiting approval (OP-ZZ5B1-E): edit that draft."
            in str(err)), err
    assert "OP-ZZ5B1-D" not in str(err), "the draft being edited is left out"
    _no_save(site)


def test_o56_failure_path_the_exclude_is_the_exact_name_never_a_pattern():
    # A draft whose name merely starts with the edited one stays pending.
    site = _o55_site()
    row, doc = _o56_draft()
    site.ops.append(row)
    site.named[row["name"]] = doc
    site.ops.append(_op("OP-ZZ5B1-D2", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 11, 1), ownership_pct=75.0,
                        consolidation_method="full", docstatus=0))
    err = _o56_save_raises(site, name=row["name"])
    assert "awaiting approval (OP-ZZ5B1-D2)" in str(err), err
    _no_save(site)


def test_o56_failure_path_editing_an_approved_period_is_refused():
    site = _o55_site()
    site.named["OP-ZZ5B1-1"] = _FakeDoc(dict(site.ops[0], doctype="Ownership Period"))
    err = _o56_save_raises(site, name="OP-ZZ5B1-1")
    assert type(err).__name__ == "ValidationError", err
    assert str(err).startswith("OP-ZZ5B1-1 is approved: record a new change instead."), err
    _no_save(site)


def test_o56_failure_path_editing_a_cancelled_period_is_refused():
    site = _o55_site()
    row, doc = _o56_draft(docstatus=2)
    site.ops.append(row)
    site.named[row["name"]] = doc
    err = _o56_save_raises(site, name=row["name"])
    assert str(err).startswith("OP-ZZ5B1-D is cancelled: record a new change instead."), err
    _no_save(site)


def test_o56_failure_path_editing_another_entitys_draft_is_refused():
    site = _o55_site()
    row, doc = _o56_draft(name="OP-ZZOTHER-D", entity="ZZOTHER")
    site.ops.append(row)
    site.named[row["name"]] = doc
    err = _o56_save_raises(site, name=row["name"])
    assert type(err).__name__ == "ValidationError", err
    assert "OP-ZZOTHER-D is the change for ZZOTHER in ECL_GROUP from 2025-10-01" in str(err), err
    _no_save(site)


def test_o56_failure_path_editing_a_draft_into_another_period_is_refused():
    # The draft's name carries its effective date (autoname), so a period
    # change is a new draft, never a silent move.
    site = _o55_site()
    row, doc = _o56_draft()
    site.ops.append(row)
    site.named[row["name"]] = doc
    err = _o56_save_raises(site, fp=11, name=row["name"])
    assert type(err).__name__ == "ValidationError", err
    assert "OP-ZZ5B1-D is the change for ZZ5B1 in ECL_GROUP from 2025-10-01" in str(err), err
    assert "2025-11-01" in str(err), err
    _no_save(site)


# --- O66: preview_ownership_change takes the draft being edited --------------
# wireframe-4.2.md §1 ("The Analyst can edit it until it is approved"). The
# preview passes ``name`` as the same exact-name exclude as O56's save, and
# refuses (as data, like every other preview problem) a name that is not a
# draft of the same node and first day.


def _o66_preview(site, fy=2025, fp=10, group=O55_GROUP, entity=O55_LEAF, pct="80",
                 method="full", name=None):
    return _invoke(site, lambda api: api.preview_ownership_change(
        fy, fp, group, entity, pct, method, name=name))


def _o66_site_with_draft(**k):
    site = _o55_site(**k)
    row, doc = _o56_draft()
    site.ops.append(row)
    site.named[row["name"]] = doc
    return site, row


def _o66_no_write(site):
    assert site.new_docs == [], [d._data for d in site.new_docs]
    for doc in site.named.values():
        assert doc.calls == [], doc.calls


def test_o66_failure_path_without_name_the_draft_still_refuses_as_pending():
    # The base line: without ``name`` the node's draft is pending.
    site, row = _o66_site_with_draft()
    result = _o66_preview(site)
    assert result["problems"] == [
        "A change for ZZ5B1 is already awaiting approval (OP-ZZ5B1-D): edit that draft."]
    assert result["effect"] is None


def test_o66_preview_of_the_draft_being_edited_is_not_refused_as_pending():
    site, row = _o66_site_with_draft()
    result = _o66_preview(site, name=row["name"])
    assert site.only_for_calls == [RATES_ROLES], site.only_for_calls
    assert result["problems"] == [], result["problems"]
    assert set(result) == {"problems", "effect", "current"}
    # The effect is the one the plain preview gives for the same change.
    assert result["effect"] == _preview(_o55_site())["effect"]
    assert result["current"]["name"] == "OP-ZZ5B1-1"
    _o66_no_write(site)


def test_o66_a_viewer_may_preview_an_edit():
    site, row = _o66_site_with_draft(roles=("EPM User",), user=VIEWER)
    result = _o66_preview(site, name=row["name"])
    assert result["problems"] == []
    assert result["effect"]["first_period"] == "FY2025 P10"
    _o66_no_write(site)


def test_o66_failure_path_another_draft_on_the_node_still_refuses():
    site, row = _o66_site_with_draft()
    site.ops.append(_op("OP-ZZ5B1-E", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 11, 1), ownership_pct=75.0,
                        consolidation_method="full", docstatus=0))
    result = _o66_preview(site, name=row["name"])
    assert result["problems"] == [
        "A change for ZZ5B1 is already awaiting approval (OP-ZZ5B1-E): edit that draft."]
    assert result["effect"] is None
    _o66_no_write(site)


def test_o66_failure_path_the_exclude_is_the_exact_name_never_a_pattern():
    site, row = _o66_site_with_draft()
    site.ops.append(_op("OP-ZZ5B1-D2", data_area_id=O55_LEAF, group=O55_GROUP,
                        effective_date=date(2025, 11, 1), ownership_pct=75.0,
                        consolidation_method="full", docstatus=0))
    result = _o66_preview(site, name=row["name"])
    assert result["problems"] == [
        "A change for ZZ5B1 is already awaiting approval (OP-ZZ5B1-D2): edit that draft."]


def test_o66_failure_path_the_name_of_an_approved_period_is_refused():
    site = _o55_site()
    site.named["OP-ZZ5B1-1"] = _FakeDoc(dict(site.ops[0], doctype="Ownership Period"))
    result = _o66_preview(site, name="OP-ZZ5B1-1")
    assert result["problems"][0] == "OP-ZZ5B1-1 is approved: record a new change instead."
    assert result["effect"] is None
    _o66_no_write(site)


def test_o66_failure_path_the_name_of_a_cancelled_draft_is_refused():
    site = _o55_site()
    row, doc = _o56_draft(docstatus=2)
    site.ops.append(row)
    site.named[row["name"]] = doc
    result = _o66_preview(site, name=row["name"])
    assert result["problems"][0] == "OP-ZZ5B1-D is cancelled: record a new change instead."
    assert result["effect"] is None


def test_o66_failure_path_another_nodes_draft_is_refused():
    site = _o55_site()
    row, doc = _o56_draft(name="OP-ZZOTHER-D", entity="ZZOTHER")
    site.ops.append(row)
    site.named[row["name"]] = doc
    result = _o66_preview(site, name=row["name"])
    assert result["problems"][0].startswith(
        "OP-ZZOTHER-D is the change for ZZOTHER in ECL_GROUP from 2025-10-01"), result
    assert result["effect"] is None
    _o66_no_write(site)


def test_o66_failure_path_the_draft_previewed_into_another_period_is_refused():
    # The save refuses moving a draft to another period (its name carries the
    # date); the preview says so before the save is tried.
    site, row = _o66_site_with_draft()
    result = _o66_preview(site, fp=11, name=row["name"])
    assert result["problems"][0].startswith(
        "OP-ZZ5B1-D is the change for ZZ5B1 in ECL_GROUP from 2025-10-01"), result
    assert "2025-11-01" in result["problems"][0]
    assert result["effect"] is None


def test_o66_failure_path_a_hidden_entitys_draft_is_refused_without_naming_it():
    site = _o55_site()
    site.allowed = {O55_LEAF}
    row, doc = _o56_draft(name="OP-ZZHIDDEN-D", entity="ZZHIDDEN")
    site.ops.append(row)
    site.named[row["name"]] = doc
    result = _o66_preview(site, name=row["name"])
    assert result["effect"] is None
    assert result["problems"][0] == (
        "OP-ZZHIDDEN-D is not a draft you can edit here: pick a draft of ZZ5B1 in ECL_GROUP "
        "from 2025-10-01.")
    assert not any("ZZHIDDEN " in p or "for ZZHIDDEN" in p for p in result["problems"])


def test_o66_failure_path_a_hidden_entity_with_a_name_throws_before_reading_the_draft():
    site, row = _o66_site_with_draft()
    site.allowed = {"ZZOTHER"}
    with pytest.raises(Exception) as info:
        _o66_preview(site, name=row["name"])
    assert type(info.value).__name__ == "ValidationError", info.value
    assert site.reads == [], site.reads
    assert site.get_doc_calls == [], site.get_doc_calls


def test_o66_failure_path_an_unknown_name_throws():
    site = _o55_site()
    with pytest.raises(Exception) as info:
        _o66_preview(site, name="OP-ZZ-MISSING")
    assert type(info.value).__name__ == "ValidationError", info.value
    assert "OP-ZZ-MISSING" in str(info.value)


def test_o66_failure_path_the_entity_accountant_is_refused_with_a_name():
    site, row = _o66_site_with_draft(roles=("Entity Accountant",), user="zz-ea@example.com")
    with pytest.raises(Exception) as info:
        _o66_preview(site, name=row["name"])
    assert type(info.value).__name__ == "PermissionError", info.value
    assert site.reads == []


# --- O57: get_pending's OP drafts carry their structural effect (story 4.2,
# 4.3; C-O4; #305-4.2-1, #305-Q1-1). The effect is computed by the REAL
# ownership_change.effect_for (loaded in _invoke) and checked against the REAL
# ownership_change_model.effect, loaded by path. ---------------------------------

O57_GOLDEN = os.path.join(FIXTURES, "close_rates_pending_payload.json")
O57_DESK_LEAF = "ZZ5B2"


def _o57_site():
    """The O55 site (ZZ5B1 100 % full from 2025-01-01, approved; P09, P11
    and P13 signed) plus three drafts: the Analyst's change of ZZ5B1 to 80 %
    full from FY2025 P10 (``supersedes`` the approved period), a Desk
    "Record ownership" draft for ZZ5B2 (no ``supersedes``), and one Historical
    Equity Rate. The Close Lead reads it under Blocked."""
    site = _o55_site(roles=("EPM Admin",), user=LEAD)
    site.policy = "Blocked"
    change = _op("OP-ZZ5B1-2025-10-01", data_area_id=O55_LEAF, group=O55_GROUP,
                 effective_date=date(2025, 10, 1), end_date=None, ownership_pct=80.0,
                 consolidation_method="full", owner=ANALYST,
                 creation=datetime(2025, 10, 2, 9, 0, 0), docstatus=0)
    change["supersedes"] = "OP-ZZ5B1-1"
    change["superseded_end_date"] = None
    desk = _op("OP-ZZ5B2-2025-10-01", data_area_id=O57_DESK_LEAF, group=O55_GROUP,
               effective_date=date(2025, 10, 1), end_date=None, ownership_pct=60.0,
               consolidation_method="equity", owner=ANALYST,
               creation=datetime(2025, 10, 3, 9, 0, 0), docstatus=0)
    site.ops.extend([change, desk])
    site.her = [_her("HER-ZZ5B1-1", data_area_id=O55_LEAF, group=O55_GROUP,
                     creation=datetime(2025, 10, 1, 9, 0, 0))]
    return site


def _o57_item(result, name):
    [item] = [i for i in result["items"] if i["name"] == name]
    return item


def test_o57_pending_matches_the_golden_payload():
    site = _o57_site()
    result = _call_pending(site)
    assert result == _golden(O57_GOLDEN), json.dumps(result, indent=1)


def test_o57_a_change_draft_carries_the_real_models_effect():
    site = _o57_site()
    result = _call_pending(site)
    item = _o57_item(result, "OP-ZZ5B1-2025-10-01")
    expected = _o55_model().effect(
        {"entity": O55_LEAF, "effective_date": "2025-10-01", "ownership_pct": 80.0,
         "consolidation_method": "full"},
        {"name": "OP-ZZ5B1-1", "effective_date": "2025-01-01", "end_date": None,
         "ownership_pct": 100.0, "consolidation_method": "full"},
        _o55_calendar(), sorted(site.signed))
    assert item["effect"] == expected
    assert item["effect"]["before"]["pct"] == 100.0 and item["effect"]["after"]["pct"] == 80.0
    assert item["effect"]["current_ends"] == "2025-09-30"
    assert item["effect"]["resign"] == ["FY2025 P11", "FY2025 P13"]
    # The detail text is unchanged (O57 goal).
    assert item["detail"] == "80% · full"
    assert item["approve"]["mode"] == "direct"


def test_o57_failure_path_a_desk_draft_without_supersedes_has_effect_none():
    """A Desk "Record ownership" draft names no predecessor: its effect is
    None (the screen says "Drafted in Desk: effect not previewed"), never a
    guessed before/after."""
    site = _o57_site()
    result = _call_pending(site)
    item = _o57_item(result, "OP-ZZ5B2-2025-10-01")
    assert "effect" in item and item["effect"] is None
    assert item["detail"] == "60% · equity"


def test_o57_her_items_carry_no_effect():
    result = _call_pending(_o57_site())
    item = _o57_item(result, "HER-ZZ5B1-1")
    assert "effect" not in item


def test_o57_failure_path_a_supersedes_that_is_not_approved_is_refused_naming_the_draft():
    """Corrupt data (the named predecessor is not an approved period) is
    refused with a sentence naming the draft, never shown with a guessed
    effect or silently without one."""
    site = _o57_site()
    site.ops[0]["docstatus"] = 2  # the predecessor was cancelled after the draft
    err = _call_pending_raises(site)
    assert type(err).__name__ == "ValidationError", err
    assert "OP-ZZ5B1-2025-10-01" in str(err), err
    assert "OP-ZZ5B1-1" in str(err), err


def test_o57_reads_grow_only_for_a_change_draft_and_nothing_is_written():
    """Each visible OP draft with ``supersedes`` costs ``effect_for``'s reads
    (its predecessor and the calendar; the signed runs are stubbed here); a
    Desk draft costs none."""
    desk_only = _o57_site()
    desk_only.ops = [r for r in desk_only.ops if not r.get("supersedes")]
    with_change = _o57_site()
    _call_pending(desk_only)
    _call_pending(with_change)
    assert len(desk_only.reads) == 7, desk_only.reads
    assert len(with_change.reads) == 9, with_change.reads
    extra = list(with_change.reads)
    for read in desk_only.reads:
        extra.remove(read)
    assert sorted(extra) == sorted([("get_all", "Ownership Period"),
                                    ("sql", "fiscal_period_rows")]), extra
    assert with_change.new_docs == [] and with_change.get_doc_calls == []


def test_o57_failure_path_a_hidden_draft_is_never_read_for_its_effect():
    site = _o57_site()
    site.allowed = {O57_DESK_LEAF}
    result = _call_pending(site)
    assert [i["name"] for i in result["items"]] == ["OP-ZZ5B2-2025-10-01"]
    assert result["counts"]["hidden"] == 2
    # Only the one draft list read: no predecessor, no calendar.
    assert site.reads.count(("get_all", "Ownership Period")) == 1, site.reads
    assert ("sql", "fiscal_period_rows") not in site.reads, site.reads
    assert O55_LEAF not in json.dumps(result)


# --- O63: get_ownership carries the change form's choices (story 4.2;
# #305-4.2-1; C-O2, C-O3; wireframe-4.2.md section 1). Entities come with
# their node's own group (one item per node, never a guessed one); periods are
# the Open Regular ones, read from the one calendar read. -------------------

O63_GOLDEN = os.path.join(FIXTURES, "close_ownership_payload.json")
O63_SUB = "ZZ_SUBGROUP"


def _o63_period(fy, fp, status, period_type="Regular", start=None):
    start = start or date(fy, fp, 1)
    return {"fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
            "period_label": "P%02d" % fp, "period_type": period_type,
            "start_date": start, "end_date": start, "quarter": "", "status": status}


def _o63_site(roles=("EPM Analyst",), user=ANALYST):
    """FY2026 P01 is listed first in the stub calendar, so calendar order is
    the endpoint's, not the stub's. FY2025 P09 is Closed, P13 is an Open
    Closing period: neither is a choice. ZZ5B1 sits on two nodes (ECL_GROUP
    and ZZ_SUBGROUP; ECL_GROUP twice, an older closed period and the current
    one). ZZ5B2 has only a draft (C-O3: a first ownership stays in Desk).
    ZZ5B3 has a submitted period that starts later. The group node's own
    period has no entity."""
    site = _Site()
    site.user = user
    site.roles = set(roles)
    site.periods = [_o63_period(2026, 1, "Open"), _o63_period(2025, 9, "Closed"),
                    _o63_period(2025, 10, "Open"),
                    _o63_period(2025, 13, "Open", "Closing", date(2025, 12, 31))]
    site.entities = [dict(_entity("ZZ5B1"), entity_name="ZZ Five B One"),
                     dict(_entity("ZZ5B2"), entity_name="ZZ Five B Two"),
                     dict(_entity("ZZ5B3"), entity_name="ZZ Five B Three")]
    site.ops = [
        _op("OP-ZZ5B1-0", data_area_id=O55_LEAF, group=O55_GROUP,
            effective_date=date(2024, 1, 1), end_date=date(2024, 12, 31), docstatus=1),
        _op("OP-ZZ5B1-1", data_area_id=O55_LEAF, group=O55_GROUP,
            effective_date=date(2025, 1, 1), docstatus=1),
        _op("OP-ZZ5B1-SUB", data_area_id=O55_LEAF, group=O63_SUB,
            effective_date=date(2025, 1, 1), docstatus=1),
        _op("OP-ZZ5B2-D", data_area_id="ZZ5B2", group=O55_GROUP,
            effective_date=date(2025, 1, 1), docstatus=0),
        _op("OP-ZZ5B3-1", data_area_id="ZZ5B3", group=O55_GROUP,
            effective_date=date(2025, 12, 1), docstatus=1),
        _op("OP-GROUP-1", data_area_id=None, group=O55_GROUP,
            effective_date=date(2025, 1, 1), docstatus=1),
    ]
    site.tbs = [_tb("ZZ5B1", fp=10)]
    return site


def _o63_call(site):
    return _call_ownership(site, 2025, 10)


def test_o63_ownership_matches_the_golden_payload():
    site = _o63_site()
    result = _o63_call(site)
    assert result == _golden(O63_GOLDEN), json.dumps(result, indent=1)


def test_o63_change_entities_carry_their_nodes_group_one_item_per_node():
    result = _o63_call(_o63_site())
    assert result["change"]["entities"] == [
        {"entity": "ZZ5B1", "entity_name": "ZZ Five B One", "consolidation_group": O55_GROUP},
        {"entity": "ZZ5B1", "entity_name": "ZZ Five B One", "consolidation_group": O63_SUB},
        {"entity": "ZZ5B3", "entity_name": "ZZ Five B Three",
         "consolidation_group": O55_GROUP},
    ]


def test_o63_change_periods_are_only_the_open_regular_ones_in_calendar_order():
    result = _o63_call(_o63_site())
    assert result["change"]["periods"] == [
        {"fiscal_year": 2025, "fiscal_period": 10, "label": "FY2025 P10",
         "start_date": "2025-10-01"},
        {"fiscal_year": 2026, "fiscal_period": 1, "label": "FY2026 P01",
         "start_date": "2026-01-01"},
    ]


def test_o63_the_calendar_is_read_once():
    site = _o63_site()
    _o63_call(site)
    assert site.reads.count(("sql", "fiscal_period_rows")) == 1, site.reads


def test_o63_failure_path_a_viewer_gets_change_none_and_no_extra_read():
    site = _o63_site(roles=("EPM User",), user=VIEWER)
    site.can_record = False
    result = _o63_call(site)
    assert result["can_record"] is False
    assert "change" in result and result["change"] is None
    assert len(site.reads) == 4, site.reads


def test_o63_failure_path_a_scoped_caller_never_sees_an_entity_outside_scope():
    site = _o63_site()
    site.allowed = {"ZZ5B3"}
    result = _o63_call(site)
    assert result["change"]["entities"] == [
        {"entity": "ZZ5B3", "entity_name": "ZZ Five B Three",
         "consolidation_group": O55_GROUP}]
    # The existing hidden rule counts out-of-scope ZZ5B2 only; the change list
    # adds nothing to it.
    assert result["hidden"] == 1
    assert "ZZ5B1" not in json.dumps(result)


def test_o63_failure_path_an_entity_on_two_nodes_gives_two_items():
    result = _o63_call(_o63_site())
    groups = [e["consolidation_group"] for e in result["change"]["entities"]
              if e["entity"] == "ZZ5B1"]
    assert groups == [O55_GROUP, O63_SUB]


def test_o63_reads_are_constant_in_the_number_of_nodes():
    small = _o63_site()
    big = _o63_site()
    big.entities = big.entities + [dict(_entity("ZZN%02d" % i), entity_name="N%d" % i)
                                   for i in range(20)]
    big.ops = big.ops + [_op("OP-ZZN%02d" % i, data_area_id="ZZN%02d" % i,
                             group=O55_GROUP, effective_date=date(2025, 1, 1), docstatus=1)
                         for i in range(20)]
    _o63_call(small)
    _o63_call(big)
    assert len(small.reads) == 6, small.reads
    assert len(big.reads) == len(small.reads), big.reads
