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
        self.signed = []
        self.sign_error = None
        self.status_calls = []
        self.status_error = None
        self.admin_checks = 0


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
    ar._warned_assertion_names = lambda run, limit=50: list(site.warned_names.get(run, []))[:limit]
    consolidation = types.ModuleType("konsol.consolidation")
    doctype_pkg = types.ModuleType("konsol.consolidation.doctype")
    ar_pkg = types.ModuleType("konsol.consolidation.doctype.assertion_run")
    ar_pkg.assertion_run = ar

    konsol.close, konsol.fiscal_calendar = close, calendar
    konsol.entity_permissions, konsol.period_status = perms, period_status
    konsol.schema_lifecycle = lifecycle
    konsol.consolidation = consolidation

    mods = {"frappe": frappe, "konsol": konsol, "konsol.close": close,
            "konsol.fiscal_calendar": calendar, "konsol.entity_permissions": perms,
            "konsol.period_status": period_status,
            "konsol.schema_lifecycle": lifecycle,
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
