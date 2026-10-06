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
    frappe.response = {}
    site.response = frappe.response
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


def _call(site, *args, **kw):
    return _load_api(site).get_trail(*args, **kw)


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


# --- summary actor resolution (T07c) ------------------------------------------

def test_summary_signoff_by_gets_by_name_like_the_events():
    site = _Site()
    site.events = [
        _event("CE-1", "signed_off", _dt(15), actor="zz-signer@example.com",
              detail=json.dumps({"signoff_status": "Signed Off", "run_status": "Green"})),
    ]
    site.users = [_user("zz-signer@example.com", "Sam Signer")]
    out = _call(site, 2026, 9)
    signoff = out["summary"]["signoff"]
    assert signoff["by"] == "zz-signer@example.com"
    assert signoff["by_name"] == "Sam Signer"
    assert signoff["by_missing"] is False


def test_summary_signoff_signer_deleted_gives_by_missing_and_the_id():
    site = _Site()
    site.events = [
        _event("CE-1", "signed_off", _dt(15), actor="zz-ghost@example.com",
              detail=json.dumps({"signoff_status": "Signed Off", "run_status": "Green"})),
    ]
    site.users = []
    out = _call(site, 2026, 9)
    signoff = out["summary"]["signoff"]
    assert signoff["by"] == "zz-ghost@example.com"
    assert signoff["by_name"] == "zz-ghost@example.com"
    assert signoff["by_missing"] is True


def test_summary_closed_and_locked_by_get_names_and_the_user_read_happens_once():
    site = _Site()
    site.events = [
        _event("CE-1", "signed_off", _dt(15), actor="zz-s@example.com",
              detail=json.dumps({"signoff_status": "Signed Off", "run_status": "Green"})),
        _event("CE-2", "period_closed", _dt(16), actor="zz-c@example.com"),
        _event("CE-3", "period_locked", _dt(17), actor="zz-l@example.com"),
    ]
    site.users = [
        _user("zz-s@example.com", "S Signer"),
        _user("zz-c@example.com", "C Closer"),
        _user("zz-l@example.com", "L Locker"),
    ]
    out = _call(site, 2026, 9)
    assert out["summary"]["closed"]["by_name"] == "C Closer"
    assert out["summary"]["closed"]["by_missing"] is False
    assert out["summary"]["locked"]["by_name"] == "L Locker"
    assert out["summary"]["locked"]["by_missing"] is False
    assert site.get_all_calls["User"] == 1


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


# --- story 10.2: filters and the CSV export ------------------------------------
#
# Events here come from the real producer: built exactly as
# close_event.record() builds one (actor_persona added to detail, reason
# stripped, source "live"), refused by the real close_event_model's
# event_problems if malformed, and stored with its detail_json -- so the
# rows are what the Close Event table holds.

import csv as _csv
import io as _io

CLOSE_EVENT_MODEL_PY = os.path.join(APP_DIR, "close", "close_event_model.py")
_CEM = _real("close_event_model_for_trail_api_test", CLOSE_EVENT_MODEL_PY)


def _produced(name, kind, at, actor, entity=None, reason=None, detail=None,
              fiscal_period=9, persona="close_lead",
              reference_doctype="Trial Balance Submission", reference_name="TB-1"):
    event = {
        "kind": kind, "fiscal_year": 2026, "fiscal_period": fiscal_period,
        "entity": entity, "reference_doctype": reference_doctype,
        "reference_name": reference_name, "actor": actor, "at": at,
        "reason": (reason or "").strip() or None,
        "detail": dict(detail or {}, actor_persona=persona),
        "source": _CEM.LIVE,
    }
    problems = _CEM.event_problems(event)
    assert not problems, problems
    return dict(event, name=name, detail=_CEM.detail_json(event["detail"]))


def _scoped_site():
    """A Viewer scoped to ZZA. ZZX's events and actor must never reach them."""
    site = _Site()
    site.allowed = {"ZZA"}
    site.events = [
        _produced("CE-1", "tb_submitted", _dt(10), "zz-a@example.com", entity="ZZA",
                  detail={"on_behalf": "No"}),
        _produced("CE-2", "tb_submitted", _dt(11), "zz-x@example.com", entity="ZZX",
                  detail={"on_behalf": "No"}),
        _produced("CE-3", "approved", _dt(12), "zz-b@example.com",
                  reference_doctype="Journal Entry", reference_name="JE-1",
                  detail={"preparer": "zz-a@example.com"}),
        _produced("CE-4", "signed_off", _dt(15), "zz-b@example.com",
                  reference_doctype="Assertion Run", reference_name="AR-1",
                  detail={"signoff_status": "Signed Off", "run_status": "Green"}),
        _produced("CE-5", "tb_cancelled", _dt(16, month=9), "zz-x@example.com",
                  entity="ZZX"),
        _produced("CE-6", "year_closed", _dt(1), "zz-b@example.com", fiscal_period=0,
                  reference_doctype="Fiscal Year", reference_name="2026",
                  detail={"via": "year"}),
    ]
    site.users = [_user("zz-a@example.com", "A Accountant"),
                  _user("zz-b@example.com", "B Lead"),
                  _user("zz-x@example.com", "X Hidden")]
    return site


def _export(site, *args, **kw):
    out = _load_api(site).export_trail_csv(*args, **kw)
    assert out is None, "the CSV goes out as frappe.response, not a return value"
    return site.response


def _csv_rows(response):
    rows = list(_csv.reader(_io.StringIO(response["filecontent"])))
    return [dict(zip(rows[0], r)) for r in rows[1:]], rows[0]


def test_get_trail_filters_by_kind_and_keeps_the_summary_on_the_whole_scoped_period():
    site = _scoped_site()
    out = _call(site, 2026, 9, kinds='["approved"]')
    assert [e["name"] for e in out["events"]] == ["CE-3"]
    # the summary describes the period, not the filtered view
    assert out["summary"]["signoff"]["state"] == "signed"
    assert out["total"] == 4  # CE-1, CE-3, CE-4, CE-6: the scoped events
    assert out["hidden"] == 2


def test_get_trail_with_no_filter_shows_every_scoped_event_and_echoes_no_filter():
    site = _scoped_site()
    out = _call(site, 2026, 9)
    assert [e["name"] for e in out["events"]] == ["CE-4", "CE-3", "CE-1", "CE-6"]
    assert out["total"] == 4
    assert out["filters"] == {"kinds": [], "actors": [], "entities": [],
                              "date_from": None, "date_to": None}


def test_get_trail_filters_by_actor_entity_and_date_as_a_get_sends_them():
    site = _scoped_site()
    out = _call(site, 2026, 9, actors='["zz-a@example.com"]', entities='["ZZA"]',
                date_from="2026-09-10", date_to="2026-09-10")
    assert [e["name"] for e in out["events"]] == ["CE-1"]
    assert out["filters"] == {"kinds": [], "actors": ["zz-a@example.com"],
                              "entities": ["ZZA"], "date_from": "2026-09-10",
                              "date_to": "2026-09-10"}


def test_get_trail_options_are_the_scoped_choices_with_actor_names():
    site = _scoped_site()
    out = _call(site, 2026, 9, kinds='["approved"]')
    opts = out["options"]
    assert opts["kinds"] == ["approved", "signed_off", "tb_submitted", "year_closed"]
    assert opts["entities"] == ["(group)", "ZZA"]
    assert opts["actors"] == [
        {"actor": "zz-a@example.com", "actor_name": "A Accountant", "actor_missing": False},
        {"actor": "zz-b@example.com", "actor_name": "B Lead", "actor_missing": False},
    ]
    blob = json.dumps(out)
    assert "ZZX" not in blob and "zz-x@example.com" not in blob and "X Hidden" not in blob


def test_get_trail_refuses_a_bad_filter_naming_it():
    site = _scoped_site()
    msg = _raises(lambda: _call(site, 2026, 9, kinds='["nope"]'), "ValidationError")
    assert "nope" in msg
    msg = _raises(lambda: _call(site, 2026, 9, date_from="2026-09-31"), "ValidationError")
    assert "2026-09-31" in msg


def test_export_is_a_csv_download_named_for_the_period():
    site = _scoped_site()
    response = _export(site, 2026, 9)
    assert response["type"] == "download"
    assert response["filename"] == "audit-trail-FY2026-P09.csv"
    assert response["content_type"] == "text/csv; charset=utf-8"
    rows, header = _csv_rows(response)
    assert header == list(_load_api(site).trail_model.CSV_COLUMNS)


def test_export_rows_are_exactly_the_screens_rows_for_the_same_filters():
    for kw in ({}, {"kinds": '["tb_submitted", "year_closed"]'},
               {"actors": '["zz-b@example.com"]', "date_to": "2026-09-12"},
               {"entities": '["(group)"]'}):
        site = _scoped_site()
        shown = [e["name"] for e in _call(site, 2026, 9, **kw)["events"]]
        site = _scoped_site()
        rows, _header = _csv_rows(_export(site, 2026, 9, **kw))
        assert [r["event"] for r in rows] == shown, kw


def test_export_row_carries_the_stored_values_and_the_resolved_name():
    site = _scoped_site()
    rows, _header = _csv_rows(_export(site, 2026, 9, kinds='["approved"]'))
    assert rows == [{
        "event": "CE-3", "at": "2026-09-12T09:00:00+01:00",
        "fiscal_year": "2026", "fiscal_period": "9", "kind": "approved",
        "entity": "", "actor": "zz-b@example.com", "actor_name": "B Lead",
        "actor_persona": "close_lead", "reference_doctype": "Journal Entry",
        "reference_name": "JE-1", "reason": "", "source": "live",
        "detail": '{"actor_persona": "close_lead", "preparer": "zz-a@example.com"}',
    }]


def test_forge_a_scoped_user_cannot_export_another_entitys_events():
    # Every way of asking for ZZX -- by entity, by its actor, by its kind
    # and day, or with no filter at all -- gives nothing of ZZX's.
    for kw in ({"entities": '["ZZX"]'},
               {"actors": '["zz-x@example.com"]'},
               {"kinds": '["tb_cancelled"]'},
               {"entities": '["ZZX", "ZZA"]'},
               {}):
        site = _scoped_site()
        response = _export(site, 2026, 9, **kw)
        content = response["filecontent"]
        assert "ZZX" not in content, kw
        assert "zz-x@example.com" not in content and "X Hidden" not in content, kw
        rows, _header = _csv_rows(response)
        assert all(r["event"] not in ("CE-2", "CE-5") for r in rows), kw
    site = _scoped_site()
    rows, _header = _csv_rows(_export(site, 2026, 9, entities='["ZZX"]'))
    assert rows == []


def test_export_scopes_through_allowed_entity_codes_exactly_once():
    site = _scoped_site()
    _export(site, 2026, 9)
    assert site.allowed_calls == 1


def test_export_for_an_unscoped_user_has_every_entity():
    site = _scoped_site()
    site.allowed = None
    rows, _header = _csv_rows(_export(site, 2026, 9))
    assert [r["event"] for r in rows] == ["CE-5", "CE-4", "CE-3", "CE-2", "CE-1", "CE-6"]


def test_export_refuses_the_entity_accountant():
    site = _scoped_site()
    site.roles = {"Entity Accountant"}
    _raises(lambda: _export(site, 2026, 9), "PermissionError")
    assert "filecontent" not in site.response


def test_export_refuses_an_undeclared_period_and_a_bad_filter():
    site = _scoped_site()
    site.periods = {}
    _raises(lambda: _export(site, 2026, 9), "PeriodNotDeclared")
    site = _scoped_site()
    _raises(lambda: _export(site, 2026, 9, entities='"ZZA"'), "ValidationError")
    assert "filecontent" not in site.response
