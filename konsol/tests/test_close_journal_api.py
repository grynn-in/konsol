"""Journal API: konsol/close/journal_api.py ``get_journals`` (konsol#305 A05;
stories 6.1, 6.2; #305-W3-3, W3-4; #305-P21-1; W2-10).

``get_journals(fiscal_year, fiscal_period)`` (GET) returns the period's
Consolidation Journals with their lines, totals, duration, status, preparer,
statement effect and last rejection; the drafting choices (groups and their
entities, the postable accounts, the reversal periods); and what the caller
may do.

Loaded against a stub frappe (pattern: test_close_rates_api.py / A05's own
``_Site`` + ``_call``, copied, not imported). ``konsol.fiscal_calendar`` is a
stub module. ``konsol.close.journal_model`` and ``konsol.close.timefmt`` are
the real, pure modules, loaded by path under their dotted names.
``konsol.close.close_event`` is stubbed with a recording ``latest_rejections``
(A05 does not read the event log directly, and this file must never name
"Close Event" -- the one-writer check, test_close_event_writer.py).
"""
import ast
import importlib.util
import json
import os
import sys
import types
from datetime import date, datetime

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")
API_PY = os.path.join(CLOSE_DIR, "journal_api.py")
JOURNAL_MODEL_PY = os.path.join(CLOSE_DIR, "journal_model.py")
TIMEFMT_PY = os.path.join(CLOSE_DIR, "timefmt.py")
#: The stub site's system time zone (mirrors test_close_rates_api.py): BST
#: (+01:00) in July 2025.
SITE_TZ = "Europe/London"

JOURNAL_ROLES = ("EPM Admin", "EPM Analyst", "EPM User", "System Manager")
LEAD = "zz-lead@example.com"
ANALYST = "zz-analyst@example.com"
VIEWER = "zz-viewer@example.com"


def _period(fy, fp, period_type="Regular", status="Open"):
    return {"fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
            "period_label": "P%02d" % fp, "period_type": period_type,
            "start_date": date(fy, fp, 1), "end_date": date(fy, fp, 28),
            "quarter": "Q%d" % ((fp - 1) // 3 + 1), "status": status}


def _journal(name, owner=ANALYST, docstatus=0, status="Draft", consolidation_group="CG1",
             adjustment_type="Topside", description="ZZ reclass", currency="EUR",
             total_debit=18500.0, total_credit=18500.0, reverse_fiscal_year=2025,
             reverse_fiscal_period=10, approved_by=None, approved_at=None,
             creation=None, modified=None, fiscal_year=2025, fiscal_period=7):
    return {"name": name, "owner": owner, "creation": creation or datetime(2025, 7, 1, 9, 0, 0),
            "modified": modified or datetime(2025, 7, 2, 10, 0, 0), "status": status,
            "docstatus": docstatus, "consolidation_group": consolidation_group,
            "adjustment_type": adjustment_type, "description": description,
            "currency": currency, "total_debit": total_debit, "total_credit": total_credit,
            "fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
            "reverse_fiscal_year": reverse_fiscal_year, "reverse_fiscal_period": reverse_fiscal_period,
            "approved_by": approved_by, "approved_at": approved_at}


def _line(parent, idx, data_area_id, main_account, debit_amount=0, credit_amount=0,
          description=""):
    return {"parent": parent, "parenttype": "Consolidation Journal", "idx": idx,
            "data_area_id": data_area_id, "main_account": main_account,
            "debit_amount": debit_amount, "credit_amount": credit_amount,
            "description": description}


#: Main Account rows: 6000/2300 are group headings, 6100/2310 their leaves
#: (the wireframe journal), 9999 a leaf with no heading (the "no heading"
#: failure path).
_ACCOUNTS = [
    {"name": "6000", "account_name": "Operating expenses", "parent_account": None,
     "is_group": 1, "statement_section": "Profit and Loss", "status": "Published"},
    {"name": "6100", "account_name": "Office supplies", "parent_account": "6000",
     "is_group": 0, "statement_section": "Profit and Loss", "status": "Published"},
    {"name": "2300", "account_name": "Current liabilities", "parent_account": None,
     "is_group": 1, "statement_section": "Balance Sheet", "status": "Published"},
    {"name": "2310", "account_name": "Accounts payable", "parent_account": "2300",
     "is_group": 0, "statement_section": "Balance Sheet", "status": "Published"},
    {"name": "9999", "account_name": "Suspense", "parent_account": None,
     "is_group": 0, "statement_section": "", "status": "Published"},
]

#: Consolidation Group rows: one root (blank data_area_id) and two members.
_GROUPS = [
    {"name": "CG-ROOT", "consolidation_group": "CG1", "data_area_id": None,
     "reporting_currency": "EUR"},
    {"name": "CG-B", "consolidation_group": "CG1", "data_area_id": "ZZB",
     "reporting_currency": None},
    {"name": "CG-A", "consolidation_group": "CG1", "data_area_id": "ZZA",
     "reporting_currency": None},
]


class _Site:
    def __init__(self):
        self.user = LEAD
        self.roles = {"EPM Admin"}
        self.periods = [_period(2025, 6, status="Closed"), _period(2025, 7),
                        _period(2025, 9, status="Closed"), _period(2025, 10)]
        self.journals = [_journal("CJ-00001")]
        self.lines = [
            _line("CJ-00001", 1, "ZZA", "6100", debit_amount=18500),
            _line("CJ-00001", 2, "ZZB", "2310", credit_amount=18500),
        ]
        self.accounts = list(_ACCOUNTS)
        self.groups = list(_GROUPS)
        #: None = no active workflow. Otherwise {"name", "workflow_state_field"}.
        self.workflow = {"name": "Consolidation Journal Workflow", "workflow_state_field": "status"}
        self.wf_states = [{"state": "Draft"}, {"state": "Pending Approval"},
                          {"state": "Approved"}, {"state": "Reversed"}]
        self.wf_transitions = [
            {"state": "Draft", "action": "Send for Approval", "allowed": "EPM Analyst"},
            {"state": "Pending Approval", "action": "Reject", "allowed": "EPM Admin"},
            {"state": "Pending Approval", "action": "Approve", "allowed": "EPM Admin"},
        ]
        self.can_draft_permission = True
        self.rejections = {}  # name -> {"reason", "actor", "at"}
        self.rejection_calls = []  # (doctype, tuple(names))
        self.reads = []  # ("get_all", doctype) / ("get_value", "Workflow") / ("get_cached_doc", "Workflow")
        self.only_for_calls = []
        self.set_value_calls = []
        self.sql_calls = []
        self.insert_calls = []
        self.save_calls = []


def _match_value(value, cond):
    if isinstance(cond, (list, tuple)):
        op, arg = cond[0], cond[1]
        if op == "in":
            return value in arg
        if op == "is":
            assert arg in ("set", "not set"), cond
            return (value not in (None, "")) == (arg == "set")
        raise AssertionError("stub: unsupported operator %r" % (op,))
    return value == cond


def _match(row, filters):
    return all(_match_value(row.get(key), cond) for key, cond in (filters or {}).items())


class _WfDoc:
    def __init__(self, states, transitions):
        self.states = [types.SimpleNamespace(**s) for s in states]
        self.transitions = [types.SimpleNamespace(**t) for t in transitions]


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

    def get_all(doctype, filters=None, fields=None, order_by=None, limit_page_length=None,
                pluck=None, **k):
        site.reads.append(("get_all", doctype))
        if doctype == "Consolidation Journal":
            rows = [r for r in site.journals if _match(r, filters)]
        elif doctype == "Consolidation Journal Line":
            rows = [r for r in site.lines if _match(r, filters)]
        elif doctype == "Main Account":
            rows = [r for r in site.accounts if _match(r, filters)]
        elif doctype == "Consolidation Group":
            assert not filters, "groups: one unfiltered read, split in Python"
            rows = list(site.groups)
        else:
            raise AssertionError("unexpected get_all on %s" % doctype)
        if pluck:
            return [r.get(pluck) for r in rows]
        return [{f: r.get(f) for f in fields} for r in rows]

    def get_value(doctype, filters=None, fieldname=None, as_dict=False, **k):
        site.reads.append(("get_value", doctype))
        assert doctype == "Workflow", doctype
        assert filters == {"document_type": "Consolidation Journal", "is_active": 1}, filters
        if site.workflow is None:
            return {} if as_dict else None
        assert as_dict, "journal_api reads the workflow row as_dict"
        return {f: site.workflow.get(f) for f in fieldname}

    def get_cached_doc(doctype, name):
        site.reads.append(("get_cached_doc", doctype))
        assert doctype == "Workflow", doctype
        assert name == site.workflow["name"], name
        return _WfDoc(site.wf_states, site.wf_transitions)

    def set_value(*a, **k):
        site.set_value_calls.append((a, k))

    def sql(query, *a, **k):
        site.sql_calls.append(query)
        raise AssertionError("journal_api.get_journals never runs raw SQL")

    def has_permission(doctype, ptype="read", *a, **k):
        assert (doctype, ptype) == ("Consolidation Journal", "create"), (doctype, ptype)
        return site.can_draft_permission

    def get_doc(*a, **k):
        site.insert_calls.append(("get_doc", a, k))
        raise AssertionError("a GET never calls get_doc")

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.only_for = only_for
    frappe.whitelist = whitelist
    frappe.get_all = get_all
    frappe.has_permission = has_permission
    frappe.get_cached_doc = get_cached_doc
    frappe.get_doc = get_doc
    frappe.get_roles = lambda user=None: sorted(site.roles)
    frappe.flags = {}
    frappe.db = types.SimpleNamespace(get_value=get_value, set_value=set_value, sql=sql)
    frappe.session = types.SimpleNamespace(user=site.user)
    frappe.utils = types.SimpleNamespace(get_system_timezone=lambda: SITE_TZ)
    return frappe


def _fiscal_calendar(site):
    fc = types.ModuleType("konsol.fiscal_calendar")

    def fiscal_period_rows():
        site.reads.append(("fiscal_period_rows",))
        return [dict(r) for r in site.periods]

    fc.fiscal_period_rows = fiscal_period_rows
    return fc


def _close_event(site):
    ce = types.ModuleType("konsol.close.close_event")

    def latest_rejections(doctype, names):
        site.rejection_calls.append((doctype, tuple(names)))
        if not names:
            return {}
        return {n: dict(site.rejections[n]) for n in names if n in site.rejections}

    ce.latest_rejections = latest_rejections
    return ce


def _load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _invoke(site, run, require_json_safe=True):
    frappe = _frappe(site)
    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    close = types.ModuleType("konsol.close")
    close.__path__ = []
    fiscal_calendar = _fiscal_calendar(site)
    close_event = _close_event(site)
    konsol.close = close
    konsol.fiscal_calendar = fiscal_calendar
    names = ["frappe", "konsol", "konsol.close", "konsol.fiscal_calendar",
             "konsol.close.close_event", "konsol.close.journal_model", "konsol.close.timefmt",
             "close_journal_api_under_test"]
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({"frappe": frappe, "konsol": konsol, "konsol.close": close,
                        "konsol.fiscal_calendar": fiscal_calendar,
                        "konsol.close.close_event": close_event})
    try:
        close.close_event = close_event
        close.journal_model = _load_path("konsol.close.journal_model", JOURNAL_MODEL_PY)
        close.timefmt = _load_path("konsol.close.timefmt", TIMEFMT_PY)
        api = _load_path("close_journal_api_under_test", API_PY)
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


def _call(site, fy=2025, fp=7):
    return _invoke(site, lambda api: api.get_journals(fy, fp))


def _call_raises(site, fy=2025, fp=7):
    with pytest.raises(Exception) as info:
        _call(site, fy, fp)
    return info.value


def _by_name(result, name):
    return next(j for j in result["journals"] if j["name"] == name)


# --- the wireframe journal ---------------------------------------------------------


def test_the_wireframe_journal_carries_lines_totals_duration_and_effect():
    site = _Site()
    result = _call(site)
    journal = _by_name(result, "CJ-00001")
    assert journal["duration"] == "Reverses in P10"
    assert journal["reverse"] == {"fiscal_year": 2025, "fiscal_period": 10}
    assert journal["total_debit"] == 18500.0 and journal["total_credit"] == 18500.0
    assert len(journal["lines"]) == 2
    assert journal["lines"][0]["account_name"] == "Office supplies"
    assert journal["last_rejection"] is None
    headings = journal["effect"]["headings"]
    assert len(headings) == 2
    sections = {h["section"] for h in headings}
    assert sections == {"Profit and Loss", "Balance Sheet"}


def test_a_journal_with_no_reversal_says_this_period_only():
    site = _Site()
    site.journals = [_journal("CJ-00002", reverse_fiscal_year=0, reverse_fiscal_period=0)]
    result = _call(site)
    journal = _by_name(result, "CJ-00002")
    assert journal["duration"] == "This period only, no reversal"
    assert journal["reverse"] is None


def test_a_draft_journal_with_a_rejection_carries_the_reason_and_a_zoned_at():
    site = _Site()
    site.journals = [_journal("CJ-00003", docstatus=0)]
    site.rejections = {"CJ-00003": {"reason": "ZZ balance the lines", "actor": LEAD,
                                    "at": datetime(2025, 7, 5, 14, 0, 0)}}
    result = _call(site)
    journal = _by_name(result, "CJ-00003")
    assert journal["last_rejection"]["reason"] == "ZZ balance the lines"
    at = journal["last_rejection"]["at"]
    assert "+" in at or "Z" in at, at


# --- failure path: no heading -------------------------------------------------------


def test_failure_path_a_line_with_no_heading_is_kept_not_dropped():
    site = _Site()
    site.lines = site.lines + [_line("CJ-00001", 3, "ZZA", "9999", debit_amount=100)]
    result = _call(site)
    journal = _by_name(result, "CJ-00001")
    assert journal["effect"]["no_heading"] == 1
    no_heading = [h for h in journal["effect"]["headings"] if h["heading"] is None]
    assert len(no_heading) == 1
    # The line itself is still in the raw lines list, never dropped.
    assert any(l["main_account"] == "9999" for l in journal["lines"])
    assert result["accounts"]["9999"]["heading"] is None


# --- can_send / can_draft (E6-P1 option (c)) -----------------------------------------


def test_can_send_is_false_when_no_workflow_is_installed():
    site = _Site()
    site.workflow = None
    site.roles = {"EPM Analyst"}
    result = _call(site)
    assert result["workflow_installed"] is False
    assert result["can_send"] is False


def test_can_send_is_false_for_an_epm_admin_only_user_when_the_transition_says_analyst():
    site = _Site()
    site.roles = {"EPM Admin"}
    result = _call(site)
    assert result["can_send"] is False


def test_can_send_is_true_for_an_epm_analyst():
    site = _Site()
    site.roles = {"EPM Analyst"}
    result = _call(site)
    assert result["can_send"] is True


def test_can_draft_follows_the_stub_has_permission():
    site = _Site()
    site.roles = {"EPM Analyst"}
    site.can_draft_permission = False
    result = _call(site)
    assert result["can_draft"] is False


def test_failure_path_an_epm_admin_only_user_gets_can_draft_and_can_send_false():
    """amended 3 Oct (E6-P1 option (c)): even when the stub has_permission and
    the workflow transition both say yes, an EPM Admin-only user is refused
    by both: A06/A07 admit only DRAFT_ROLES."""
    site = _Site()
    site.roles = {"EPM Admin"}
    site.can_draft_permission = True
    # Make the workflow transition admit EPM Admin too, to isolate the
    # DRAFT_ROLES gate from the workflow's own role check.
    site.wf_transitions = [
        {"state": "Draft", "action": "Send for Approval", "allowed": "EPM Admin"},
    ]
    result = _call(site)
    assert result["can_draft"] is False
    assert result["can_send"] is False


def test_an_epm_analyst_gets_true_for_both():
    site = _Site()
    site.roles = {"EPM Analyst"}
    site.can_draft_permission = True
    result = _call(site)
    assert result["can_draft"] is True
    assert result["can_send"] is True


def test_a_system_manager_gets_can_draft_true():
    site = _Site()
    site.roles = {"System Manager"}
    site.can_draft_permission = True
    result = _call(site)
    assert result["can_draft"] is True


# --- reversal_choices ---------------------------------------------------------------


def test_reversal_choices_excludes_a_closed_period():
    site = _Site()
    result = _call(site)
    codes = {c["code"] for c in result["reversal_choices"]}
    assert "P09" not in codes
    assert "P10" in codes


# --- failure paths: period and role gates --------------------------------------------


def test_failure_path_an_undeclared_period_is_refused_before_any_journal_read():
    site = _Site()
    err = _call_raises(site, 2031, 9)
    assert "not a declared period" in str(err), err
    assert ("get_all", "Consolidation Journal") not in site.reads
    assert site.reads == [("fiscal_period_rows",)]


def test_failure_path_the_entity_accountant_is_refused():
    site = _Site()
    site.roles = {"Entity Accountant"}
    err = _call_raises(site)
    assert type(err).__name__ == "PermissionError", err
    assert site.only_for_calls == [JOURNAL_ROLES]
    assert site.reads == []


# --- GET writes nothing ---------------------------------------------------------------


def test_get_writes_nothing():
    site = _Site()
    _call(site)
    assert site.set_value_calls == []
    assert site.sql_calls == []
    assert site.insert_calls == []
    assert site.save_calls == []


# --- read count is constant in the number of journals --------------------------------


def _many_journals(n):
    site = _Site()
    site.journals = [_journal("CJ-%05d" % i) for i in range(n)]
    site.lines = []
    for i in range(n):
        site.lines.append(_line("CJ-%05d" % i, 1, "ZZA", "6100", debit_amount=100))
        site.lines.append(_line("CJ-%05d" % i, 2, "ZZB", "2310", credit_amount=100))
    return site


def test_the_read_count_is_constant_in_the_number_of_journals():
    one, five = _many_journals(1), _many_journals(5)
    r1, r5 = _call(one), _call(five)
    assert len(r1["journals"]) == 1 and len(r5["journals"]) == 5
    assert len(one.reads) == len(five.reads), (one.reads, five.reads)
    assert one.rejection_calls and five.rejection_calls


# --- the event-log doctype literal never appears here (the one-writer check) ---------


def test_journal_api_never_names_the_close_event_doctype():
    with open(API_PY, encoding="utf-8") as f:
        source = f.read()
    # Same literals as test_close_event_writer.py's NAMES.
    names = ('"Close Event"', "'Close Event'")
    assert not any(n in source for n in names), source
    ast.parse(source)  # also must parse cleanly
