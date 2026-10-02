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
        self.move_answer = None
        self.move_calls = []
        self.needs_calls = []
        self.only_for_calls = []
        self.reads = []  # one entry per MariaDB read


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
        site.reads.append(("get_all", doctype))
        if doctype == "Group Exchange Rate":
            rows = [r for r in site.docs if _match(r, filters)]
        elif doctype == "Version":
            rows = [r for r in site.versions if _match(r, filters)]
        else:
            raise AssertionError("unexpected get_all on %s" % doctype)
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

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.only_for = only_for
    frappe.whitelist = whitelist
    frappe.get_all = get_all
    frappe.get_roles = lambda user=None: sorted(site.roles)
    frappe.flags = {}
    frappe.db = types.SimpleNamespace(sql=sql, get_single_value=get_single_value,
                                      get_value=get_value)
    frappe.session = types.SimpleNamespace(user=site.user)
    return frappe


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
    frappe = _frappe(site)
    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    close = types.ModuleType("konsol.close")
    close.__path__ = []
    group_rates = _group_rates(site)
    fiscal_calendar = _fiscal_calendar(site)
    konsol.close = close
    konsol.group_rates = group_rates
    konsol.fiscal_calendar = fiscal_calendar
    names = ["frappe", "konsol", "konsol.close", "konsol.group_rates", "konsol.fiscal_calendar",
             "konsol.close.close_policy_model", "konsol.close.rates_model",
             "konsol.close.self_approval", "close_rates_api_under_test"]
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({"frappe": frappe, "konsol": konsol, "konsol.close": close,
                        "konsol.group_rates": group_rates,
                        "konsol.fiscal_calendar": fiscal_calendar})
    try:
        close.close_policy_model = _load_path(
            "konsol.close.close_policy_model", os.path.join(CLOSE_DIR, "close_policy_model.py"))
        close.rates_model = _load_path(
            "konsol.close.rates_model", os.path.join(CLOSE_DIR, "rates_model.py"))
        close.self_approval = _load_path(
            "konsol.close.self_approval", os.path.join(CLOSE_DIR, "self_approval.py"))
        api = _load_path("close_rates_api_under_test", API_PY)
        result = api.get_rates(fy, fp)
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
