"""Audit trail API: konsol/close/trail_api.py (konsol#305 T07b; story 10.1;
#297 R-decision "auditors are Viewers with audit trail"; amended 2 Oct by
#305-W2-9).

`get_trail(fiscal_year, fiscal_period)` (GET) reads the period's Close
Events (that period's own plus the year's `fiscal_period == 0` events),
scopes them to the caller's allowed entities (`trail_model.visible`) before
computing the summary, resolves each actor's full name, and returns
datetimes as ISO strings.

Stub-frappe test. `trail_model` and `timefmt` load for real by path (the
same pattern test_close_grid_api.py uses for `REAL_MODELS`); `frappe`,
`konsol.period_status` and `konsol.entity_permissions` are stubbed. The
`get_all`/`_match` stub is copied (not imported) from test_close_grid_api.py.
"""
import importlib.util
import json
import os
import sys
import types
from datetime import datetime

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PY = os.path.join(APP_DIR, "close", "trail_api.py")
TRAIL_MODEL_PY = os.path.join(APP_DIR, "close", "trail_model.py")
TIMEFMT_PY = os.path.join(APP_DIR, "close", "timefmt.py")

TRAIL_ROLES = ("EPM Admin", "EPM Analyst", "EPM User", "System Manager")


def _match(value, cond):
    if isinstance(cond, (list, tuple)):
        op, arg = cond[0], cond[1]
        if op == "in":
            return value in arg
        raise AssertionError("stub: unsupported operator %r" % (op,))
    return value == cond


def _dt(day, hour=9, month=9):
    return datetime(2026, month, day, hour, 0, 0)


def _event(name, kind, at, **kw):
    """A Close Event row as `frappe.get_all` would return it: `at` is a real
    `datetime` (Frappe casts a Datetime field), and `detail` is a JSON
    string (or None), exactly as `close_event_model.detail_json` stores
    it."""
    event = {
        "name": name, "kind": kind, "at": at,
        "fiscal_year": 2026, "fiscal_period": 9,
        "entity": None, "reference_doctype": None, "reference_name": None,
        "actor": "zz-a@example.com", "reason": None, "detail": None,
        "source": "live",
    }
    event.update(kw)
    return event


def _user(name, full_name):
    return {"name": name, "full_name": full_name}


class _Site:
    def __init__(self):
        self.roles = {"EPM User"}
        self.user = "zz-viewer@example.com"
        self.allowed = None
        self.periods = {(2026, 9): {"fiscal_year": 2026, "fiscal_period": 9,
                                    "code": "P09", "status": "Open"}}
        self.events = []
        self.users = []
        self.only_for_calls = []
        self.get_all_calls = {}
        self.period_row_calls = 0
        self.allowed_calls = 0


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

    def get_all(doctype, filters=None, fields=None, order_by=None,
                limit_page_length=None, **k):
        site.get_all_calls[doctype] = site.get_all_calls.get(doctype, 0) + 1
        filters = filters or {}
        if doctype == "Close Event":
            rows = [r for r in site.events
                    if all(_match(r.get(f), c) for f, c in filters.items())]
            return [{f: r.get(f) for f in fields} for r in rows]
        if doctype == "User":
            cond = filters.get("name")
            assert isinstance(cond, (list, tuple)) and cond[0] == "in", filters
            wanted = set(cond[1])
            rows = [u for u in site.users if u["name"] in wanted]
            return [{f: r.get(f) for f in fields} for r in rows]
        raise AssertionError("unexpected get_all(%r)" % (doctype,))

    def forbidden(*a, **k):
        raise AssertionError("get_trail must not write")

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.only_for = only_for
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.get_all = get_all
    frappe.get_doc = forbidden
    frappe.enqueue = forbidden
    frappe.session = types.SimpleNamespace(user=site.user)
    frappe.db = types.SimpleNamespace(set_value=forbidden, commit=forbidden, sql=forbidden)
    frappe.utils = types.SimpleNamespace(get_system_timezone=lambda: "Europe/London")
    return frappe


def _period_status(site, frappe):
    ps = types.ModuleType("konsol.period_status")
    ps.PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})

    def period_row(fiscal_year, fiscal_period):
        site.period_row_calls += 1
        try:
            key = (int(fiscal_year), int(fiscal_period))
        except (TypeError, ValueError):
            raise ps.PeriodNotDeclared(
                "FY%s period %s is not a fiscal period." % (fiscal_year, fiscal_period))
        if key not in site.periods:
            raise ps.PeriodNotDeclared("FY%d has no period %d." % key)
        return dict(site.periods[key])

    ps.period_row = period_row
    return ps


def _entity_permissions(site):
    ep = types.ModuleType("konsol.entity_permissions")

    def allowed_entity_codes(user=None):
        site.allowed_calls += 1
        return site.allowed

    ep.allowed_entity_codes = allowed_entity_codes
    return ep


def _real(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_api(site):
    frappe = _frappe(site)
    trail_model = _real("konsol.close.trail_model", TRAIL_MODEL_PY)
    timefmt = _real("konsol.close.timefmt", TIMEFMT_PY)
    period_status = _period_status(site, frappe)
    entity_permissions = _entity_permissions(site)

    konsol = types.ModuleType("konsol")
    konsol_close = types.ModuleType("konsol.close")
    konsol_close.trail_model = trail_model
    konsol_close.timefmt = timefmt
    konsol.close = konsol_close
    konsol.period_status = period_status
    konsol.entity_permissions = entity_permissions

    mods = {
        "frappe": frappe,
        "konsol": konsol,
        "konsol.close": konsol_close,
        "konsol.close.trail_model": trail_model,
        "konsol.close.timefmt": timefmt,
        "konsol.period_status": period_status,
        "konsol.entity_permissions": entity_permissions,
    }
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("konsol.close.trail_api", API_PY)
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    site.errors = types.SimpleNamespace(PermissionError=frappe.PermissionError,
                                        PeriodNotDeclared=period_status.PeriodNotDeclared)
    return api


def _call(site, *args):
    return _load_api(site).get_trail(*args)


def _raises(fn, exc_name):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        assert type(e).__name__ == exc_name, "%s: %s" % (type(e).__name__, e)
        return str(e)
    raise AssertionError("expected %s" % (exc_name,))


# --- the happy path ------------------------------------------------------------

def test_reads_the_period_plus_the_year_events_newest_first():
    site = _Site()
    site.events = [
        _event("CE-1", "tb_submitted", _dt(10), entity="ZZA"),
        _event("CE-2", "approved", _dt(12)),
        _event("CE-3", "signed_off", _dt(15),
              detail=json.dumps({"signoff_status": "Signed Off", "run_status": "Green"})),
        _event("CE-4", "year_closed", _dt(1), fiscal_period=0),
    ]
    site.users = [_user("zz-a@example.com", "A Accountant")]
    out = _call(site, 2026, 9)
    assert [e["name"] for e in out["events"]] == ["CE-3", "CE-2", "CE-1", "CE-4"]
    assert out["events"][0]["actor_name"] == "A Accountant"
    assert out["events"][0]["actor_missing"] is False
    assert out["events"][0]["at"] == "2026-09-15T09:00:00+01:00"
    assert out["period"] == {"fiscal_year": 2026, "fiscal_period": 9, "code": "P09", "status": "Open"}
    assert out["summary"]["signoff"]["state"] == "signed"
    assert out["summary"]["signoff"]["at"] == "2026-09-15T09:00:00+01:00"
    assert out["hidden"] == 0
    json.dumps(out)  # JSON-safe


# --- failure paths ---------------------------------------------------------------

def test_an_entity_accountant_is_refused():
    site = _Site()
    site.roles = {"Entity Accountant"}
    _raises(lambda: _call(site, 2026, 9), "PermissionError")


def test_an_undeclared_period_raises():
    site = _Site()
    site.periods = {}
    _raises(lambda: _call(site, 2026, 9), "PeriodNotDeclared")


def test_unreadable_detail_raises_naming_the_event():
    site = _Site()
    site.events = [_event("CE-9", "approved", _dt(10), detail="{bad")]
    msg = _raises(lambda: _call(site, 2026, 9), "ValidationError")
    assert "CE-9" in msg


# --- actor resolution --------------------------------------------------------

def test_a_deleted_actor_shows_the_id_and_is_marked_missing():
    site = _Site()
    site.events = [_event("CE-1", "approved", _dt(10),
                          actor="zz-ghost@example.com")]
    site.users = []
    out = _call(site, 2026, 9)
    event = out["events"][0]
    assert event["actor_name"] == "zz-ghost@example.com"
    assert event["actor_missing"] is True


# --- period scoping -----------------------------------------------------------

def test_another_periods_events_are_not_returned():
    site = _Site()
    site.events = [
        _event("CE-1", "approved", _dt(10), fiscal_period=9),
        _event("CE-2", "approved", _dt(10, month=8), fiscal_period=8),
    ]
    out = _call(site, 2026, 9)
    assert [e["name"] for e in out["events"]] == ["CE-1"]


def test_an_empty_period_returns_no_events_and_no_signoff():
    site = _Site()
    out = _call(site, 2026, 9)
    assert out["events"] == []
    assert out["summary"]["signoff"] == {"state": "none"}


# --- entity scope (#305-W2-9) --------------------------------------------------

def test_allowed_entities_hides_the_other_entitys_event():
    site = _Site()
    site.allowed = {"ZZA"}
    site.events = [
        _event("CE-1", "tb_submitted", _dt(10), entity="ZZA"),
        _event("CE-2", "tb_submitted", _dt(11), entity="ZZX"),
        _event("CE-3", "approved", _dt(12)),  # group-level (entity None)
    ]
    out = _call(site, 2026, 9)
    assert [e["name"] for e in out["events"]] == ["CE-3", "CE-1"]
    assert out["hidden"] == 1
    assert "ZZX" not in json.dumps(out)


def test_allowed_none_keeps_every_event():
    site = _Site()
    site.allowed = None
    site.events = [
        _event("CE-1", "tb_submitted", _dt(10), entity="ZZA"),
        _event("CE-2", "tb_submitted", _dt(11), entity="ZZX"),
    ]
    out = _call(site, 2026, 9)
    assert len(out["events"]) == 2
    assert out["hidden"] == 0


def test_allowed_entity_codes_is_called_exactly_once():
    site = _Site()
    site.events = [_event("CE-1", "approved", _dt(10))]
    _call(site, 2026, 9)
    assert site.allowed_calls == 1
