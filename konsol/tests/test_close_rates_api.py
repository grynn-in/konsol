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
import sys
import types
from datetime import date, datetime

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")
API_PY = os.path.join(CLOSE_DIR, "rates_api.py")

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
        assert arg == "Group Exchange Rate", arg
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
        self._data.setdefault("name", "GER-NEW")
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


def _invoke(site, run):
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
             "close_rates_api_under_test"]
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
        api = _load_path("close_rates_api_under_test", API_PY)
        site.frappe = frappe
        result = run(api)
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
    assert len(one.reads) == 7, one.reads
    assert len(six.reads) == 7, six.reads
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
    small = _Site()
    small.entities = [_entity("ZZA"), _entity("ZZB"), _entity("ZZC")]
    big = _Site()
    big.entities = [_entity("ZZ%03d" % i) for i in range(30)]
    _call_ownership(small)
    _call_ownership(big)
    assert len(small.reads) == 4, small.reads
    assert len(big.reads) == 4, big.reads


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
    dumped = json.dumps(result)
    assert "ZZB" not in dumped, dumped
