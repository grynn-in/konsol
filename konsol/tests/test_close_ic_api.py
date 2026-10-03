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
    names = ["frappe", "konsol", "konsol.close", "konsol.fiscal_calendar",
             "konsol.entity_permissions", "konsol.close.ch_read", "konsol.close.ic_model",
             "konsol.close.close_policy_model", "konsol.close.timefmt",
             "close_ic_api_under_test"]
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({"frappe": frappe, "konsol": konsol, "konsol.close": close,
                        "konsol.fiscal_calendar": fiscal_calendar,
                        "konsol.entity_permissions": entity_permissions,
                        "konsol.close.ch_read": ch_read})
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
