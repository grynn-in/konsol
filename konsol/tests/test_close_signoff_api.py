"""Sign-off API: konsol/close/signoff_api.py `get_signoff` and `sign` (konsol#305 A30, A49, A32; stories 9.1, 9.2, 9.3, 9.5).

GET `get_signoff(fiscal_year, fiscal_period)` returns the A21 summary plus
`can_sign`, `can_override`, `period_status`, `closed_by` and `closed_on`.
POST `sign(fiscal_year, fiscal_period, acknowledgement, override_reason)` signs
the period's latest terminal run through `sign_off_close` (stubbed here: it
records its arguments and raises `site.sign_error` when set).
POST `close_period(fiscal_year, fiscal_period, note)` and
`reopen_period(fiscal_year, fiscal_period, reason)` (A34) go through
`period_status.set_status` (stubbed here: it records its arguments and raises
`site.status_error` when set), which calls the EPM Fiscal Year actions.

Loaded against a stub frappe (pattern: test_close_tb_read_api.py `_load`,
copied, not imported). The gates are the REAL `signoff_gate` (A17) over the
REAL `signoff_model` and `period_model`, all loaded by path, so the order and
completeness gates and the quarterly covers note are computed, not assumed.
The stub site applies the filters the modules send ("in", "<=", "is set").
`assertion_run` is stubbed over the site's runs: its `latest_close_run` returns
the same fields as the real one (no `warned`), so the count must be read.
"""
import importlib.util
import json
import os
import re
import sys
import types
from datetime import date, datetime

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")
API_PY = os.path.join(CLOSE_DIR, "signoff_api.py")
# P05: signoff_gate.py now imports close_policy_model; loaded for real below
# alongside signoff_model/period_model/timefmt (real, by path).

def _real_reject_roles():
    """assertion_run.REJECT_ROLES, read from its source (S12): the stub
    module carries the real definition, not a copy."""
    import ast
    path = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run", "assertion_run.py")
    with open(path) as fh:
        tree = ast.parse(fh.read())
    (value,) = [node.value for node in tree.body if isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "REJECT_ROLES" for t in node.targets)]
    return ast.literal_eval(value)


ALL_CLOSE_ROLES = ("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager")
QUARTERS = {1: "Q1", 2: "Q1", 3: "Q1", 4: "Q2", 5: "Q2", 6: "Q2",
            7: "Q3", 8: "Q3", 9: "Q3", 10: "Q4", 11: "Q4", 12: "Q4"}
LEAD = "zz-lead@example.com"
CLOSED_ON = datetime(2025, 9, 5, 17, 30)


class _D(dict):
    """frappe._dict: item and attribute access."""

    def __getattr__(self, name):
        return self.get(name)


def _month_end(fy, fp):
    nxt = date(fy + (fp == 12), fp % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def _period(fy, fp, status):
    return {"fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
            "period_label": "P%02d" % fp, "period_type": "Regular",
            "start_date": date(fy, fp, 1), "end_date": _month_end(fy, fp),
            "quarter": QUARTERS[fp], "status": status}


def _run(name, fp, status="Green", signoff="Signed Off", warned=0, completed=None):
    return {"name": name, "fiscal_year": 2025, "fiscal_period": fp, "status": status,
            "signoff_status": signoff, "failed": 0, "errored": 0, "warned": warned,
            "completed_at": completed or datetime(2025, fp + 1 if fp < 12 else 12, 2, 9, 0),
            "creation": completed or datetime(2025, fp + 1 if fp < 12 else 12, 2, 8, 0)}


def _tb(entity, fp=9, on_behalf="No", owner=LEAD, docstatus=1):
    return {"name": "TB-%s-%d" % (entity, fp), "data_area_id": entity, "fiscal_year": 2025,
            "fiscal_period": fp, "docstatus": docstatus, "owner": owner,
            "uploaded_on_behalf": on_behalf}


def _exc(entity, fp=9, reason="Dormant", docstatus=1):
    return {"name": "EXC-%s-%d" % (entity, fp), "data_area_id": entity, "fiscal_year": 2025,
            "fiscal_period": fp, "docstatus": docstatus, "reason": reason, "declared_by": LEAD,
            "creation": datetime(2025, fp + 1, 3, 10, 0)}


def _entity(name, frequency="Monthly"):
    return {"name": name, "is_group": 0, "status": "Active", "reporting_frequency": frequency}


def _ownership(entity):
    return {"data_area_id": entity, "docstatus": 1, "effective_date": date(2020, 1, 1),
            "end_date": None}


class _Site:
    """FY2025, first close P01. P01-P08 Closed and signed; P09 Open, target.

    In scope (all with a submitted ownership period):
    - ZZA Monthly: P09 TB uploaded on behalf ("Yes") by the lead;
    - ZZB Monthly: P09 TB, "No";
    - ZZC Monthly: P09 TB, blank on-behalf (uploaded before A18: unknown);
    - ZZD Monthly: P08 exception, P09 TB -> "ZZD: covers P08-P09";
    - ZZE Monthly: P09 exception "Dormant";
    - ZZQ Quarterly: P09 TB (Q3 = P07-P09) -> "ZZQ: quarterly - covers P07-P09".
    P09's latest terminal run is Amber with 3 warnings, 2 names listed.
    """

    def __init__(self, roles=("EPM Admin",)):
        self.roles = list(roles)
        self.allowed = None
        self.can_write = True
        rows = [_period(2025, fp, "Closed") for fp in range(1, 9)]
        rows += [_period(2025, fp, "Open") for fp in range(9, 13)]
        self.rows = rows
        self.first_close = (2025, 1)
        #: P05 (#305-D2-3, #305-D2-9): both declared, so signoff_gate adds no
        #: policy gap by default; a test sets ``site.policies`` to probe a gap.
        self.policies = ("Blocked", 50)
        #: W4-1 (N44t): the declared statement accounts (CTA, current-year
        #: result), so signoff_gate's N45 read finds them. Until N45 nothing
        #: reads them.
        self.statement_accounts = ("3300", "3100")
        #: W5-2 (8.4): Close Settings (amount, percent, combine) — an amount
        #: declared, so signoff_gate adds no commentary-threshold gap by
        #: default; a test clears it to probe the gap.
        self.commentary_threshold = (5000, 0, "")
        self.closed = {(2025, fp): (LEAD, CLOSED_ON) for fp in range(1, 9)}
        #: A63: the period rows' data-change fields, by (year, period).
        self.data_changed = {}
        runs = [_run("RUN-%02d" % fp, fp) for fp in range(1, 9)]
        runs.append(_run("RUN-09", 9, status="Amber", signoff="Not Signed Off", warned=3))
        entities = ["ZZA", "ZZB", "ZZC", "ZZD", "ZZE"]
        self.records = {
            "Entity": [_entity(e) for e in entities] + [_entity("ZZQ", "Quarterly")],
            "Ownership Period": [_ownership(e) for e in entities + ["ZZQ"]],
            "Assertion Run": runs,
            "Trial Balance Submission": [
                _tb("ZZA", on_behalf="Yes"), _tb("ZZB", on_behalf="No"),
                _tb("ZZC", on_behalf=""), _tb("ZZD"), _tb("ZZQ"),
                _tb("ZZA", fp=8), _tb("ZZB", fp=8), _tb("ZZC", fp=8), _tb("ZZE", fp=8),
            ],
            "TB Exception": [_exc("ZZE"), _exc("ZZD", fp=8, reason="Merged into P09")],
            "Main Account": [
                {"name": "3300", "is_group": 0, "status": "Published",
                 "statement_section": "Balance Sheet", "account_name": "AOCI — CTA",
                 "parent_account": "3"},
                {"name": "3100", "is_group": 0, "status": "Published",
                 "statement_section": "Balance Sheet", "account_name": "Retained earnings",
                 "parent_account": "3"},
                #: M45t: two Published headings, so M46's import and reads
                #: resolve (konsol.close.commentary_model is loaded below).
                {"name": "4", "is_group": 1, "status": "Published",
                 "statement_section": "Profit and Loss", "account_name": "NET SALES",
                 "lft": 10},
                {"name": "7", "is_group": 1, "status": "Published",
                 "statement_section": "Profit and Loss", "account_name": "OPERATING EXPENSES",
                 "lft": 20},
            ],
            #: M45t: so M46's import and reads resolve.
            "Statement Commentary": [],
            #: M46: the root group ic_api's filter finds (data_area_id not
            #: set), so missing_commentary has a group to report against.
            "Consolidation Group": [{"consolidation_group": "ZZGRP", "data_area_id": None}],
        }
        self.warned_names = {"RUN-09": ["assert_a", "assert_b"]}
        self.only_for = []
        self.whitelisted = {}
        self.writes = []
        self.signed = []
        self.sign_error = None
        #: #305-W5-1 (story 9.4): the stubbed assertion_run.reject_signoff
        #: records its arguments and raises ``reject_error`` when set.
        self.rejected = []
        self.reject_error = None
        self.status_calls = []
        self.status_error = None
        self.admin_checks = 0
        #: C09t: the stubbed `konsol.close.ic_api.signoff_summary`/`tolerance_gap`
        #: read these. `ic_calls` records every `signoff_summary` call (C10, C21).
        self.ic_summary = {"state": "checked", "message": None,
                           "counts": {"pairs": 0, "matched": 0, "within_tolerance": 0,
                                      "fx_difference": 0, "over_tolerance": 0, "unmatched": 0},
                           "sent_back_open": 0}
        self.ic_tolerance_gap = None
        #: #305 5.4: the stubbed `konsol.close.ic_balance_api.rule_gaps` returns this list.
        self.ic_rule_gaps = []
        self.ic_calls = []
        #: W5-2 (8.4): the stubbed `konsol.close.statement_api.signoff_commentary`
        #: answer (default: checked, nothing required — the real
        #: commentary_model.requirement over a declared threshold and no
        #: group) and every call to it.
        self.commentary_line = _commentary_none()
        self.commentary_calls = []


def _close_model(name):
    spec = importlib.util.spec_from_file_location(
        name + "_for_signoff_api_test", os.path.join(CLOSE_DIR, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _commentary_none():
    return _close_model("commentary_model").requirement(
        _close_model("close_policy_model").commentary_threshold(5000, 0, ""), [])


#: The real statement_api.signoff_commentary output, committed by
#: test_close_statement_api.py (golden; 3 headings required).
COMMENTARY_REQUIRED_FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "close_signoff_commentary_required.json")


def _commentary_required():
    with open(COMMENTARY_REQUIRED_FIXTURE) as fh:
        return json.load(fh)


def _match(value, cond):
    if isinstance(cond, (list, tuple)):
        op, arg = cond[0], cond[1]
        if op == "in":
            return value in arg
        if op in ("=", "=="):
            return value == arg
        if op == "<=":
            return value is not None and value <= arg
        if op == "is":
            assert arg in ("set", "not set"), arg
            return (value not in (None, "")) == (arg == "set")
        raise AssertionError("stub: unsupported operator %r" % (op,))
    return value == cond


def _by_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
        roles = tuple(roles) if isinstance(roles, (list, tuple)) else (roles,)
        site.only_for.append(roles)
        if not set(roles) & set(site.roles):
            raise frappe.PermissionError("not permitted")

    def _rows(doctype, filters):
        assert doctype in site.records, "stub: unexpected doctype %s" % doctype
        return [r for r in site.records[doctype]
                if all(_match(r.get(f), c) for f, c in (filters or {}).items())]

    def get_all(doctype, filters=None, fields=None, order_by=None, pluck=None, **k):
        rows = _rows(doctype, filters)
        if order_by and order_by.startswith("completed_at desc"):
            rows = sorted(rows, key=lambda r: (r["completed_at"], r["creation"]), reverse=True)
        if pluck:
            return [r.get(pluck) for r in rows]
        return [_D({f: r.get(f) for f in (fields or ["name"])}) for r in rows]

    def get_value(doctype, name, fieldname, as_dict=False, **k):
        if doctype == "EPM Fiscal Year Period":
            assert set(name) >= {"parent", "fiscal_period"}, name
            key = (int(name["parent"]), int(name["fiscal_period"]))
            by, on = site.closed.get(key, (None, None))
            row = _D(closed_by=by, closed_on=on, **site.data_changed.get(key, {}))
        else:
            rows = _rows(doctype, {"name": name})
            if not rows:
                return None
            row = _D(rows[0])
        if isinstance(fieldname, (list, tuple)):
            return _D({f: row.get(f) for f in fieldname}) if as_dict else tuple(
                row.get(f) for f in fieldname)
        return row.get(fieldname)

    def get_single_value(doctype, field):
        assert doctype == "Close Settings", doctype
        fy, fp = site.first_close or (0, 0)
        self_approval, rate_move_threshold = site.policies
        cta_account, result_account = site.statement_accounts
        amount, percent, combine = site.commentary_threshold
        return {"first_close_fiscal_year": fy, "first_close_fiscal_period": fp,
                "commentary_threshold_amount": amount,
                "commentary_threshold_percent": percent,
                "commentary_threshold_combine": combine,
                "self_approval": self_approval,
                "rate_move_threshold": rate_move_threshold,
                "statement_cta_account": cta_account,
                "statement_result_account": result_account}[field]

    def _write(*a, **k):
        site.writes.append(a)
        raise AssertionError("a GET wrote: %r" % (a,))

    def has_permission(doctype, ptype="read", *a, **k):
        assert (doctype, ptype) == ("Assertion Run", "write"), (doctype, ptype)
        return site.can_write

    frappe.throw = throw
    frappe.whitelist = whitelist
    frappe.only_for = only_for
    frappe.get_all = get_all
    frappe.get_roles = lambda user=None: list(site.roles)
    frappe.has_permission = has_permission
    frappe.db = types.SimpleNamespace(get_value=get_value, get_single_value=get_single_value,
                                      set_value=_write, commit=_write, sql=_write)
    frappe.utils = types.SimpleNamespace(get_system_timezone=lambda: "Europe/London")
    frappe._ = lambda s: s
    frappe._dict = _D
    frappe.session = types.SimpleNamespace(user="zz-caller@example.com")

    konsol = types.ModuleType("konsol")
    close = types.ModuleType("konsol.close")
    close.__path__ = [CLOSE_DIR]
    calendar = types.ModuleType("konsol.fiscal_calendar")
    calendar.fiscal_period_rows = lambda: [dict(r) for r in site.rows]
    perms = types.ModuleType("konsol.entity_permissions")
    perms.allowed_entity_codes = lambda user=None: (
        None if site.allowed is None else set(site.allowed))
    period_status = types.ModuleType("konsol.period_status")
    period_status.PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})
    period_status.OPEN, period_status.CLOSED, period_status.LOCKED = "Open", "Closed", "Locked"

    def set_status(fiscal_year, fiscal_period, status, start_date=None, end_date=None,
                   reason=None, note=None):
        """A34: records the call; raises ``site.status_error`` (a gate refusal
        from the EPM Fiscal Year action) when set."""
        site.status_calls.append({"fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
                                  "status": status, "start_date": start_date,
                                  "end_date": end_date, "reason": reason, "note": note})
        if site.status_error is not None:
            raise site.status_error
        stamped = status == "Closed"
        return _D(name="ROW-%s-%s" % (fiscal_year, fiscal_period),
                  fiscal_year=str(fiscal_year), fiscal_period=int(fiscal_period),
                  period_code="P%02d" % int(fiscal_period), status=status,
                  closed_by=LEAD if stamped else None,
                  closed_on=CLOSED_ON if stamped else None)

    period_status.set_status = set_status
    lifecycle = types.ModuleType("konsol.schema_lifecycle")

    def check_epm_admin():
        """The real guard (schema_lifecycle.py:10): EPM Admin, System Manager
        or Administrator."""
        site.admin_checks += 1
        if not {"EPM Admin", "System Manager", "Administrator"} & set(site.roles):
            raise frappe.PermissionError("You need the 'EPM Admin' role.")

    lifecycle.check_epm_admin = check_epm_admin

    ar = types.ModuleType("konsol.consolidation.doctype.assertion_run.assertion_run")
    ar.OVERRIDE_ROLES = {"System Manager", "EPM Admin"}
    ar.REJECT_ROLES = _real_reject_roles()
    ar.TERMINAL_STATUSES = ("Green", "Amber", "Red", "Error")
    ar.SIGNED_STATES = ("Signed Off", "Acknowledged", "Overridden")

    def latest_close_run(fiscal_year, fiscal_period):
        rows = get_all("Assertion Run",
                       filters={"fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
                                "status": ["in", ar.TERMINAL_STATUSES]},
                       fields=["name", "status", "signoff_status", "failed", "errored"],
                       order_by="completed_at desc, creation desc")
        return rows[0] if rows else None

    ar.latest_close_run = latest_close_run

    def sign_off_close(close_run, override_reason=None, acknowledgement=None):
        site.signed.append((close_run, override_reason, acknowledgement))
        if site.sign_error is not None:
            raise site.sign_error
        return {"signoff_status": "Acknowledged", "signed_off_by": LEAD}

    ar.sign_off_close = sign_off_close

    def reject_signoff(close_run, reason):
        site.rejected.append((close_run, reason))
        if site.reject_error is not None:
            raise site.reject_error
        return {"signoff_status": "Not Signed Off"}

    ar.reject_signoff = reject_signoff
    ar._warned_assertion_names = lambda run, limit=50: list(site.warned_names.get(run, []))[:limit]
    consolidation = types.ModuleType("konsol.consolidation")
    doctype_pkg = types.ModuleType("konsol.consolidation.doctype")
    ar_pkg = types.ModuleType("konsol.consolidation.doctype.assertion_run")
    ar_pkg.assertion_run = ar

    # C09t: a stub `konsol.close.ic_api`, so `from konsol.close import ic_api`
    # (C10 on) resolves to this rather than the real module, which would
    # otherwise run against this fake frappe (whose frappe.db has no `count`).
    ic_api = types.ModuleType("konsol.close.ic_api")

    def signoff_summary(fiscal_year, fiscal_period):
        site.ic_calls.append((fiscal_year, fiscal_period))
        return dict(site.ic_summary)

    ic_api.signoff_summary = signoff_summary
    ic_api.tolerance_gap = lambda: site.ic_tolerance_gap
    close.ic_api = ic_api

    # #305 5.4: the gate imports konsol.close.ic_balance_api lazily.
    ic_balance_api = types.ModuleType("konsol.close.ic_balance_api")
    ic_balance_api.rule_gaps = (lambda fiscal_year, fiscal_period, reads=None:
                                list(site.ic_rule_gaps))
    close.ic_balance_api = ic_balance_api

    # W5-2 (8.4): a stub `konsol.close.statement_api` (the real one reads
    # ClickHouse), for signoff_gate.commentary's lazy import.
    statement_api = types.ModuleType("konsol.close.statement_api")

    def signoff_commentary(fiscal_year, fiscal_period):
        site.commentary_calls.append((fiscal_year, fiscal_period))
        return json.loads(json.dumps(site.commentary_line))

    statement_api.signoff_commentary = signoff_commentary
    close.statement_api = statement_api

    konsol.close, konsol.fiscal_calendar = close, calendar
    konsol.entity_permissions, konsol.period_status = perms, period_status
    konsol.schema_lifecycle = lifecycle
    konsol.consolidation = consolidation

    mods = {"frappe": frappe, "konsol": konsol, "konsol.close": close,
            "konsol.close.ic_api": ic_api,
            "konsol.close.ic_balance_api": ic_balance_api,
            "konsol.close.statement_api": statement_api,
            "konsol.fiscal_calendar": calendar, "konsol.entity_permissions": perms,
            "konsol.period_status": period_status,
            "konsol.schema_lifecycle": lifecycle,
            "konsol.consolidation": consolidation,
            "konsol.consolidation.doctype": doctype_pkg,
            "konsol.consolidation.doctype.assertion_run": ar_pkg,
            "konsol.consolidation.doctype.assertion_run.assertion_run": ar}
    saved = {n: sys.modules.get(n) for n in list(mods) + [
        "konsol.close.signoff_model", "konsol.close.period_model", "konsol.close.signoff_gate",
        "konsol.close.timefmt", "konsol.close.close_policy_model", "konsol.close.scope_model",
        "konsol.close.commentary_model"]}
    sys.modules.update(mods)
    try:
        #: M45t: commentary_model loaded for real by path, like the other
        #: pure modules, so M46's `from konsol.close import ... commentary_model`
        #: resolves (unblocks M46).
        for name in ("close_policy_model", "signoff_model", "period_model", "timefmt",
                      "scope_model", "signoff_gate", "commentary_model"):
            mod = _by_path("konsol.close." + name, os.path.join(CLOSE_DIR, name + ".py"))
            sys.modules["konsol.close." + name] = mod
            setattr(close, name, mod)
            mods["konsol.close." + name] = mod
        module = _by_path("close_signoff_api_under_test", API_PY)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return module, mods, frappe


def _get(site, fy=2025, fp=9):
    """Call get_signoff with the stubs installed; the result must be JSON-safe."""
    module, mods, _frappe = _load(site)
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        result = module.get_signoff(fy, fp)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    json.dumps(result)
    assert site.writes == []
    return result


def _raises(site, fy, fp):
    module, mods, frappe = _load(site)
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        try:
            module.get_signoff(fy, fp)
        except Exception as exc:  # noqa: BLE001 - the type is asserted by the caller
            return exc, mods
        raise AssertionError("get_signoff(%s, %s) did not refuse" % (fy, fp))
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


# --- N44t: the stub site declares the statement accounts (unblocks N45) -----

def test_statement_accounts_are_declared_on_the_stub_site():
    site = _Site()
    _module, _mods, frappe = _load(site)
    assert frappe.db.get_single_value("Close Settings", "statement_cta_account") == "3300"
    assert frappe.db.get_single_value("Close Settings", "statement_result_account") == "3100"
    rows = {r["name"]: r for r in site.records["Main Account"]}
    assert rows["3300"]["is_group"] == 0
    assert rows["3300"]["status"] == "Published"
    assert rows["3300"]["statement_section"] == "Balance Sheet"
    assert rows["3100"]["is_group"] == 0
    assert rows["3100"]["status"] == "Published"
    assert rows["3100"]["statement_section"] == "Balance Sheet"


# --- M45t: the stub site carries commentary_model and the commentary records -

def test_commentary_model_is_installed_and_the_commentary_records_exist():
    site = _Site()
    _module, mods, _frappe = _load(site)
    assert mods["konsol.close.commentary_model"].__name__ == "konsol.close.commentary_model"
    assert hasattr(mods["konsol.close.commentary_model"], "missing_commentary")
    assert site.records["Statement Commentary"] == []
    headings = {r["name"]: r for r in site.records["Main Account"] if r.get("is_group")}
    assert set(headings) == {"4", "7"}
    for code in ("4", "7"):
        assert headings[code]["status"] == "Published", code
        assert "lft" in headings[code], code


# --- contract ----------------------------------------------------------------

def test_get_signoff_is_get_only_and_gated_on_every_close_role():
    site = _Site()
    _get(site)
    assert site.whitelisted["get_signoff"] == ["GET"]
    assert site.only_for[0] == ALL_CLOSE_ROLES


def test_a_user_without_a_close_role_is_refused():
    site = _Site(roles=("Guest",))
    exc, _mods = _raises(site, 2025, 9)
    assert type(exc).__name__ == "PermissionError"


# --- the summary is assembled -------------------------------------------------

def test_summary_is_assembled_for_the_close_lead():
    site = _Site()
    result = _get(site)
    assert result["action"] == "acknowledge"
    assert result["checks"]["run"] == "RUN-09"
    assert result["checks"]["status"] == "Amber"
    assert result["gates"]["messages"] == []
    assert result["can_sign"] is True
    assert result["can_override"] is True


def test_warned_count_is_read_so_the_acknowledgement_total_is_known():
    # latest_close_run does not return `warned`; the API must read it.
    result = _get(_Site())
    assert result["acknowledgements"] == {"names": ["assert_a", "assert_b"], "total": 3,
                                          "unlisted": 1, "intercompany": None, "commentary": None}


def test_on_behalf_flags_map_yes_no_blank_to_1_0_unknown():
    result = _get(_Site())
    assert result["on_behalf"]["labels"] == ["by %s for ZZA" % LEAD]
    assert result["on_behalf"]["unknown"] == [
        "ZZC: by %s; on-behalf not recorded (uploaded before it was tracked)" % LEAD]
    assert "ZZB" not in json.dumps(result["on_behalf"])


def test_unknown_on_behalf_value_is_refused_not_guessed():
    site = _Site()
    site.records["Trial Balance Submission"][1]["uploaded_on_behalf"] = "Maybe"
    exc, _mods = _raises(site, 2025, 9)
    assert type(exc).__name__ == "ValidationError"
    assert "'Maybe'; expected Yes, No or blank" in str(exc)


def test_exceptions_of_the_period_only():
    result = _get(_Site())
    assert result["exceptions"] == [{"entity": "ZZE", "reason": "Dormant", "declared_by": LEAD,
                                     "declared_on": "2025-10-03T10:00:00+01:00"}]


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


def test_every_datetime_in_the_open_period_summary_carries_the_site_offset():
    stamps = _assert_every_datetime_zoned(_get(_Site()))
    assert stamps == ["2025-10-03T10:00:00+01:00"], stamps


def test_every_datetime_in_a_closed_period_summary_carries_the_site_offset():
    site = _Site()
    result = _get(site, 2025, 8)
    stamps = _assert_every_datetime_zoned(result)
    assert sorted(stamps) == ["2025-09-03T10:00:00+01:00", "2025-09-05T17:30:00+01:00"], stamps
    assert result["exceptions"][0]["declared_on"] == "2025-09-03T10:00:00+01:00"


def test_a_plain_date_stays_a_date_and_blank_stays_none():
    module, _mods, _frappe = _load(_Site())
    assert module._iso(date(2025, 9, 5)) == "2025-09-05"
    assert module._iso(None) is None and module._iso("") is None


def test_covers_notes_include_the_quarterly_entity_and_the_exception_run():
    result = _get(_Site())
    assert result["covers"] == ["ZZD: covers FY2025 P08–FY2025 P09",
                                "ZZQ: quarterly — covers FY2025 P07–FY2025 P09"]


def test_previous_periods_from_the_first_close_up_to_the_target():
    result = _get(_Site())
    assert [p["code"] for p in result["previous"]] == ["FY2025 P%02d" % fp for fp in range(1, 9)]
    assert all(p["status"] == "Closed" and p["signoff"] == "Signed Off"
               for p in result["previous"])


def test_viewer_reads_the_summary_but_cannot_sign_or_override():
    site = _Site(roles=("EPM User",))
    site.can_write = False
    result = _get(site)
    assert result["action"] == "acknowledge"
    assert result["can_sign"] is False
    assert result["can_override"] is False


def test_can_sign_is_the_write_permission_and_can_override_the_role():
    # Live (25 Sep): the EPM Analyst has no write on Assertion Run (R3), so
    # can_sign is False; it is the permission that decides, not the role name.
    site = _Site(roles=("EPM Analyst",))
    site.can_write = False
    site.records["Assertion Run"][-1].update(status="Red", warned=0)
    result = _get(site)
    assert result["can_sign"] is False
    assert result["can_override"] is False
    assert result["action"] == "blocked"
    site = _Site(roles=("EPM Analyst",))
    assert _get(site)["can_sign"] is True


# --- C10: the sign-off summary carries the intercompany line -----------------

def test_intercompany_line_passes_through_unchanged():
    site = _Site()
    result = _get(site)
    assert result["intercompany"] == site.ic_summary
    assert site.ic_calls == [(2025, 9)]


def test_intercompany_not_configured_does_not_change_the_action():
    site = _Site()
    site.ic_summary = {"state": "not_configured",
                       "message": "Intercompany not configured — nothing was checked.",
                       "counts": None, "sent_back_open": None}
    result = _get(site)
    assert result["intercompany"]["state"] == "not_configured"
    # No gate change (E5-P11): the same site's action is unchanged from the
    # known fixture value with intercompany "checked" (the test above, and
    # test_summary_is_assembled_for_the_close_lead).
    assert result["action"] == "acknowledge"


def test_viewer_and_entity_accountant_get_the_same_intercompany_counts():
    base = _get(_Site())["intercompany"]
    viewer = _Site(roles=("EPM User",))
    viewer.can_write = False
    assert _get(viewer)["intercompany"] == base
    entity = _Site(roles=("Entity Accountant",))
    entity.can_write = False
    entity.allowed = {"ZZD"}
    assert _get(entity)["intercompany"] == base


def test_sign_never_reads_the_intercompany_summary():
    site = _Site()
    _call_sign(site, 2025, 9, run="RUN-09", acknowledgement="Seen")
    assert site.ic_calls == []


# --- C21: get_signoff passes the one IC read into signoff_model.summary -----

def _over_tolerance(site, n):
    """A Green, unsigned RUN-09 (the Open P09 run) with ``n`` IC pairs over
    tolerance -- Amber only because of intercompany, never a dbt warning."""
    site.records["Assertion Run"][-1].update(status="Green", warned=0)
    counts = dict(site.ic_summary["counts"], pairs=n, over_tolerance=n)
    site.ic_summary = dict(site.ic_summary, counts=counts)


def test_a_green_run_with_ic_over_tolerance_pairs_is_acknowledge():
    site = _Site()
    _over_tolerance(site, 2)
    result = _get(site)
    assert result["action"] == "acknowledge"
    assert result["acknowledgements"]["intercompany"] == "Intercompany: 2 pairs over tolerance"
    assert result["intercompany"]["counts"]["over_tolerance"] == 2
    # One read of the IC line serves both signoff_model.summary and the
    # result's own "intercompany" key (C21: "the same value, one read").
    assert site.ic_calls == [(2025, 9)]


def test_the_viewer_gets_the_same_ic_driven_amber_as_the_close_lead():
    site = _Site()
    _over_tolerance(site, 2)
    base = _get(site)

    viewer = _Site(roles=("EPM User",))
    viewer.can_write = False
    _over_tolerance(viewer, 2)
    result = _get(viewer)
    assert result["action"] == base["action"] == "acknowledge"
    assert result["intercompany"] == base["intercompany"]


# --- M46: the sign-off summary carries the missing-commentary line -----------

COMMENTARY_FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "close_signoff_commentary.json")


def test_no_commentary_reports_both_headings_missing_in_lft_order():
    result = _get(_Site())
    assert result["commentary"] == [{
        "consolidation_group": "ZZGRP", "headings": 2, "with_commentary": 0,
        "missing": ["NET SALES", "OPERATING EXPENSES"],
    }]


def test_one_commented_heading_counts_one_with_commentary_one_missing():
    site = _Site()
    site.records["Statement Commentary"] = [
        {"consolidation_group": "ZZGRP", "heading": "4", "fiscal_year": 2025, "fiscal_period": 9, "text": "Volume up 3% on FX."}]
    result = _get(site)
    assert result["commentary"] == [{
        "consolidation_group": "ZZGRP", "headings": 2, "with_commentary": 1,
        "missing": ["OPERATING EXPENSES"],
    }]


def test_commentary_never_changes_the_action_or_the_gates():
    # story 9.1: commentary is informational only, never a gate.
    site = _Site()
    before = _get(site)
    site.records["Statement Commentary"] = [
        {"consolidation_group": "ZZGRP", "heading": "4", "fiscal_year": 2025, "fiscal_period": 9, "text": "Volume up 3% on FX."}]
    after = _get(site)
    assert before["action"] == after["action"]
    assert before["gates"] == after["gates"]
    assert before["commentary"] != after["commentary"]


def test_commentary_matches_the_golden_fixture():
    # U47 loads this same file: the committed golden value of this key.
    result = _get(_Site())
    with open(COMMENTARY_FIXTURE) as fh:
        golden = json.load(fh)
    assert result["commentary"] == golden


# --- A49: the period's own status ----------------------------------------------

def test_open_period_reports_open_and_no_closer():
    result = _get(_Site())
    assert result["period_status"] == "Open"
    assert result["closed_by"] is None
    assert result["closed_on"] is None


def test_closed_period_reports_closed_with_who_and_when():
    result = _get(_Site(), 2025, 8)
    assert result["action"] == "signed"
    assert result["period_status"] == "Closed"
    assert result["closed_by"] == LEAD
    assert result["closed_on"] == "2025-09-05T17:30:00+01:00"


# --- A59: a Closed or Locked period offers neither Run nor Sign ----------------

def test_a_closed_period_with_a_newer_unsigned_run_offers_no_sign():
    """A59: a run made on a closed period (before A59 refused it) is the latest
    and unsigned. The summary must not offer Sign or Run: reopen first."""
    for state in ("Closed", "Locked"):
        site = _Site()
        site.rows[7]["status"] = state  # P08
        site.records["Assertion Run"].append(
            _run("RUN-08b", 8, status="Green", signoff="Not Signed Off",
                 completed=datetime(2025, 9, 20, 9, 0)))
        result = _get(site, 2025, 8)
        assert result["checks"]["run"] == "RUN-08b", result["checks"]
        assert result["period_status"] == state
        assert result["action"] == "blocked", (state, result["action"])
        assert result["label"] == (
            "The period is %s; reopen it to run the checks or sign off" % state), result["label"]


def test_a_signed_closed_period_still_reads_signed():
    site = _Site()
    site.rows[7]["status"] = "Locked"
    result = _get(site, 2025, 8)
    assert result["action"] == "signed"
    assert result["period_status"] == "Locked"


# --- failure paths --------------------------------------------------------------

def test_an_open_earlier_period_blocks_with_its_name():
    site = _Site()
    site.rows[6]["status"] = "Open"  # P07
    site.closed.pop((2025, 7))
    site.records["Assertion Run"][6]["signoff_status"] = "Not Signed Off"
    result = _get(site)
    assert result["action"] == "blocked"
    assert result["label"] == "Sign off FY2025 P07 first"
    assert result["gates"]["order"]["blocking"] == "FY2025 P07"


def test_an_undeclared_period_is_refused_as_not_declared():
    exc, _mods = _raises(_Site(), 2031, 1)
    assert type(exc).__name__ == "PeriodNotDeclared"
    assert "FY2031 P01" in str(exc)


def test_undeclared_first_close_blocks_with_the_gap_and_no_previous():
    site = _Site()
    site.first_close = None
    result = _get(site)
    assert result["action"] == "blocked"
    assert [g["code"] for g in result["gates"]["config_gaps"]] == ["first_close_undeclared"]
    assert result["previous"] == []


def test_a_non_regular_period_is_refused():
    site = _Site()
    site.rows.append({"fiscal_year": 2025, "fiscal_period": 13, "period_code": "P13",
                      "period_label": "Closing", "period_type": "Closing",
                      "start_date": date(2025, 12, 31), "end_date": date(2025, 12, 31),
                      "quarter": "", "status": "Open"})
    exc, _mods = _raises(site, 2025, 13)
    assert "only Regular periods" in str(exc)


def test_a_period_that_is_not_a_number_is_refused():
    exc, _mods = _raises(_Site(), "2025", "P9")
    assert "whole numbers" in str(exc)


# --- entity scope (security boundary) ---------------------------------------------

def test_entity_accountant_sees_only_their_entities():
    site = _Site(roles=("Entity Accountant",))
    site.can_write = False
    site.allowed = {"ZZD"}
    # A missing monthly entity and a blank frequency, both outside the scope.
    site.records["Entity"] += [_entity("ZZM"), _entity("ZZN", "")]
    site.records["Ownership Period"] += [_ownership("ZZM"), _ownership("ZZN")]
    result = _get(site)
    text = json.dumps(result)
    for other in ("ZZA", "ZZB", "ZZC", "ZZE", "ZZQ", "ZZM", "ZZN"):
        assert other not in text, other
    assert result["on_behalf"] == {"labels": [], "unknown": []}
    assert result["exceptions"] == []
    assert result["covers"] == ["ZZD: covers FY2025 P08–FY2025 P09"]
    # Still blocked, and says how many entities outside the scope block it.
    assert result["action"] == "blocked"
    completeness = result["gates"]["completeness"]
    assert completeness["missing"] == []
    assert completeness["hidden"] == 1
    assert "1 entity outside your scope" in completeness["message"]
    frequency = [g for g in result["gates"]["config_gaps"] if g["code"] == "frequency_undeclared"]
    assert frequency[0]["entities"] == [] and frequency[0]["hidden"] == 1


def test_entity_accountant_sees_their_own_missing_entity_by_name():
    site = _Site(roles=("Entity Accountant",))
    site.can_write = False
    site.allowed = {"ZZM", "ZZD"}
    site.records["Entity"].append(_entity("ZZM"))
    site.records["Ownership Period"].append(_ownership("ZZM"))
    result = _get(site)
    completeness = result["gates"]["completeness"]
    assert completeness["missing"] == ["ZZM"]
    assert completeness.get("hidden", 0) == 0
    assert result["label"] == completeness["message"]
    assert "ZZM" in completeness["message"]


def test_entity_accountant_with_no_entities_sees_no_entity():
    site = _Site(roles=("Entity Accountant",))
    site.can_write = False
    site.allowed = set()
    result = _get(site)
    text = json.dumps(result)
    for other in ("ZZA", "ZZB", "ZZC", "ZZD", "ZZE", "ZZQ"):
        assert other not in text, other
    assert result["covers"] == []


def test_unowned_tb_gap_is_scoped_to_the_callers_entities():
    """E205c (#289): a scoped caller sees a count, not the generic fallback."""
    site = _Site()
    site.records["Entity"].append(_entity("ZZX"))
    site.records["Trial Balance Submission"].append(_tb("ZZX"))
    site.allowed = {"ZZA"}
    result = _get(site)
    gaps = [g for g in result["gates"]["config_gaps"] if g["code"] == "tb_without_ownership"]
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["entities"] == []
    assert gap["hidden"] == 1
    assert "1 entity outside your scope" in gap["message"]
    assert "record the ownership" in gap["message"]
    assert "ZZX" not in gap["message"]
    assert "tb_without_ownership (" not in gap["message"]


def test_unowned_tb_gap_unscoped_names_the_entity():
    """E205a's message, unmodified, when the caller is not scoped."""
    site = _Site()
    site.records["Entity"].append(_entity("ZZX"))
    site.records["Trial Balance Submission"].append(_tb("ZZX"))
    site.allowed = None
    result = _get(site)
    gaps = [g for g in result["gates"]["config_gaps"] if g["code"] == "tb_without_ownership"]
    assert len(gaps) == 1
    assert gaps[0]["entities"] == ["ZZX"]
    assert "ZZX" in gaps[0]["message"]


def _ic_balance_model():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "close", "ic_balance_model.py")
    spec = importlib.util.spec_from_file_location("ic_balance_model_for_signoff_api", path)
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    return model


def _real_rule_gaps(*pairs):
    """#305 5.4: the real producer's gaps (ic_balance_model.rule_gaps) for
    draft IC Balances of ``pairs`` with no rule."""
    model = _ic_balance_model()
    balances = [{"name": "B%d" % i, "selling_entity": s, "buying_entity": b, "docstatus": 0}
                for i, (s, b) in enumerate(pairs)]
    return model.rule_gaps(balances, [])


def _real_ambiguous_gaps(*pairs):
    """F51b: the real producer's gaps for draft IC Balances of ``pairs``
    that two wildcard rules both match."""
    balances = [{"name": "B%d" % i, "selling_entity": s, "buying_entity": b, "docstatus": 0,
                 "ending_inventory_from_ic": 40.0} for i, (s, b) in enumerate(pairs)]
    rules = [{"rule_id": r, "rule_type": "unrealized_profit", "margin_pct": 10,
              "debit_entity_pattern": "*", "credit_entity_pattern": "*"} for r in ("R1", "R2")]
    return _ic_balance_model().rule_gaps(balances, rules)


def test_ic_rule_gap_unscoped_names_the_pairs():
    site = _Site()
    site.ic_rule_gaps = _real_rule_gaps(("ZZA", "ZZX"))
    gaps = [g for g in _get(site)["gates"]["config_gaps"]
            if g["code"] == "ic_unrealized_profit_rule_undeclared"]
    assert len(gaps) == 1
    assert "ZZA → ZZX" in gaps[0]["message"]


def test_ic_rule_gap_is_scoped_to_the_callers_entities():
    """#305 5.4: a scoped caller sees its own entity and a count; neither the
    message nor the pairs name an entity outside its scope."""
    site = _Site()
    site.ic_rule_gaps = _real_rule_gaps(("ZZA", "ZZX"), ("ZZY", "ZZZ"))
    site.allowed = {"ZZA"}
    result = _get(site)
    gaps = [g for g in result["gates"]["config_gaps"]
            if g["code"] == "ic_unrealized_profit_rule_undeclared"]
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["entities"] == ["ZZA"] and gap["hidden"] == 3
    assert "ZZA" in gap["message"] and "3 entities outside your scope" in gap["message"]
    assert "IC Elimination Rule" in gap["message"]
    text = json.dumps(gap)
    for other in ("ZZX", "ZZY", "ZZZ"):
        assert other not in text, other
    assert "ic_unrealized_profit_rule_undeclared (" not in gap["message"]


# --- A32: sign ---------------------------------------------------------------------

def _call_sign(site, *args, **kwargs):
    """Call sign with the stubs installed. Returns (result, exception)."""
    module, mods, _frappe = _load(site)
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        try:
            return module.sign(*args, **kwargs), None
        except Exception as exc:  # noqa: BLE001 - the type is asserted by the caller
            return None, exc
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def test_sign_is_post_only_and_gated_on_the_close_lead():
    site = _Site()
    _call_sign(site, 2025, 9, run="RUN-09", acknowledgement="Seen")
    assert site.whitelisted["sign"] == ["POST"]
    assert site.only_for[0] == ("EPM Admin", "System Manager")


def test_sign_passes_the_latest_terminal_run_and_the_arguments_through():
    site = _Site()
    result, exc = _call_sign(site, 2025, 9, run="RUN-09", acknowledgement="Seen the 3 warnings",
                             override_reason="n/a")
    assert exc is None, exc
    assert site.signed == [("RUN-09", "n/a", "Seen the 3 warnings")]
    assert result == {"signoff_status": "Acknowledged", "signed_off_by": LEAD}


def test_sign_takes_the_latest_terminal_run_not_a_queued_one():
    site = _Site()
    site.records["Assertion Run"].append(
        _run("RUN-09-Q", 9, status="Queued", signoff="Not Signed Off",
             completed=datetime(2025, 10, 9, 9, 0)))
    site.records["Assertion Run"].append(
        _run("RUN-09-B", 9, status="Red", signoff="Not Signed Off",
             completed=datetime(2025, 10, 8, 9, 0)))
    _result, exc = _call_sign(site, "2025", "9", run="RUN-09-B", override_reason="Known FX gap")
    assert exc is None, exc
    assert site.signed == [("RUN-09-B", "Known FX gap", None)]


def test_sign_with_no_run_is_refused_naming_the_period():
    site = _Site()
    site.records["Assertion Run"] = [r for r in site.records["Assertion Run"]
                                     if r["fiscal_period"] != 9]
    _result, exc = _call_sign(site, 2025, 9, run="RUN-09")
    assert type(exc).__name__ == "ValidationError", exc
    assert str(exc) == "Run the checks for FY2025 P09 first."
    assert site.signed == []


def test_an_analyst_cannot_sign():
    site = _Site(roles=("EPM Analyst",))
    _result, exc = _call_sign(site, 2025, 9, run="RUN-09", acknowledgement="Seen")
    assert type(exc).__name__ == "PermissionError", exc
    assert site.signed == []


def test_other_close_roles_cannot_sign():
    for role in ("Entity Accountant", "EPM User", "Guest"):
        site = _Site(roles=(role,))
        _result, exc = _call_sign(site, 2025, 9, run="RUN-09", acknowledgement="Seen")
        assert type(exc).__name__ == "PermissionError", (role, exc)
        assert site.signed == [], role


def test_a_gate_refusal_from_sign_off_close_propagates_unchanged():
    site = _Site()
    refusal = RuntimeError("Sign-off blocked: Sign off P07 first.")
    site.sign_error = refusal
    _result, exc = _call_sign(site, 2025, 9, run="RUN-09", acknowledgement="Seen")
    assert exc is refusal
    assert site.signed == [("RUN-09", None, "Seen")]


def test_sign_refuses_an_undeclared_period():
    _result, exc = _call_sign(_Site(), 2031, 1, run="RUN-09")
    assert type(exc).__name__ == "PeriodNotDeclared", exc


def test_sign_refuses_a_period_that_is_not_a_number():
    site = _Site()
    _result, exc = _call_sign(site, "2025", "P9", run="RUN-09")
    assert "whole numbers" in str(exc)
    assert site.signed == []


# --- A33: declare_tb_exception -----------------------------------------------------

class _TBXDoc:
    """A stub TB Exception: records insert and submit. The controller (A08) is
    not loaded here; its refusals are simulated by ``site.tbx_error``."""

    def __init__(self, site, data):
        self.site, self.data, self.name = site, dict(data), None
        self.steps = []

    def insert(self, *a, **k):
        assert not a and not k, "insert must not bypass permissions: %r %r" % (a, k)
        self.steps.append("insert")
        self.site.tbx_steps.append("insert")
        if self.site.tbx_error is not None:
            raise self.site.tbx_error
        self.name = "TBX-00042"
        return self

    def submit(self, *a, **k):
        assert not a and not k, "submit must not bypass permissions: %r %r" % (a, k)
        assert self.steps == ["insert"], self.steps
        self.steps.append("submit")
        self.site.tbx_steps.append("submit")
        return self


def _call_declare(site, *args, **kwargs):
    """Call declare_tb_exception with the stubs installed. Returns (result, exception)."""
    module, mods, frappe = _load(site)
    site.tbx_docs, site.tbx_steps = [], []
    site.tbx_error = getattr(site, "tbx_error", None)

    def get_doc(arg, *a, **k):
        assert isinstance(arg, dict) and not a and not k, (arg, a, k)
        doc = _TBXDoc(site, arg)
        site.tbx_docs.append(doc)
        return doc

    frappe.get_doc = get_doc
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        try:
            return module.declare_tb_exception(*args, **kwargs), None
        except Exception as exc:  # noqa: BLE001 - the type is asserted by the caller
            return None, exc
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def test_declare_is_post_only_and_gated_on_the_close_lead():
    site = _Site()
    _call_declare(site, "ZZB", 2025, 9, "Dormant since June")
    assert site.whitelisted["declare_tb_exception"] == ["POST"]
    assert site.only_for[0] == ("EPM Admin", "System Manager")


def test_declare_inserts_then_submits_with_the_reason_passed_through():
    site = _Site()
    result, exc = _call_declare(site, "ZZB", "2025", "9", "Dormant since June")
    assert exc is None, exc
    assert result == "TBX-00042"
    assert site.tbx_steps == ["insert", "submit"]
    [doc] = site.tbx_docs
    assert doc.data == {"doctype": "TB Exception", "data_area_id": "ZZB",
                        "fiscal_year": 2025, "fiscal_period": 9,
                        "reason": "Dormant since June"}


def test_declare_never_sends_declared_by():
    """The controller sets declared_by to the submitting user (A08); the endpoint
    neither takes it nor sends it, so a forged value in the request cannot land."""
    import inspect
    module, _mods, _frappe = _load(_Site())
    params = inspect.signature(module.declare_tb_exception).parameters
    assert list(params) == ["entity", "fiscal_year", "fiscal_period", "reason"], list(params)
    assert all(p.kind is p.POSITIONAL_OR_KEYWORD for p in params.values())
    site = _Site()
    _call_declare(site, "ZZB", 2025, 9, "Dormant")
    assert "declared_by" not in site.tbx_docs[0].data
    _result, exc = _call_declare(_Site(), "ZZB", 2025, 9, "Dormant",
                                 declared_by="someone-else@example.com")
    assert isinstance(exc, TypeError), exc


def test_a_blank_reason_is_refused_before_insert():
    for reason in ("", "   \n\t", None):
        site = _Site()
        _result, exc = _call_declare(site, "ZZB", 2025, 9, reason)
        assert type(exc).__name__ == "ValidationError", (reason, exc)
        assert "reason" in str(exc).lower(), exc
        assert site.tbx_docs == [] and site.tbx_steps == [], reason


def test_an_analyst_cannot_declare():
    site = _Site(roles=("EPM Analyst",))
    _result, exc = _call_declare(site, "ZZB", 2025, 9, "Dormant")
    assert type(exc).__name__ == "PermissionError", exc
    assert site.tbx_docs == [] and site.tbx_steps == []


def test_other_close_roles_cannot_declare():
    for role in ("Entity Accountant", "EPM User", "Guest"):
        site = _Site(roles=(role,))
        _result, exc = _call_declare(site, "ZZB", 2025, 9, "Dormant")
        assert type(exc).__name__ == "PermissionError", (role, exc)
        assert site.tbx_steps == [], role


def test_a_controller_refusal_propagates_unchanged_and_nothing_is_submitted():
    site = _Site()
    refusal = RuntimeError("TBX-00001 already declares no trial balance for ZZE FY2025 P9.")
    site.tbx_error = refusal
    _result, exc = _call_declare(site, "ZZE", 2025, 9, "Dormant")
    assert exc is refusal
    assert site.tbx_steps == ["insert"]


def test_declare_refuses_a_period_that_is_not_a_number():
    site = _Site()
    _result, exc = _call_declare(site, "ZZB", "2025", "P9", "Dormant")
    assert "whole numbers" in str(exc)
    assert site.tbx_steps == []


# --- A34: close_period and reopen_period ---------------------------------------------

def _call_status(site, fn, *args, **kwargs):
    """Call close_period / reopen_period with the stubs installed. Returns
    (result, exception)."""
    module, mods, _frappe = _load(site)
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        try:
            result = getattr(module, fn)(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the type is asserted by the caller
            return None, exc
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    json.dumps(result)
    return result, None


def test_close_and_reopen_are_post_only_and_gated_on_the_close_lead():
    for fn, args in (("close_period", (2025, 9)), ("reopen_period", (2025, 8, "Late TB"))):
        site = _Site()
        _call_status(site, fn, *args)
        assert site.whitelisted[fn] == ["POST"], fn
        assert site.only_for[0] == ("EPM Admin", "System Manager"), fn
        assert site.admin_checks == 1, fn


def test_close_passes_the_note_through_set_status():
    site = _Site()
    result, exc = _call_status(site, "close_period", "2025", "9", note="All TBs in")
    assert exc is None, exc
    assert site.status_calls == [{"fiscal_year": 2025, "fiscal_period": 9, "status": "Closed",
                                  "start_date": None, "end_date": None,
                                  "reason": None, "note": "All TBs in"}]
    assert result == {"status": "Closed", "closed_by": LEAD,
                      "closed_on": "2025-09-05T17:30:00+01:00"}


def test_close_without_a_note_sends_none():
    site = _Site()
    _result, exc = _call_status(site, "close_period", 2025, 9)
    assert exc is None, exc
    assert site.status_calls[0]["note"] is None
    assert site.status_calls[0]["status"] == "Closed"


def test_reopen_passes_the_reason_through_set_status():
    site = _Site()
    result, exc = _call_status(site, "reopen_period", 2025, 8, "ZZB restated its TB")
    assert exc is None, exc
    assert site.status_calls == [{"fiscal_year": 2025, "fiscal_period": 8, "status": "Open",
                                  "start_date": None, "end_date": None,
                                  "reason": "ZZB restated its TB", "note": None}]
    assert result == {"status": "Open", "closed_by": None, "closed_on": None}


def test_a_blank_reopen_reason_is_refused_before_set_status():
    for reason in ("", "   \n\t", None):
        site = _Site()
        _result, exc = _call_status(site, "reopen_period", 2025, 8, reason)
        assert type(exc).__name__ == "ValidationError", (reason, exc)
        assert "reason" in str(exc).lower(), exc
        assert "P08" in str(exc), exc
        assert site.status_calls == [], reason


def test_an_analyst_cannot_close_or_reopen():
    for fn, args in (("close_period", (2025, 9)), ("reopen_period", (2025, 8, "Late TB"))):
        site = _Site(roles=("EPM Analyst",))
        _result, exc = _call_status(site, fn, *args)
        assert type(exc).__name__ == "PermissionError", (fn, exc)
        assert site.status_calls == [], fn


def test_other_close_roles_cannot_close_or_reopen():
    for role in ("Entity Accountant", "EPM User", "Guest"):
        for fn, args in (("close_period", (2025, 9)), ("reopen_period", (2025, 8, "Late"))):
            site = _Site(roles=(role,))
            _result, exc = _call_status(site, fn, *args)
            assert type(exc).__name__ == "PermissionError", (role, fn, exc)
            assert site.status_calls == [], (role, fn)


def test_a_gate_refusal_from_set_status_propagates_unchanged():
    for fn, args in (("close_period", (2025, 9)), ("reopen_period", (2025, 8, "Late TB"))):
        site = _Site()
        refusal = RuntimeError("P09 can't close: sign off the checks first.")
        site.status_error = refusal
        _result, exc = _call_status(site, fn, *args)
        assert exc is refusal, (fn, exc)
        assert len(site.status_calls) == 1, fn


def test_close_and_reopen_refuse_a_period_that_is_not_a_number():
    for fn, args in (("close_period", ("2025", "P9")), ("reopen_period", ("2025", "P8", "x"))):
        site = _Site()
        _result, exc = _call_status(site, fn, *args)
        assert "whole numbers" in str(exc), (fn, exc)
        assert site.status_calls == [], fn


# --- A58: sign is bound to the run the Close Lead reviewed ------------------------------

def _rerun(site):
    """An Analyst re-ran the checks after the summary showed RUN-09: RUN-09-B
    (Red) finished later and is now the latest terminal run."""
    site.records["Assertion Run"].append(
        _run("RUN-09-B", 9, status="Red", signoff="Not Signed Off",
             completed=datetime(2025, 10, 9, 9, 0)))


def test_sign_with_a_stale_run_is_refused_and_signs_nothing():
    site = _Site()
    _rerun(site)
    _result, exc = _call_sign(site, 2025, 9, run="RUN-09", acknowledgement="Seen the 3 warnings")
    assert type(exc).__name__ == "ValidationError", exc
    assert str(exc) == ("The checks were re-run (now RUN-09-B, Red); "
                        "review the new result before signing.")
    # Neither the reviewed run nor the new one is signed.
    assert site.signed == []
    assert site.writes == []
    states = {r["name"]: r["signoff_status"] for r in site.records["Assertion Run"]
              if r["fiscal_period"] == 9}
    assert states == {"RUN-09": "Not Signed Off", "RUN-09-B": "Not Signed Off"}


def test_sign_without_a_run_name_is_refused_and_signs_nothing():
    for missing in (None, "", "   "):
        site = _Site()
        _result, exc = _call_sign(site, 2025, 9, run=missing, acknowledgement="Seen")
        assert type(exc).__name__ == "ValidationError", (missing, exc)
        assert str(exc) == ("Reload the sign-off for FY2025 P09: the request did not say "
                            "which checks run it signs."), missing
        assert site.signed == [], missing


def test_sign_with_no_run_argument_at_all_is_refused():
    site = _Site()
    _result, exc = _call_sign(site, 2025, 9, acknowledgement="Seen")
    assert type(exc).__name__ == "ValidationError", exc
    assert site.signed == []


def test_sign_with_the_matching_run_signs_it():
    site = _Site()
    _rerun(site)
    _result, exc = _call_sign(site, 2025, 9, run="RUN-09-B", override_reason="Known FX gap")
    assert exc is None, exc
    assert site.signed == [("RUN-09-B", "Known FX gap", None)]


# --- A63: the summary carries the period's last data change, zoned ------------

def test_get_signoff_carries_the_data_change_zoned():
    site = _Site()
    site.data_changed[(2025, 9)] = {
        "data_changed_at": datetime(2025, 10, 4, 11, 0), "data_changed_by": LEAD,
        "data_change": "TB TB-ZZA-9 cancelled"}
    result = _get(site)
    assert result["data_changed_at"] == "2025-10-04T11:00:00+01:00", result["data_changed_at"]
    assert result["data_changed_by"] == LEAD
    assert result["data_change"] == "TB TB-ZZA-9 cancelled"
    _assert_every_datetime_zoned(result)


def test_get_signoff_with_no_data_change_recorded_sends_none():
    result = _get(_Site())
    for field in ("data_changed_at", "data_changed_by", "data_change"):
        assert field in result, "get_signoff has no %s" % field
        assert result[field] is None, (field, result[field])


# --- A66: the summary does not offer a sign the server will refuse -----------

def _changed(site, started_at):
    site.data_changed[(2025, 9)] = {
        "data_changed_at": datetime(2025, 10, 4, 11, 0, 30), "data_changed_by": LEAD,
        "data_change": "TB TB-ZZA-9 cancelled"}
    run = next(r for r in site.records["Assertion Run"] if r["name"] == "RUN-09")
    run["started_at"] = started_at


def test_get_signoff_blocks_a_run_that_started_before_the_data_change():
    label = ("TB TB-ZZA-9 cancelled at 2025-10-04 11:00:30 by %s, after these checks "
             "started; run the checks again" % LEAD)
    for started in (datetime(2025, 10, 4, 10, 0), datetime(2025, 10, 4, 11, 0, 30), None):
        site = _Site()
        _changed(site, started)
        result = _get(site)
        assert (result["action"], result["label"]) == ("blocked", label), (started, result["label"])


def test_get_signoff_offers_the_sign_to_a_run_that_started_after_the_change():
    site = _Site()
    _changed(site, datetime(2025, 10, 4, 11, 5))
    assert _get(site)["action"] == "acknowledge"


# --- C09t: the loader carries a stub konsol.close.ic_api, for C10 ----------


def test_the_ic_api_stub_is_installed():
    site = _Site()
    _module, mods, _frappe = _load(site)
    assert mods["konsol.close.ic_api"].signoff_summary(2025, 7)["state"] == "checked"
    assert site.ic_calls == [(2025, 7)]
    assert mods["konsol.close.ic_api"].tolerance_gap() is site.ic_tolerance_gap


# --- #305-W5-1 (story 9.4, #157): reject a signature with a reason --------------

def _signed_site(roles=("EPM Admin",)):
    """P09's latest terminal run RUN-09 is Green and Signed Off."""
    site = _Site(roles=roles)
    site.records["Assertion Run"][-1].update(status="Green", warned=0,
                                             signoff_status="Signed Off")
    return site


def _call_reject(site, *args, **kwargs):
    """Call reject with the stubs installed. Returns (result, exception)."""
    module, mods, _frappe = _load(site)
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        try:
            return module.reject(*args, **kwargs), None
        except Exception as exc:  # noqa: BLE001 - the type is asserted by the caller
            return None, exc
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def test_reject_is_post_only_and_gated_on_the_close_lead():
    site = _signed_site()
    _call_reject(site, 2025, 9, run="RUN-09", reason="ZZA's TB is the draft")
    assert site.whitelisted["reject"] == ["POST"]
    assert site.only_for[0] == ("EPM Admin", "System Manager")


def test_reject_passes_the_latest_terminal_run_and_the_reason_through():
    site = _signed_site()
    result, exc = _call_reject(site, 2025, 9, run="RUN-09", reason="ZZA's TB is the draft")
    assert exc is None, exc
    assert site.rejected == [("RUN-09", "ZZA's TB is the draft")]
    assert result == {"signoff_status": "Not Signed Off"}


def test_a_blank_reject_reason_is_refused_before_anything():
    for reason in (None, "", "  \n "):
        site = _signed_site()
        _result, exc = _call_reject(site, 2025, 9, run="RUN-09", reason=reason)
        assert type(exc).__name__ == "ValidationError", (reason, exc)
        assert str(exc) == ("Give the reason the sign-off of FY2025 P09 is rejected: "
                            "the preparer reads it."), reason
        assert site.rejected == [], reason


def test_reject_without_a_run_name_is_refused():
    for missing in (None, "", "   "):
        site = _signed_site()
        _result, exc = _call_reject(site, 2025, 9, run=missing, reason="rework")
        assert type(exc).__name__ == "ValidationError", (missing, exc)
        assert str(exc) == ("Reload the sign-off for FY2025 P09: the request did not say "
                            "which checks run it rejects."), missing
        assert site.rejected == [], missing


def test_reject_with_a_stale_run_is_refused_and_rejects_nothing():
    site = _signed_site()
    _rerun(site)
    _result, exc = _call_reject(site, 2025, 9, run="RUN-09", reason="rework")
    assert type(exc).__name__ == "ValidationError", exc
    # The prefix signoffMachine.STALE_RUN_REFUSAL reloads on.
    assert str(exc) == ("The checks were re-run (now RUN-09-B, Red); "
                        "review the new result before rejecting.")
    assert site.rejected == []


def test_reject_with_no_run_at_all_is_refused_naming_the_period():
    site = _signed_site()
    site.records["Assertion Run"] = [r for r in site.records["Assertion Run"]
                                     if r["fiscal_period"] != 9]
    _result, exc = _call_reject(site, 2025, 9, run="RUN-09", reason="rework")
    assert str(exc) == "FY2025 P09 has no checks run to reject."
    assert site.rejected == []


def test_no_other_close_role_can_reject():
    for role in ("EPM Analyst", "Entity Accountant", "EPM User", "Guest"):
        site = _signed_site(roles=(role,))
        _result, exc = _call_reject(site, 2025, 9, run="RUN-09", reason="rework")
        assert type(exc).__name__ == "PermissionError", (role, exc)
        assert site.rejected == [], role


def test_a_refusal_from_reject_signoff_propagates_unchanged():
    site = _signed_site()
    refusal = RuntimeError("Assertion Run RUN-09 is Not Signed Off; ...")
    site.reject_error = refusal
    _result, exc = _call_reject(site, 2025, 9, run="RUN-09", reason="rework")
    assert exc is refusal


def test_reject_refuses_an_undeclared_period():
    _result, exc = _call_reject(_signed_site(), 2031, 1, run="RUN-09", reason="rework")
    assert type(exc).__name__ == "PeriodNotDeclared", exc


def test_get_signoff_says_whether_the_caller_may_reject():
    for roles, expected in ((("EPM Admin",), True), (("System Manager",), True),
                            (("EPM Analyst",), False), (("EPM User",), False),
                            (("Entity Accountant",), False)):
        assert _get(_signed_site(roles=roles))["can_reject"] is expected, roles


# --- W5-2 (story 8.4): the commentary-threshold line --------------------------
#
# get_signoff reads signoff_gate.commentary once, feeds it to
# signoff_model.summary (Amber + acknowledgement, the #265 path) and returns
# it as ``commentary_required``, beside the informational ``commentary``.

ACK_COMMENTARY_FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures",
    "close_signoff_acknowledgements_commentary.json")


def _green_site():
    site = _Site()
    site.records["Assertion Run"][-1].update(status="Green", warned=0)
    site.warned_names = {}
    return site


def test_required_commentary_makes_a_green_run_acknowledge():
    site = _green_site()
    assert _get(site)["action"] == "sign"
    site.commentary_line = _commentary_required()
    result = _get(site)
    assert result["action"] == "acknowledge"
    assert result["acknowledgements"]["commentary"] == (
        "Commentary: 3 headings above the threshold without commentary")


def test_the_commentary_line_is_read_once_and_returned_as_commentary_required():
    site = _green_site()
    site.commentary_line = _commentary_required()
    result = _get(site)
    assert site.commentary_calls == [(2025, 9)]
    assert result["commentary_required"] == _commentary_required()
    # The informational per-group list is unchanged beside it.
    assert result["commentary"][0]["missing"] == ["NET SALES", "OPERATING EXPENSES"]


def test_a_commentary_line_that_cannot_be_checked_blocks():
    site = _green_site()
    cm = _close_model("commentary_model")
    site.commentary_line = cm.requirement(
        _close_model("close_policy_model").commentary_threshold(5000, 0, ""),
        [{"consolidation_group": "ZZGRP", "state": "error", "message": "boom",
          "statement": None, "texts": {}}])
    result = _get(site)
    assert result["action"] == "blocked"
    assert "Nothing can be signed" in result["label"]


def test_viewer_and_entity_accountant_get_the_same_commentary_line():
    base = _get(_green_site())
    for roles in (("EPM User",), ("Entity Accountant",)):
        site = _green_site()
        site.roles = list(roles)
        site.can_write = False
        assert _get(site)["commentary_required"] == base["commentary_required"], roles


def test_an_undeclared_threshold_is_a_config_gap_that_blocks():
    site = _green_site()
    site.commentary_threshold = (0, 0, "")
    site.commentary_line = _close_model("commentary_model").requirement(
        _close_model("close_policy_model").commentary_threshold(0, 0, ""), None)
    result = _get(site)
    assert result["action"] == "blocked"
    codes = [g["code"] for g in result["gates"]["config_gaps"]]
    assert codes == ["commentary_threshold_undeclared"], codes
    assert result["commentary_required"]["state"] == "undeclared"


def test_commentary_acknowledgements_match_the_golden_fixture():
    # close-ui's signoff test loads this same file: the real get_signoff
    # acknowledgements for a Green run with 3 headings required.
    site = _green_site()
    site.commentary_line = _commentary_required()
    result = _get(site)
    with open(ACK_COMMENTARY_FIXTURE) as fh:
        assert result["acknowledgements"] == json.load(fh)



# --- F51b / review S2: the ambiguous-rule gap -----------------------------------

def test_ic_rule_ambiguous_gap_unscoped_names_pair_and_rules():
    site = _Site()
    site.ic_rule_gaps = _real_ambiguous_gaps(("ZZA", "ZZX"))
    gaps = [g for g in _get(site)["gates"]["config_gaps"]
            if g["code"] == "ic_unrealized_profit_rule_ambiguous"]
    assert len(gaps) == 1
    assert "ZZA → ZZX (R1, R2)" in gaps[0]["message"]


def test_ic_rule_ambiguous_gap_is_scoped_to_the_callers_entities():
    site = _Site()
    site.ic_rule_gaps = _real_ambiguous_gaps(("ZZA", "ZZX"), ("ZZY", "ZZZ"))
    site.allowed = {"ZZA"}
    gaps = [g for g in _get(site)["gates"]["config_gaps"]
            if g["code"] == "ic_unrealized_profit_rule_ambiguous"]
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["entities"] == ["ZZA"] and gap["hidden"] == 3 and gap["pairs"] == []
    assert "ZZA" in gap["message"] and "3 entities outside your scope" in gap["message"]
    assert "more than once" in gap["message"] and "IC Elimination Rule" in gap["message"]
    text = json.dumps(gap)
    for other in ("ZZX", "ZZY", "ZZZ"):
        assert other not in text, other
    assert "ic_unrealized_profit_rule_ambiguous (" not in gap["message"]



# --- I53 (#305-S8-1): the pending-draft gap -------------------------------------

def _real_pending_gaps(*pairs):
    """I53: the real producer's gap (ic_balance_model.pending_gap) for one
    draft IC Balance per pair, inventory 100, that one wildcard rule matches."""
    balances = [{"name": "ICB-%s-%s" % (s, b), "selling_entity": s, "buying_entity": b,
                 "docstatus": 0, "ending_inventory_from_ic": 100.0} for s, b in pairs]
    rules = [{"rule_id": "R1", "rule_type": "unrealized_profit", "margin_pct": 20,
              "debit_entity_pattern": "*", "credit_entity_pattern": "*"}]
    gap = _ic_balance_model().pending_gap(balances, rules)
    assert gap is not None and gap["code"] == "ic_balance_draft_pending", gap
    return [gap]


def _pending(site):
    return [g for g in _get(site)["gates"]["config_gaps"]
            if g["code"] == "ic_balance_draft_pending"]


def test_ic_balance_pending_gap_unscoped_keeps_the_producers_message():
    site = _Site()
    site.ic_rule_gaps = _real_pending_gaps(("ZZA", "ZZB"))
    gaps = _pending(site)
    assert len(gaps) == 1
    assert gaps[0] == site.ic_rule_gaps[0]
    assert "ZZA → ZZB (ICB-ZZA-ZZB)" in gaps[0]["message"]


def test_ic_balance_pending_gap_is_scoped_to_the_callers_entities():
    site = _Site()
    site.ic_rule_gaps = _real_pending_gaps(("ZZA", "ZZB"), ("ZZC", "ZZD"))
    site.allowed = {"ZZA"}
    gaps = _pending(site)
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["entities"] == ["ZZA"] and gap["hidden"] == 3 and gap["pairs"] == []
    assert gap["message"] == (
        "IC Balance drafts of ZZA, 3 entities outside your scope for FY2025 P07 have a "
        "matching rule but are not approved: approve them in Approvals or delete the drafts "
        "before signing off."), gap["message"]
    text = json.dumps(gap)
    for other in ("ZZB", "ZZC", "ZZD"):
        assert other not in text, other


def test_ic_balance_pending_gap_scoped_one_pair_hides_the_partner():
    site = _Site()
    site.ic_rule_gaps = _real_pending_gaps(("ZZA", "ZZB"))
    site.allowed = {"ZZA"}
    gap = _pending(site)[0]
    assert gap["entities"] == ["ZZA"] and gap["hidden"] == 1 and gap["pairs"] == []
    assert "1 entity outside your scope" in gap["message"]
    assert "ZZB" not in json.dumps(gap)


def test_ic_balance_pending_gap_scoped_never_uses_the_generic_fallback():
    site = _Site()
    site.ic_rule_gaps = _real_pending_gaps(("ZZA", "ZZB"), ("ZZC", "ZZD"))
    site.allowed = {"ZZA"}
    gap = _pending(site)[0]
    assert "ic_balance_draft_pending (" not in gap["message"]
    assert "Approvals" in gap["message"]


def test_ic_balance_pending_gap_scoped_keeps_a_pair_seen_whole():
    site = _Site()
    site.ic_rule_gaps = _real_pending_gaps(("ZZA", "ZZB"), ("ZZC", "ZZD"))
    site.allowed = {"ZZA", "ZZB"}
    gap = _pending(site)[0]
    assert gap["hidden"] == 2
    assert gap["pairs"] == [{"selling_entity": "ZZA", "buying_entity": "ZZB",
                             "names": ["ICB-ZZA-ZZB"]}]


# --- review-w5 S12: the reject roles are defined once -------------------------

import ast  # noqa: E402

ASSERTION_RUN_PY = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run",
                                "assertion_run.py")


def _tree(path):
    with open(path) as fh:
        return ast.parse(fh.read())


def _module_assigns(tree, name):
    return [node for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)]


def test_reject_roles_are_defined_once_in_assertion_run_and_imported():
    api = _tree(API_PY)
    assert _module_assigns(api, "REJECT_ROLES") == [], "signoff_api redefines REJECT_ROLES"
    imported = {alias.name for node in api.body if isinstance(node, ast.ImportFrom)
                and node.module == "konsol.consolidation.doctype.assertion_run.assertion_run"
                for alias in node.names}
    assert "REJECT_ROLES" in imported, imported
    (definition,) = _module_assigns(_tree(ASSERTION_RUN_PY), "REJECT_ROLES")
    roles = ast.literal_eval(definition.value)
    # The only_for literal stays (the endpoint contract test reads it); it
    # must name exactly the roles reject_signoff checks.
    reject = next(node for node in api.body
                  if isinstance(node, ast.FunctionDef) and node.name == "reject")
    only_for = next(node for node in ast.walk(reject) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and node.func.attr == "only_for")
    assert set(ast.literal_eval(only_for.args[0])) == set(roles)
