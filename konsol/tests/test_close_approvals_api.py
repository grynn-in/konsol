"""Approvals API: konsol/close/approvals_api.py ``get_queue`` (konsol#305 A10;
stories 6.2, 6.3, 6.4; R2, R5; #305-W2-10).

``get_queue()`` (GET) returns ``{items, sent_back, counts, hidden,
self_approval, can_approve, waiting}`` over the pending documents of all 7
approval doctypes, and writes nothing.

Loaded against a stub frappe (pattern: test_close_journal_api.py's ``_Site``
+ ``_call``, copied, not imported). ``konsol.fiscal_calendar`` and
``konsol.entity_permissions`` are stub modules. ``konsol.close.self_approval``
and ``konsol.close.close_event`` are stubbed (a recording ``preparers_for`` /
``latest_rejections``, and ``close_event``'s real ``ENTITY_FIELDS`` copied in,
mirroring test_close_approvals_model.py's own copy). ``konsol.close.journal_model``,
``konsol.close.close_policy_model``, ``konsol.close.approvals_model`` and
``konsol.close.timefmt`` are the real, pure modules, loaded by path under
their dotted names.

This file never names the event-log doctype directly (the one-writer check,
test_close_event_writer.py): rejections are read only through
``close_event.latest_rejections``, and that literal is asserted absent from
the source under test.
"""
import importlib.util
import os
import sys
import types
from datetime import date, datetime

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")
API_PY = os.path.join(CLOSE_DIR, "approvals_api.py")
APPROVALS_MODEL_PY = os.path.join(CLOSE_DIR, "approvals_model.py")
CLOSE_POLICY_MODEL_PY = os.path.join(CLOSE_DIR, "close_policy_model.py")
JOURNAL_MODEL_PY = os.path.join(CLOSE_DIR, "journal_model.py")
TIMEFMT_PY = os.path.join(CLOSE_DIR, "timefmt.py")

#: BST (+01:00) in July 2026, mirrors test_close_journal_api.py.
SITE_TZ = "Europe/London"

JOURNAL = "Consolidation Journal"
BC = "Business Combination"
BD = "Business Disposal"
GER = "Group Exchange Rate"
OP = "Ownership Period"
HER = "Historical Equity Rate"
IC_BALANCE = "IC Balance"

LEAD = "zz-lead@example.com"
ANALYST = "zz-analyst@example.com"


def _period(fy, fp):
    return {"fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
            "period_label": "P%02d" % fp, "period_type": "Regular",
            "start_date": date(fy, fp, 1), "end_date": date(fy, fp, 28),
            "quarter": "Q%d" % ((fp - 1) // 3 + 1), "status": "Open"}


def _journal(name, owner=ANALYST, creation=None, modified=None, docstatus=0, status="Pending Approval",
             fiscal_year=2026, fiscal_period=7, adjustment_type="topside", description="ZZ accrue",
             total_debit=100.0, currency="USD", reverse_fiscal_year=0, reverse_fiscal_period=0):
    return {"name": name, "owner": owner, "creation": creation or datetime(2026, 7, 1, 9, 0, 0),
            "modified": modified or datetime(2026, 7, 1, 9, 0, 0), "docstatus": docstatus,
            "status": status, "fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
            "adjustment_type": adjustment_type, "description": description,
            "total_debit": total_debit, "currency": currency,
            "reverse_fiscal_year": reverse_fiscal_year, "reverse_fiscal_period": reverse_fiscal_period}


def _ger(name, owner=ANALYST, creation=None, modified=None, docstatus=0,
         from_currency="USD", to_currency="EUR", rate_type="Closing",
         fiscal_year=2026, fiscal_period=7, quote_label="1.10 EUR per USD", change_reason=None):
    return {"name": name, "owner": owner, "creation": creation or datetime(2026, 7, 1, 9, 0, 0),
            "modified": modified or datetime(2026, 7, 1, 9, 0, 0), "docstatus": docstatus,
            "from_currency": from_currency, "to_currency": to_currency, "rate_type": rate_type,
            "fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
            "quote_label": quote_label, "change_reason": change_reason}


def _her(name, owner=ANALYST, creation=None, modified=None, docstatus=0,
         consolidation_group="G1", data_area_id="DE02", main_account="4000",
         rate_date=None, historical_rate=1.2):
    return {"name": name, "owner": owner, "creation": creation or datetime(2026, 7, 1, 9, 0, 0),
            "modified": modified or datetime(2026, 7, 1, 9, 0, 0), "docstatus": docstatus,
            "consolidation_group": consolidation_group, "data_area_id": data_area_id,
            "main_account": main_account, "rate_date": rate_date or date(2026, 6, 30),
            "historical_rate": historical_rate}


def _op(name, owner=ANALYST, creation=None, modified=None, docstatus=0,
        consolidation_group="G1", data_area_id="DE02", effective_date=None, end_date=None,
        ownership_pct=80, consolidation_method="Full"):
    return {"name": name, "owner": owner, "creation": creation or datetime(2026, 7, 1, 9, 0, 0),
            "modified": modified or datetime(2026, 7, 1, 9, 0, 0), "docstatus": docstatus,
            "consolidation_group": consolidation_group, "data_area_id": data_area_id,
            "effective_date": effective_date or date(2026, 1, 1), "end_date": end_date,
            "ownership_pct": ownership_pct, "consolidation_method": consolidation_method}


def _ic_balance(name, owner=ANALYST, creation=None, modified=None, docstatus=0,
                selling_entity="DE02", buying_entity="UK01", fiscal_year=2026, fiscal_period=7,
                ic_sales_amount=500.0, ending_inventory_from_ic=0):
    return {"name": name, "owner": owner, "creation": creation or datetime(2026, 7, 1, 9, 0, 0),
            "modified": modified or datetime(2026, 7, 1, 9, 0, 0), "docstatus": docstatus,
            "selling_entity": selling_entity, "buying_entity": buying_entity,
            "fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
            "ic_sales_amount": ic_sales_amount, "ending_inventory_from_ic": ending_inventory_from_ic}


def _bc(name, owner=ANALYST, creation=None, modified=None, docstatus=0, status="Pending Approval",
        consolidation_group="G1", acquired_entity="DE02", acquisition_date=None,
        share_acquired_pct=60, goodwill=1000.0):
    return {"name": name, "owner": owner, "creation": creation or datetime(2026, 7, 1, 9, 0, 0),
            "modified": modified or datetime(2026, 7, 1, 9, 0, 0), "docstatus": docstatus,
            "status": status, "consolidation_group": consolidation_group,
            "acquired_entity": acquired_entity, "acquisition_date": acquisition_date or date(2026, 1, 15),
            "share_acquired_pct": share_acquired_pct, "goodwill": goodwill}


def _bd(name, owner=ANALYST, creation=None, modified=None, docstatus=0, status="Pending Approval",
        consolidation_group="G1", disposed_entity="DE02", disposal_date=None,
        share_disposed_pct=100, total_proceeds=2000.0):
    return {"name": name, "owner": owner, "creation": creation or datetime(2026, 7, 1, 9, 0, 0),
            "modified": modified or datetime(2026, 7, 1, 9, 0, 0), "docstatus": docstatus,
            "status": status, "consolidation_group": consolidation_group,
            "disposed_entity": disposed_entity, "disposal_date": disposal_date or date(2026, 1, 1),
            "share_disposed_pct": share_disposed_pct, "total_proceeds": total_proceeds}


def _line(parent, idx, data_area_id, main_account, debit_amount=0, credit_amount=0, description=""):
    return {"parent": parent, "parenttype": "Consolidation Journal", "idx": idx,
            "data_area_id": data_area_id, "main_account": main_account,
            "debit_amount": debit_amount, "credit_amount": credit_amount, "description": description}


_ACCOUNTS = [
    {"name": "6000", "account_name": "Operating expenses", "parent_account": None,
     "is_group": 1, "statement_section": "Profit and Loss", "status": "Published"},
    {"name": "6100", "account_name": "Office supplies", "parent_account": "6000",
     "is_group": 0, "statement_section": "Profit and Loss", "status": "Published"},
    {"name": "9999", "account_name": "Suspense", "parent_account": None,
     "is_group": 0, "statement_section": "", "status": "Published"},
]

_DOCTYPE_LIST_ATTR = {
    JOURNAL: "journals", GER: "gers", HER: "hers", OP: "ops",
    IC_BALANCE: "ic_balances", BC: "bcs", BD: "bds",
}


class _Site:
    def __init__(self):
        self.user = LEAD
        self.roles = {"EPM Admin"}
        self.allowed = None  # entity_permissions.allowed_entity_codes stub return
        self.self_approval_policy = "Allowed with reason"
        self.periods = [_period(2026, 7)]
        self.accounts = list(_ACCOUNTS)
        self.lines = []
        self.journals = []
        self.gers = []
        self.hers = []
        self.ops = []
        self.ic_balances = []
        self.bcs = []
        self.bds = []
        #: doctype -> None, or {"name", "workflow_state_field"}.
        self.workflows = {}
        #: workflow name -> ordered list of state names (idx order).
        self.wf_states = {}
        #: doctype -> {name: frozenset} (self_approval.preparers_for's answer).
        self.preparers = {}
        #: doctype -> {name: {"reason", "actor", "at"}}.
        self.rejections = {}
        self.reads = []
        self.only_for_calls = []
        self.preparers_calls = []
        self.rejection_calls = []
        self.allowed_calls = []


def _match_value(value, cond):
    if isinstance(cond, (list, tuple)):
        op, arg = cond[0], cond[1]
        if op == "in":
            return value in arg
        raise AssertionError("stub: unsupported operator %r" % (op,))
    return value == cond


def _match(row, filters):
    return all(_match_value(row.get(key), cond) for key, cond in (filters or {}).items())


class _WfDoc:
    def __init__(self, state_names):
        self.states = [types.SimpleNamespace(state=s) for s in state_names]


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

    def get_all(doctype, filters=None, fields=None, order_by=None, limit_page_length=None, **k):
        site.reads.append(("get_all", doctype))
        if doctype == "Consolidation Journal Line":
            rows = [r for r in site.lines if _match(r, filters)]
        elif doctype == "Main Account":
            rows = [r for r in site.accounts if _match(r, filters)]
        elif doctype in _DOCTYPE_LIST_ATTR:
            rows = [r for r in getattr(site, _DOCTYPE_LIST_ATTR[doctype]) if _match(r, filters)]
        else:
            raise AssertionError("unexpected get_all on %s" % doctype)
        return [{f: r.get(f) for f in fields} for r in rows]

    def get_value(doctype, filters=None, fieldname=None, as_dict=False, **k):
        site.reads.append(("get_value", doctype))
        assert doctype == "Workflow", doctype
        dt = filters["document_type"]
        wf = site.workflows.get(dt)
        if not wf:
            return {} if as_dict else None
        assert as_dict, "approvals_api reads the workflow row as_dict"
        return {f: wf.get(f) for f in fieldname}

    def get_single_value(doctype, fieldname):
        site.reads.append(("get_single_value", doctype, fieldname))
        assert (doctype, fieldname) == ("Close Settings", "self_approval")
        return site.self_approval_policy

    def get_cached_doc(doctype, name):
        site.reads.append(("get_cached_doc", doctype))
        assert doctype == "Workflow", doctype
        return _WfDoc(site.wf_states[name])

    def set_value(*a, **k):
        raise AssertionError("get_queue never writes")

    def get_doc(*a, **k):
        raise AssertionError("get_queue never writes")

    def sql(*a, **k):
        raise AssertionError("get_queue never runs raw SQL")

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.only_for = only_for
    frappe.whitelist = whitelist
    frappe.get_all = get_all
    frappe.get_doc = get_doc
    frappe.get_cached_doc = get_cached_doc
    frappe.get_roles = lambda user=None: sorted(site.roles)
    frappe.flags = {}
    frappe.db = types.SimpleNamespace(get_value=get_value, get_single_value=get_single_value,
                                      set_value=set_value, sql=sql, commit=lambda: None)
    frappe.session = types.SimpleNamespace(user=site.user)
    frappe.utils = types.SimpleNamespace(get_system_timezone=lambda: SITE_TZ)
    return frappe


def _fiscal_calendar(site):
    mod = types.ModuleType("konsol.fiscal_calendar")

    def fiscal_period_rows():
        site.reads.append(("fiscal_period_rows",))
        return [dict(r) for r in site.periods]

    mod.fiscal_period_rows = fiscal_period_rows
    return mod


def _entity_permissions(site):
    mod = types.ModuleType("konsol.entity_permissions")

    def allowed_entity_codes(user=None):
        site.allowed_calls.append(user)
        return site.allowed

    mod.allowed_entity_codes = allowed_entity_codes
    return mod


def _self_approval(site):
    mod = types.ModuleType("konsol.close.self_approval")

    def preparers_for(doctype, owners):
        site.preparers_calls.append((doctype, tuple(sorted(owners))))
        if not owners:
            return {}
        table = site.preparers.get(doctype, {})
        return {name: frozenset(table.get(name, (owners[name],))) for name in owners}

    mod.preparers_for = preparers_for
    return mod


#: Mirrors close_event.ENTITY_FIELDS (close_event.py), copied in because the
#: real module imports frappe/period_status (test_close_approvals_model.py
#: does the same).
_ENTITY_FIELDS = {
    "Ownership Period": "data_area_id",
    "Historical Equity Rate": "data_area_id",
    "Business Combination": "acquired_entity",
    "Business Disposal": "disposed_entity",
    "Group Exchange Rate": None,
    "Consolidation Journal": None,
    "IC Balance": None,
}


def _close_event(site):
    mod = types.ModuleType("konsol.close.close_event")
    mod.ENTITY_FIELDS = dict(_ENTITY_FIELDS)

    def latest_rejections(doctype, names):
        site.rejection_calls.append((doctype, tuple(names)))
        if not names:
            return {}
        table = site.rejections.get(doctype, {})
        return {n: dict(table[n]) for n in names if n in table}

    mod.latest_rejections = latest_rejections
    return mod


def _load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _invoke(site, run):
    frappe = _frappe(site)
    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    close = types.ModuleType("konsol.close")
    close.__path__ = []
    fiscal_calendar = _fiscal_calendar(site)
    entity_permissions = _entity_permissions(site)
    self_approval = _self_approval(site)
    close_event = _close_event(site)
    konsol.close = close
    konsol.fiscal_calendar = fiscal_calendar
    konsol.entity_permissions = entity_permissions
    names = ["frappe", "konsol", "konsol.close", "konsol.fiscal_calendar",
             "konsol.entity_permissions", "konsol.close.self_approval",
             "konsol.close.close_event", "konsol.close.journal_model",
             "konsol.close.close_policy_model", "konsol.close.approvals_model",
             "konsol.close.timefmt", "close_approvals_api_under_test"]
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({
        "frappe": frappe, "konsol": konsol, "konsol.close": close,
        "konsol.fiscal_calendar": fiscal_calendar,
        "konsol.entity_permissions": entity_permissions,
        "konsol.close.self_approval": self_approval,
        "konsol.close.close_event": close_event,
    })
    try:
        close.self_approval = self_approval
        close.close_event = close_event
        close.journal_model = _load_path("konsol.close.journal_model", JOURNAL_MODEL_PY)
        close.close_policy_model = _load_path("konsol.close.close_policy_model", CLOSE_POLICY_MODEL_PY)
        close.approvals_model = _load_path("konsol.close.approvals_model", APPROVALS_MODEL_PY)
        close.timefmt = _load_path("konsol.close.timefmt", TIMEFMT_PY)
        api = _load_path("close_approvals_api_under_test", API_PY)
        return run(api)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _call(site):
    return _invoke(site, lambda api: api.get_queue())


def _all_items(result):
    return result["items"] + result["sent_back"]


# --- workflow-aware pending state ----------------------------------------------------


def test_workflow_doctype_lists_pending_approval_not_draft():
    site = _Site()
    site.workflows[JOURNAL] = {"name": "Consolidation Journal Workflow", "workflow_state_field": "status"}
    site.wf_states["Consolidation Journal Workflow"] = ["Draft", "Pending Approval", "Approved", "Reversed"]
    site.journals = [
        _journal("CJ-DRAFT", status="Draft"),
        _journal("CJ-PENDING", status="Pending Approval"),
    ]
    result = _call(site)
    names = {item["name"] for item in _all_items(result)}
    assert names == {"CJ-PENDING"}


def test_e6_p2_draft_is_pending_when_no_workflow_is_installed():
    site = _Site()
    site.workflows[JOURNAL] = None  # no active workflow (live today)
    site.journals = [_journal("CJ-DRAFT", status="Draft")]
    result = _call(site)
    names = {item["name"] for item in _all_items(result)}
    assert names == {"CJ-DRAFT"}


def test_ger_her_op_ic_balance_use_the_docstatus_fallback():
    site = _Site()
    site.gers = [_ger("GER-0", docstatus=0), _ger("GER-1", docstatus=1)]
    site.hers = [_her("HER-0", docstatus=0), _her("HER-1", docstatus=1)]
    site.ops = [_op("OP-0", docstatus=0), _op("OP-1", docstatus=1)]
    site.ic_balances = [_ic_balance("IC-0", docstatus=0), _ic_balance("IC-1", docstatus=1)]
    result = _call(site)
    names = {item["name"] for item in _all_items(result)}
    assert names == {"GER-0", "HER-0", "OP-0", "IC-0"}


# --- the journal item carries lines and effect ---------------------------------------


def test_journal_item_carries_lines_and_effect_no_heading_kept():
    site = _Site()
    site.journals = [_journal("CJ-1")]
    site.lines = [
        _line("CJ-1", 1, "DE02", "6100", debit_amount=100),
        _line("CJ-1", 2, "DE02", "9999", credit_amount=100),
    ]
    result = _call(site)
    item = next(i for i in _all_items(result) if i["name"] == "CJ-1")
    assert len(item["lines"]) == 2
    assert item["effect"]["no_heading"] == 1
    no_heading = [h for h in item["effect"]["headings"] if h["heading"] is None]
    assert len(no_heading) == 1


# --- sent back (A09) --------------------------------------------------------------


def test_ger_rejected_after_modified_is_sent_back_with_a_zoned_rejection_at():
    site = _Site()
    site.gers = [_ger("GER-5", modified=datetime(2026, 7, 2, 8, 0, 0))]
    site.rejections[GER] = {"GER-5": {
        "reason": "wrong rate", "actor": LEAD, "at": datetime(2026, 7, 2, 9, 0, 0)}}
    result = _call(site)
    assert [i["name"] for i in result["items"]] == []
    sent = next(i for i in result["sent_back"] if i["name"] == "GER-5")
    assert sent["rejection"]["reason"] == "wrong rate"
    at = sent["rejection"]["at"]
    assert "+" in at or "Z" in at, at


# --- failure path: R2, the EPM Analyst is not an approver -----------------------------


def test_failure_path_r2_epm_analyst_cannot_approve_anything():
    site = _Site()
    site.roles = {"EPM Analyst"}
    site.gers = [_ger("GER-9")]
    site.hers = [_her("HER-9")]
    result = _call(site)
    assert result["can_approve"] is False
    for item in _all_items(result):
        assert item["approve"]["mode"] == "not_approver"


# --- failure path: a Viewer reads, writes nothing --------------------------------------


def test_failure_path_viewer_reads_and_writes_nothing():
    site = _Site()
    site.roles = {"EPM User"}
    site.gers = [_ger("GER-10")]
    result = _call(site)  # any write attempt would raise AssertionError from the stub
    assert any(i["name"] == "GER-10" for i in _all_items(result))


# --- failure path: Entity Accountant is refused ----------------------------------------


def test_failure_path_entity_accountant_is_refused():
    site = _Site()
    site.roles = {"Entity Accountant"}
    with pytest.raises(Exception):
        _call(site)


# --- failure path: scope cuts a HER and counts it as hidden ---------------------------


def test_failure_path_scope_hides_an_out_of_scope_her_and_counts_it():
    site = _Site()
    site.allowed = {"UK01"}
    site.hers = [_her("HER-11", data_area_id="DE02")]
    site.gers = [_ger("GER-11")]  # group-level: never hidden
    result = _call(site)
    names = {item["name"] for item in _all_items(result)}
    assert "HER-11" not in names
    assert "GER-11" in names
    assert result["hidden"] == 1
    assert result["counts"][HER] == 0


# --- read count is independent of the number of pending documents ----------------------


def test_read_count_is_the_same_for_1_and_6_pending_documents():
    site1 = _Site()
    site1.journals = [_journal("CJ-ONE")]
    site1.lines = [_line("CJ-ONE", 1, "DE02", "6100", debit_amount=10)]
    _call(site1)
    accounts_reads_1 = len([r for r in site1.reads if r[0] == "get_all" and r[1] == "Main Account"])
    line_reads_1 = len([r for r in site1.reads
                         if r[0] == "get_all" and r[1] == "Consolidation Journal Line"])

    site6 = _Site()
    site6.journals = [_journal("CJ-%d" % i) for i in range(6)]
    site6.lines = [_line("CJ-%d" % i, 1, "DE02", "6100", debit_amount=10) for i in range(6)]
    _call(site6)
    accounts_reads_6 = len([r for r in site6.reads if r[0] == "get_all" and r[1] == "Main Account"])
    line_reads_6 = len([r for r in site6.reads
                         if r[0] == "get_all" and r[1] == "Consolidation Journal Line"])

    assert accounts_reads_1 == accounts_reads_6 == 1
    assert line_reads_1 == line_reads_6 == 1


# --- sent_back_for (A21): the caller's own drafts that were sent back ----------------


def _sent_back_for(site, user):
    return _invoke(site, lambda api: api.sent_back_for(user))


def test_sent_back_for_filters_every_get_all_by_owner_and_docstatus_zero():
    site = _Site()
    site.gers = [_ger("GER-40", owner=ANALYST, modified=datetime(2026, 7, 2, 8, 0, 0))]
    site.rejections[GER] = {"GER-40": {
        "reason": "wrong rate", "actor": LEAD, "at": datetime(2026, 7, 2, 9, 0, 0)}}
    items = _sent_back_for(site, ANALYST)
    assert [i["name"] for i in items] == ["GER-40"]
    # one get_all per doctype (the 7 APPROVAL_DOCTYPES), each filtered by
    # owner + docstatus 0 (the stub's _match enforces the filter values).
    get_all_calls = [r for r in site.reads if r[0] == "get_all"]
    assert len(get_all_calls) == 7


def test_sent_back_for_excludes_another_users_rejected_draft():
    """Failure path: the stub holds one, and the owner filter excludes it."""
    site = _Site()
    site.gers = [_ger("GER-41", owner="zz-other@example.com",
                       modified=datetime(2026, 7, 2, 8, 0, 0))]
    site.rejections[GER] = {"GER-41": {
        "reason": "wrong rate", "actor": LEAD, "at": datetime(2026, 7, 2, 9, 0, 0)}}
    assert _sent_back_for(site, ANALYST) == []


def test_sent_back_for_calls_latest_rejections_only_for_doctypes_with_names():
    site = _Site()
    site.gers = [_ger("GER-42", owner=ANALYST)]
    _sent_back_for(site, ANALYST)
    doctypes_with_calls = {dt for dt, names in site.rejection_calls}
    assert doctypes_with_calls == {GER}


def test_sent_back_for_read_count_same_for_1_and_6_drafts():
    site1 = _Site()
    site1.gers = [_ger("GER-50", owner=ANALYST)]
    _sent_back_for(site1, ANALYST)
    reads_1 = len(site1.reads)

    site6 = _Site()
    site6.gers = [_ger("GER-%d" % i, owner=ANALYST) for i in range(6)]
    _sent_back_for(site6, ANALYST)
    reads_6 = len(site6.reads)

    assert reads_1 == reads_6


def test_sent_back_for_formats_rejection_at_with_the_zoned_iso_pattern():
    site = _Site()
    site.gers = [_ger("GER-51", owner=ANALYST, modified=datetime(2026, 7, 2, 8, 0, 0))]
    site.rejections[GER] = {"GER-51": {
        "reason": "wrong rate", "actor": LEAD, "at": datetime(2026, 7, 2, 9, 0, 0)}}
    items = _sent_back_for(site, ANALYST)
    at = items[0]["rejection"]["at"]
    assert "+" in at or "Z" in at, at


# --- the source names no event-log doctype literal -------------------------------------


def test_source_contains_no_event_log_doctype_literal():
    with open(API_PY, encoding="utf-8") as f:
        source = f.read()
    assert '"Close Event"' not in source
    assert "'Close Event'" not in source
