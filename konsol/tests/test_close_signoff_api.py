"""Sign-off API: konsol/close/signoff_api.py `get_signoff` (konsol#305 A30, A49; stories 9.1, 9.2, 9.3, 9.5).

GET `get_signoff(fiscal_year, fiscal_period)` returns the A21 summary plus
`can_sign`, `can_override`, `period_status`, `closed_by` and `closed_on`.

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
import sys
import types
from datetime import date, datetime

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")
API_PY = os.path.join(CLOSE_DIR, "signoff_api.py")

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
            "fiscal_period": fp, "docstatus": docstatus, "reason": reason, "declared_by": LEAD}


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
        self.closed = {(2025, fp): (LEAD, CLOSED_ON) for fp in range(1, 9)}
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
        }
        self.warned_names = {"RUN-09": ["assert_a", "assert_b"]}
        self.only_for = []
        self.whitelisted = {}
        self.writes = []


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
            assert arg == "set", arg
            return value not in (None, "")
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
            row = _D(closed_by=by, closed_on=on)
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
        return {"first_close_fiscal_year": fy, "first_close_fiscal_period": fp}[field]

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

    ar = types.ModuleType("konsol.consolidation.doctype.assertion_run.assertion_run")
    ar.OVERRIDE_ROLES = {"System Manager", "EPM Admin"}
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
    ar._warned_assertion_names = lambda run, limit=50: list(site.warned_names.get(run, []))[:limit]
    consolidation = types.ModuleType("konsol.consolidation")
    doctype_pkg = types.ModuleType("konsol.consolidation.doctype")
    ar_pkg = types.ModuleType("konsol.consolidation.doctype.assertion_run")
    ar_pkg.assertion_run = ar

    konsol.close, konsol.fiscal_calendar = close, calendar
    konsol.entity_permissions, konsol.period_status = perms, period_status
    konsol.consolidation = consolidation

    mods = {"frappe": frappe, "konsol": konsol, "konsol.close": close,
            "konsol.fiscal_calendar": calendar, "konsol.entity_permissions": perms,
            "konsol.period_status": period_status,
            "konsol.consolidation": consolidation,
            "konsol.consolidation.doctype": doctype_pkg,
            "konsol.consolidation.doctype.assertion_run": ar_pkg,
            "konsol.consolidation.doctype.assertion_run.assertion_run": ar}
    saved = {n: sys.modules.get(n) for n in list(mods) + [
        "konsol.close.signoff_model", "konsol.close.period_model", "konsol.close.signoff_gate",
        "konsol.close.timefmt"]}
    sys.modules.update(mods)
    try:
        for name in ("signoff_model", "period_model", "timefmt", "signoff_gate"):
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
                                          "unlisted": 1}


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
    assert result["exceptions"] == [{"entity": "ZZE", "reason": "Dormant", "declared_by": LEAD}]


def test_covers_notes_include_the_quarterly_entity_and_the_exception_run():
    result = _get(_Site())
    assert result["covers"] == ["ZZD: covers P08–P09",
                                "ZZQ: quarterly — covers P07–P09"]


def test_previous_periods_from_the_first_close_up_to_the_target():
    result = _get(_Site())
    assert [p["code"] for p in result["previous"]] == ["P%02d" % fp for fp in range(1, 9)]
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


# --- failure paths --------------------------------------------------------------

def test_an_open_earlier_period_blocks_with_its_name():
    site = _Site()
    site.rows[6]["status"] = "Open"  # P07
    site.closed.pop((2025, 7))
    site.records["Assertion Run"][6]["signoff_status"] = "Not Signed Off"
    result = _get(site)
    assert result["action"] == "blocked"
    assert result["label"] == "Sign off P07 first"
    assert result["gates"]["order"]["blocking"] == "P07"


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
    assert result["covers"] == ["ZZD: covers P08–P09"]
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
