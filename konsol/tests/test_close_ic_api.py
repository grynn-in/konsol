"""Intercompany API: konsol/close/ic_api.py ``get_ic`` (konsol#305 C03;
stories 5.1, 5.2; #305-W3-2, W3-6, W3-7; W3 engineering calls: IC reads fail
visibly, and 0 Published Intercompany Accounts is "not configured — nothing
was checked").

``get_ic(fiscal_year, fiscal_period)`` (GET) returns the period's IC state,
the pairs grouped by consolidation group with their send-back state, the
partnerless rows, the counts, the W3-2 mask for a scoped caller and whether
the caller may send back.

Loaded against a stub frappe (pattern: test_close_rates_api.py ``_Site`` /
``_frappe`` / ``_call``, copied, not imported). ``konsol.close.ch_read`` is a
stub module: its ``rows`` is how ClickHouse is stubbed, so no ClickHouse
client is imported. The real ``ic_model.py``, ``close_policy_model.py`` and
``timefmt.py`` are loaded by path under their dotted names.
"""
import importlib.util
import inspect
import json
import os
import sys
import types
from datetime import date, datetime

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")
API_PY = os.path.join(CLOSE_DIR, "ic_api.py")
SITE_TZ = "Europe/London"

LEAD = "zz-lead@example.com"
ANALYST = "zz-analyst@example.com"
VIEWER = "zz-viewer@example.com"
ENTITY_ACC = "zz-entity@example.com"
GONE = "zz-gone@example.com"


def _load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


IC_MODEL = _load_path("ic_model_for_ic_api_test", os.path.join(CLOSE_DIR, "ic_model.py"))
sys.modules.pop("ic_model_for_ic_api_test", None)


def _period(fy, fp, status="Open"):
    return {"fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
            "period_label": "P%02d" % fp, "period_type": "Regular",
            "start_date": date(fy, fp, 1), "end_date": date(fy, fp, 28),
            "quarter": "Q%d" % ((fp - 1) // 3 + 1), "status": status}


def _pair(group, ea, aa, eb, ab, status, difference="12.5", tolerance="5",
          balance_a="100.0", balance_b="-87.5", diff_account=""):
    """A gold_ic_reconciliation row as FORMAT JSON hands it back: Float64 /
    Decimal values may arrive as strings (E5-P15)."""
    return {"consolidation_group": group, "fiscal_year": 2025, "fiscal_period": 7,
            "entity_a": ea, "account_a": aa, "entity_b": eb, "account_b": ab,
            "basis": "balance", "pair_event": "", "currency_a": "GBP", "currency_b": "EUR",
            "local_a": balance_a, "local_b": balance_b,
            "balance_a": balance_a, "balance_b": balance_b,
            "group_balance_a": balance_a, "group_balance_b": balance_b,
            "share_a": "1", "share_b": "1", "matched_amount": "87.5",
            "difference": difference, "net_balance": difference,
            "residual_a": "12.5", "residual_b": "0",
            "difference_cause": "booking" if status in ("over_tolerance", "within_tolerance")
            else ("fx" if status == "fx_difference" else "none"),
            "ic_difference_account": diff_account, "tolerance": tolerance,
            "match_status": status}


def _unmatched(group, entity, account="1810", amount="42.0"):
    return {"consolidation_group": group, "data_area_id": entity, "main_account": account,
            "counterpart_account": "2810", "unmatched_local_amount": amount,
            "unmatched_amount": amount}


def _event(name, ea, aa, eb, ab, actor=ANALYST, at=None, reason="Our side agrees to INV-5531"):
    detail = {"entity_a": ea, "account_a": aa, "entity_b": eb, "account_b": ab,
              "groups": [], "actor_persona": "Group Accountant"}
    return {"name": name, "kind": "ic_sent_back", "fiscal_year": 2025, "fiscal_period": 7,
            "at": at or datetime(2025, 8, 3, 9, 30, 0), "actor": actor, "reason": reason,
            "detail": json.dumps(detail, sort_keys=True)}


class _Site:
    def __init__(self):
        self.user = ANALYST
        self.roles = {"EPM Analyst"}
        self.periods = [_period(2025, 6, "Closed"), _period(2025, 7, "Open")]
        self.published = 3
        self.ic_table = True
        self.settings = {"intercompany_declaration": ""}
        self.ic_rows = [
            _pair("ROOT", "UK01", "1810", "DE01", "2810", "over_tolerance"),
            _pair("ROOT", "UK01", "1820", "FR01", "2820", "matched", difference="0"),
            _pair("SUB", "DE01", "1830", "FR01", "2830", "within_tolerance", difference="1"),
        ]
        self.unmatched_rows = [_unmatched("ROOT", "FR01")]
        self.ch_error = None
        self.ch_calls = []
        self.events = [_event("CE-1", "UK01", "1810", "DE01", "2810")]
        self.groups = [
            {"consolidation_group": "ROOT", "data_area_id": None, "reporting_currency": "GBP"},
            {"consolidation_group": "SUB", "data_area_id": "", "reporting_currency": "EUR"},
            {"consolidation_group": "ROOT", "data_area_id": "UK01", "reporting_currency": "GBP"},
        ]
        self.users = [{"name": ANALYST, "full_name": "Zz Analyst"},
                      {"name": LEAD, "full_name": "Zz Lead"}]
        self.allowed = None
        self.reads = []
        self.only_for_calls = []
        self.recorded = []  # close_event.record calls (send_back, C04)


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

    def get_all(doctype, filters=None, fields=None, order_by=None, limit=None,
                limit_page_length=None, pluck=None, **k):
        site.reads.append(("get_all", doctype))
        if doctype == "Close Event":
            rows = [r for r in site.events if _match(r, filters)]
        elif doctype == "Consolidation Group":
            rows = [r for r in site.groups if _match(r, filters)]
        elif doctype == "User":
            rows = [r for r in site.users if _match(r, filters)]
        else:
            raise AssertionError("unexpected get_all on %s" % doctype)
        if pluck:
            return [r.get(pluck) for r in rows]
        return [{f: r.get(f) for f in fields} for r in rows]

    def count(doctype, filters=None, **k):
        site.reads.append(("count", doctype))
        assert doctype == "Intercompany Account", doctype
        assert filters == {"status": "Published"}, filters
        return site.published

    def table_exists(doctype, **k):
        site.reads.append(("table_exists", doctype))
        assert doctype == "Intercompany Account", doctype
        return site.ic_table

    def get_single_value(doctype, field, **k):
        site.reads.append(("single", doctype, field))
        assert (doctype, field) == ("Close Settings", "intercompany_declaration"), (doctype, field)
        return site.settings.get("intercompany_declaration")

    def sql(*a, **k):
        site.reads.append(("sql", a[0] if a else None))
        raise AssertionError("get_ic reads no raw SQL")

    def get_value(*a, **k):
        site.reads.append(("get_value", a[0] if a else None))
        raise AssertionError("get_ic reads no get_value")

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.only_for = only_for
    frappe.whitelist = whitelist
    frappe.get_all = get_all
    frappe.get_roles = lambda user=None: sorted(site.roles)
    frappe.flags = {}
    frappe.db = types.SimpleNamespace(count=count, table_exists=table_exists,
                                      get_single_value=get_single_value, sql=sql,
                                      get_value=get_value)
    frappe.session = types.SimpleNamespace(user=site.user)
    frappe.utils = types.SimpleNamespace(get_system_timezone=lambda: SITE_TZ)
    return frappe


def _ch_read(site):
    ch = types.ModuleType("konsol.close.ch_read")

    def rows(sql, params=None):
        site.ch_calls.append((sql, dict(params or {})))
        if site.ch_error is not None:
            raise site.ch_error
        if "gold_ic_reconciliation" in sql:
            keys = ("ea", "aa", "eb", "ab")
            if params and any(k in params for k in keys):
                # send_back's read binds the four pair keys (C04)
                return [dict(r) for r in site.ic_rows
                        if (r["entity_a"], r["account_a"], r["entity_b"], r["account_b"])
                        == tuple(params[k] for k in keys)]
            return [dict(r) for r in site.ic_rows]
        if "gold_ic_unmatched" in sql:
            return [dict(r) for r in site.unmatched_rows]
        raise AssertionError("unexpected ClickHouse read: %s" % sql)

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
        site.recorded.append({"kind": kind, "fiscal_year": fiscal_year,
                              "fiscal_period": fiscal_period,
                              "reference_doctype": reference_doctype,
                              "reference_name": reference_name, "reason": reason,
                              "detail": detail, "entity": entity})
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
    ch_read = _ch_read(site)
    entity_permissions = types.ModuleType("konsol.entity_permissions")
    entity_permissions.allowed_entity_codes = lambda user=None: site.allowed
    konsol.close = close
    konsol.fiscal_calendar = fiscal_calendar
    konsol.entity_permissions = entity_permissions
    close.ch_read = ch_read
    close_event = _close_event(site)
    close.close_event = close_event
    names = ["frappe", "konsol", "konsol.close", "konsol.fiscal_calendar",
             "konsol.entity_permissions", "konsol.close.ch_read", "konsol.close.close_event",
             "konsol.close.ic_model",
             "konsol.close.close_policy_model", "konsol.close.timefmt",
             "close_ic_api_under_test"]
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({"frappe": frappe, "konsol": konsol, "konsol.close": close,
                        "konsol.fiscal_calendar": fiscal_calendar,
                        "konsol.entity_permissions": entity_permissions,
                        "konsol.close.ch_read": ch_read,
                        "konsol.close.close_event": close_event})
    try:
        close.ic_model = _load_path("konsol.close.ic_model", os.path.join(CLOSE_DIR, "ic_model.py"))
        close.close_policy_model = _load_path(
            "konsol.close.close_policy_model", os.path.join(CLOSE_DIR, "close_policy_model.py"))
        close.timefmt = _load_path("konsol.close.timefmt", os.path.join(CLOSE_DIR, "timefmt.py"))
        api = _load_path("close_ic_api_under_test", API_PY)
        return run(api)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _call(site, fy=2025, fp=7):
    result = _invoke(site, lambda api: api.get_ic(fy, fp))
    json.dumps(result)  # JSON-safe
    return result


def _call_raises(site, fy=2025, fp=7):
    with pytest.raises(Exception) as info:
        _call(site, fy, fp)
    return info.value


def _values(obj):
    """Every scalar anywhere in a nested result (keys included)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _values(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _values(v)
    else:
        yield obj


def _all_pairs(result):
    return [p for g in result["groups"] for p in g["pairs"]]


def _mariadb_reads(site):
    return [r for r in site.reads if r[0] in ("get_all", "count", "table_exists", "get_value",
                                              "sql", "single")]


# --- not configured / not applicable: nothing is read from the warehouse ---

def test_zero_published_is_not_configured_and_reads_no_warehouse():
    """Failure path: 0 Published Intercompany Accounts (live today) never
    reads as reconciled, and ClickHouse is never asked."""
    site = _Site()
    site.published = 0
    out = _call(site)
    assert out["state"] == "not_configured"
    assert out["message"] == IC_MODEL.NOT_CONFIGURED
    assert out["help"] == IC_MODEL.SETUP_HELP
    assert out["counts"] is None
    assert out["groups"] == []
    assert out["unmatched"] == []
    assert out["published"] == 0
    assert out["declared_none"] is False
    assert out["can_send_back"] is False
    assert site.ch_calls == []
    assert "reconciled" not in [v for v in _values(out) if isinstance(v, str)]


def test_missing_intercompany_table_counts_zero_and_reads_no_warehouse():
    """Failure path: a site the Intercompany Account doctype has not reached
    (hot-copied before migrate) is not configured, not an error."""
    site = _Site()
    site.ic_table = False
    out = _call(site)
    assert out["state"] == "not_configured"
    assert out["published"] == 0
    assert ("count", "Intercompany Account") not in site.reads
    assert site.ch_calls == []


def test_declared_none_with_zero_published_is_not_applicable():
    """Failure path — not applicable on the IC screen (W3-7)."""
    site = _Site()
    site.published = 0
    site.settings["intercompany_declaration"] = "None in this group"
    out = _call(site)
    assert out["state"] == "not_applicable"
    assert out["message"] == IC_MODEL.NOT_APPLICABLE
    assert out["help"] is None
    assert out["counts"] is None
    assert out["groups"] == [] and out["unmatched"] == []
    assert out["can_send_back"] is False
    assert out["declared_none"] is True
    assert site.ch_calls == []
    assert "reconciled" not in [v for v in _values(out) if isinstance(v, str)]


def test_declared_none_with_published_is_an_error_and_reads_no_warehouse():
    """Failure path: the declaration with 2 Published shows, never "not
    applicable", and still reads nothing from ClickHouse."""
    site = _Site()
    site.published = 2
    site.settings["intercompany_declaration"] = "None in this group"
    out = _call(site)
    assert out["state"] == "error"
    assert "2 Intercompany Account(s)" in out["message"]
    assert out["groups"] == [] and out["counts"] is None
    assert out["can_send_back"] is False
    assert site.ch_calls == []


# --- configured and checked ---

def test_configured_checked_groups_pairs_and_sent_back_state():
    site = _Site()
    out = _call(site)
    assert out["state"] == "checked"
    assert out["message"] is None
    assert out["help"] is None
    assert out["published"] == 3
    assert out["declared_none"] is False
    assert out["period"] == {"fiscal_year": 2025, "fiscal_period": 7, "period_code": "P07",
                             "status": "Open"}
    assert [g["consolidation_group"] for g in out["groups"]] == ["ROOT", "SUB"]
    root = out["groups"][0]
    assert root["reporting_currency"] == "GBP"
    assert root["tolerance"] == 5.0
    assert root["tolerance_declared"] is True
    first = root["pairs"][0]
    assert (first["entity_a"], first["account_a"]) == ("UK01", "1810")
    assert first["match_status"] == "over_tolerance"
    assert first["can_send_back"] is True
    assert first["sent_back"]["reason"] == "Our side agrees to INV-5531"
    assert first["sent_back"]["by"] == ANALYST
    assert first["sent_back"]["by_name"] == "Zz Analyst"
    assert first["sent_back"]["at"] == "2025-08-03T09:30:00+01:00"
    assert first["difference"] == 12.5 and first["balance_b"] == -87.5
    assert root["pairs"][1]["sent_back"] is None
    assert out["groups"][1]["reporting_currency"] == "EUR"
    assert out["counts"] == {"pairs": 3, "matched": 1, "within_tolerance": 1,
                             "fx_difference": 0, "over_tolerance": 1, "unmatched": 1}
    assert out["unmatched"][0]["unmatched_amount"] == 42.0
    assert out["unmatched"][0]["unmatched_local_amount"] == 42.0
    assert out["hidden"] == {"pairs": 0, "unmatched": 0}
    assert out["can_send_back"] is True
    assert len(site.ch_calls) == 2
    for sql, params in site.ch_calls:
        assert params == {"fy": 2025, "fp": 7}
        assert "{fy:UInt16}" in sql and "{fp:UInt16}" in sql


def test_undeclared_tolerance_group_never_offers_send_back():
    """Failure path (W3-6, W3-P1): a group whose tolerance is 0 shows its
    over-tolerance pair, but the pair cannot be sent back."""
    site = _Site()
    site.ic_rows = [_pair("ROOT", "UK01", "1810", "DE01", "2810", "over_tolerance",
                          tolerance="0")]
    out = _call(site)
    root = out["groups"][0]
    assert root["tolerance_declared"] is False
    assert root["pairs"][0]["can_send_back"] is False


def test_send_backs_are_read_for_the_period_only():
    site = _Site()
    site.events.append(dict(_event("CE-OLD", "UK01", "1820", "FR01", "2820"), fiscal_period=6))
    out = _call(site)
    by_key = {(p["entity_a"], p["account_a"]): p for p in _all_pairs(out)}
    assert by_key[("UK01", "1820")]["sent_back"] is None


# --- warehouse failures: shown, never an empty list ---

def test_warehouse_error_is_shown_not_an_empty_reconciliation():
    """Failure path — warehouse down."""
    site = _Site()
    site.ch_error = RuntimeError("boom")
    out = _call(site)
    assert out["state"] == "error"
    assert "RuntimeError" in out["message"]
    assert out["groups"] == [] and out["unmatched"] == []
    assert out["counts"] is None
    assert out["can_send_back"] is False


def test_unbuilt_tables_are_not_built():
    site = _Site()
    site.ch_error = RuntimeError("(UNKNOWN_TABLE)")
    out = _call(site)
    assert out["state"] == "not_built"
    assert out["message"] == IC_MODEL.NOT_BUILT
    assert out["groups"] == [] and out["counts"] is None


# --- W3-2 scope ---

def test_scoped_caller_sees_own_side_and_partner_code_only():
    """Failure path — W3-2: the partner's amounts are blanked; a pair with
    neither side allowed, and an unmatched row outside scope, are hidden."""
    site = _Site()
    site.allowed = {"UK01"}
    site.ic_rows.append(_pair("ROOT", "DE01", "1840", "FR01", "2840", "over_tolerance"))
    out = _call(site)
    pairs = _all_pairs(out)
    keys = [(p["entity_a"], p["entity_b"]) for p in pairs]
    assert ("DE01", "FR01") not in keys
    uk_de = next(p for p in pairs if (p["entity_a"], p["entity_b"]) == ("UK01", "DE01"))
    assert uk_de["balance_b"] is None and uk_de["local_b"] is None
    assert uk_de["masked_b"] is True and uk_de["masked_a"] is False
    assert uk_de["difference"] == 12.5 and uk_de["entity_b"] == "DE01"
    assert out["hidden"]["pairs"] == 2  # DE01↔FR01 (SUB) and DE01↔FR01 (ROOT)
    assert out["unmatched"] == []
    assert out["hidden"]["unmatched"] == 1
    assert out["counts"]["pairs"] == len(pairs)
    assert out["counts"]["unmatched"] == 0


def test_sent_back_entry_only_on_a_shown_pair():
    site = _Site()
    site.allowed = {"FR01"}
    out = _call(site)
    assert all((p["entity_a"], p["account_a"]) != ("UK01", "1810") for p in _all_pairs(out))
    assert "Our side agrees to INV-5531" not in [v for v in _values(out) if isinstance(v, str)]


# --- roles and periods ---

def test_viewer_reads_but_cannot_send_back():
    site = _Site()
    site.user, site.roles = VIEWER, {"EPM User"}
    out = _call(site)
    assert out["state"] == "checked"
    assert out["can_send_back"] is False


def test_close_lead_may_send_back():
    site = _Site()
    site.user, site.roles = LEAD, {"EPM Admin"}
    assert _call(site)["can_send_back"] is True


def test_entity_accountant_is_refused():
    """Failure path: the Entity Accountant reads fix items through My work,
    never this screen."""
    site = _Site()
    site.user, site.roles = ENTITY_ACC, {"Entity Accountant"}
    err = _call_raises(site)
    assert "Not permitted" in str(err)
    assert site.only_for_calls[0] == ("EPM Admin", "EPM Analyst", "EPM User", "System Manager")
    assert site.ch_calls == []


def test_closed_period_cannot_send_back():
    site = _Site()
    out = _call(site, 2025, 6)
    assert out["period"]["status"] == "Closed"
    assert out["can_send_back"] is False


def test_undeclared_period_is_refused():
    """Failure path."""
    site = _Site()
    err = _call_raises(site, 2025, 9)
    assert "FY2025 P09 is not declared" in str(err)
    assert site.ch_calls == []


def test_non_integer_period_is_refused():
    """Failure path."""
    site = _Site()
    err = _call_raises(site, "x", 7)
    assert "whole numbers" in str(err)


def test_sent_back_actor_without_user_shows_the_id():
    site = _Site()
    site.events = [_event("CE-1", "UK01", "1810", "DE01", "2810", actor=GONE)]
    out = _call(site)
    first = out["groups"][0]["pairs"][0]
    assert first["sent_back"]["by"] == GONE
    assert first["sent_back"]["by_name"] == GONE


def test_no_events_reads_no_users():
    site = _Site()
    site.events = []
    _call(site)
    assert ("get_all", "User") not in site.reads


# --- query count ---

def test_query_count_is_constant_in_pairs():
    small = _Site()
    small.ic_rows = small.ic_rows[:1]
    _call(small)
    big = _Site()
    big.ic_rows = [_pair("ROOT", "UK0%d" % i, "18%d0" % i, "DE0%d" % i, "28%d0" % i,
                         "over_tolerance") for i in range(6)]
    big.events = [_event("CE-%d" % i, "UK0%d" % i, "18%d0" % i, "DE0%d" % i, "28%d0" % i,
                         actor=LEAD if i % 2 else ANALYST) for i in range(6)]
    _call(big)
    assert len(_mariadb_reads(small)) == len(_mariadb_reads(big))
    assert len(small.ch_calls) == 2 and len(big.ch_calls) == 2


def test_configured_mariadb_reads_are_named():
    site = _Site()
    _call(site)
    assert sorted(set(_mariadb_reads(site))) == sorted({
        ("sql", "fiscal_period_rows"), ("table_exists", "Intercompany Account"),
        ("count", "Intercompany Account"),
        ("single", "Close Settings", "intercompany_declaration"),
        ("get_all", "Close Event"), ("get_all", "Consolidation Group"), ("get_all", "User")})
    assert len(_mariadb_reads(site)) == 7


def test_group_node_filter_reads_only_group_rows():
    """The group node is the Consolidation Group row with no entity."""
    site = _Site()
    site.groups.append({"consolidation_group": "SUB", "data_area_id": "DE01",
                        "reporting_currency": "USD"})
    out = _call(site)
    assert out["groups"][1]["reporting_currency"] == "EUR"


# --- send_back (C04): an ic_sent_back Close Event for an over-tolerance pair ---

SEND_BACK_PARAMS = ["fiscal_year", "fiscal_period", "entity_a", "account_a", "entity_b",
                    "account_b", "reason"]
PAIR = {"entity_a": "UK01", "account_a": "1810", "entity_b": "DE01", "account_b": "2810"}


def _send(site, fy=2025, fp=7, reason="Our side agrees to INV-5531", **pair):
    args = dict(PAIR, **pair)

    def run(api):
        return api.send_back(fy, fp, args["entity_a"], args["account_a"], args["entity_b"],
                             args["account_b"], reason)

    result = _invoke(site, run)
    json.dumps(result)  # JSON-safe
    return result


def _send_refused(site, **kw):
    """A refusal writes nothing: no record call, whatever the reason."""
    with pytest.raises(Exception) as info:
        _send(site, **kw)
    assert site.recorded == []
    return str(info.value)


def _forge_problems(fn):
    """Why a request could carry a forged key into ``fn``: Frappe drops any
    request key the signature does not name, unless it takes **kwargs."""
    params = inspect.signature(fn).parameters
    problems = []
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        problems.append("takes **kwargs")
    if list(params) != SEND_BACK_PARAMS:
        problems.append("parameters are %s" % list(params))
    return problems


def test_send_back_records_one_event_with_the_servers_amounts():
    site = _Site()
    out = _send(site)
    assert out == {"event": "CE-NEW-1", "fix_items_for": ["UK01", "DE01"]}
    assert len(site.recorded) == 1
    ev = site.recorded[0]
    assert ev["kind"] == "ic_sent_back"
    assert (ev["fiscal_year"], ev["fiscal_period"]) == (2025, 7)
    assert ev["entity"] == "UK01"
    assert ev["reason"] == "Our side agrees to INV-5531"
    assert ev["reference_doctype"] is None and ev["reference_name"] is None
    assert ev["detail"] == {"entity_a": "UK01", "account_a": "1810", "entity_b": "DE01",
                            "account_b": "2810",
                            "groups": [{"consolidation_group": "ROOT", "difference": 12.5,
                                        "tolerance": 5.0, "match_status": "over_tolerance"}]}
    # one warehouse read, the period and the four keys bound as parameters
    assert len(site.ch_calls) == 1
    sql, params = site.ch_calls[0]
    assert "gold_ic_reconciliation" in sql
    assert params == {"fy": 2025, "fp": 7, "ea": "UK01", "aa": "1810", "eb": "DE01",
                      "ab": "2810"}
    for name in ("{fy:UInt16}", "{fp:UInt16}", "{ea:String}", "{aa:String}", "{eb:String}",
                 "{ab:String}"):
        assert name in sql
    assert "UK01" not in sql and "2810" not in sql  # bound, never interpolated


def test_send_back_lists_every_group_row_of_the_pair():
    """E5-P1: the event records each group's difference at send time."""
    site = _Site()
    site.ic_rows.append(_pair("SUB", "UK01", "1810", "DE01", "2810", "within_tolerance",
                              difference="3", tolerance="4"))
    _send(site)
    groups = site.recorded[0]["detail"]["groups"]
    assert groups == [
        {"consolidation_group": "ROOT", "difference": 12.5, "tolerance": 5.0,
         "match_status": "over_tolerance"},
        {"consolidation_group": "SUB", "difference": 3.0, "tolerance": 4.0,
         "match_status": "within_tolerance"}]


def test_send_back_signature_takes_no_forged_keys():
    """Forge: the parameters are exactly the seven named and there is no
    **kwargs, so a forged actor, at, entity, difference, detail, kind or
    source never reaches the function (get_newargs drops it)."""
    site = _Site()
    fn = _invoke(site, lambda api: api.send_back)
    assert _forge_problems(fn) == []


def test_forge_check_catches_kwargs_and_extra_parameters():
    """Failure path: the forge check can fail."""
    def with_kwargs(fiscal_year, fiscal_period, entity_a, account_a, entity_b, account_b,
                    reason, **kwargs):
        pass

    def with_difference(fiscal_year, fiscal_period, entity_a, account_a, entity_b, account_b,
                        reason, difference=None):
        pass

    def with_actor(fiscal_year, fiscal_period, entity_a, account_a, entity_b, account_b,
                   reason, actor=None):
        pass

    assert "takes **kwargs" in _forge_problems(with_kwargs)
    assert _forge_problems(with_difference)
    assert _forge_problems(with_actor)


def test_send_back_blank_reason_is_refused():
    """Failure path (E5-P6)."""
    site = _Site()
    msg = _send_refused(site, reason="   ")
    assert "Say why the difference is sent back: the entities read this reason." in msg
    assert site.ch_calls == []


def test_send_back_closed_period_is_refused():
    """Failure path."""
    site = _Site()
    msg = _send_refused(site, fp=6)
    assert "FY2025 P06 is Closed: send a difference back only in an Open period." in msg
    assert site.ch_calls == []


def test_send_back_undeclared_period_is_refused():
    """Failure path."""
    site = _Site()
    msg = _send_refused(site, fp=9)
    assert "FY2025 P09 is not declared" in msg
    assert site.ch_calls == []


def test_send_back_not_configured_is_refused_without_a_warehouse_read():
    """Failure path: 0 Published (live today)."""
    site = _Site()
    site.published = 0
    msg = _send_refused(site)
    assert IC_MODEL.NOT_CONFIGURED in msg
    assert "Nothing can be sent back." in msg
    assert site.ch_calls == []


def test_send_back_declared_none_is_refused_without_a_warehouse_read():
    """Failure path (W3-7)."""
    site = _Site()
    site.published = 0
    site.settings["intercompany_declaration"] = "None in this group"
    msg = _send_refused(site)
    assert "Close Settings declares no intercompany in this group: nothing can be sent back." \
        in msg
    assert site.ch_calls == []


def test_send_back_scoped_caller_with_neither_entity_is_refused():
    """Failure path (E5-P7)."""
    site = _Site()
    site.allowed = {"FR01"}
    msg = _send_refused(site)
    assert "You can see neither entity of this pair." in msg
    assert site.ch_calls == []


def test_send_back_scoped_caller_with_side_b_only_may_send():
    site = _Site()
    site.allowed = {"DE01"}
    _send(site)
    assert len(site.recorded) == 1
    assert site.recorded[0]["entity"] == "UK01"  # side A (E5-P8)


def test_send_back_warehouse_error_is_refused():
    """Failure path."""
    site = _Site()
    site.ch_error = RuntimeError("boom")
    msg = _send_refused(site)
    assert "could not be checked" in msg and "RuntimeError" in msg
    assert "Nothing was sent back." in msg


def test_send_back_unbuilt_tables_are_refused():
    """Failure path."""
    site = _Site()
    site.ch_error = RuntimeError("(UNKNOWN_TABLE)")
    msg = _send_refused(site)
    assert IC_MODEL.NOT_BUILT in msg and "Nothing was sent back." in msg


def test_send_back_pair_not_in_the_build_is_refused():
    """Failure path."""
    site = _Site()
    msg = _send_refused(site, account_b="9999")
    assert "UK01 1810 ↔ DE01 9999 is not an intercompany pair in FY2025 P07's last build." \
        in msg


def test_send_back_within_tolerance_pair_is_refused():
    """Failure path (E5-P2)."""
    site = _Site()
    site.ic_rows = [_pair("ROOT", "UK01", "1810", "DE01", "2810", "within_tolerance",
                          difference="1")]
    msg = _send_refused(site)
    assert "Only a difference over tolerance can be sent back; this pair is within_tolerance." \
        in msg


def test_send_back_fx_difference_pair_is_refused():
    """Failure path (E5-P2)."""
    site = _Site()
    site.ic_rows = [_pair("ROOT", "UK01", "1810", "DE01", "2810", "fx_difference")]
    msg = _send_refused(site)
    assert "Only a difference over tolerance can be sent back; this pair is fx_difference." \
        in msg


def test_send_back_movement_fx_pair_is_refused():
    """Failure path (W3-5): a movement pair stays fx_difference and is refused."""
    site = _Site()
    row = _pair("ROOT", "UK01", "1810", "DE01", "2810", "fx_difference")
    row["basis"] = "movement"
    site.ic_rows = [row]
    msg = _send_refused(site)
    assert "Only a difference over tolerance can be sent back" in msg


def test_send_back_cross_currency_balance_pair_over_tolerance_is_sent():
    """W3-5: dbt judges a cross-currency balance pair against the tolerance
    (V31); over tolerance it is sent like any other."""
    site = _Site()
    row = _pair("ROOT", "UK01", "1810", "DE01", "2810", "over_tolerance", tolerance="10")
    row["difference_cause"], row["basis"] = "fx", "balance"
    site.ic_rows = [row]
    _send(site)
    assert len(site.recorded) == 1


def test_send_back_undeclared_tolerance_is_refused():
    """Failure path (W3-6, W3-P1): the only over-tolerance row is in a group
    whose tolerance is 0."""
    site = _Site()
    site.ic_rows = [_pair("ROOT", "UK01", "1810", "DE01", "2810", "over_tolerance",
                          tolerance="0")]
    msg = _send_refused(site)
    assert ("ROOT has not declared an intercompany difference tolerance (0 is undeclared): "
            "declare it on the Consolidation Group before sending a difference back.") in msg


def test_send_back_undeclared_tolerance_names_every_group():
    """Failure path: two undeclared groups are both named."""
    site = _Site()
    site.ic_rows = [
        _pair("ROOT", "UK01", "1810", "DE01", "2810", "over_tolerance", tolerance="0"),
        _pair("SUB", "UK01", "1810", "DE01", "2810", "over_tolerance", tolerance="0")]
    msg = _send_refused(site)
    assert "ROOT, SUB have not declared" in msg and "0 is undeclared" in msg


def test_send_back_one_declared_over_tolerance_group_is_enough():
    site = _Site()
    site.ic_rows = [
        _pair("ROOT", "UK01", "1810", "DE01", "2810", "over_tolerance", tolerance="5"),
        _pair("SUB", "UK01", "1810", "DE01", "2810", "over_tolerance", tolerance="0")]
    _send(site)
    groups = site.recorded[0]["detail"]["groups"]
    assert [g["consolidation_group"] for g in groups] == ["ROOT", "SUB"]


def test_send_back_viewer_is_refused():
    """Failure path — roles."""
    site = _Site()
    site.user, site.roles = VIEWER, {"EPM User"}
    msg = _send_refused(site)
    assert "Not permitted" in msg
    assert site.only_for_calls[0] == ("EPM Analyst", "EPM Admin", "System Manager")
    assert site.ch_calls == []


def test_send_back_entity_accountant_is_refused():
    """Failure path — roles."""
    site = _Site()
    site.user, site.roles = ENTITY_ACC, {"Entity Accountant"}
    msg = _send_refused(site)
    assert "Not permitted" in msg
    assert site.ch_calls == []


def test_send_back_close_lead_may_send():
    site = _Site()
    site.user, site.roles = LEAD, {"EPM Admin"}
    _send(site)
    assert len(site.recorded) == 1


def test_send_back_twice_writes_two_events():
    site = _Site()
    _send(site)
    _send(site, reason="Still open")
    assert [e["reason"] for e in site.recorded] == ["Our side agrees to INV-5531", "Still open"]


# --- C05: setup_gap / tolerance_gap / open_fixes / signoff_summary ---
# Not whitelisted, not endpoints: called by My work (C08) and the sign-off
# summary (C21). The stub site's defaults (3 Published, no declaration) are
# "configured"; each test sets up its own gap.

def _h(site, fn_name, *args):
    """Call a C05 helper by name against the stub site, JSON-safety checked
    where the result is JSON-safe (dicts of plain values)."""
    return _invoke(site, lambda api: getattr(api, fn_name)(*args))


def test_setup_gap_not_configured_names_the_setup_help():
    """Failure path — not configured: 0 Published (live today)."""
    site = _Site()
    site.published = 0
    assert _h(site, "setup_gap") == IC_MODEL.SETUP_HELP


def test_setup_gap_configured_is_none():
    site = _Site()
    assert _h(site, "setup_gap") is None


def test_setup_gap_declared_none_is_none_even_with_zero_published():
    """Failure path (W3-7): "none in this group" is not the "not configured"
    gap — it is not a gap at all."""
    site = _Site()
    site.published = 0
    site.settings["intercompany_declaration"] = "None in this group"
    assert _h(site, "setup_gap") is None


def test_setup_gap_declared_none_with_published_still_none():
    """W3-6/W3-7 only define setup_gap by declared_none/published for the
    "no intercompany" gap; the conflict itself is ic_model.state's job
    (get_ic, signoff_summary), not setup_gap's."""
    site = _Site()
    site.settings["intercompany_declaration"] = "None in this group"
    assert _h(site, "setup_gap") is None


# --- tolerance_gap (W3-6) ---

def test_tolerance_gap_not_configured_is_none_and_reads_no_groups():
    """Failure path: 0 Published → None, no Consolidation Group read."""
    site = _Site()
    site.published = 0
    assert _h(site, "tolerance_gap") is None
    assert ("get_all", "Consolidation Group") not in site.reads


def test_tolerance_gap_declared_none_is_none_and_reads_no_groups():
    site = _Site()
    site.settings["intercompany_declaration"] = "None in this group"
    assert _h(site, "tolerance_gap") is None
    assert ("get_all", "Consolidation Group") not in site.reads


def test_tolerance_gap_undeclared_group_is_named():
    """Failure path (W3-6): 1 Published, a group node with tolerance 0."""
    site = _Site()
    site.groups = [{"consolidation_group": "ROOT", "data_area_id": None,
                    "reporting_currency": "GBP", "ic_difference_tolerance": 0}]
    gap = _h(site, "tolerance_gap")
    assert gap["code"] == IC_MODEL.TOLERANCE_UNDECLARED
    assert gap["groups"] == ["ROOT"]


def test_tolerance_gap_every_group_declared_is_none():
    site = _Site()
    site.groups = [{"consolidation_group": "ROOT", "data_area_id": None,
                    "reporting_currency": "GBP", "ic_difference_tolerance": 0.01}]
    assert _h(site, "tolerance_gap") is None


def test_tolerance_gap_reads_group_nodes_only():
    """The same blank-data_area_id filter as the currencies read."""
    site = _Site()
    site.groups = [
        {"consolidation_group": "ROOT", "data_area_id": None, "reporting_currency": "GBP",
         "ic_difference_tolerance": 0},
        {"consolidation_group": "ROOT", "data_area_id": "UK01", "reporting_currency": "GBP",
         "ic_difference_tolerance": 5},
    ]
    gap = _h(site, "tolerance_gap")
    assert gap["groups"] == ["ROOT"]


# --- open_fixes (D2-7, E5-P2, E5-P4) ---

def test_open_fixes_not_configured_is_empty_and_reads_no_events():
    """Failure path: 0 Published → {} with no Close Event read."""
    site = _Site()
    site.published = 0
    assert _h(site, "open_fixes", [(2025, 7)]) == {}
    assert ("get_all", "Close Event") not in site.reads
    assert site.ch_calls == []


def test_open_fixes_declared_none_is_empty():
    """Failure path (W3-7): send_back refuses every event once declared."""
    site = _Site()
    site.settings["intercompany_declaration"] = "None in this group"
    assert _h(site, "open_fixes", [(2025, 7)]) == {}
    assert ("get_all", "Close Event") not in site.reads


def test_open_fixes_over_tolerance_pair_is_open_only_one_clickhouse_call():
    """P08 has no sent-back events, so only P07 is read from ClickHouse."""
    site = _Site()
    out = _h(site, "open_fixes", [(2025, 7), (2025, 8)])
    assert list(out.keys()) == [(2025, 7)]
    fixes = out[(2025, 7)]
    assert len(fixes) == 1
    assert fixes[0]["state"] == "over_tolerance"
    assert fixes[0]["entity_a"] == "UK01" and fixes[0]["account_a"] == "1810"
    assert len(site.ch_calls) == 1


def test_open_fixes_warehouse_down_is_cannot_check_never_dropped():
    """Failure path: a warehouse failure never drops the fix item."""
    site = _Site()
    site.ch_error = RuntimeError("boom")
    out = _h(site, "open_fixes", [(2025, 7)])
    fixes = out[(2025, 7)]
    assert len(fixes) == 1
    assert fixes[0]["state"] == "cannot_check"
    assert "RuntimeError" in fixes[0]["error"]


def test_open_fixes_cleared_pair_is_omitted():
    """Failure path — cleared: the sent-back pair is now within tolerance, so
    the key carries no open fix and is left out entirely."""
    site = _Site()
    site.ic_rows = [_pair("ROOT", "UK01", "1810", "DE01", "2810", "within_tolerance",
                          difference="1")]
    assert _h(site, "open_fixes", [(2025, 7)]) == {}


def test_open_fixes_no_keys_reads_nothing():
    site = _Site()
    assert _h(site, "open_fixes", []) == {}
    assert site.reads == []


# --- signoff_summary (E5-P13: counts only, unscoped) ---

def test_signoff_summary_not_configured():
    """Failure path: 0 Published (live today)."""
    site = _Site()
    site.published = 0
    out = _h(site, "signoff_summary", 2025, 7)
    assert out["state"] == "not_configured"
    assert out["counts"] is None
    assert out["sent_back_open"] is None
    assert site.ch_calls == []


def test_signoff_summary_declared_none_is_not_applicable_no_clickhouse_read():
    """Failure path (W3-7)."""
    site = _Site()
    site.published = 0
    site.settings["intercompany_declaration"] = "None in this group"
    out = _h(site, "signoff_summary", 2025, 7)
    assert out["state"] == "not_applicable"
    assert out["counts"] is None
    assert site.ch_calls == []


def test_signoff_summary_declared_none_with_published_is_an_error():
    """Failure path (C01 conflict state)."""
    site = _Site()
    site.settings["intercompany_declaration"] = "None in this group"
    out = _h(site, "signoff_summary", 2025, 7)
    assert out["state"] == "error"


def test_signoff_summary_configured_counts_every_pair_unscoped():
    """No mask applied: a scoped caller still counts every pair (E5-P13)."""
    site = _Site()
    site.allowed = {"FR01"}  # would hide most pairs on the IC screen (W3-2)
    out = _h(site, "signoff_summary", 2025, 7)
    assert out["state"] == "checked"
    assert out["counts"]["pairs"] == 3
    assert out["sent_back_open"] == 1


def test_signoff_summary_warehouse_down_is_error_never_raises():
    """Failure path."""
    site = _Site()
    site.ch_error = RuntimeError("boom")
    out = _h(site, "signoff_summary", 2025, 7)
    assert out["state"] == "error"
    assert out["counts"] is None
    assert out["sent_back_open"] is None


def test_signoff_summary_unbuilt_tables_never_raises():
    """Failure path."""
    site = _Site()
    site.ch_error = RuntimeError("(UNKNOWN_TABLE)")
    out = _h(site, "signoff_summary", 2025, 7)
    assert out["state"] == "not_built"
