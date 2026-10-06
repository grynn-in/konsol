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
#: konsolidat#245 option D: journal_api.py imports the real, pure
#: tb_dimension_model for ``is_flag_on``, loaded by
#: path under its dotted name like journal_model/timefmt.
TB_DIMENSION_MODEL_PY = os.path.join(APP_DIR, "tb_dimension_model.py")
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
        #: konsolidat#245 option D, D02: declared journal dimensions. Empty by
        #: default, so every pre-existing test (no dims declared) behaves
        #: exactly as before.
        self.dimensions = []
        #: D06: the fields Consolidation Journal Line actually has
        #: (``frappe.get_meta(...).get_valid_columns()``). None = the base
        #: columns plus a field for every row in ``dimensions`` — the state
        #: once schema_apply's queued Custom Field sync has run. A list models
        #: the window before it runs (konsol#135).
        self.line_columns = None
        self.hierarchies = []
        self.hierarchy_members = []
        self.dimension_mappings = []
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
        #: A06: None for a GET (get_doc refuses); the POST tests set
        #: ``_PostSite.get_doc``.
        self.get_doc_impl = None


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


#: Consolidation Journal Line's own columns, as get_valid_columns() lists them.
_BASE_LINE_COLUMNS = ("name", "owner", "creation", "modified", "modified_by", "docstatus",
                      "idx", "parent", "parentfield", "parenttype", "data_area_id",
                      "main_account", "debit_amount", "credit_amount", "description")


def _line_columns(site):
    if site.line_columns is not None:
        return site.line_columns
    return _BASE_LINE_COLUMNS + tuple(d["dimension_name"] for d in site.dimensions)


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
            # Frappe raises on a field the table does not have; so does this.
            missing = set(fields or ()) - set(_line_columns(site))
            if missing:
                raise AssertionError("Unknown column(s) %s" % sorted(missing))
            rows = [r for r in site.lines if _match(r, filters)]
        elif doctype == "Main Account":
            rows = [r for r in site.accounts if _match(r, filters)]
        elif doctype == "Consolidation Group":
            assert not filters, "groups: one unfiltered read, split in Python"
            rows = list(site.groups)
        elif doctype == "Dimension":
            rows = [r for r in site.dimensions if _match(r, filters)]
            rows = sorted(rows, key=lambda r: r.get("dimension_name") or "")
        elif doctype == "Reporting Hierarchy":
            rows = [r for r in site.hierarchies if _match(r, filters)]
        elif doctype == "Reporting Hierarchy Member":
            rows = [r for r in site.hierarchy_members if _match(r, filters)]
        elif doctype == "Dimension Mapping":
            rows = [r for r in site.dimension_mappings if _match(r, filters)]
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
        if site.get_doc_impl is not None:  # A06: the POST tests install one
            return site.get_doc_impl(frappe, *a, **k)
        site.insert_calls.append(("get_doc", a, k))
        raise AssertionError("a GET never calls get_doc")

    def get_meta(doctype):
        site.reads.append(("get_meta", doctype))
        assert doctype == "Consolidation Journal Line", doctype
        return types.SimpleNamespace(get_valid_columns=lambda: list(_line_columns(site)))

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.get_meta = get_meta
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


def _frappe_workflow(site, frappe):
    """A07: a stub ``frappe.model.workflow`` whose ``apply_workflow`` records
    ``(doc, action)`` in ``site.apply_calls`` and moves the doc to
    ``site.apply_next_state``; ``site.apply_raises`` makes it raise instead
    (Frappe's own refusal), ``site.apply_returns_none`` makes it return None."""
    wf = types.ModuleType("frappe.model.workflow")

    def apply_workflow(doc, action):
        calls = getattr(site, "apply_calls", None)
        if calls is None:
            raise AssertionError("apply_workflow called on a site that does not expect it")
        calls.append((doc, action))
        if getattr(site, "apply_raises", None):
            raise site.apply_raises
        doc.status = site.apply_next_state
        if getattr(site, "existing", None) is not None and doc.name in site.existing:
            site.existing[doc.name]["status"] = site.apply_next_state
        if getattr(site, "apply_returns_none", False):
            return None
        return doc

    wf.apply_workflow = apply_workflow
    return wf


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
    # A07: ``send_for_approval`` imports ``frappe.model.workflow.apply_workflow``
    # inside the function; the stub records each call.
    frappe_model = types.ModuleType("frappe.model")
    frappe_model.__path__ = []
    frappe_workflow = _frappe_workflow(site, frappe)
    frappe_model.workflow = frappe_workflow
    frappe.model = frappe_model
    names = ["frappe", "frappe.model", "frappe.model.workflow", "konsol", "konsol.close",
             "konsol.fiscal_calendar", "konsol.tb_dimension_model",
             "konsol.close.close_event", "konsol.close.journal_model", "konsol.close.timefmt",
             "close_journal_api_under_test"]
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({"frappe": frappe, "frappe.model": frappe_model,
                        "frappe.model.workflow": frappe_workflow,
                        "konsol": konsol, "konsol.close": close,
                        "konsol.fiscal_calendar": fiscal_calendar,
                        "konsol.close.close_event": close_event})
    try:
        close.close_event = close_event
        close.journal_model = _load_path("konsol.close.journal_model", JOURNAL_MODEL_PY)
        close.timefmt = _load_path("konsol.close.timefmt", TIMEFMT_PY)
        konsol.tb_dimension_model = _load_path(
            "konsol.tb_dimension_model", TB_DIMENSION_MODEL_PY)
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
    assert journal["duration"] == "Reverses in FY2025 P10"
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


# --- konsolidat#245 option D, D02: declared journal dimensions (get_journals) --------


def _dim(name, label=None, in_journal=1, status="Published"):
    return {"dimension_name": name, "label": label, "in_journal": in_journal, "status": status}


def _rh(name, dimension, status="Published"):
    return {"name": name, "dimension": dimension, "status": status}


def _rhm(reporting_hierarchy, member_code, is_group=0):
    return {"reporting_hierarchy": reporting_hierarchy, "member_code": member_code,
            "is_group": is_group}


def _dmap(dimension, canonical_value, status="Published"):
    return {"dimension": dimension, "canonical_value": canonical_value, "status": status}


def test_zero_declared_dimensions_leaves_dimensions_and_lines_unchanged():
    site = _Site()
    result = _call(site)
    assert result["dimensions"] == []
    journal = _by_name(result, "CJ-00001")
    assert set(journal["lines"][0]) == {
        "idx", "data_area_id", "main_account", "account_name",
        "debit_amount", "credit_amount", "description",
    }
    assert ("get_all", "Dimension") in site.reads
    assert ("get_all", "Reporting Hierarchy") not in site.reads
    assert ("get_all", "Reporting Hierarchy Member") not in site.reads
    assert ("get_all", "Dimension Mapping") not in site.reads


def test_a_published_in_journal_dimension_is_declared_with_its_label():
    site = _Site()
    site.dimensions = [_dim("dim_cost_center", label="Cost Center")]
    result = _call(site)
    assert result["dimensions"] == [
        {"key": "dim_cost_center", "label": "Cost Center", "suggestions": []}
    ]


def test_a_dimension_with_no_label_falls_back_to_its_name():
    site = _Site()
    site.dimensions = [_dim("dim_cost_center", label=None)]
    result = _call(site)
    assert result["dimensions"][0]["label"] == "dim_cost_center"


def test_off_text_in_journal_values_are_off_not_truthy():
    """``is_flag_on``'s off-text handling (tb_dimension_model._OFF_TEXT), reused
    here rather than ``if doc.in_journal``."""
    for off in ("0", "false", "No", "", 0, False):
        site = _Site()
        site.dimensions = [_dim("dim_x", in_journal=off)]
        result = _call(site)
        assert result["dimensions"] == [], off


def test_on_values_declare_the_dimension():
    for on in (1, True, "1", "yes"):
        site = _Site()
        site.dimensions = [_dim("dim_x", in_journal=on)]
        result = _call(site)
        assert len(result["dimensions"]) == 1, on


def test_an_illegal_dimension_name_is_never_declared_even_in_journal_and_published():
    site = _Site()
    site.dimensions = [_dim("dim_x\n")]
    result = _call(site)
    assert result["dimensions"] == []


def test_d05_journal_api_reads_the_flag_with_the_public_is_flag_on():
    """D05 (konsol-50 made ``is_flag_on`` public in #324): journal_api reads
    ``in_journal`` with tb_dimension_model's public ``is_flag_on``, never the
    private ``_is_on`` alias, so a rename of the alias cannot break it."""
    tree = ast.parse(open(API_PY, encoding="utf-8").read())
    imported = {
        alias.name
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        and node.module == "konsol.tb_dimension_model"
        for alias in node.names
    }
    assert "is_flag_on" in imported, imported
    assert "_is_on" not in imported, imported
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "_is_on" not in names


def test_d05_a_ticked_dimension_without_the_dim_prefix_is_absent_not_an_error():
    """The name rule (konsol-50, 4 Oct): schema_apply gives a ticked
    in_journal Dimension whose name fails ``^dim_[a-z0-9_]+\\Z`` no Custom
    Field. ``business_unit`` is legal on a Dimension outside the trial balance
    (dimension.py's own example), so it is reachable: get_journals leaves it
    out of ``dimensions`` and off every line, and does not raise."""
    site = _Site()
    site.dimensions = [_dim("business_unit", label="Business unit"),
                       _dim("dim_cost_center", label="Cost Center")]
    site.lines[0]["business_unit"] = "BU1"
    result = _call(site)
    assert [d["key"] for d in result["dimensions"]] == ["dim_cost_center"]
    for line in _by_name(result, "CJ-00001")["lines"]:
        assert "business_unit" not in line


def test_a_draft_dimension_is_not_declared_even_with_the_flag_on():
    site = _Site()
    site.dimensions = [_dim("dim_x", status="Draft")]
    result = _call(site)
    assert result["dimensions"] == []


def test_declared_dimensions_are_ordered_by_dimension_name():
    site = _Site()
    site.dimensions = [_dim("dim_zzz"), _dim("dim_aaa")]
    result = _call(site)
    assert [d["key"] for d in result["dimensions"]] == ["dim_aaa", "dim_zzz"]


def test_suggestions_combine_published_leaves_and_published_mappings_deduped_and_sorted():
    site = _Site()
    site.dimensions = [_dim("dim_cost_center")]
    site.hierarchies = [_rh("RH-1", "dim_cost_center")]
    site.hierarchy_members = [
        _rhm("RH-1", "CC2"),
        _rhm("RH-1", "CC1"),
        _rhm("RH-1", "CC-GROUP", is_group=1),  # not a leaf: excluded
    ]
    site.dimension_mappings = [
        _dmap("dim_cost_center", "CC1"),  # duplicate of the leaf: deduped
        _dmap("dim_cost_center", "CC3"),
    ]
    result = _call(site)
    assert result["dimensions"][0]["suggestions"] == ["CC1", "CC2", "CC3"]


def test_suggestions_exclude_a_non_published_hierarchy_and_a_non_published_mapping():
    site = _Site()
    site.dimensions = [_dim("dim_cost_center")]
    site.hierarchies = [_rh("RH-1", "dim_cost_center", status="Draft")]
    site.hierarchy_members = [_rhm("RH-1", "CC1")]
    site.dimension_mappings = [_dmap("dim_cost_center", "CC9", status="Draft")]
    result = _call(site)
    assert result["dimensions"][0]["suggestions"] == []


def test_suggestions_for_one_dimension_never_leak_into_another():
    site = _Site()
    site.dimensions = [_dim("dim_cost_center"), _dim("dim_project")]
    site.hierarchies = [_rh("RH-CC", "dim_cost_center"), _rh("RH-PR", "dim_project")]
    site.hierarchy_members = [_rhm("RH-CC", "CC1"), _rhm("RH-PR", "P1")]
    site.dimension_mappings = [_dmap("dim_cost_center", "CC2"), _dmap("dim_project", "P2")]
    result = _call(site)
    by_key = {d["key"]: d["suggestions"] for d in result["dimensions"]}
    assert by_key == {"dim_cost_center": ["CC1", "CC2"], "dim_project": ["P1", "P2"]}


def test_each_line_carries_its_declared_dimension_value_defaulting_blank():
    site = _Site()
    site.dimensions = [_dim("dim_cost_center")]
    site.lines = [
        _line("CJ-00001", 1, "ZZA", "6100", debit_amount=18500),
        _line("CJ-00001", 2, "ZZB", "2310", credit_amount=18500),
    ]
    site.lines[0]["dim_cost_center"] = "CC1"
    result = _call(site)
    journal = _by_name(result, "CJ-00001")
    assert journal["lines"][0]["dim_cost_center"] == "CC1"
    assert journal["lines"][1]["dim_cost_center"] == ""


def test_d06_a_declared_dimension_whose_field_does_not_exist_yet_is_absent_not_an_error():
    """D06: schema_apply's Custom Field sync is queued after the commit
    (konsol#135), so a Dimension can be Published with ``in_journal`` ticked
    before Consolidation Journal Line has its field. Selecting that field would
    make frappe.get_all raise and take the whole Adjustments screen down; the
    dimension is left out until its field exists (the same rule
    consolidation_journal's resync uses: journal_model.journal_dimension_columns)."""
    site = _Site()
    site.dimensions = [_dim("dim_cost_center"), _dim("dim_brand_new")]
    site.line_columns = _BASE_LINE_COLUMNS + ("dim_cost_center",)
    site.lines[0]["dim_cost_center"] = "CC1"
    result = _call(site)
    assert [d["key"] for d in result["dimensions"]] == ["dim_cost_center"]
    line = _by_name(result, "CJ-00001")["lines"][0]
    assert line["dim_cost_center"] == "CC1"
    assert "dim_brand_new" not in line


def test_d06_the_line_read_names_exactly_the_declared_dimension_fields():
    """D06: get_journals reads the declared dimension fields with the lines, so
    a saved value comes back; an undeclared field the table still has (an
    orphan from an un-ticked dimension) is not read."""
    site = _Site()
    site.dimensions = [_dim("dim_cost_center")]
    site.line_columns = _BASE_LINE_COLUMNS + ("dim_cost_center", "dim_orphan")
    site.lines[0]["dim_orphan"] = "OLD"
    result = _call(site)
    for line in _by_name(result, "CJ-00001")["lines"]:
        assert "dim_orphan" not in line
        assert "dim_cost_center" in line


def test_d06_save_journal_refuses_a_dimension_whose_field_does_not_exist_yet():
    """The save uses the same declared list as the read, so a value for a
    dimension whose field is not there yet is refused with a sentence rather
    than silently dropped by the document."""
    site = _post_site()
    site.dimensions = [_dim("dim_brand_new")]
    site.line_columns = list(_BASE_LINE_COLUMNS)
    lines = [dict(line, dim_brand_new="X") for line in WIREFRAME_LINES]
    err = _save_raises(site, lines=json.dumps(lines))
    assert "dim_brand_new" in str(err), err
    assert site.insert_calls == []


def test_bounded_reads_dimension_reads_are_constant_in_the_number_of_journals():
    """Suggestion reads do not scale with the number of journals or lines
    (bounded reads, no N+1 per line)."""
    one, five = _many_journals(1), _many_journals(5)
    for site in (one, five):
        site.dimensions = [_dim("dim_cost_center")]
        site.hierarchies = [_rh("RH-1", "dim_cost_center")]
        site.hierarchy_members = [_rhm("RH-1", "CC1")]
        site.dimension_mappings = [_dmap("dim_cost_center", "CC2")]
    r1, r5 = _call(one), _call(five)
    assert len(r1["journals"]) == 1 and len(r5["journals"]) == 5
    assert len(one.reads) == len(five.reads), (one.reads, five.reads)
    assert r1["dimensions"] == r5["dimensions"] == [
        {"key": "dim_cost_center", "label": "dim_cost_center", "suggestions": ["CC1", "CC2"]}
    ]


# --- the event-log doctype literal never appears here (the one-writer check) ---------


# --- konsol#305 story 6.5: auto-reversals visible ------------------------------------

#: The golden fixture close-ui's adjustments tests load: one ``reversing_in``
#: item exactly as the real ``get_journals`` returns it.
_REVERSING_FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "fixtures", "close_journals_reversing_in.json")


def _reversing_site():
    """P7's own wireframe journal (reverses in P10) plus an Approved P6
    journal that reverses into P7, a P6 Draft and a P6 Reversed journal that
    also name P7, and an Approved P6 journal that reverses into P10."""
    site = _Site()
    site.journals = [
        _journal("CJ-00001"),
        _journal("CJ-P6-A", docstatus=1, status="Approved", fiscal_period=6, adjustment_type="topside",
                 description="ZZ accrue June bonus\nsecond line", reverse_fiscal_period=7,
                 total_debit=1200.0, total_credit=1200.0, approved_by=LEAD,
                 approved_at=datetime(2025, 6, 30, 16, 0, 0),
                 creation=datetime(2025, 6, 28, 9, 0, 0), modified=datetime(2025, 6, 30, 16, 0, 0)),
        _journal("CJ-P6-D", docstatus=0, status="Draft", fiscal_period=6, reverse_fiscal_period=7),
        _journal("CJ-P6-R", docstatus=2, status="Reversed", fiscal_period=6, reverse_fiscal_period=7),
        _journal("CJ-P6-10", docstatus=1, status="Approved", fiscal_period=6, reverse_fiscal_period=10),
    ]
    site.lines = [
        _line("CJ-00001", 1, "ZZA", "6100", debit_amount=18500),
        _line("CJ-00001", 2, "ZZB", "2310", credit_amount=18500),
        _line("CJ-P6-A", 1, "ZZA", "6100", debit_amount=1200, description="bonus"),
        _line("CJ-P6-A", 2, "ZZA", "2310", credit_amount=1200),
        _line("CJ-P6-D", 1, "ZZA", "6100", debit_amount=5),
        _line("CJ-P6-D", 2, "ZZA", "2310", credit_amount=5),
        _line("CJ-P6-R", 1, "ZZA", "6100", debit_amount=7),
        _line("CJ-P6-R", 2, "ZZA", "2310", credit_amount=7),
        _line("CJ-P6-10", 1, "ZZA", "6100", debit_amount=9),
        _line("CJ-P6-10", 2, "ZZA", "2310", credit_amount=9),
    ]
    return site


def test_reversing_in_is_always_sent_even_empty():
    result = _call(_Site())
    assert result["reversing_in"] == []


def test_an_approved_earlier_journal_reversing_here_is_listed_with_its_origin():
    result = _call(_reversing_site())
    assert [j["name"] for j in result["reversing_in"]] == ["CJ-P6-A"]
    item = result["reversing_in"][0]
    assert item["origin"] == {"fiscal_year": 2025, "fiscal_period": 6, "code": "P06"}
    assert item["label"] == "Reverses here from FY2025 P06"
    assert item["title"] == "ZZ accrue June bonus"
    assert item["approved_by"] == LEAD
    assert "+" in item["approved_at"] or "Z" in item["approved_at"]


def test_the_reversing_item_is_read_only_and_never_in_the_periods_own_journals():
    result = _call(_reversing_site())
    assert [j["name"] for j in result["journals"]] == ["CJ-00001"]
    item = result["reversing_in"][0]
    for key in ("status", "docstatus", "preparer", "last_rejection", "reverse"):
        assert key not in item, key


def test_the_reversing_items_lines_are_the_reversal_posting_debit_and_credit_swapped():
    item = _call(_reversing_site())["reversing_in"][0]
    assert [(l["main_account"], l["debit_amount"], l["credit_amount"]) for l in item["lines"]] == \
        [("6100", 0.0, 1200.0), ("2310", 1200.0, 0.0)]
    assert item["total_debit"] == 1200.0 and item["total_credit"] == 1200.0


def test_the_reversing_items_effect_is_the_original_effect_negated():
    site = _reversing_site()
    item = _call(site)["reversing_in"][0]
    by_heading = {h["heading"]: h["net_debit"] for h in item["effect"]["headings"]}
    assert by_heading == {"6000": -1200.0, "2300": 1200.0}


def test_a_draft_or_reversed_journal_naming_this_period_does_not_reverse_here():
    """Only a submitted journal is in the warehouse (resync_staging reads
    docstatus 1), so only it posts a reversal; a cancelled one's reversal
    rows vanish with it (#305-D2-2)."""
    names = [j["name"] for j in _call(_reversing_site())["reversing_in"]]
    assert "CJ-P6-D" not in names and "CJ-P6-R" not in names and "CJ-P6-10" not in names


def test_the_original_period_still_says_reverses_in_the_target_period():
    site = _reversing_site()
    result = _call(site, 2025, 6)
    by_name = {j["name"]: j for j in result["journals"]}
    assert by_name["CJ-P6-A"]["duration"] == "Reverses in FY2025 P07"
    assert result["reversing_in"] == []


def test_the_read_count_is_constant_in_the_number_of_reversing_journals():
    def site_with(n):
        site = _Site()
        for i in range(n):
            name = "CJ-R%05d" % i
            site.journals.append(_journal(name, docstatus=1, status="Approved",
                                          fiscal_period=6, reverse_fiscal_period=7))
            site.lines.append(_line(name, 1, "ZZA", "6100", debit_amount=1))
            site.lines.append(_line(name, 2, "ZZA", "2310", credit_amount=1))
        return site
    one, five = site_with(1), site_with(5)
    assert len(_call(one)["reversing_in"]) == 1 and len(_call(five)["reversing_in"]) == 5
    one.reads.clear(); five.reads.clear()
    _call(one); _call(five)
    assert len(one.reads) == len(five.reads), (one.reads, five.reads)


def test_reversing_item_matches_the_golden_fixture():
    """The committed fixture close-ui's adjustments tests load (a real
    producer shape, never a hand-built dict)."""
    item = _call(_reversing_site())["reversing_in"][0]
    with open(_REVERSING_FIXTURE_PATH) as f:
        golden = json.load(f)
    assert item == golden


def test_journal_api_never_names_the_close_event_doctype():
    with open(API_PY, encoding="utf-8") as f:
        source = f.read()
    # Same literals as test_close_event_writer.py's NAMES.
    names = ('"Close Event"', "'Close Event'")
    assert not any(n in source for n in names), source
    ast.parse(source)  # also must parse cleanly


# =====================================================================================
# A06 — save_journal (POST): the Group Accountant saves a balanced draft (forge-tested)
# =====================================================================================

import inspect  # noqa: E402

SAVE_PARAMS = ("fiscal_year", "fiscal_period", "consolidation_group", "adjustment_type",
               "description", "lines", "reverse_fiscal_year", "reverse_fiscal_period", "name")
DRAFT_ROLES = ("EPM Analyst", "System Manager")
NEW_DOC_KEYS = {"doctype", "consolidation_group", "adjustment_type", "fiscal_year",
                "fiscal_period", "description", "reverse_fiscal_year", "reverse_fiscal_period",
                "lines"}
LINE_KEYS = ("data_area_id", "main_account", "debit_amount", "credit_amount", "description")
WIREFRAME_LINES = [
    {"data_area_id": "ZZA", "main_account": "6100", "debit_amount": 18500,
     "credit_amount": 0, "description": "ZZ accrual"},
    {"data_area_id": "ZZB", "main_account": "2310", "debit_amount": 0,
     "credit_amount": 18500, "description": "ZZ accrual"},
]


class _Doc:
    """A Consolidation Journal document as the stub site hands it out: records
    insert/save, and exposes ``flags`` so a test can prove no ``ignore_*`` was
    set."""

    def __init__(self, site, data):
        self._site = site
        self.flags = types.SimpleNamespace()
        for key, value in data.items():
            setattr(self, key, value)
        self.lines = [dict(r) for r in data.get("lines", [])]

    def get(self, key, default=None):
        return getattr(self, key, default)

    def set(self, key, value):
        setattr(self, key, value)

    def append(self, field, row):
        getattr(self, field).append(dict(row))

    def _totals(self):
        self.total_debit = float(sum(float(r.get("debit_amount") or 0) for r in self.lines))
        self.total_credit = float(sum(float(r.get("credit_amount") or 0) for r in self.lines))

    def insert(self):
        self._site.insert_calls.append(self)
        if self._site.insert_raises:
            raise self._site.insert_raises
        self.name = "CJ-NEW01"
        self.status = "Draft"
        self.docstatus = 0
        self._totals()
        return self

    def save(self):
        self._site.save_calls.append(self)
        self._totals()
        return self


def _post_site(roles=("EPM Analyst",)):
    site = _Site()
    site.user = ANALYST
    site.roles = set(roles)
    site.insert_raises = None
    site.get_doc_calls = []
    site.existing = {
        "CJ-00001": {"name": "CJ-00001", "doctype": "Consolidation Journal",
                     "owner": ANALYST, "docstatus": 0, "status": "Draft",
                     "fiscal_year": 2025, "fiscal_period": 7,
                     "consolidation_group": "CG1", "adjustment_type": "topside",
                     "description": "ZZ old", "reverse_fiscal_year": 0,
                     "reverse_fiscal_period": 0, "approved_by": None,
                     "currency": "EUR", "total_debit": 5.0, "total_credit": 5.0,
                     "lines": [{"data_area_id": "ZZA", "main_account": "6100",
                                "debit_amount": 5, "credit_amount": 0, "description": ""},
                               {"data_area_id": "ZZB", "main_account": "2310",
                                "debit_amount": 0, "credit_amount": 5, "description": ""}]},
    }

    def get_doc_impl(frappe, *a, **k):
        site.get_doc_calls.append((a, k))
        if len(a) == 1 and isinstance(a[0], dict):
            return _Doc(site, a[0])
        doctype, name = a
        assert doctype == "Consolidation Journal", doctype
        if name not in site.existing:
            raise frappe.ValidationError(f"Consolidation Journal {name} not found")
        return _Doc(site, site.existing[name])

    site.get_doc_impl = get_doc_impl
    return site


def _save(site, **overrides):
    kwargs = {"fiscal_year": 2025, "fiscal_period": 7, "consolidation_group": "CG1",
              "adjustment_type": "topside", "description": "ZZ accrual\nWhy: cut-off",
              "lines": json.dumps(WIREFRAME_LINES), "reverse_fiscal_year": 2025,
              "reverse_fiscal_period": 10}
    kwargs.update(overrides)
    return _invoke(site, lambda api: api.save_journal(**kwargs))


def _save_raises(site, **overrides):
    with pytest.raises(Exception) as info:
        _save(site, **overrides)
    return info.value


def _load_api_source():
    with open(API_PY, encoding="utf-8") as f:
        return f.read()


def _only_for_literal(fn_name):
    tree = ast.parse(_load_api_source())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == fn_name)
    first = fn.body[0]
    call = first.value
    assert isinstance(call, ast.Call) and ast.unparse(call.func) == "frappe.only_for", ast.unparse(first)
    return ast.literal_eval(call.args[0])


# --- forge: the signature ---------------------------------------------------------------


def test_save_journal_signature_is_exactly_the_nine_names_and_has_no_kwargs():
    site = _post_site()
    params = _invoke(site, lambda api: list(inspect.signature(api.save_journal).parameters.values()),
                     require_json_safe=False)
    assert tuple(p.name for p in params) == SAVE_PARAMS
    kinds = [p.kind for p in params]
    assert inspect.Parameter.VAR_KEYWORD not in kinds
    assert inspect.Parameter.VAR_POSITIONAL not in kinds
    forged = ("status", "approved_by", "approved_at", "docstatus", "workflow_state", "owner",
              "currency", "total_debit", "total_credit", "amended_from")
    for name in forged:
        assert name not in SAVE_PARAMS and name not in [p.name for p in params], name


def test_forged_keyword_arguments_never_reach_save_journal():
    """Frappe drops request keys a function does not name; a direct call with
    a forged keyword raises TypeError and nothing is written."""
    forged = {"status": "Approved", "approved_by": "Administrator", "approved_at": "2025-07-01",
              "docstatus": 1, "workflow_state": "Approved", "owner": "Administrator"}
    for key, value in forged.items():
        site = _post_site()
        with pytest.raises(TypeError):
            _save(site, **{key: value})
        assert site.get_doc_calls == [], key
        assert site.insert_calls == [] and site.save_calls == [], key


def test_the_only_for_literal_is_draft_roles():
    """amended 3 Oct (E6-P1 option (c)): A05's DRAFT_ROLES and save_journal's
    only_for literal are the same tuple."""
    site = _post_site()
    draft_roles = _invoke(site, lambda api: api.DRAFT_ROLES)
    assert tuple(draft_roles) == DRAFT_ROLES
    assert tuple(_only_for_literal("save_journal")) == tuple(draft_roles)


# --- forge: inside a line ---------------------------------------------------------------


def test_forge_a_line_carrying_docstatus_parent_or_name_is_refused_naming_each_key():
    site = _post_site()
    forged = [dict(WIREFRAME_LINES[0], docstatus=1, parent="CJ-99999"),
              dict(WIREFRAME_LINES[1], name="row-1")]
    err = _save_raises(site, lines=json.dumps(forged))
    text = str(err)
    for key in ("docstatus", "parent", "name"):
        assert key in text, (key, text)
    assert site.get_doc_calls == []


# --- new draft --------------------------------------------------------------------------


def test_a_new_draft_goes_through_get_doc_and_insert_with_exactly_the_nine_keys():
    site = _post_site()
    result = _save(site)
    assert len(site.get_doc_calls) == 1
    (payload,), _k = site.get_doc_calls[0]
    assert set(payload) == NEW_DOC_KEYS, sorted(payload)
    assert payload["doctype"] == "Consolidation Journal"
    assert (payload["fiscal_year"], payload["fiscal_period"]) == (2025, 7)
    assert (payload["reverse_fiscal_year"], payload["reverse_fiscal_period"]) == (2025, 10)
    for line in payload["lines"]:
        assert tuple(line) == LINE_KEYS, line
    assert len(site.insert_calls) == 1 and site.save_calls == []
    doc = site.insert_calls[0]
    assert not [k for k in vars(doc.flags) if k.startswith("ignore")], vars(doc.flags)
    assert result == {"name": "CJ-NEW01", "docstatus": 0, "status": "Draft",
                      "total_debit": 18500.0, "total_credit": 18500.0}


def test_a_new_draft_with_no_reversal_is_this_period_only():
    for blank in ((None, None), ("", ""), (0, 0), ("0", "0")):
        site = _post_site()
        _save(site, reverse_fiscal_year=blank[0], reverse_fiscal_period=blank[1])
        (payload,), _k = site.get_doc_calls[0]
        assert (payload["reverse_fiscal_year"], payload["reverse_fiscal_period"]) == (0, 0), blank


def test_lines_as_a_list_are_accepted_too():
    site = _post_site()
    _save(site, lines=list(WIREFRAME_LINES))
    assert len(site.insert_calls) == 1


# --- update -----------------------------------------------------------------------------


def test_an_update_changes_only_the_header_fields_and_replaces_the_lines():
    site = _post_site()
    result = _save(site, name="CJ-00001", consolidation_group="CG1",
                   adjustment_type="reclassification", description="ZZ new",
                   reverse_fiscal_year=None, reverse_fiscal_period=None)
    assert site.insert_calls == [] and len(site.save_calls) == 1
    doc = site.save_calls[0]
    assert doc.adjustment_type == "reclassification"
    assert doc.description == "ZZ new"
    assert (doc.reverse_fiscal_year, doc.reverse_fiscal_period) == (0, 0)
    assert [tuple(l) for l in doc.lines] == [LINE_KEYS, LINE_KEYS]
    assert [l["debit_amount"] for l in doc.lines] == [18500, 0]
    # Untouched: owner, status, docstatus, approver, currency.
    assert doc.owner == ANALYST and doc.status == "Draft" and doc.docstatus == 0
    assert doc.approved_by is None and doc.currency == "EUR"
    assert not [k for k in vars(doc.flags) if k.startswith("ignore")], vars(doc.flags)
    assert result["name"] == "CJ-00001" and result["total_debit"] == 18500.0


def test_failure_path_a_named_approved_or_pending_journal_is_refused_without_save():
    cases = [({"docstatus": 1, "status": "Approved"}, "approved"),
             ({"docstatus": 0, "status": "Pending Approval"}, "waiting for approval")]
    for change, phrase in cases:
        site = _post_site()
        site.existing["CJ-00001"].update(change)
        err = _save_raises(site, name="CJ-00001")
        assert phrase in str(err), (change, str(err))
        assert site.save_calls == [] and site.insert_calls == [], change


def test_failure_path_a_named_journal_in_another_period_is_refused_naming_both():
    site = _post_site()
    site.existing["CJ-00001"]["fiscal_period"] = 10
    err = _save_raises(site, name="CJ-00001")
    text = str(err)
    assert "P10" in text and "P07" in text, text
    assert site.save_calls == []


# --- failure paths before any get_doc ---------------------------------------------------


def test_failure_path_a_closed_period_is_refused_before_any_get_doc():
    site = _post_site()
    err = _save_raises(site, fiscal_period=6)
    assert "Closed" in str(err) and "open period" in str(err), err
    assert site.get_doc_calls == []


def test_failure_path_an_undeclared_period_is_refused_before_any_get_doc():
    site = _post_site()
    err = _save_raises(site, fiscal_year=2031, fiscal_period=9)
    assert "not a declared period" in str(err), err
    assert site.get_doc_calls == []


def test_failure_path_a_reversal_into_a_closed_or_earlier_period_is_refused():
    cases = [((2025, 9), "is Closed"), ((2025, 6), "is not after"),
             ((2025, None), "Name both")]
    for (ry, rp), phrase in cases:
        site = _post_site()
        err = _save_raises(site, reverse_fiscal_year=ry, reverse_fiscal_period=rp)
        assert phrase in str(err), ((ry, rp), str(err))
        assert site.get_doc_calls == [], (ry, rp)


def test_failure_path_adjustment_type_other_is_refused_before_any_read():
    site = _post_site()
    err = _save_raises(site, adjustment_type="other")
    assert "other" in str(err), err
    assert site.reads == [] and site.get_doc_calls == []


def test_failure_path_epm_user_and_entity_accountant_are_refused_by_only_for():
    for role in ("EPM User", "Entity Accountant"):
        site = _post_site(roles=(role,))
        err = _save_raises(site)
        assert type(err).__name__ == "PermissionError", (role, err)
        assert site.only_for_calls == [DRAFT_ROLES], role
        assert site.reads == [] and site.get_doc_calls == [], role


def test_failure_path_an_epm_admin_only_user_is_refused_by_only_for():
    """amended 3 Oct (E6-P1 option (c)): the Close Lead approves, never drafts."""
    site = _post_site(roles=("EPM Admin",))
    err = _save_raises(site)
    assert type(err).__name__ == "PermissionError", err
    assert site.only_for_calls == [DRAFT_ROLES]
    assert site.get_doc_calls == [] and site.reads == []


def test_failure_path_bad_json_in_lines_is_a_sentence_not_a_traceback():
    for bad in ("[{not json", "{}", '"text"'):
        site = _post_site()
        err = _save_raises(site, lines=bad)
        assert type(err).__name__ == "ValidationError", (bad, err)
        assert "lines" in str(err).lower(), (bad, str(err))
        assert site.get_doc_calls == [], bad


def test_a_validate_error_from_insert_propagates_uncaught():
    site = _post_site()
    site.insert_raises = ValueError("Main Account 0000 is not a Published postable account")
    lines = [dict(WIREFRAME_LINES[0], main_account="0000"), WIREFRAME_LINES[1]]
    with pytest.raises(ValueError) as info:
        _save(site, lines=json.dumps(lines))
    assert "0000" in str(info.value)
    assert len(site.insert_calls) == 1


def test_save_journal_never_sets_ignore_flags_or_writes_around_the_document():
    source = _load_api_source()
    for banned in ("ignore_permissions", "ignore_validate", "ignore_mandatory", "db_set",
                   "set_value", "db_insert"):
        assert banned not in source, banned


# --- konsolidat#245 option D, D02: declared dimension keys round-trip through save_journal ---


def test_a_new_draft_carries_the_declared_dimension_value_on_each_line():
    site = _post_site()
    site.dimensions = [_dim("dim_cost_center")]
    lines = [dict(WIREFRAME_LINES[0], dim_cost_center="CC1"),
             dict(WIREFRAME_LINES[1], dim_cost_center="CC2")]
    _save(site, lines=json.dumps(lines))
    (payload,), _k = site.get_doc_calls[0]
    for line in payload["lines"]:
        assert tuple(line) == LINE_KEYS + ("dim_cost_center",), line
    assert [l["dim_cost_center"] for l in payload["lines"]] == ["CC1", "CC2"]


def test_a_new_draft_with_a_declared_dimension_and_no_value_is_blank_never_none():
    site = _post_site()
    site.dimensions = [_dim("dim_cost_center")]
    _save(site, lines=json.dumps(WIREFRAME_LINES))
    (payload,), _k = site.get_doc_calls[0]
    for line in payload["lines"]:
        assert line["dim_cost_center"] == ""


def test_failure_path_an_undeclared_dimension_key_on_a_line_is_refused_like_a_forged_key():
    site = _post_site()  # no dimensions declared
    lines = [dict(WIREFRAME_LINES[0], dim_cost_center="CC1"), WIREFRAME_LINES[1]]
    err = _save_raises(site, lines=json.dumps(lines))
    assert "dim_cost_center" in str(err) and "Line 1" in str(err), err
    assert site.get_doc_calls == []


def test_an_update_replaces_the_lines_declared_dimension_values_too():
    site = _post_site()
    site.dimensions = [_dim("dim_cost_center")]
    lines = [dict(WIREFRAME_LINES[0], dim_cost_center="CC9"), WIREFRAME_LINES[1]]
    _save(site, name="CJ-00001", lines=json.dumps(lines))
    doc = site.save_calls[0]
    assert doc.lines[0]["dim_cost_center"] == "CC9"
    assert doc.lines[1]["dim_cost_center"] == ""


# =====================================================================================
# A07 — send_for_approval (POST): Draft -> Pending Approval through the workflow
# =====================================================================================

NO_WORKFLOW_SENTENCE = ("The journal workflow is not installed on this site (migrate installs "
                        "it); the Close Lead approves a draft directly from Approvals.")


def _send_site(roles=("EPM Analyst",)):
    site = _post_site(roles=roles)
    site.apply_calls = []
    site.apply_raises = None
    site.apply_next_state = "Pending Approval"
    site.apply_returns_none = False
    return site


def _send(site, name="CJ-00001"):
    return _invoke(site, lambda api: api.send_for_approval(name))


def _send_raises(site, name="CJ-00001"):
    with pytest.raises(Exception) as info:
        _send(site, name)
    return info.value


def test_send_for_approval_applies_the_send_action_once_and_returns_the_new_status():
    site = _send_site()
    result = _send(site)
    assert [(doc.name, action) for doc, action in site.apply_calls] == [
        ("CJ-00001", "Send for Approval")]
    assert result == {"name": "CJ-00001", "status": "Pending Approval"}
    assert site.only_for_calls == [DRAFT_ROLES]
    assert site.insert_calls == [] and site.save_calls == []


def test_send_for_approval_rereads_the_journal_when_apply_workflow_returns_nothing():
    site = _send_site()
    site.apply_returns_none = True
    result = _send(site)
    assert len(site.apply_calls) == 1
    assert result == {"name": "CJ-00001", "status": "Pending Approval"}
    assert len(site.get_doc_calls) == 2, site.get_doc_calls


def test_failure_path_no_workflow_refuses_naming_the_fix_without_apply_workflow():
    site = _send_site()
    site.workflow = None
    err = _send_raises(site)
    assert str(err) == NO_WORKFLOW_SENTENCE, str(err)
    assert type(err).__name__ == "ValidationError", err
    assert site.apply_calls == [] and site.get_doc_calls == []


def test_failure_path_an_approved_or_already_sent_journal_is_refused_without_apply_workflow():
    cases = [({"docstatus": 1, "status": "Approved"}, "approved"),
             ({"docstatus": 2, "status": "Reversed"}, "Reversed"),
             ({"docstatus": 0, "status": "Pending Approval"}, "already sent")]
    for change, phrase in cases:
        site = _send_site()
        site.existing["CJ-00001"].update(change)
        err = _send_raises(site)
        assert type(err).__name__ == "ValidationError", (change, err)
        assert phrase in str(err), (change, str(err))
        assert site.apply_calls == [], change


def test_failure_path_a_journal_in_a_closed_period_is_refused_without_apply_workflow():
    site = _send_site()
    site.existing["CJ-00001"]["fiscal_period"] = 6
    err = _send_raises(site)
    assert "Closed" in str(err) and "open period" in str(err), str(err)
    assert site.apply_calls == []


def test_failure_path_a_journal_in_an_undeclared_period_is_refused():
    site = _send_site()
    site.existing["CJ-00001"]["fiscal_year"] = 2031
    err = _send_raises(site)
    assert "not a declared period" in str(err), str(err)
    assert site.apply_calls == []


def test_failure_path_epm_user_and_entity_accountant_are_refused_by_only_for():
    for role in ("EPM User", "Entity Accountant"):
        site = _send_site(roles=(role,))
        err = _send_raises(site)
        assert type(err).__name__ == "PermissionError", (role, err)
        assert site.only_for_calls == [DRAFT_ROLES], role
        assert site.reads == [] and site.get_doc_calls == [] and site.apply_calls == [], role


def test_failure_path_an_epm_admin_only_user_is_refused_by_only_for_before_apply_workflow():
    """amended 3 Oct (E6-P1 option (c)): the Close Lead approves, never sends."""
    site = _send_site(roles=("EPM Admin",))
    # Even with a transition that would admit EPM Admin, only_for refuses first.
    site.wf_transitions = [dict(t, allowed="EPM Admin") for t in site.wf_transitions]
    err = _send_raises(site)
    assert type(err).__name__ == "PermissionError", err
    assert site.only_for_calls == [DRAFT_ROLES]
    assert site.apply_calls == [] and site.get_doc_calls == [] and site.reads == []


def test_frappes_own_workflow_refusal_propagates_uncaught():
    site = _send_site(roles=("System Manager",))
    site.apply_raises = RuntimeError("Not a valid Workflow Action")
    with pytest.raises(RuntimeError) as info:
        _send(site)
    assert "Not a valid Workflow Action" in str(info.value)
    assert len(site.apply_calls) == 1


def test_send_for_approval_signature_is_exactly_name_with_no_kwargs():
    site = _send_site()
    params = _invoke(site, lambda api: list(
        inspect.signature(api.send_for_approval).parameters.values()), require_json_safe=False)
    assert tuple(p.name for p in params) == ("name",)
    kinds = [p.kind for p in params]
    assert inspect.Parameter.VAR_KEYWORD not in kinds
    assert inspect.Parameter.VAR_POSITIONAL not in kinds


def test_forged_keyword_arguments_never_reach_send_for_approval():
    for key, value in {"status": "Approved", "docstatus": 1, "workflow_state": "Approved",
                       "action": "Approve", "owner": "Administrator"}.items():
        site = _send_site()
        with pytest.raises(TypeError):
            _invoke(site, lambda api: api.send_for_approval("CJ-00001", **{key: value}))
        assert site.apply_calls == [] and site.get_doc_calls == [], key


def test_send_for_approval_only_for_literal_is_draft_roles():
    site = _send_site()
    draft_roles = _invoke(site, lambda api: api.DRAFT_ROLES)
    assert tuple(_only_for_literal("send_for_approval")) == tuple(draft_roles) == DRAFT_ROLES


def test_send_for_approval_is_a_post():
    tree = ast.parse(_load_api_source())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "send_for_approval")
    decorators = [ast.unparse(d) for d in fn.decorator_list]
    assert decorators == ["frappe.whitelist(methods=['POST'])"], decorators
