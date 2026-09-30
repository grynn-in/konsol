"""Period grid API: konsol/close/grid_api.py (konsol#305 E203; stories 2.2, 2.3; W2-2, W2-4).

`get_period_grid(fiscal_year, fiscal_period)` (GET) reads the period's
entities, ownership, trial balances, exceptions and Closing rates in a fixed
number of queries, whatever the entity count, and returns
`period_grid_model.period_grid(...)` plus the period.

Stub-frappe tests. The counting `get_all` stub and `_call` are copied (not
imported) from test_close_mywork_api.py; the filters are applied like
test_close_signoff_gate.py `_match`. `scope_model`, `signoff_model` and
`period_grid_model` load for real by path; `konsol.group_rates.rate_gate`,
`konsol.entity_permissions.allowed_entity_codes`, `konsol.fiscal_calendar`
and `konsol.period_status` are stubbed.
"""
import importlib.util
import json
import os
import sys
import types
from datetime import date

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PY = os.path.join(APP_DIR, "close", "grid_api.py")
REAL_MODELS = ("scope_model", "signoff_model", "period_grid_model")
GRID_ROLES = ("EPM Admin", "EPM Analyst", "EPM User", "System Manager")


class _D(dict):
    """frappe._dict: item and attribute access."""

    def __getattr__(self, name):
        return self.get(name)


def _rows():
    """FY2025: P01-P12 Regular (quarters declared), P13 Closing."""
    rows = []
    for fp in range(1, 13):
        rows.append({"fiscal_year": 2025, "fiscal_period": fp, "period_code": "P%02d" % fp,
                     "period_label": "P%02d" % fp, "period_type": "Regular",
                     "start_date": date(2025, fp, 1), "end_date": date(2025, fp, 28),
                     "quarter": "Q%d" % ((fp - 1) // 3 + 1), "status": "Open"})
    rows.append({"fiscal_year": 2025, "fiscal_period": 13, "period_code": "P13",
                 "period_label": "Closing", "period_type": "Closing",
                 "start_date": date(2025, 12, 31), "end_date": date(2025, 12, 31),
                 "quarter": "", "status": "Open"})
    return rows


def _entity(code, currency="EUR"):
    return _D(name=code, entity_name="Entity " + code, status="Active", is_group=0,
              functional_currency=currency, reporting_frequency="Monthly")


def _owner(code):
    return _D(data_area_id=code, effective_date=date(2020, 1, 1), end_date=None,
              consolidation_group="ZZG", ownership_pct=100.0, consolidation_method="full",
              docstatus=1)


def _tb(code, docstatus=1):
    return _D(data_area_id=code, name="TB-" + code, owner="zz-a@example.com",
              uploaded_on_behalf="No", fiscal_year=2025, fiscal_period=9, docstatus=docstatus)


class _Site:
    def __init__(self, n_extra=0):
        self.roles = {"EPM Admin"}
        self.user = "zz-lead@example.com"
        self.allowed = None
        self.rows = _rows()
        codes = ["ZZA", "ZZB", "ZZC"] + ["ZZE%02d" % i for i in range(n_extra)]
        self.data = {
            "Entity": [_entity(c) for c in codes] + [_entity("ZZX")],
            "Ownership Period": [_owner(c) for c in codes],
            "Trial Balance Submission": [_tb(c) for c in codes] + [_tb("ZZX")],
            "TB Exception": [],
            "Consolidation Group": [_D(name="ZZG", data_area_id=None, reporting_currency="USD"),
                                    _D(name="ZZG-SUB", data_area_id="ZZA",
                                       reporting_currency="GBP")],
            "Group Exchange Rate": [_D(from_currency="EUR", to_currency="USD",
                                       rate_type="Closing", fiscal_year=2025, fiscal_period=9,
                                       docstatus=1)],
        }
        self.rate_result = ([], None, [])
        self.rate_calls = []
        self.only_for_calls = []
        self.get_all_calls = {}
        self.period_rows_calls = 0


def _match(value, cond):
    if isinstance(cond, (list, tuple)):
        op, arg = cond[0], cond[1]
        if op == "in":
            return value in arg
        if op == "is":
            assert arg in ("set", "not set"), cond
            return (value not in (None, "")) == (arg == "set")
        if op == "<=":
            return value is not None and value <= arg
        if op == ">=":
            return value is not None and value >= arg
        if op == "<":
            return value is not None and value < arg
        if op in ("=", "=="):
            return value == arg
        if op in ("!=",):
            return value != arg
        raise AssertionError("stub: unsupported operator %r" % (op,))
    return value == cond


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

    def get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, limit=None,
                limit_page_length=None, **k):
        if doctype not in site.data:
            raise AssertionError("unexpected get_all(%r)" % doctype)
        site.get_all_calls[doctype] = site.get_all_calls.get(doctype, 0) + 1
        filters = filters or {}
        assert isinstance(filters, dict), filters
        rows = [r for r in site.data[doctype]
                if all(_match(r.get(f), c) for f, c in filters.items())]
        if pluck:
            return [r[pluck] for r in rows]
        n = limit or limit_page_length
        rows = rows[:n] if n else rows
        return [_D(r) if not fields else _D({f: r.get(f) for f in fields}) for r in rows]

    def forbidden(*a, **k):
        raise AssertionError("get_period_grid must not write")

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.only_for = only_for
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.get_all = get_all
    frappe.get_roles = lambda user=None: sorted(site.roles)
    frappe.session = types.SimpleNamespace(user=site.user)
    frappe.db = types.SimpleNamespace(set_value=forbidden, commit=forbidden, sql=forbidden)
    frappe.get_doc = forbidden
    frappe.enqueue = forbidden
    return frappe


def _model(name):
    spec = importlib.util.spec_from_file_location(
        "konsol.close." + name, os.path.join(APP_DIR, "close", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call(site, fy=2025, fp=9):
    frappe = _frappe(site)
    names = ["konsol", "konsol.close"]
    mods = {n: types.ModuleType(n) for n in names}
    mods["frappe"] = frappe
    for name in REAL_MODELS:
        mods["konsol.close." + name] = _model(name)
        setattr(mods["konsol.close"], name, mods["konsol.close." + name])

    fiscal_calendar = types.ModuleType("konsol.fiscal_calendar")

    def fiscal_period_rows(*a, **k):
        site.period_rows_calls += 1
        return [dict(r) for r in site.rows]

    fiscal_calendar.fiscal_period_rows = fiscal_period_rows
    group_rates = types.ModuleType("konsol.group_rates")

    def rate_gate(fy, fp, lock=False):
        assert lock is False, "the grid reads plain, no lock"
        site.rate_calls.append((fy, fp))
        return site.rate_result

    group_rates.rate_gate = rate_gate
    entity_permissions = types.ModuleType("konsol.entity_permissions")
    entity_permissions.allowed_entity_codes = lambda user=None: site.allowed
    period_status = types.ModuleType("konsol.period_status")
    period_status.PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})

    stubs = {
        "konsol.fiscal_calendar": fiscal_calendar,
        "konsol.group_rates": group_rates,
        "konsol.entity_permissions": entity_permissions,
        "konsol.period_status": period_status,
    }
    mods.update(stubs)
    for full, module in stubs.items():
        parent, _, leaf = full.rpartition(".")
        setattr(mods[parent], leaf, module)

    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("konsol.close.grid_api", API_PY)
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        site.errors = types.SimpleNamespace(PermissionError=frappe.PermissionError,
                                            ValidationError=frappe.ValidationError,
                                            PeriodNotDeclared=period_status.PeriodNotDeclared)
        result = api.get_period_grid(fy, fp)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    json.dumps(result)  # JSON-safe
    return result


def _call_raises(site, fy=2025, fp=9):
    with pytest.raises(Exception) as info:
        _call(site, fy, fp)
    return info.value


# --- permission ---------------------------------------------------------------

def test_an_entity_accountant_is_refused():
    site = _Site()
    site.roles = {"Entity Accountant"}
    err = _call_raises(site)
    assert isinstance(err, site.errors.PermissionError), err
    assert site.only_for_calls == [GRID_ROLES]
    assert site.get_all_calls == {} and site.rate_calls == []


def test_every_grid_role_reads():
    for role in GRID_ROLES:
        site = _Site()
        site.roles = {role}
        assert _call(site)["counts"]["rows"] == 4, role
        assert site.only_for_calls == [GRID_ROLES]


# --- period checks ------------------------------------------------------------

def test_an_undeclared_period_is_refused():
    site = _Site()
    err = _call_raises(site, 2031, 9)
    assert isinstance(err, site.errors.PeriodNotDeclared), err
    assert "FY2031 P09" in str(err)
    assert site.get_all_calls == {} and site.rate_calls == []


def test_a_closing_period_is_refused_naming_regular():
    site = _Site()
    err = _call_raises(site, 2025, 13)
    assert isinstance(err, site.errors.ValidationError), err
    assert not isinstance(err, site.errors.PeriodNotDeclared), err
    assert "Regular" in str(err) and "Closing" in str(err), err
    assert site.get_all_calls == {} and site.rate_calls == []


def test_string_arguments_are_cast_to_int():
    site = _Site()
    result = _call(site, "2025", "9")
    assert result["period"]["fiscal_year"] == 2025
    assert result["period"]["fiscal_period"] == 9


# --- the grid -------------------------------------------------------------------

def test_a_clean_site_gives_four_rows_with_the_unowned_tb_a_problem():
    site = _Site()
    result = _call(site)
    assert result["period"] == {"fiscal_year": 2025, "fiscal_period": 9, "code": "P09",
                                "status": "Open", "start_date": "2025-09-01"}
    rows = {r["entity"]: r for r in result["rows"]}
    assert sorted(rows) == ["ZZA", "ZZB", "ZZC", "ZZX"]
    zzx = rows["ZZX"]
    assert zzx["problem"] is True
    assert zzx["in_scope"] is False
    assert zzx["ownership"] == {"tone": "blocking", "label": "None for P09"}
    assert zzx["tb"] == {"tone": "blocking", "label": "Not consolidated: no ownership"}
    zza = rows["ZZA"]
    assert zza["problem"] is False, zza
    assert zza["ownership"]["label"] == "Full · 100%"
    assert zza["tb"] == {"tone": "ok", "label": "Received"}
    # Group currency comes from the root group only (data_area_id not set):
    # USD, not the sub-group's GBP; EUR->USD Closing is approved.
    assert zza["rate"] == {"tone": "ok", "label": "Approved"}
    assert zza["name"] == "Entity ZZA" and zza["currency"] == "EUR"
    assert result["counts"] == {"rows": 4, "problems": 1, "hidden": 0}
    assert result["rates_error"] is None


def test_an_unsubmitted_tb_does_not_make_the_entity_a_row():
    site = _Site()
    site.data["Trial Balance Submission"][-1]["docstatus"] = 0
    result = _call(site)
    assert sorted(r["entity"] for r in result["rows"]) == ["ZZA", "ZZB", "ZZC"]


def test_other_periods_records_do_not_count():
    site = _Site()
    site.data["Trial Balance Submission"][0]["fiscal_period"] = 8
    rows = {r["entity"]: r for r in _call(site)["rows"]}
    assert rows["ZZA"]["tb"] == {"tone": "blocking", "label": "Missing"}


def test_a_draft_rate_in_the_missing_list_reads_awaiting_approval():
    site = _Site()
    site.data["Group Exchange Rate"] = [
        _D(from_currency="EUR", to_currency="USD", rate_type="Closing", fiscal_year=2025,
           fiscal_period=9, docstatus=0),
        _D(from_currency="EUR", to_currency="USD", rate_type="Closing", fiscal_year=2025,
           fiscal_period=9, docstatus=2),
        _D(from_currency="EUR", to_currency="USD", rate_type="Average", fiscal_year=2025,
           fiscal_period=9, docstatus=1),
    ]
    site.rate_result = ([("EUR", "USD", "Closing"), ("EUR", "USD", "Average")], None, [])
    rows = {r["entity"]: r for r in _call(site)["rows"]}
    assert rows["ZZA"]["rate"] == {"tone": "blocking", "label": "Awaiting approval"}


def test_the_query_count_does_not_grow_with_the_entities():
    small, large = _Site(), _Site(n_extra=37)
    small_result, large_result = _call(small), _call(large)
    assert small_result["counts"]["rows"] == 4
    assert large_result["counts"]["rows"] == 41
    assert small.get_all_calls == large.get_all_calls
    assert set(small.get_all_calls) == {"Entity", "Ownership Period", "Trial Balance Submission",
                                        "TB Exception", "Consolidation Group",
                                        "Group Exchange Rate"}
    assert all(n == 1 for n in small.get_all_calls.values()), small.get_all_calls
    assert small.rate_calls == [(2025, 9)] and large.rate_calls == [(2025, 9)]
    assert small.period_rows_calls == 1 and large.period_rows_calls == 1


def test_rows_are_cut_to_the_allowed_entities():
    site = _Site()
    site.allowed = {"ZZA"}
    result = _call(site)
    assert [r["entity"] for r in result["rows"]] == ["ZZA"]
    assert result["counts"] == {"rows": 1, "problems": 0, "hidden": 3}
    text = json.dumps(result)
    for code in ("ZZB", "ZZC", "ZZX"):
        assert code not in text, code


def test_a_warehouse_error_makes_every_rate_cell_cannot_check():
    site = _Site()
    site.rate_result = (None, "ServerException UNKNOWN_TABLE", [])
    result = _call(site)
    assert result["rates_error"] == "ServerException UNKNOWN_TABLE"
    assert result["rows"]
    for row in result["rows"]:
        assert row["rate"]["tone"] == "blocking"
        assert row["rate"]["label"].startswith("Cannot check"), row["rate"]


def test_nothing_is_written():
    """The stub's db.set_value/commit/sql, get_doc and enqueue raise: a write
    would fail the call. And the source names none of them."""
    site = _Site()
    _call(site)
    with open(API_PY) as fh:
        src = fh.read()
    for word in (".insert(", ".save(", ".submit(", "db.set_value", "db.commit", "db.sql",
                 "enqueue", "get_doc"):
        assert word not in src, word
