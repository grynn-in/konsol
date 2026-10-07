"""Remind endpoint: konsol/close/remind_api.py ``remind`` (konsol#305 Y54;
story 1.5; #305-1.5-1; #305-Q2-1; C-R1..C-R5).

``remind(fiscal_year, fiscal_period, entity, topic)`` (POST): the Close Lead
or Group Accountant reminds one entity about one topic. Every refusal comes
before any write. Then, in the request's transaction, one Notification Log per
recipient (type Alert, never email) and one ``reminder_sent`` event through
``close_event.record``, after the logs.

Loaded against a stub frappe (the ``_Site`` pattern of test_close_ic_api.py,
copied, not imported). The real ``remind_model.py`` is loaded by path under
its dotted name, so the refusals and recipients are the real producer's.

R53b (#305-R52-2-1): topic ic reads the period's intercompany pairs through
the REAL ``ic_api.checked_rows`` (loaded by path with the real ``ic_model``,
``close_policy_model`` and ``timefmt``; only ``ch_read`` is a stub, as in
test_close_ic_api.py), and refuses an entity with no over-tolerance pair.
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
API_PY = os.path.join(CLOSE_DIR, "remind_api.py")

LEAD = "zz-lead@example.com"
EA1 = "zz-ea1@example.com"
EA2 = "zz-ea2@example.com"
OFF = "zz-off@example.com"

REMIND_PARAMS = ["fiscal_year", "fiscal_period", "entity", "topic"]


def _load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


REMIND_MODEL = _load_path("remind_model_for_remind_api_test",
                          os.path.join(CLOSE_DIR, "remind_model.py"))
sys.modules.pop("remind_model_for_remind_api_test", None)


def _period(fy, fp, status="Open"):
    return {"fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
            "period_label": "P%02d" % fp, "period_type": "Regular",
            "start_date": date(fy, fp, 1), "end_date": date(fy, fp, 28),
            "quarter": "Q%d" % ((fp - 1) // 3 + 1), "status": status}


def _perm(user, entity="UK01", allow="Entity"):
    return {"user": user, "allow": allow, "for_value": entity}


def _ic_row(group, ea, eb, status, difference="12.5", tolerance="5"):
    """A gold_ic_reconciliation row as FORMAT JSON hands it back."""
    return {"consolidation_group": group, "fiscal_year": 2025, "fiscal_period": 7,
            "entity_a": ea, "account_a": "1810", "entity_b": eb, "account_b": "2810",
            "difference": difference, "tolerance": tolerance, "match_status": status}


class _Site:
    def __init__(self):
        self.user = LEAD
        self.roles = {"EPM Admin"}
        self.periods = [_period(2025, 6, "Closed"), _period(2025, 7, "Open")]
        self.permissions = [_perm(EA2), _perm(EA1), _perm(EA1), _perm("Administrator"),
                            _perm(OFF), _perm("zz-de@example.com", "DE01")]
        self.users = [{"name": EA1, "enabled": 1}, {"name": EA2, "enabled": 1},
                      {"name": OFF, "enabled": 0}, {"name": LEAD, "enabled": 1}]
        self.tbs = []   # submitted Trial Balance Submissions: {data_area_id, fy, fp, docstatus}
        self.tbes = []  # submitted TB Exceptions, same keys
        # R53b: the intercompany side (ic_api.checked_rows): Published
        # Intercompany Accounts, the Close Settings declaration and the
        # warehouse's gold_ic_reconciliation rows. UK01 is over tolerance
        # with DE01; FR01 has only a matched pair.
        self.published = 3
        self.declaration = ""
        self.ic_rows = [_ic_row("ROOT", "UK01", "DE01", "over_tolerance"),
                        _ic_row("ROOT", "UK01", "FR01", "matched"),
                        _ic_row("SUB", "FR01", "DE01", "within_tolerance")]
        self.ch_error = None
        self.ch_calls = []
        self.allowed = None
        self.insert_error = None
        self.reads = []
        self.log = []        # ("insert", doc) and ("record", event), in order
        self.whitelist_kwargs = []
        self.only_for_calls = []

    @property
    def inserts(self):
        return [x for kind, x in self.log if kind == "insert"]

    @property
    def recorded(self):
        return [x for kind, x in self.log if kind == "record"]


def _match_value(value, cond):
    if isinstance(cond, (list, tuple)):
        op, arg = cond[0], cond[1]
        if op == "in":
            return value in arg
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
        site.whitelist_kwargs.append(k)
        return lambda fn: fn

    def get_all(doctype, filters=None, fields=None, limit_page_length=None, **k):
        site.reads.append(("get_all", doctype, filters, fields, limit_page_length))
        if doctype == "User Permission":
            rows = [r for r in site.permissions if _match(r, filters)]
        elif doctype == "User":
            rows = [r for r in site.users if _match(r, filters)]
        else:
            raise AssertionError("unexpected get_all on %s" % doctype)
        return [{f: r.get(f) for f in fields} for r in rows]

    def exists(doctype, filters=None, **k):
        site.reads.append(("exists", doctype, filters))
        rows = {"Trial Balance Submission": site.tbs, "TB Exception": site.tbes}.get(doctype)
        if rows is None:
            raise AssertionError("unexpected exists on %s" % doctype)
        hits = [r for r in rows if _match(r, filters)]
        return "X-1" if hits else None

    class _Doc:
        def __init__(self, data):
            self.data = dict(data)

        def insert(self, ignore_permissions=False):
            if site.insert_error is not None:
                raise site.insert_error
            site.log.append(("insert", dict(self.data, _ignore_permissions=ignore_permissions)))
            return self

    def get_doc(data):
        assert isinstance(data, dict), data
        return _Doc(data)

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.only_for = only_for
    frappe.whitelist = whitelist
    frappe.get_all = get_all
    frappe.get_doc = get_doc
    frappe.get_roles = lambda user=None: sorted(site.roles)
    def count(doctype, filters=None, **k):
        site.reads.append(("count", doctype, filters))
        assert (doctype, filters) == ("Intercompany Account", {"status": "Published"})
        return site.published

    def table_exists(doctype, **k):
        site.reads.append(("table_exists", doctype))
        assert doctype == "Intercompany Account", doctype
        return True

    def get_single_value(doctype, field, **k):
        site.reads.append(("single", doctype, field))
        assert (doctype, field) == ("Close Settings", "intercompany_declaration")
        return site.declaration

    frappe.db = types.SimpleNamespace(exists=exists, count=count, table_exists=table_exists,
                                      get_single_value=get_single_value)
    frappe.session = types.SimpleNamespace(user=site.user)
    frappe.utils = types.SimpleNamespace(get_fullname=lambda user=None: "Zz Lead"
                                         if user == LEAD else "ZZ " + str(user),
                                         get_system_timezone=lambda: "Europe/London")
    return frappe


def _ch_read(site):
    ch = types.ModuleType("konsol.close.ch_read")

    def rows(sql, params=None):
        site.ch_calls.append((sql, dict(params or {})))
        if site.ch_error is not None:
            raise site.ch_error
        assert "gold_ic_reconciliation" in sql, sql
        return [dict(r) for r in site.ic_rows]

    ch.rows = rows
    ch.not_built = lambda e: "UNKNOWN_TABLE" in str(e)
    ch.error_names = lambda e: set()
    return ch


def _fiscal_calendar(site):
    fc = types.ModuleType("konsol.fiscal_calendar")

    def fiscal_period_rows():
        site.reads.append(("sql", "fiscal_period_rows"))
        return [dict(r) for r in site.periods]

    fc.fiscal_period_rows = fiscal_period_rows
    return fc


def _close_event(site):
    ce = types.ModuleType("konsol.close.close_event")

    def record(kind, fiscal_year, fiscal_period, reference_doctype=None, reference_name=None,
               reason=None, detail=None, entity=None):
        site.log.append(("record", {"kind": kind, "fiscal_year": fiscal_year,
                                    "fiscal_period": fiscal_period,
                                    "reference_doctype": reference_doctype,
                                    "reference_name": reference_name, "reason": reason,
                                    "detail": detail, "entity": entity}))
        return "CE-NEW-%d" % len(site.recorded)

    ce.record = record
    return ce


def _invoke(site, run):
    frappe = _frappe(site)
    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    close = types.ModuleType("konsol.close")
    close.__path__ = []
    fiscal_calendar = _fiscal_calendar(site)
    entity_permissions = types.ModuleType("konsol.entity_permissions")
    entity_permissions.allowed_entity_codes = lambda user=None: site.allowed
    konsol.close = close
    konsol.fiscal_calendar = fiscal_calendar
    konsol.entity_permissions = entity_permissions
    close_event = _close_event(site)
    close.close_event = close_event
    ch_read = _ch_read(site)
    close.ch_read = ch_read
    names = ["frappe", "konsol", "konsol.close", "konsol.fiscal_calendar",
             "konsol.entity_permissions", "konsol.close.close_event",
             "konsol.close.remind_model", "konsol.close.ch_read", "konsol.close.ic_model",
             "konsol.close.close_policy_model", "konsol.close.timefmt",
             "konsol.close.ic_api", "close_remind_api_under_test"]
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({"frappe": frappe, "konsol": konsol, "konsol.close": close,
                        "konsol.fiscal_calendar": fiscal_calendar,
                        "konsol.entity_permissions": entity_permissions,
                        "konsol.close.close_event": close_event,
                        "konsol.close.ch_read": ch_read})
    try:
        close.remind_model = _load_path("konsol.close.remind_model",
                                        os.path.join(CLOSE_DIR, "remind_model.py"))
        # R53b: the real ic_api (and the real pure modules it imports).
        close.ic_model = _load_path("konsol.close.ic_model", os.path.join(CLOSE_DIR, "ic_model.py"))
        close.close_policy_model = _load_path(
            "konsol.close.close_policy_model", os.path.join(CLOSE_DIR, "close_policy_model.py"))
        close.timefmt = _load_path("konsol.close.timefmt", os.path.join(CLOSE_DIR, "timefmt.py"))
        close.ic_api = _load_path("konsol.close.ic_api", os.path.join(CLOSE_DIR, "ic_api.py"))
        api = _load_path("close_remind_api_under_test", API_PY)
        return run(api)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _remind(site, fy=2025, fp=7, entity="UK01", topic="tb"):
    result = _invoke(site, lambda api: api.remind(fy, fp, entity, topic))
    json.dumps(result)  # JSON-safe
    return result


def _refused(site, **kw):
    """The remind is refused, and nothing at all was written."""
    with pytest.raises(Exception) as info:
        _remind(site, **kw)
    assert site.log == [], site.log
    return info.value


# --- the happy path ---------------------------------------------------------

def test_remind_writes_one_alert_per_recipient_then_one_event():
    site = _Site()
    out = _remind(site)
    assert out == {"event": "CE-NEW-1", "recipients": 2}
    kinds = [kind for kind, _ in site.log]
    assert kinds == ["insert", "insert", "record"], kinds  # the event comes after the logs
    subject = "Reminder from Zz Lead: UK01's FY2025 P07 trial balance is still missing"
    assert subject == REMIND_MODEL.subject("Zz Lead", "UK01", "tb", "FY2025 P07")
    for doc, user in zip(site.inserts, [EA1, EA2]):
        assert doc == {"doctype": "Notification Log", "subject": subject, "for_user": user,
                       "from_user": LEAD, "type": "Alert", "document_type": "Entity",
                       "document_name": "UK01", "link": "/close/2025/7/trial-balances",
                       "_ignore_permissions": True}
    ev = site.recorded[0]
    assert ev["kind"] == "reminder_sent"
    assert (ev["fiscal_year"], ev["fiscal_period"], ev["entity"]) == (2025, 7, "UK01")
    assert ev["reason"] is None
    assert ev["reference_doctype"] is None and ev["reference_name"] is None
    assert ev["detail"] == {"topic": "tb", "recipients": [EA1, EA2], "subject": subject}


def test_remind_recipients_are_the_real_models_direct_permissions():
    """#305-Q2-1: direct Entity permissions only; disabled, Administrator,
    duplicates and other entities' users are left out by remind_model."""
    site = _Site()
    _remind(site)
    expected = REMIND_MODEL.recipients(
        "UK01", [p for p in site.permissions if p["for_value"] == "UK01"],
        {u["name"]: {"enabled": u["enabled"]} for u in site.users})
    assert [d["for_user"] for d in site.inserts] == expected == [EA1, EA2]
    perm_reads = [r for r in site.reads if r[:2] == ("get_all", "User Permission")]
    assert perm_reads == [("get_all", "User Permission", {"allow": "Entity", "for_value": "UK01"},
                           ["user", "allow", "for_value"], 0)]


def test_remind_ic_links_to_intercompany_and_reads_no_tb():
    site = _Site()
    site.tbs = [{"data_area_id": "UK01", "fiscal_year": 2025, "fiscal_period": 7, "docstatus": 1}]
    out = _remind(site, topic="ic")
    assert out["recipients"] == 2
    assert {d["link"] for d in site.inserts} == {"/close/2025/7/intercompany"}
    assert {d["type"] for d in site.inserts} == {"Alert"}
    assert site.recorded[0]["detail"]["topic"] == "ic"
    assert site.recorded[0]["detail"]["subject"].endswith(
        "UK01's FY2025 P07 intercompany difference needs attention")
    assert not [r for r in site.reads if r[0] == "exists"]


def test_group_accountant_and_system_manager_may_remind():
    for roles in ({"EPM Analyst"}, {"System Manager"}):
        site = _Site()
        site.roles = roles
        assert _remind(site)["recipients"] == 2
        assert site.only_for_calls[0] == tuple(REMIND_MODEL.REMIND_ROLES)


def test_period_given_as_text_is_read_as_numbers():
    site = _Site()
    assert _remind(site, fy="2025", fp="7")["recipients"] == 2
    assert (site.recorded[0]["fiscal_year"], site.recorded[0]["fiscal_period"]) == (2025, 7)


# --- refusals: each one writes nothing ---------------------------------------

def test_refused_unknown_topic():
    site = _Site()
    err = _refused(site, topic="x")
    assert str(err) == REMIND_MODEL.UNKNOWN_TOPIC


def test_refused_period_not_open():
    site = _Site()
    err = _refused(site, fp=6)
    assert str(err) == "FY2025 P06 is Closed: remind only in an Open period."


def test_refused_undeclared_period():
    site = _Site()
    err = _refused(site, fp=8)
    assert "FY2025 P08 is not declared" in str(err)


def test_refused_period_not_a_number():
    site = _Site()
    err = _refused(site, fp="seven")
    assert "whole numbers" in str(err)


def test_refused_entity_the_caller_cannot_see():
    site = _Site()
    site.allowed = {"DE01"}
    err = _refused(site)
    assert str(err) == ("UK01 is not one of your entities: you can remind only about "
                        "entities you can see.")


def test_scoped_caller_may_remind_an_entity_they_see():
    site = _Site()
    site.allowed = {"UK01"}
    assert _remind(site)["recipients"] == 2


def test_refused_tb_already_submitted():
    site = _Site()
    site.tbs = [{"data_area_id": "UK01", "fiscal_year": 2025, "fiscal_period": 7, "docstatus": 1}]
    err = _refused(site)
    assert str(err) == "UK01's FY2025 P07 trial balance is already in: nothing to remind."
    assert ("exists", "Trial Balance Submission",
            {"data_area_id": "UK01", "fiscal_year": 2025, "fiscal_period": 7,
             "docstatus": 1}) in site.reads


def test_refused_tb_exception_declared():
    site = _Site()
    site.tbes = [{"data_area_id": "UK01", "fiscal_year": 2025, "fiscal_period": 7,
                  "docstatus": 1}]
    err = _refused(site)
    assert "already in" in str(err)


def test_a_draft_or_other_period_tb_does_not_count_as_in():
    site = _Site()
    site.tbs = [{"data_area_id": "UK01", "fiscal_year": 2025, "fiscal_period": 7, "docstatus": 0},
                {"data_area_id": "UK01", "fiscal_year": 2025, "fiscal_period": 6, "docstatus": 1},
                {"data_area_id": "DE01", "fiscal_year": 2025, "fiscal_period": 7, "docstatus": 1}]
    assert _remind(site)["recipients"] == 2


def test_refused_nobody_assigned():
    site = _Site()
    site.permissions = [_perm("zz-de@example.com", "DE01")]
    err = _refused(site)
    assert str(err) == ("No one is named for UK01: a System Manager gives a user a User "
                        "Permission on Entity UK01 before it can be reminded.")
    assert not [r for r in site.reads if r[:2] == ("get_all", "User")]


def test_refused_when_every_permitted_user_is_disabled_or_administrator():
    site = _Site()
    site.permissions = [_perm(OFF), _perm("Administrator"), _perm("Guest")]
    err = _refused(site)
    assert str(err).startswith("No one is named for UK01")


def test_a_permitted_user_with_no_user_record_raises_and_writes_nothing():
    site = _Site()
    site.permissions = [_perm(EA1), _perm("zz-ghost@example.com")]
    err = _refused(site)
    assert "zz-ghost@example.com" in str(err)


def test_refused_entity_accountant_before_any_read():
    site = _Site()
    site.roles = {"Entity Accountant"}
    err = _refused(site)
    assert type(err).__name__ == "PermissionError"
    assert site.reads == []


def test_refusals_come_in_the_models_order():
    """Closed period AND an unseen entity AND nobody assigned: the period
    sentence wins (remind_model.refusal's order)."""
    site = _Site()
    site.allowed = {"DE01"}
    site.permissions = []
    err = _refused(site, fp=6)
    assert "is Closed" in str(err)


# --- R53b (#305-R52-2-1): topic ic needs an over-tolerance pair on the entity ---

IC_MODEL = _load_path("ic_model_for_remind_api_test", os.path.join(CLOSE_DIR, "ic_model.py"))
sys.modules.pop("ic_model_for_remind_api_test", None)


def test_r53b_ic_refused_for_an_entity_with_only_matched_pairs():
    """Failure path: FR01's pairs are matched / within tolerance: refused
    before any write (no Notification Log, no Close Event)."""
    site = _Site()
    site.permissions = [_perm(EA1, "FR01")]
    err = _refused(site, entity="FR01", topic="ic")
    assert str(err) == ("FR01 has no intercompany pair over tolerance in FY2025 P07: "
                        "nothing to remind about.")
    assert str(err) == REMIND_MODEL.ic_refusal("FR01", site.ic_rows, "FY2025 P07")
    assert len(site.ch_calls) == 1 and "gold_ic_reconciliation" in site.ch_calls[0][0]
    assert site.ch_calls[0][1] == {"fy": 2025, "fp": 7}


def test_r53b_ic_refused_for_an_entity_with_no_pair_at_all():
    site = _Site()
    site.permissions = [_perm(EA1, "NL01")]
    err = _refused(site, entity="NL01", topic="ic")
    assert str(err) == ("NL01 has no intercompany pair over tolerance in FY2025 P07: "
                        "nothing to remind about.")


def test_r53b_ic_accepted_on_either_side_of_an_over_tolerance_pair():
    for entity in ("UK01", "DE01"):
        site = _Site()
        site.permissions = [_perm(EA1, entity)]
        out = _remind(site, entity=entity, topic="ic")
        assert out == {"event": "CE-NEW-1", "recipients": 1}, entity
        assert site.recorded[0]["detail"]["topic"] == "ic"


def test_r53b_ic_not_configured_not_applicable_or_unreadable_is_refused():
    """Failure path: no warehouse answer is never read as "no pair" or as
    "go ahead"; the state's own sentence refuses, before any write."""
    for setup in ("not_configured", "declared_none", "conflict", "error", "not_built"):
        site = _Site()
        if setup == "not_configured":
            site.published = 0
        elif setup in ("declared_none", "conflict"):
            site.declaration = "None in this group"
            site.published = 0 if setup == "declared_none" else 3
        elif setup == "error":
            site.ch_error = RuntimeError("ClickHouse down")
        else:
            site.ch_error = RuntimeError("UNKNOWN_TABLE gold_ic_reconciliation")
        err = _refused(site, topic="ic")
        assert str(err).endswith(" Nothing was reminded."), (setup, str(err))
        if setup == "not_configured":
            assert str(err) == IC_MODEL.NOT_CONFIGURED + " Nothing was reminded."
            assert site.ch_calls == []
        if setup == "declared_none":
            assert str(err) == IC_MODEL.NOT_APPLICABLE + " Nothing was reminded."
            assert site.ch_calls == []
        if setup == "error":
            assert str(err).startswith("Intercompany could not be checked: RuntimeError")
        if setup == "not_built":
            assert str(err) == IC_MODEL.NOT_BUILT + " Nothing was reminded."


def test_r53b_earlier_refusals_come_first_and_read_no_warehouse():
    """A closed period, or an entity the caller cannot see, refuses with its
    own sentence and reads no intercompany pairs."""
    site = _Site()
    err = _refused(site, fp=6, entity="FR01", topic="ic")
    assert "is Closed" in str(err)
    assert site.ch_calls == []
    site = _Site()
    site.allowed = {"DE01"}
    err = _refused(site, entity="FR01", topic="ic")
    assert "not one of your entities" in str(err)
    assert site.ch_calls == []


def test_r53b_tb_reads_no_intercompany():
    site = _Site()
    _remind(site)
    assert site.ch_calls == []
    assert not [r for r in site.reads if r[0] in ("count", "single", "table_exists")]


# --- the write is all or nothing ----------------------------------------------

def test_a_failed_notification_insert_propagates_and_records_no_event():
    site = _Site()
    site.insert_error = RuntimeError("Notification Log insert failed")
    with pytest.raises(RuntimeError):
        _remind(site)
    assert site.recorded == []


# --- the endpoint contract ------------------------------------------------------

def test_remind_is_post_only():
    site = _Site()
    _invoke(site, lambda api: api.remind)
    assert {"methods": ["POST"]} in site.whitelist_kwargs


def _forge_problems(fn):
    """Why a request could carry a forged key into ``fn``: Frappe drops any
    request key the signature does not name, unless it takes **kwargs."""
    params = inspect.signature(fn).parameters
    problems = []
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        problems.append("takes **kwargs")
    if list(params) != REMIND_PARAMS:
        problems.append("parameters are %s" % list(params))
    return problems


def test_remind_signature_takes_no_forged_keys():
    """Forge: a forged recipients, subject, actor, link or type never reaches
    the function."""
    site = _Site()
    fn = _invoke(site, lambda api: api.remind)
    assert _forge_problems(fn) == []


def test_forge_check_catches_kwargs_and_extra_parameters():
    """Failure path: the forge check can fail."""
    def with_kwargs(fiscal_year, fiscal_period, entity, topic, **kwargs):
        pass

    def with_recipients(fiscal_year, fiscal_period, entity, topic, recipients=None):
        pass

    assert "takes **kwargs" in _forge_problems(with_kwargs)
    assert _forge_problems(with_recipients)


def test_module_names_no_close_event_doctype():
    """The one-writer rule: the event is written only through
    close_event.record (test_close_event_writer.py ALLOWED)."""
    with open(API_PY, encoding="utf-8") as fh:
        source = fh.read()
    assert '"Close Event"' not in source and "'Close Event'" not in source
