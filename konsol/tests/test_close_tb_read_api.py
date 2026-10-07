"""TB read API: konsol/close/tb_read_api.py `my_tbs` (konsol#305 A25; stories 3.1, 3.7).

GET `my_tbs(fiscal_year, fiscal_period)` lists the caller's in-scope entities
with a status each for the period. Loaded against a stub frappe (pattern:
test_close_signoff_gate.py `_load`/`_call`, copied, not imported). The stub
site applies the filters the module sends (docstatus, "in", period), so a rule
enforced by the query is really exercised, not assumed by the stub.
`signoff_gate.in_scope_entities` (A17, tested there) and
`entity_permissions.allowed_entity_codes` are stubbed; `signoff_model` (A10)
is the real module, loaded by path.
"""
import importlib.util
import json
import os
import re
import sys
import types
from datetime import date, datetime

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PY = os.path.join(APP_DIR, "close", "tb_read_api.py")
SIGNOFF_MODEL_PY = os.path.join(APP_DIR, "close", "signoff_model.py")
VIEW_MODEL_PY = os.path.join(APP_DIR, "close", "tb_view_model.py")
TIMEFMT_PY = os.path.join(APP_DIR, "close", "timefmt.py")
DEADLINES_PY = os.path.join(APP_DIR, "close", "deadlines.py")
DEADLINE_MODEL_PY = os.path.join(APP_DIR, "close", "deadline_model.py")
#: The stub site's system time zone (A55): BST (+01:00) in October 2025.
SITE_TZ = "Europe/London"
BASIS_MODEL_PY = os.path.join(APP_DIR, "tb_basis_model.py")
CONTROLLER_PY = os.path.join(APP_DIR, "consolidation", "doctype", "trial_balance_submission",
                             "trial_balance_submission.py")
CONTROLLER = "konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission"

ALL_CLOSE_ROLES = ("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager")
QUARTERS = {1: "Q1", 2: "Q1", 3: "Q1", 4: "Q2", 5: "Q2", 6: "Q2",
            7: "Q3", 8: "Q3", 9: "Q3", 10: "Q4", 11: "Q4", 12: "Q4"}


class _D(dict):
    """frappe._dict: item and attribute access."""

    def __getattr__(self, name):
        return self.get(name)


def _month_end(fy, fp):
    nxt = date(fy + (fp == 12), fp % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def _year(fy, status="Open", quarters=QUARTERS, closing=None):
    rows = []
    for fp in range(1, 13):
        rows.append({"fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
                     "period_label": "P%02d" % fp, "period_type": "Regular",
                     "start_date": date(fy, fp, 1), "end_date": _month_end(fy, fp),
                     "quarter": quarters.get(fp, ""), "status": status})
    if closing is not None:
        rows.append({"fiscal_year": fy, "fiscal_period": 13, "period_code": "P13",
                     "period_label": "Closing", "period_type": "Closing",
                     "start_date": date(fy, 12, 31), "end_date": date(fy, 12, 31),
                     "quarter": "", "status": closing})
    return rows


def _entity(name, frequency="Monthly"):
    return {"name": name, "entity_name": "Entity " + name, "reporting_frequency": frequency}


def _tb(name, entity, fy=2025, fp=9, docstatus=1, owner="zz-lead@example.com", on_behalf="No",
        basis="Period movement"):
    return {"name": name, "data_area_id": entity, "fiscal_year": fy, "fiscal_period": fp,
            "docstatus": docstatus, "owner": owner, "uploaded_on_behalf": on_behalf,
            "creation": datetime(2025, 10, 3, 9, 30), "amount_basis": basis,
            "tb_file": "/private/files/%s.csv" % name}


def _exc(name, entity, fy=2025, fp=9, docstatus=1):
    return {"name": name, "data_area_id": entity, "fiscal_year": fy, "fiscal_period": fp,
            "docstatus": docstatus, "reason": "Dormant", "declared_by": "zz-lead@example.com",
            "creation": datetime(2025, 10, 4, 11, 15)}


class _Site:
    """FY2025, every period Open. In scope: ZZA (Monthly, TB received),
    ZZB (Quarterly; P09 is a quarter-end), ZZC (Monthly, no TB).
    The caller is unrestricted (allowed_entity_codes -> None) and may create TBs."""

    def __init__(self):
        self.rows = _year(2025)
        self.in_scope = ["ZZA", "ZZB", "ZZC"]
        self.allowed = None
        self.can_create = True
        self.records = {
            "Entity": [_entity("ZZA"), _entity("ZZB", "Quarterly"), _entity("ZZC"),
                       _entity("ZZOUT")],
            "Trial Balance Submission": [_tb("TB-A", "ZZA")],
            "TB Exception": [],
        }
        self.only_for = []
        self.whitelisted = {}
        self.get_all_calls = []
        self.files = {}          # file_url -> content (bytes or str), for tb_compare
        self.files_read = []
        self.access_checked = []
        self.gaps = []                    # sign_off_problems()["config_gaps"]
        self.sign_off_problems_calls = []
        self.reminders = []               # close_event.reminders(keys, topic)
        # deadlines.period_deadlines(keys, today): None -> the stub computes it
        # with the REAL deadline_model from deadline_rules/holidays (D56).
        self.deadlines = None
        self.deadline_rules = []          # Close Deadline Rule rows (none declared)
        self.holidays = set()             # Close Holiday dates
        self.deadline_calls = []          # D56: (keys, today) per period_deadlines read
        self.today = date(2025, 10, 6)    # frappe.utils.getdate()
        self.reminder_calls = []          # Y56: (keys, topic) per close_event.reminders read
        self.roles = ["EPM Admin"]        # frappe.get_roles() (Y56: can_remind)
        self.records["User"] = []         # Y56: the reminder senders' full names


def _match(value, cond):
    if isinstance(cond, (list, tuple)):
        op, arg = cond[0], cond[1]
        if op == "in":
            return value in arg
        if op in ("=", "=="):
            return value == arg
        raise AssertionError("stub: unsupported operator %r" % (op,))
    return value == cond


def _load(site):
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, title=None, **k):
        err = (exc or frappe.ValidationError)(msg)
        err.title = title
        raise err

    def whitelist(*a, **k):
        def deco(fn):
            site.whitelisted[fn.__name__] = k.get("methods")
            return fn
        return deco

    def only_for(roles, *a, **k):
        site.only_for.append(tuple(roles) if isinstance(roles, (list, tuple)) else (roles,))

    def get_all(doctype, filters=None, fields=None, order_by=None, pluck=None, **k):
        site.get_all_calls.append((doctype, dict(filters or {})))
        assert doctype in site.records, "stub: unexpected doctype %s" % doctype
        rows = [r for r in site.records[doctype]
                if all(_match(r.get(f), c) for f, c in (filters or {}).items())]
        if pluck:
            return [r.get(pluck) for r in rows]
        return [_D({f: r.get(f) for f in (fields or ["name"])}) for r in rows]

    def has_permission(doctype, ptype="read", *a, **k):
        assert (doctype, ptype) == ("Trial Balance Submission", "create"), (doctype, ptype)
        return site.can_create

    class _File:
        def __init__(self, url):
            self.url = url

        def get_content(self):
            site.files_read.append(self.url)
            return site.files[self.url]

    def get_doc(doctype, filters=None, *a, **k):
        assert doctype == "File" and set(filters) == {"file_url"}, (doctype, filters)
        assert filters["file_url"] in site.files, "stub: no file %s" % filters["file_url"]
        return _File(filters["file_url"])

    frappe.get_doc = get_doc
    frappe.get_roles = lambda user=None: list(site.roles)
    frappe.throw = throw
    frappe.whitelist = whitelist
    frappe.only_for = only_for
    frappe.get_all = get_all
    frappe.has_permission = has_permission
    frappe._ = lambda s: s
    frappe._dict = _D
    frappe.session = types.SimpleNamespace(user="zz-ea@example.com")
    frappe.utils = types.SimpleNamespace(get_system_timezone=lambda: SITE_TZ,
                                         getdate=lambda *a: site.today)

    def _by_path(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    konsol = types.ModuleType("konsol")
    close = types.ModuleType("konsol.close")
    signoff_model = _by_path("konsol.close.signoff_model", SIGNOFF_MODEL_PY)
    gate = types.ModuleType("konsol.close.signoff_gate")

    def in_scope_entities(fy, fp):
        assert (fy, fp) == site.asked, (fy, fp)
        return list(site.in_scope)

    def sign_off_problems(fy, fp):
        site.sign_off_problems_calls.append((fy, fp))
        return {"config_gaps": list(site.gaps), "order": None, "completeness": None}

    gate.in_scope_entities = in_scope_entities
    gate.sign_off_problems = sign_off_problems
    close.signoff_model, close.signoff_gate = signoff_model, gate
    close.timefmt = _by_path("konsol.close.timefmt", TIMEFMT_PY)
    # Wave 5b (C-X1): Y56/D56 lazily import close_event.reminders and
    # deadlines.period_deadlines; the stub package carries both.
    close_event = types.ModuleType("konsol.close.close_event")

    def reminders(keys, topic=None):
        site.reminder_calls.append((list(keys), topic))
        return site.reminders

    close_event.reminders = reminders
    deadlines = types.ModuleType("konsol.close.deadlines")

    def period_deadlines(keys, today):
        site.deadline_calls.append((list(keys), today))
        if site.deadlines is not None:
            return site.deadlines
        model = _by_path("test_close_tb_read_api_deadline_model", DEADLINE_MODEL_PY)
        wanted = {(int(fy), int(fp)) for fy, fp in keys}
        return {(r["fiscal_year"], r["fiscal_period"]): model.period_deadlines(
                    site.deadline_rules, site.holidays, r["end_date"], today)
                for r in site.rows if (r["fiscal_year"], r["fiscal_period"]) in wanted
                and r["period_type"] == "Regular"}

    deadlines.period_deadlines = period_deadlines
    close.close_event, close.deadlines = close_event, deadlines
    calendar = types.ModuleType("konsol.fiscal_calendar")
    calendar.fiscal_period_rows = lambda: [dict(r) for r in site.rows]
    perms = types.ModuleType("konsol.entity_permissions")
    perms.allowed_entity_codes = lambda user=None: (
        None if site.allowed is None else set(site.allowed))

    def assert_entity_access(code, user=None):
        site.access_checked.append(code)
        if site.allowed is not None and code not in site.allowed:
            raise frappe.PermissionError("Not permitted to access entity '%s'" % code)

    perms.assert_entity_access = assert_entity_access
    period_status = types.ModuleType("konsol.period_status")
    period_status.PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})
    period_status.assert_open = period_status.assert_postable = lambda *a, **k: None
    konsol.close, konsol.fiscal_calendar = close, calendar
    konsol.entity_permissions, konsol.period_status = perms, period_status

    # tb_compare parses with the REAL parse_tb_csv (the controller, loaded by
    # path) and compares with the REAL A12 model; only frappe is stubbed.
    doc_mod = types.ModuleType("frappe.model.document")
    doc_mod.Document = type("Document", (), {})
    clickhouse = types.ModuleType("konsol.clickhouse")

    def _no_clickhouse(*a, **k):
        raise AssertionError("stub: a TB read must not touch ClickHouse")

    clickhouse.execute = clickhouse.ensure_raw_tables = _no_clickhouse
    # konsol#255: the site's Dimension records; none unless a test declares one.
    tb_dimension = types.ModuleType("konsol.tb_dimension")
    tb_dimension.declared_dimensions = lambda: list(getattr(site, "tb_dimensions", ()))
    mods = {"frappe": frappe, "frappe.model": types.ModuleType("frappe.model"),
            "frappe.model.document": doc_mod, "konsol": konsol, "konsol.close": close,
            "konsol.close.signoff_model": signoff_model,
            "konsol.close.signoff_gate": gate,
            "konsol.close.timefmt": close.timefmt,
            "konsol.close.close_event": close_event,
            "konsol.close.deadlines": deadlines,
            "konsol.fiscal_calendar": calendar, "konsol.entity_permissions": perms,
            "konsol.period_status": period_status, "konsol.clickhouse": clickhouse,
            "konsol.tb_dimension": tb_dimension,
            "konsol.consolidation": types.ModuleType("konsol.consolidation"),
            "konsol.consolidation.doctype": types.ModuleType("konsol.consolidation.doctype"),
            "konsol.consolidation.doctype.trial_balance_submission":
                types.ModuleType("konsol.consolidation.doctype.trial_balance_submission")}
    saved = {n: sys.modules.get(n) for n in list(mods) + ["konsol.tb_basis_model",
                                                          "konsol.tb_dimension_model",
                                                          "konsol.tb_currency_model",
                                                          "konsol.tb_balance_model", CONTROLLER,
                                                          "konsol.close.tb_view_model"]}
    sys.modules.update(mods)
    try:
        mods["konsol.tb_basis_model"] = _by_path("konsol.tb_basis_model", BASIS_MODEL_PY)
        sys.modules["konsol.tb_basis_model"] = mods["konsol.tb_basis_model"]
        mods["konsol.tb_dimension_model"] = _by_path(
            "konsol.tb_dimension_model", os.path.join(APP_DIR, "tb_dimension_model.py"))
        sys.modules["konsol.tb_dimension_model"] = mods["konsol.tb_dimension_model"]
        # konsol#252: the controller imports the pure currency rule too.
        mods["konsol.tb_currency_model"] = _by_path(
            "konsol.tb_currency_model", os.path.join(APP_DIR, "tb_currency_model.py"))
        sys.modules["konsol.tb_currency_model"] = mods["konsol.tb_currency_model"]
        # konsol#180: and the balance rule, which imports the currency rule.
        mods["konsol.tb_balance_model"] = _by_path(
            "konsol.tb_balance_model", os.path.join(APP_DIR, "tb_balance_model.py"))
        sys.modules["konsol.tb_balance_model"] = mods["konsol.tb_balance_model"]
        mods[CONTROLLER] = _by_path(CONTROLLER, CONTROLLER_PY)
        sys.modules[CONTROLLER] = mods[CONTROLLER]
        # D56: the payload form is the REAL deadlines.as_payload.
        deadlines.as_payload = _by_path("test_close_tb_read_api_deadlines",
                                        DEADLINES_PY).as_payload
        close.tb_view_model = _by_path("konsol.close.tb_view_model", VIEW_MODEL_PY)
        mods["konsol.close.tb_view_model"] = close.tb_view_model
        sys.modules["konsol.close.tb_view_model"] = close.tb_view_model
        spec = importlib.util.spec_from_file_location("close_tb_read_api_under_test", API_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return module, mods


def _my_tbs(site, fy=2025, fp=9):
    """Call my_tbs with the stubs installed; the result must be JSON-safe."""
    site.asked = (fy, fp)
    module, mods = _load(site)
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        result = module.my_tbs(fy, fp)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    json.dumps(result)  # dates must already be ISO strings
    return result


def _by_entity(result):
    return {e["entity"]: e for e in result["entities"]}


def _real_signoff_model():
    """The real signoff_model (A10), loaded by path: for building a realistic
    #289 gap dict, the same shape signoff_gate.sign_off_problems returns."""
    spec = importlib.util.spec_from_file_location(
        "test_close_tb_read_api_signoff_model", SIGNOFF_MODEL_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_SIGNOFF_MODEL = _real_signoff_model()


def _unowned_gap(entities, fy=2025, fp=9):
    return _SIGNOFF_MODEL.unowned_tb_gap(entities, (fy, fp))


# --- the gate and the method ---------------------------------------------------

def test_is_a_get_endpoint_gated_on_every_close_role():
    site = _Site()
    _my_tbs(site)
    assert site.whitelisted["my_tbs"] == ["GET"]
    assert site.only_for == [ALL_CLOSE_ROLES]


def test_stub_close_carries_reminders_and_deadlines():
    """C-X1 guard (T51t): the stub konsol.close resolves close_event.reminders
    and deadlines.period_deadlines, both as attributes and as imports."""
    site = _Site()
    site.reminders = [{"topic": "tb"}]
    site.deadlines = {(2025, 9): "2025-10-10"}
    _module, mods = _load(site)
    close = mods["konsol.close"]
    assert close.close_event.reminders([(2025, 9)]) == site.reminders
    assert close.close_event.reminders([(2025, 9)], topic="tb") == site.reminders
    assert close.deadlines.period_deadlines([(2025, 9)], "2025-10-01") == site.deadlines
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        from konsol.close import close_event, deadlines
        from konsol.close.close_event import reminders
        from konsol.close.deadlines import period_deadlines
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    assert close_event is close.close_event and deadlines is close.deadlines
    assert reminders([(2025, 9)]) == site.reminders
    assert period_deadlines([(2025, 9)], "2025-10-01") == site.deadlines
    assert _Site().reminders == [] and _Site().deadlines is None


# --- who sees what ---------------------------------------------------------------

def test_an_admin_sees_every_in_scope_entity_and_nothing_else():
    site = _Site()
    site.records["Trial Balance Submission"].append(_tb("TB-OUT", "ZZOUT"))
    result = _my_tbs(site)
    assert sorted(_by_entity(result)) == ["ZZA", "ZZB", "ZZC"]
    assert "ZZOUT" not in json.dumps(result)


def test_an_entity_accountant_sees_only_their_entities():
    # ZZB is quarterly and FY2025's quarters are undeclared, so a quarter gap
    # exists for ZZB. Neither it nor ZZB's TB nor ZZC may appear anywhere.
    site = _Site()
    site.rows = _year(2025, quarters={})
    site.allowed = {"ZZA", "ZZNOTINSCOPE"}
    site.records["Trial Balance Submission"].append(_tb("TB-B", "ZZB"))
    site.records["TB Exception"].append(_exc("EXC-C", "ZZC"))
    result = _my_tbs(site)
    assert [e["entity"] for e in result["entities"]] == ["ZZA"]
    text = json.dumps(result)
    for other in ("ZZB", "ZZC", "TB-B", "EXC-C", "ZZNOTINSCOPE"):
        assert other not in text, (other, text)
    # and no other entity's records are even read
    reads = [f for d, f in site.get_all_calls
             if d in ("Trial Balance Submission", "TB Exception")]
    assert len(reads) == 2, site.get_all_calls
    for filters in reads:
        assert filters["data_area_id"] == ["in", ["ZZA"]], filters


def test_an_empty_allowed_set_means_no_entities_not_all():
    site = _Site()
    site.allowed = set()
    result = _my_tbs(site)
    assert result["entities"] == []
    assert "ZZ" not in json.dumps(result)


# --- statuses -----------------------------------------------------------------------

def test_statuses_and_missing_entities_sort_first():
    site = _Site()
    site.in_scope = ["ZZA", "ZZB", "ZZC", "ZZD", "ZZE"]
    site.records["Entity"] += [_entity("ZZD"), _entity("ZZE")]
    site.records["TB Exception"].append(_exc("EXC-D", "ZZD"))
    result = _my_tbs(site)
    got = [(e["entity"], e["status"]) for e in result["entities"]]
    assert got == [("ZZB", "Missing"), ("ZZC", "Missing"), ("ZZE", "Missing"),
                   ("ZZA", "Received"), ("ZZD", "Exception declared")], got
    by = _by_entity(result)
    assert by["ZZA"]["name"] == "Entity ZZA"
    assert by["ZZD"]["exception"]["name"] == "EXC-D"
    assert by["ZZD"]["tb"] is None
    assert by["ZZC"]["tb"] is None and by["ZZC"]["exception"] is None


def test_a_quarterly_entity_is_not_expected_before_quarter_end():
    site = _Site()
    by = _by_entity(_my_tbs(site, 2025, 8))
    assert by["ZZB"]["status"] == "Not expected this period"
    # and at the quarter-end it is missing
    assert _by_entity(_my_tbs(site, 2025, 9))["ZZB"]["status"] == "Missing"


def test_a_blank_frequency_is_not_assumed_monthly():
    site = _Site()
    site.records["Entity"] = [_entity("ZZA"), _entity("ZZB", "Quarterly"), _entity("ZZC", "")]
    by = _by_entity(_my_tbs(site))
    assert by["ZZC"]["status"] == "Frequency not declared"


def test_an_undeclared_quarter_is_named_not_guessed():
    site = _Site()
    site.rows = _year(2025, quarters={})
    by = _by_entity(_my_tbs(site))
    assert by["ZZB"]["status"] == "Quarter not declared"
    assert by["ZZC"]["status"] == "Missing"


def test_a_draft_or_other_period_tb_is_not_received():
    site = _Site()
    site.records["Trial Balance Submission"] = [
        _tb("TB-A0", "ZZA", docstatus=0), _tb("TB-A2", "ZZA", docstatus=2),
        _tb("TB-A8", "ZZA", fp=8), _tb("TB-A24", "ZZA", fy=2024)]
    site.records["TB Exception"] = [_exc("EXC-A", "ZZA", docstatus=0)]
    by = _by_entity(_my_tbs(site))
    assert by["ZZA"]["status"] == "Missing"
    assert by["ZZA"]["tb"] is None and by["ZZA"]["exception"] is None


# --- #289: a TB with no covering ownership (konsol#305 E209a) -----------------------

NOT_CONSOLIDATED = "Not consolidated: no ownership for this period"


def test_an_unowned_tb_shows_not_consolidated_and_sorts_with_missing():
    site = _Site()
    site.in_scope = []
    site.records["Entity"] = [_entity("ZZX")]
    site.records["Trial Balance Submission"] = [_tb("TB-X", "ZZX")]
    site.gaps = [_unowned_gap(["ZZX"])]
    result = _my_tbs(site)
    got = [(e["entity"], e["status"]) for e in result["entities"]]
    assert got == [("ZZX", NOT_CONSOLIDATED)], got
    by = _by_entity(result)
    assert by["ZZX"]["tb"]["name"] == "TB-X"
    assert by["ZZX"]["exception"] is None
    assert by["ZZX"]["name"] == "Entity ZZX"


def test_an_unowned_entity_outside_allowed_leaks_nowhere():
    site = _Site()
    site.in_scope = []
    site.allowed = {"ZZA"}
    site.gaps = [_unowned_gap(["ZZA", "ZZX"])]
    result = _my_tbs(site)
    assert [e["entity"] for e in result["entities"]] == ["ZZA"]
    assert _by_entity(result)["ZZA"]["status"] == NOT_CONSOLIDATED
    text = json.dumps(result)
    assert "ZZX" not in text, text


def test_an_empty_allowed_set_never_calls_sign_off_problems():
    site = _Site()
    site.allowed = set()
    site.gaps = [_unowned_gap(["ZZX"])]
    result = _my_tbs(site)
    assert result["entities"] == []
    assert site.sign_off_problems_calls == []


def test_sign_off_problems_is_called_once_and_the_tb_query_stays_one():
    site = _Site()
    result = _my_tbs(site)
    assert site.sign_off_problems_calls == [(2025, 9)]
    tb_reads = [f for d, f in site.get_all_calls if d == "Trial Balance Submission"]
    assert len(tb_reads) == 1, site.get_all_calls
    assert result["entities"]  # the ordinary in-scope path still works


def test_tb_statuses_holds_every_status_my_tbs_can_emit():
    module, _mods = _load(_Site())
    assert module.TB_STATUSES == (
        "Received", "Exception declared", "Not expected this period", "Missing",
        "Frequency not declared", "Quarter not declared", NOT_CONSOLIDATED), module.TB_STATUSES
    assert len(set(module.TB_STATUSES)) == 7


# --- the TB and the on-behalf label (R4) --------------------------------------------

def test_the_received_tb_is_json_safe():
    tb = _by_entity(_my_tbs(_Site()))["ZZA"]["tb"]
    assert tb == {"name": "TB-A", "owner": "zz-lead@example.com", "on_behalf": False,
                  "on_behalf_label": "by zz-lead@example.com",
                  "creation": "2025-10-03T09:30:00+01:00"}, tb


# --- A55: every datetime carries the site's time zone ------------------------------

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


def _assert_every_datetime_zoned(payload):
    stamps = [s for s in _strings(payload) if _DATETIME.match(s)]
    naive = [s for s in stamps if not _OFFSET.search(s)]
    assert not naive, "datetimes sent without a time zone: %r" % naive
    return stamps


def test_every_datetime_in_my_tbs_carries_the_site_offset():
    # Measured in C1 (25 Sep): my_tbs sent "2026-09-25T20:22:31.605721" (no zone).
    site = _Site()
    site.in_scope = ["ZZA", "ZZB", "ZZC"]
    site.records["TB Exception"] = [_exc("EXC-C", "ZZC")]
    result = _my_tbs(site)
    stamps = _assert_every_datetime_zoned(result)
    assert sorted(stamps) == ["2025-10-03T09:30:00+01:00", "2025-10-04T11:15:00+01:00"], stamps


def test_the_exception_carries_when_it_was_declared_with_its_zone():
    site = _Site()
    site.records["TB Exception"] = [_exc("EXC-C", "ZZC")]
    exc = _by_entity(_my_tbs(site))["ZZC"]["exception"]
    assert exc == {"name": "EXC-C", "reason": "Dormant", "declared_by": "zz-lead@example.com",
                   "declared_on": "2025-10-04T11:15:00+01:00"}, exc


def test_the_offset_is_the_site_zone_on_that_date_not_a_fixed_one():
    # GMT in January: +00:00, not the +01:00 of the summer.
    site = _Site()
    site.records["Trial Balance Submission"][0]["creation"] = datetime(2025, 1, 10, 8, 0)
    tb = _by_entity(_my_tbs(site))["ZZA"]["tb"]
    assert tb["creation"] == "2025-01-10T08:00:00+00:00", tb


def test_a_plain_date_stays_a_date_and_blank_stays_none():
    module, _mods = _load(_Site())
    assert module._iso(date(2025, 10, 3)) == "2025-10-03"
    assert module._iso(None) is None and module._iso("") is None


def test_an_on_behalf_tb_is_labelled():
    site = _Site()
    site.records["Trial Balance Submission"] = [_tb("TB-A", "ZZA", on_behalf="Yes")]
    tb = _by_entity(_my_tbs(site))["ZZA"]["tb"]
    assert tb["on_behalf"] is True
    assert tb["on_behalf_label"] == "by zz-lead@example.com for ZZA"


def test_a_blank_on_behalf_is_unknown_not_no():
    for blank in ("", None):
        site = _Site()
        site.records["Trial Balance Submission"] = [_tb("TB-A", "ZZA", on_behalf=blank)]
        tb = _by_entity(_my_tbs(site))["ZZA"]["tb"]
        assert tb["on_behalf"] is None, blank
        assert tb["on_behalf_label"] == "by zz-lead@example.com (on behalf: not recorded)", tb


# --- the period and can_upload ------------------------------------------------------

def test_can_upload_needs_create_permission_and_an_open_period():
    site = _Site()
    result = _my_tbs(site)
    assert (result["period_open"], result["can_upload"]) == (True, True)
    site.can_create = False
    result = _my_tbs(site)
    assert (result["period_open"], result["can_upload"]) == (True, False)
    site.can_create = True
    site.rows = _year(2025, status="Closed")
    result = _my_tbs(site)
    assert (result["period_open"], result["can_upload"]) == (False, False)


def test_an_undeclared_period_is_refused():
    site = _Site()
    with pytest.raises(Exception) as info:
        _my_tbs(site, 2031, 1)
    assert type(info.value).__name__ == "PeriodNotDeclared"
    assert "EPM Fiscal Year" in str(info.value)


def test_a_non_regular_period_is_refused():
    site = _Site()
    site.rows = _year(2025, closing="Open")
    with pytest.raises(Exception) as info:
        _my_tbs(site, 2025, 13)
    assert "Regular" in str(info.value), str(info.value)


# === tb_compare (konsol#305 A28; story 3.4) ===========================================
#
# GET tb_compare(entity, fiscal_year, fiscal_period): the A12 comparison of the
# entity's submitted TB against the previous declared Regular period's, plus the
# basis and the TB name of both periods. The TB files go through the REAL
# parse_tb_csv and the REAL tb_view_model.compare.

BOM = "﻿"
CUR_CSV = (BOM + "main_account,debit,credit,partner_data_area_id\n"
           "1010,100,0,\n2010,0,130,\n4010,30,0,ZZB\n").encode("utf-8")
PREV_CSV = "main_account,debit,credit\n1010,60,0\n3000,0,60\n"


def _compare_site():
    site = _Site()
    site.records["Trial Balance Submission"] = [
        _tb("TB-A9", "ZZA", fp=9), _tb("TB-A8", "ZZA", fp=8),
        _tb("TB-A9-DRAFT", "ZZA", fp=9, docstatus=0), _tb("TB-B8", "ZZB", fp=8)]
    site.files = {"/private/files/TB-A9.csv": CUR_CSV, "/private/files/TB-A8.csv": PREV_CSV,
                  "/private/files/TB-A9-DRAFT.csv": "main_account,debit,credit\n9999,1,1\n",
                  "/private/files/TB-B8.csv": "main_account,debit,credit\n8888,5,5\n"}
    return site


def _tb_compare(site, entity="ZZA", fy=2025, fp=9):
    """Call tb_compare with the stubs installed; the result must be JSON-safe."""
    module, mods = _load(site)
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        result = module.tb_compare(entity, fy, fp)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    json.dumps(result)
    return result


def _raises_compare(site, **kwargs):
    try:
        _tb_compare(site, **kwargs)
    except Exception as e:  # noqa: BLE001 - the type is asserted by the caller
        return e
    raise AssertionError("tb_compare did not raise")


def _row_map(result):
    return {(r["account"], r["partner"]): r for r in result["rows"]}


def test_tb_compare_is_a_get_endpoint_gated_on_every_close_role():
    site = _compare_site()
    _tb_compare(site)
    assert site.whitelisted["tb_compare"] == ["GET"]
    assert site.only_for == [ALL_CLOSE_ROLES]


def test_the_comparison_is_joined_by_account_and_partner():
    site = _compare_site()
    result = _tb_compare(site)
    rows = _row_map(result)
    assert sorted(rows) == [("1010", ""), ("2010", ""), ("3000", ""), ("4010", "ZZB")], rows
    assert rows[("1010", "")]["current"] == 100 and rows[("1010", "")]["previous"] == 60
    assert rows[("1010", "")]["change"] == 40
    assert rows[("2010", "")]["previous"] is None and rows[("2010", "")]["change"] is None
    assert rows[("3000", "")]["current"] is None and rows[("3000", "")]["previous"] == -60
    assert rows[("4010", "ZZB")]["is_ic"] is True
    assert result["basis_note"] is None and result["previous_note"] is None
    # the basis and the TB names of both periods
    assert result["entity"] == "ZZA"
    assert result["current"] == {"fiscal_year": 2025, "fiscal_period": 9, "code": "FY2025 P09",
                                 "tb": "TB-A9", "basis": "Period movement"}, result["current"]
    assert result["previous"] == {"fiscal_year": 2025, "fiscal_period": 8, "code": "FY2025 P08",
                                  "tb": "TB-A8", "basis": "Period movement"}, result["previous"]
    assert result["previous_code"] == "FY2025 P08"
    # only the entity's submitted TBs were read; a draft and another entity's TB were not
    assert sorted(site.files_read) == ["/private/files/TB-A8.csv", "/private/files/TB-A9.csv"]
    assert site.access_checked == ["ZZA"]


def test_the_previous_period_crosses_the_year_boundary():
    site = _compare_site()
    # FY2024 has a Closing period (P13) after P12: it is never the "previous".
    site.rows = _year(2024, closing="Open") + _year(2025)
    site.records["Trial Balance Submission"] = [
        _tb("TB-A1", "ZZA", fy=2025, fp=1), _tb("TB-A24-12", "ZZA", fy=2024, fp=12),
        _tb("TB-A24-13", "ZZA", fy=2024, fp=13)]
    site.files = {"/private/files/TB-A1.csv": CUR_CSV,
                  "/private/files/TB-A24-12.csv": PREV_CSV,
                  "/private/files/TB-A24-13.csv": "main_account,debit,credit\n9999,1,1\n"}
    result = _tb_compare(site, fy=2025, fp=1)
    assert result["previous"]["fiscal_year"] == 2024 and result["previous"]["fiscal_period"] == 12
    assert result["previous"]["tb"] == "TB-A24-12"
    assert result["previous_code"] == "FY2024 P12", result["previous_code"]
    assert _row_map(result)[("1010", "")]["change"] == 40
    assert "/private/files/TB-A24-13.csv" not in site.files_read


def test_a_stored_tb_with_an_extra_column_is_still_compared():
    """A TB already landed is read back, not re-judged (konsol#255). Intake now
    refuses a column outside the contract, but a file accepted before that
    rule, when an extra column was silently ignored, is still the previous
    period's TB and must stay comparable."""
    site = _compare_site()
    site.files["/private/files/TB-A8.csv"] = (
        "main_account,debit,credit,account_name\n1010,60,0,Cash\n3000,0,60,Equity\n")
    rows = _row_map(_tb_compare(site))
    assert rows[("1010", "")]["previous"] == 60 and rows[("1010", "")]["change"] == 40, rows


def test_a_stored_tb_whose_dimension_is_no_longer_declared_is_still_compared():
    """A dimension a site later un-declares (unticked, Draft, deleted) leaves
    its column in files that landed while it was declared. Those files are
    history: reading them back must not depend on today's declarations."""
    site = _compare_site()
    site.files["/private/files/TB-A8.csv"] = (
        "main_account,debit,credit,dim_zzseg\n1010,40,0,ZZA\n1010,20,0,ZZB\n3000,0,60,\n")
    rows = _row_map(_tb_compare(site))
    assert rows[("1010", "")]["previous"] == 60, rows


def test_a_stored_tb_with_a_repeated_header_is_still_compared():
    """Stored files are read back without any header refusal, repeated
    columns included: an older intake that accepted the file decided what
    landed, and the close screen only shows it."""
    site = _compare_site()
    site.files["/private/files/TB-A8.csv"] = (
        "main_account,debit,credit,amount_basis,amount_basis\n"
        "1010,60,0,,\n3000,0,60,,\n")
    rows = _row_map(_tb_compare(site))
    assert rows[("1010", "")]["previous"] == 60, rows


# --- failure paths ---------------------------------------------------------------------

def test_no_previous_tb_is_a_note_never_a_comparison_against_zero():
    site = _compare_site()
    site.records["Trial Balance Submission"] = [
        _tb("TB-A9", "ZZA", fp=9), _tb("TB-A8-DRAFT", "ZZA", fp=8, docstatus=0),
        _tb("TB-A8-CANCELLED", "ZZA", fp=8, docstatus=2)]
    result = _tb_compare(site)
    assert result["previous_note"] == "No trial balance for FY2025 P08", result
    assert result["previous"] == {"fiscal_year": 2025, "fiscal_period": 8, "code": "FY2025 P08",
                                  "tb": None, "basis": None}, result["previous"]
    assert result["rows"], result
    for row in result["rows"]:
        assert row["previous"] is None and row["change"] is None, row
    assert site.files_read == ["/private/files/TB-A9.csv"]


def test_the_first_declared_period_has_no_previous_period():
    site = _compare_site()
    site.records["Trial Balance Submission"] = [_tb("TB-A1", "ZZA", fp=1)]
    site.files = {"/private/files/TB-A1.csv": CUR_CSV}
    result = _tb_compare(site, fp=1)
    assert result["previous"] is None and result["previous_code"] is None
    assert result["previous_note"] == "No previous period is declared in the fiscal calendar"
    for row in result["rows"]:
        assert row["change"] is None, row


def test_different_bases_are_shown_but_not_compared():
    site = _compare_site()
    site.records["Trial Balance Submission"] = [
        _tb("TB-A9", "ZZA", fp=9, basis="Period-end balance"),
        _tb("TB-A8", "ZZA", fp=8, basis="Period movement")]
    result = _tb_compare(site)
    assert result["basis_note"] == (
        "This period is Period-end balance and FY2025 P08 is Period movement: "
        "the change is not comparable."), result["basis_note"]
    assert result["current"]["basis"] == "Period-end balance"
    assert result["previous"]["basis"] == "Period movement"
    for row in result["rows"]:
        assert row["change"] is None, row


def test_no_current_tb_is_refused_by_name():
    site = _compare_site()
    site.records["Trial Balance Submission"] = [
        _tb("TB-A9-DRAFT", "ZZA", fp=9, docstatus=0), _tb("TB-A8", "ZZA", fp=8)]
    err = _raises_compare(site)
    assert "No submitted trial balance for ZZA FY2025 P09" in str(err), str(err)
    assert site.files_read == []


def test_an_entity_the_user_cannot_access_is_refused_before_anything_is_read():
    site = _compare_site()
    site.allowed = {"ZZC"}
    err = _raises_compare(site, entity="ZZA")
    assert type(err).__name__ == "PermissionError", type(err)
    assert site.files_read == []
    assert not [c for c in site.get_all_calls if c[0] == "Trial Balance Submission"]
    # and an empty allowed set is not "all"
    site = _compare_site()
    site.allowed = set()
    assert type(_raises_compare(site)).__name__ == "PermissionError"


def test_an_undeclared_basis_is_refused_not_guessed():
    site = _compare_site()
    site.records["Trial Balance Submission"] = [
        _tb("TB-A9", "ZZA", fp=9), _tb("TB-A8", "ZZA", fp=8, basis="")]
    err = _raises_compare(site)
    assert "TB-A8" in str(err) and "amount basis" in str(err), str(err)


def test_tb_compare_refuses_undeclared_and_non_regular_periods():
    site = _compare_site()
    err = _raises_compare(site, fy=2031, fp=1)
    assert type(err).__name__ == "PeriodNotDeclared", type(err)
    site = _compare_site()
    site.rows = _year(2025, closing="Open")
    err = _raises_compare(site, fp=13)
    assert "Regular" in str(err), str(err)


# --- Y56: each row carries its reminders; can_remind (story 1.5, C-R6) -------------

REMIND_MODEL_PY = os.path.join(APP_DIR, "close", "remind_model.py")
_RM_SPEC = importlib.util.spec_from_file_location("test_close_tb_read_api_remind_model",
                                                  REMIND_MODEL_PY)
REMIND_MODEL = importlib.util.module_from_spec(_RM_SPEC)
_RM_SPEC.loader.exec_module(REMIND_MODEL)

#: The golden payload close-ui's TB list tests load (W4-E19): exactly what the
#: real ``my_tbs`` returns for ``_golden_site()``, never a hand-built dict.
MY_TBS_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "fixtures", "close_my_tbs_payload.json")


def _reminder(name, entity, at, actor, topic="tb", fy=2025, fp=9):
    """One event as ``close_event.reminders`` returns it (Y53): the input
    ``remind_model.summary`` reads."""
    return {"name": name, "fiscal_year": fy, "fiscal_period": fp, "entity": entity,
            "actor": actor, "at": at,
            "detail": {"topic": topic, "recipients": ["zz-ea@example.com"],
                       "subject": "Reminder"}}


def _user(name, full_name):
    return {"name": name, "full_name": full_name}


def _reminded_site():
    """ZZC (Missing) reminded twice, an hour apart, by two senders; nothing else."""
    site = _Site()
    site.roles = ["EPM Analyst"]
    site.reminders = [
        _reminder("CE-0002", "ZZC", datetime(2025, 10, 6, 10, 0), "zz-lead@example.com"),
        _reminder("CE-0001", "ZZC", datetime(2025, 10, 6, 9, 0), "zz-ga@example.com"),
    ]
    site.records["User"] = [_user("zz-lead@example.com", "Zed Lead"),
                            _user("zz-ga@example.com", "Gee Accountant")]
    return site


def _golden_site():
    """D56: plus a declared rule whose TB due date (2025-10-07) is past on
    2025-10-08, so the SPA sees overdue Missing rows."""
    site = _reminded_site()
    site.deadline_rules = [_rule(date(2025, 1, 1), tb=5)]
    site.today = date(2025, 10, 8)
    return site


def test_the_events_are_the_real_summary_input():
    """The stub events are the shape remind_model.summary reads: a count of 2."""
    got = REMIND_MODEL.summary(_reminded_site().reminders)
    assert got[(2025, 9, "ZZC", "tb")]["count"] == 2


def test_a_reminded_row_carries_count_last_at_and_the_latest_sender():
    site = _reminded_site()
    by = _by_entity(_my_tbs(site))
    assert by["ZZC"]["reminders"] == {
        "count": 2, "last_at": "2025-10-06T10:00:00+01:00",
        "last_by": "zz-lead@example.com", "last_by_name": "Zed Lead"}, by["ZZC"]
    assert by["ZZA"]["reminders"] is None and by["ZZB"]["reminders"] is None


def test_the_latest_is_by_time_not_by_list_order():
    site = _reminded_site()
    site.reminders = list(reversed(site.reminders))
    assert _by_entity(_my_tbs(site))["ZZC"]["reminders"]["last_by_name"] == "Zed Lead"


def test_reminders_are_read_once_for_the_period_and_topic_tb():
    site = _reminded_site()
    _my_tbs(site)
    assert site.reminder_calls == [([(2025, 9)], "tb")]
    user_reads = [f for d, f in site.get_all_calls if d == "User"]
    assert user_reads == [{"name": ["in", ["zz-lead@example.com"]]}], user_reads


def test_no_reminders_reads_no_user():
    site = _Site()
    result = _my_tbs(site)
    assert all(e["reminders"] is None for e in result["entities"])
    assert [d for d, _f in site.get_all_calls if d == "User"] == []


def test_an_unowned_row_carries_reminders_too():
    site = _reminded_site()
    site.records["Entity"].append(_entity("ZZU"))
    site.records["Trial Balance Submission"].append(_tb("TB-U", "ZZU"))
    site.gaps = [_unowned_gap(["ZZU"])]
    site.reminders.append(
        _reminder("CE-0003", "ZZU", datetime(2025, 10, 6, 8, 0), "zz-ga@example.com"))
    by = _by_entity(_my_tbs(site))
    assert by["ZZU"]["status"] == NOT_CONSOLIDATED
    assert by["ZZU"]["reminders"]["count"] == 1
    assert by["ZZU"]["reminders"]["last_by_name"] == "Gee Accountant"


def test_a_hidden_entitys_reminders_never_leave_the_server():
    """The recipient (an Entity Accountant on ZZA) sees their own row's
    reminders, and nothing about ZZC's: not its count, not its sender."""
    site = _reminded_site()
    site.roles = ["Entity Accountant"]
    site.allowed = {"ZZA"}
    site.reminders.append(
        _reminder("CE-0003", "ZZA", datetime(2025, 10, 6, 8, 0), "zz-ga@example.com"))
    result = _my_tbs(site)
    assert [e["entity"] for e in result["entities"]] == ["ZZA"]
    assert result["entities"][0]["reminders"]["count"] == 1
    text = json.dumps(result)
    # zz-lead@example.com also owns ZZA's own TB, so its id is legitimately
    # present; its full name comes only from ZZC's reminder.
    for hidden in ("ZZC", "Zed Lead", "CE-0002"):
        assert hidden not in text, (hidden, text)
    user_reads = [f for d, f in site.get_all_calls if d == "User"]
    assert user_reads == [{"name": ["in", ["zz-ga@example.com"]]}], user_reads


def test_an_unknown_topic_raises_never_a_silent_zero():
    site = _reminded_site()
    site.reminders.append(_reminder("CE-0009", "ZZA", datetime(2025, 10, 6, 8, 0),
                                    "zz-ga@example.com", topic="x"))
    with pytest.raises(ValueError) as info:
        _my_tbs(site)
    assert "CE-0009" in str(info.value)


def test_a_non_datetime_time_raises():
    site = _reminded_site()
    site.reminders[0]["at"] = "2025-10-06 10:00:00"
    with pytest.raises(ValueError) as info:
        _my_tbs(site)
    assert "CE-0002" in str(info.value)


def test_a_sender_with_no_full_name_is_refused_not_shown_as_an_id():
    for users in ([], [_user("zz-lead@example.com", ""),
                       _user("zz-ga@example.com", "Gee Accountant")]):
        site = _reminded_site()
        site.records["User"] = users
        with pytest.raises(Exception) as info:
            _my_tbs(site)
        assert "zz-lead@example.com" in str(info.value), str(info.value)
        assert "full name" in str(info.value), str(info.value)


def test_can_remind_only_for_remind_roles_in_an_open_period():
    for roles, expected in ((["EPM Admin"], True), (["EPM Analyst"], True),
                            (["System Manager"], True), (["EPM User"], False),
                            (["Entity Accountant"], False), ([], False),
                            (["Entity Accountant", "EPM Analyst"], True)):
        site = _Site()
        site.roles = roles
        assert _my_tbs(site)["can_remind"] is expected, roles
    site = _Site()
    site.rows = _year(2025, status="Closed")
    assert _my_tbs(site)["can_remind"] is False


def test_can_remind_is_present_when_the_caller_sees_no_entity():
    site = _Site()
    site.allowed = set()
    result = _my_tbs(site)
    assert result["can_remind"] is True and result["entities"] == []
    assert site.reminder_calls == []


def test_every_row_carries_the_reminders_key():
    site = _Site()
    site.in_scope = ["ZZA", "ZZB", "ZZC", "ZZD"]
    site.records["Entity"].append(_entity("ZZD"))
    site.records["TB Exception"].append(_exc("EXC-D", "ZZD"))
    for row in _my_tbs(site)["entities"]:
        assert "reminders" in row, row


def test_my_tbs_matches_the_golden_fixture():
    out = json.loads(json.dumps(_my_tbs(_golden_site())))
    with open(MY_TBS_FIXTURE) as f:
        golden = json.load(f)
    assert out == golden
    assert golden["can_remind"] is True
    assert [e["entity"] for e in golden["entities"] if e["reminders"]] == ["ZZC"]


# --- D56: the TB due date, and overdue on Missing rows ------------------------------

DEADLINE_SPEC = importlib.util.spec_from_file_location("test_close_tb_read_api_dm", DEADLINE_MODEL_PY)
DEADLINE_MODEL = importlib.util.module_from_spec(DEADLINE_SPEC)
DEADLINE_SPEC.loader.exec_module(DEADLINE_MODEL)


def _rule(valid_from, tb=0, ic=0, journals=0, signoff=0, week=("monday", "tuesday", "wednesday",
                                                              "thursday", "friday")):
    """One Close Deadline Rule row (D53 fields), as deadlines.py reads it."""
    rule = {"valid_from": valid_from, "tb_due_days": tb, "ic_due_days": ic,
            "journals_due_days": journals, "signoff_due_days": signoff}
    for wd in DEADLINE_MODEL.WEEKDAYS:
        rule[wd] = 1 if wd in week else 0
    return rule


def _deadline_site(today):
    """FY2025 P09 ends Tue 30 Sep; TB due 5 working days later = Tue 7 Oct.
    ZZA Received, ZZB and ZZC Missing; ZZD has an exception."""
    site = _Site()
    site.in_scope = ["ZZA", "ZZB", "ZZC", "ZZD"]
    site.records["Entity"].append(_entity("ZZD"))
    site.records["TB Exception"].append(_exc("EXC-D", "ZZD"))
    site.deadline_rules = [_rule(date(2025, 1, 1), tb=5, signoff=10)]
    site.today = today
    return site


def test_the_stub_deadlines_are_the_real_model_output():
    site = _deadline_site(date(2025, 10, 8))
    got = DEADLINE_MODEL.period_deadlines(site.deadline_rules, set(), date(2025, 9, 30),
                                          site.today)
    assert got["tb"] == {"due": date(2025, 10, 7), "past": True, "text": "Due 2025-10-07"}


def test_a_past_tb_due_makes_the_missing_rows_overdue_and_no_other():
    result = _my_tbs(_deadline_site(date(2025, 10, 8)))
    assert result["deadline"] == {"due": "2025-10-07", "past": True, "text": "Due 2025-10-07"}
    by = _by_entity(result)
    assert by["ZZB"]["status"] == by["ZZC"]["status"] == "Missing"
    assert by["ZZB"]["overdue"] is True and by["ZZC"]["overdue"] is True
    assert by["ZZA"]["status"] == "Received" and by["ZZA"]["overdue"] is False
    assert by["ZZD"]["status"] == "Exception declared" and by["ZZD"]["overdue"] is False


def test_on_the_due_date_nothing_is_overdue():
    result = _my_tbs(_deadline_site(date(2025, 10, 7)))
    assert result["deadline"] == {"due": "2025-10-07", "past": False, "text": "Due 2025-10-07"}
    assert all(e["overdue"] is False for e in result["entities"])


def test_an_undeclared_rule_reads_no_due_date_declared_and_nothing_is_overdue():
    """Failure path (#305-2.4-1): no rule -> the sentence, never a guessed date."""
    site = _deadline_site(date(2030, 1, 1))
    site.deadline_rules = []
    result = _my_tbs(site)
    assert result["deadline"] == {"due": None, "past": False, "text": "No due date declared"}
    assert result["entities"] and all(e["overdue"] is False for e in result["entities"])


def test_a_rule_with_no_tb_offset_is_undeclared_for_the_tb():
    site = _deadline_site(date(2030, 1, 1))
    site.deadline_rules = [_rule(date(2025, 1, 1), tb=0, signoff=10)]
    result = _my_tbs(site)
    assert result["deadline"]["text"] == "No due date declared"
    assert all(e["overdue"] is False for e in result["entities"])


def test_a_rule_valid_after_the_period_end_does_not_govern_it():
    site = _deadline_site(date(2030, 1, 1))
    site.deadline_rules = [_rule(date(2025, 10, 1), tb=5)]
    assert _my_tbs(site)["deadline"]["text"] == "No due date declared"


def test_an_unowned_row_is_never_overdue():
    site = _deadline_site(date(2025, 10, 8))
    site.records["Entity"].append(_entity("ZZU"))
    site.records["Trial Balance Submission"].append(_tb("TB-U", "ZZU"))
    site.gaps = [_unowned_gap(["ZZU"])]
    by = _by_entity(_my_tbs(site))
    assert by["ZZU"]["status"] == NOT_CONSOLIDATED and by["ZZU"]["overdue"] is False


def test_deadlines_are_read_once_for_the_period_with_today():
    site = _deadline_site(date(2025, 10, 8))
    _my_tbs(site)
    assert site.deadline_calls == [([(2025, 9)], date(2025, 10, 8))]


def test_the_deadline_is_present_when_the_caller_sees_no_entity():
    site = _deadline_site(date(2025, 10, 8))
    site.allowed = set()
    result = _my_tbs(site)
    assert result["entities"] == []
    assert result["deadline"]["text"] == "Due 2025-10-07"


def test_a_regular_period_missing_from_the_deadline_read_raises():
    """No silent fallback: the reader returning nothing for the asked Regular
    period is an error naming it, never "No due date declared"."""
    site = _deadline_site(date(2025, 10, 8))
    site.deadlines = {}
    with pytest.raises(Exception) as info:
        _my_tbs(site)
    assert "FY2025 P09" in str(info.value), str(info.value)


def test_a_bad_step_map_raises():
    site = _deadline_site(date(2025, 10, 8))
    site.deadlines = {(2025, 9): {"tb": {"due": None, "past": False, "text": "x"}}}
    with pytest.raises(Exception):
        _my_tbs(site)


def test_every_row_carries_the_overdue_key():
    for row in _my_tbs(_deadline_site(date(2025, 10, 8)))["entities"]:
        assert isinstance(row.get("overdue"), bool), row


def test_the_no_due_date_docstring_line_is_gone():
    with open(API_PY) as f:
        assert "No due date is shown" not in f.read()


def test_the_golden_fixture_carries_the_deadline_and_overdue():
    with open(MY_TBS_FIXTURE) as f:
        golden = json.load(f)
    assert golden["deadline"] == {"due": "2025-10-07", "past": True, "text": "Due 2025-10-07"}
    overdue = sorted(e["entity"] for e in golden["entities"] if e["overdue"])
    assert overdue == ["ZZB", "ZZC"], overdue
