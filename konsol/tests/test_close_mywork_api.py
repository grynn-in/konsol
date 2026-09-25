"""My-work API: konsol/close/mywork_api.py (konsol#305 A29; stories 1.1-1.4, 0.3).

`get_my_work()` (GET) returns `{items, counts}` for the caller's persona across
every Open, started (start_date <= today), non-history Regular period. It reads
the site and passes it through the pure A14/A20 model
(`konsol.close.mywork_model`), the real A13 staleness rule and the real A03
persona rule, all loaded by path. The frappe-bound readers it leans on
(`signoff_gate.sign_off_problems`, `group_rates.rate_gate`,
`freshness_api.current_freshness`, `latest_close_run`,
`entity_permissions.allowed_entity_codes`) are stubbed; each has its own tests.

Coordinator notes honoured here:
- A20: `period_items` raises on an undeclared first close, so the API returns
  only the setup-gap items then (Close Settings reads back 0 when unset).
- A45: every per-period fact carries `status`.
- B08: `counts.by_screen[screen]` is `{count, blocking}` for every screen the
  persona sees; the screen list is held equal to close-ui/src/nav.js.
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
REPO_DIR = os.path.dirname(APP_DIR)
API_PY = os.path.join(APP_DIR, "close", "mywork_api.py")
NAV_JS = os.path.join(REPO_DIR, "close-ui", "src", "nav.js")
REAL_MODELS = ("mywork_model", "checks_model", "period_model", "signoff_model")

ALL_CLOSE_ROLES = ("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager")
TODAY = date(2025, 9, 15)


class _D(dict):
    """frappe._dict: item and attribute access."""

    def __getattr__(self, name):
        return self.get(name)


def _month_end(fy, fp):
    nxt = date(fy + (fp == 12), fp % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def _rows():
    """FY2025: an Open Opening row, P05 Closed, P06-P10 Open, P11 Open (future)."""
    rows = [{"fiscal_year": 2025, "fiscal_period": 0, "period_code": "P00",
             "period_label": "Opening", "period_type": "Opening",
             "start_date": date(2025, 1, 1), "end_date": date(2025, 1, 1),
             "quarter": "", "status": "Open"}]
    for fp in range(5, 12):
        rows.append({"fiscal_year": 2025, "fiscal_period": fp, "period_code": "P%02d" % fp,
                     "period_label": "P%02d" % fp, "period_type": "Regular",
                     "start_date": date(2025, fp, 1), "end_date": _month_end(2025, fp),
                     "quarter": "Q%d" % ((fp - 1) // 3 + 1),
                     "status": "Closed" if fp == 5 else "Open"})
    return rows


def _entity(name, freq="Monthly"):
    return _D(name=name, reporting_frequency=freq, is_group=0, status="Active")


def _owner(entity):
    return _D(data_area_id=entity, end_date=None, effective_date=date(2020, 1, 1), docstatus=1)


class _Site:
    """First close 2025 P07. P07: a Red run (3 failed), 2 rates missing, TBs
    missing from ZZA and ZZB. P08: a current Green run, blocked by the order
    gate. P09: no run yet, TB missing from ZZA."""

    def __init__(self, roles=("EPM Admin",), user="zz-lead@example.com", allowed=None):
        self.roles = set(roles)
        self.user = user
        self.allowed = allowed  # allowed_entity_codes(): None = unrestricted
        self.first_close = (2025, 7)
        self.rows = _rows()
        self.entities = [_entity("ZZA", ""), _entity("ZZB", ""), _entity("ZZC"), _entity("ZZD")]
        self.owners = [_owner("ZZA"), _owner("ZZB"), _owner("ZZD")]  # ZZC uncovered
        self.accountants = ["zz-ea@example.com", "zz-idle@example.com"]
        self.permitted = {"zz-ea@example.com"}
        self.chart = {"1000": {}}
        self.as_of = "2025-09-01T00:00:00"
        self.runs = [
            _D(name="ZZ-RUN-1", fiscal_year=2025, fiscal_period=7, status="Red",
               signoff_status="Not Signed Off", failed=3, errored=0,
               creation=datetime(2025, 9, 10), completed_at=datetime(2025, 9, 10, 1)),
            _D(name="ZZ-RUN-2", fiscal_year=2025, fiscal_period=8, status="Green",
               signoff_status="Not Signed Off", failed=0, errored=0,
               creation=datetime(2025, 9, 11), completed_at=datetime(2025, 9, 11, 1)),
        ]
        self.problems = {
            (2025, 7): {"config_gaps": [], "order": None,
                        "completeness": {"missing": ["ZZA", "ZZB"], "message": "No TB"}},
            (2025, 8): {"config_gaps": [], "order": {"blocking": "P07", "periods": ["P07"],
                                                      "message": "Sign off and close P07 first"},
                        "completeness": None},
            (2025, 9): {"config_gaps": [], "order": None,
                        "completeness": {"missing": ["ZZA"], "message": "No TB"}},
        }
        self.rates = {(2025, 7): ([("EUR", "USD", "Closing"), ("EUR", "USD", "Average")], None, [])}
        self.rate_calls = []
        self.problem_calls = []
        self.only_for_calls = []


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
        filters = filters or {}
        if doctype == "Entity":
            assert filters == {"is_group": 0, "status": "Active"}, filters
            rows = site.entities
        elif doctype == "Ownership Period":
            assert filters.get("docstatus") == 1, filters
            start = filters["effective_date"][1]
            rows = [o for o in site.owners if o.effective_date <= start]
        elif doctype == "Has Role":
            assert filters.get("role") == "Entity Accountant", filters
            rows = [_D(parent=u) for u in site.accountants]
        elif doctype == "User":
            rows = [_D(name=u) for u in site.accountants if u in filters["name"][1]]
        elif doctype == "User Permission":
            assert filters.get("allow") == "Entity", filters
            rows = [_D(user=u) for u in site.permitted if u in filters["user"][1]]
        elif doctype == "Assertion Run":
            fy, fp = filters["fiscal_year"], filters["fiscal_period"]
            rows = sorted([r for r in site.runs
                           if (r.fiscal_year, r.fiscal_period) == (fy, fp)],
                          key=lambda r: r.creation, reverse=True)
        else:
            raise AssertionError("unexpected get_all(%r)" % doctype)
        if pluck:
            return [r[pluck] for r in rows]
        n = limit or limit_page_length
        rows = rows[:n] if n else rows
        return [_D(r) if not fields else _D({f: r.get(f) for f in fields}) for r in rows]

    def get_single_value(doctype, field):
        assert doctype == "Close Settings", doctype
        fy, fp = site.first_close
        return {"first_close_fiscal_year": fy, "first_close_fiscal_period": fp}[field]

    def forbidden(*a, **k):
        raise AssertionError("get_my_work must not write")

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.only_for = only_for
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.get_all = get_all
    frappe.get_roles = lambda user=None: sorted(site.roles)
    frappe.session = types.SimpleNamespace(user=site.user)
    frappe.utils = types.SimpleNamespace(getdate=lambda *a: TODAY)
    frappe.db = types.SimpleNamespace(get_single_value=get_single_value, set_value=forbidden,
                                      commit=forbidden, sql=forbidden)
    frappe.get_doc = forbidden
    frappe.enqueue = forbidden
    return frappe


def _model(name):
    spec = importlib.util.spec_from_file_location(
        "konsol.close." + name, os.path.join(APP_DIR, "close", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _latest_close_run(site):
    def latest_close_run(fiscal_year, fiscal_period):
        runs = [r for r in site.runs if (r.fiscal_year, r.fiscal_period) == (fiscal_year, fiscal_period)
                and r.status in ("Green", "Amber", "Red", "Error")]
        runs.sort(key=lambda r: r.completed_at, reverse=True)
        r = runs[0] if runs else None
        return None if r is None else _D({k: r[k] for k in
                                          ("name", "status", "signoff_status", "failed", "errored")})
    return latest_close_run


def _call(site):
    frappe = _frappe(site)
    names = ["konsol", "konsol.close", "konsol.consolidation", "konsol.consolidation.doctype",
             "konsol.consolidation.doctype.assertion_run"]
    mods = {n: types.ModuleType(n) for n in names}
    mods["frappe"] = frappe
    for name in REAL_MODELS:
        mods["konsol.close." + name] = _model(name)
        setattr(mods["konsol.close"], name, mods["konsol.close." + name])

    fiscal_calendar = types.ModuleType("konsol.fiscal_calendar")
    fiscal_calendar.fiscal_period_rows = lambda *a, **k: [dict(r) for r in site.rows]
    group_chart = types.ModuleType("konsol.group_chart")
    group_chart.chart_accounts = lambda: dict(site.chart)
    group_rates = types.ModuleType("konsol.group_rates")

    def rate_gate(fy, fp, lock=False):
        site.rate_calls.append((fy, fp))
        return site.rates.get((fy, fp), ([], None, []))

    group_rates.rate_gate = rate_gate
    entity_permissions = types.ModuleType("konsol.entity_permissions")
    entity_permissions.allowed_entity_codes = lambda user=None: site.allowed
    signoff_gate = types.ModuleType("konsol.close.signoff_gate")

    def sign_off_problems(fy, fp):
        row = next(r for r in site.rows if (r["fiscal_year"], r["fiscal_period"]) == (fy, fp))
        assert row["period_type"] == "Regular", "only Regular periods are gated"
        site.problem_calls.append((fy, fp))
        return site.problems.get((fy, fp), {"config_gaps": [], "order": None, "completeness": None})

    signoff_gate.sign_off_problems = sign_off_problems
    freshness_api = types.ModuleType("konsol.close.freshness_api")
    freshness_api.current_freshness = lambda: {"state": "fresh", "as_of": site.as_of,
                                               "pending": 0, "changed_since": [],
                                               "last_failed": None}
    assertion_run = types.ModuleType("konsol.consolidation.doctype.assertion_run.assertion_run")
    assertion_run.latest_close_run = _latest_close_run(site)
    assertion_run.TERMINAL_STATUSES = ("Green", "Amber", "Red", "Error")

    stubs = {
        "konsol.fiscal_calendar": fiscal_calendar,
        "konsol.group_chart": group_chart,
        "konsol.group_rates": group_rates,
        "konsol.entity_permissions": entity_permissions,
        "konsol.close.signoff_gate": signoff_gate,
        "konsol.close.freshness_api": freshness_api,
        "konsol.consolidation.doctype.assertion_run.assertion_run": assertion_run,
    }
    mods.update(stubs)
    for full, module in stubs.items():
        parent, _, leaf = full.rpartition(".")
        setattr(mods[parent], leaf, module)

    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("konsol.close.mywork_api", API_PY)
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        result = api.get_my_work()
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    json.dumps(result)  # JSON-safe
    return result


def _ids(result):
    return [i["id"] for i in result["items"]]


def _nav_screens():
    """SCREENS_BY_PERSONA from close-ui/src/nav.js, as {persona: [screen, ...]}."""
    with open(NAV_JS) as fh:
        src = fh.read()
    consts = dict(re.findall(r'export const (SCREEN_\w+) = "([\w-]+)";', src))
    block = re.search(r"const SCREENS_BY_PERSONA = \{(.*?)\n\};", src, re.S).group(1)
    out = {}
    for persona, body in re.findall(r"(\w+): \[([^\]]*)\]", block):
        out[persona] = [consts[c.strip()] for c in body.split(",") if c.strip()]
    return out


def _assert_counts_add_up(result, persona):
    items, counts = result["items"], result["counts"]
    for kind in ("blocking", "todo", "waiting"):
        assert counts[kind] == sum(1 for i in items if i["kind"] == kind), kind
    assert counts["blocking"] + counts["todo"] + counts["waiting"] == len(items)
    by_screen = counts["by_screen"]
    assert list(by_screen) == _nav_screens()[persona], by_screen
    for screen, entry in by_screen.items():
        assert set(entry) == {"count", "blocking"}, entry
        on = items if screen == "my-work" else [
            i for i in items if i["action"].get("screen") == screen]
        assert entry["count"] == len(on), (screen, entry)
        assert entry["blocking"] == sum(1 for i in on if i["kind"] == "blocking"), (screen, entry)


# --- the gate ------------------------------------------------------------------

def test_gate_is_every_close_role_and_no_role_is_refused():
    site = _Site()
    _call(site)
    assert site.only_for_calls == [ALL_CLOSE_ROLES]
    with pytest.raises(Exception) as info:
        _call(_Site(roles=("Guest",)))
    assert type(info.value).__name__ == "PermissionError"


# --- per-role items, tagged with their periods ---------------------------------

def test_close_lead_items_are_tagged_with_open_started_periods_only():
    site = _Site()
    result = _call(site)
    periods = {(i["period"]["fiscal_year"], i["period"]["fiscal_period"])
               for i in result["items"] if "period" in i}
    assert periods == {(2025, 7), (2025, 8), (2025, 9)}, periods
    # P06 is history, P05 Closed, P10/P11 not started, P00 not Regular.
    assert sorted(site.problem_calls) == [(2025, 7), (2025, 8), (2025, 9)]
    ids = _ids(result)
    assert "rates:2025-07" in ids
    assert "signoff-wait:2025-08" in ids  # the order gate blocks P08
    assert "signoff:2025-08" not in ids
    assert "checks-waiting:2025-09" in ids
    # Setup gaps: frequency (ZZA, ZZB), ownership (ZZC), one idle accountant.
    assert {"gap:frequency", "gap:ownership", "gap:accountants"} <= set(ids)
    assert "gap:first_close" not in ids
    # Ranked: blocking before todo before waiting.
    kinds = [i["kind"] for i in result["items"]]
    assert kinds == sorted(kinds, key=("blocking", "todo", "waiting").index)
    _assert_counts_add_up(result, "close_lead")


def test_group_accountant_runs_checks_and_sees_failures():
    result = _call(_Site(roles=("EPM Analyst",), user="zz-ga@example.com"))
    ids = _ids(result)
    assert "checks-failing:2025-07" in ids  # the Red run, 3 failed
    assert next(i for i in result["items"] if i["id"] == "checks-failing:2025-07")["title"] \
        == "3 checks failing"
    assert "checks-run:2025-09" in ids  # never run
    assert "checks-run:2025-08" not in ids  # current Green run
    assert "tbs-waiting:2025-07" in ids
    assert not any(i["id"].startswith("rates") for i in result["items"])
    _assert_counts_add_up(result, "group_accountant")


def test_stale_run_asks_for_a_rerun():
    site = _Site(roles=("EPM Analyst",))
    site.as_of = "2025-09-12T00:00:00"  # rebuilt after P08's run
    assert "checks-run:2025-08" in _ids(_call(site))


def test_entity_accountant_sees_only_its_own_entities():
    site = _Site(roles=("Entity Accountant",), user="zz-ea@example.com", allowed={"ZZA"})
    result = _call(site)
    ids = _ids(result)
    assert "tb:2025-07:ZZA" in ids and "tb:2025-09:ZZA" in ids
    assert next(i for i in result["items"] if i["id"] == "tb:2025-07:ZZA")["kind"] == "blocking"
    assert next(i for i in result["items"] if i["id"] == "tb:2025-09:ZZA")["kind"] == "todo"
    # The frequency gap names ZZA only; ZZB and ZZC (not mine) never appear, nor
    # other users.
    gap = next(i for i in result["items"] if i["id"] == "gap:frequency")
    assert gap["entities"] == ["ZZA"]
    text = json.dumps(result)
    for other in ("ZZB", "ZZC", "ZZD", "zz-idle@example.com"):
        assert other not in text, other
    assert "gap:ownership" not in ids and "gap:accountants" not in ids
    _assert_counts_add_up(result, "entity_accountant")


def test_viewer_has_no_items_and_zero_counts_on_its_screens():
    site = _Site(roles=("EPM User",))
    result = _call(site)
    assert result["items"] == []
    _assert_counts_add_up(result, "viewer")
    assert "my-work" not in result["counts"]["by_screen"]
    assert site.rate_calls == [] and site.problem_calls == []


def test_by_screen_matches_nav_js_for_every_persona():
    screens = _nav_screens()
    assert set(screens) == {"close_lead", "group_accountant", "entity_accountant", "viewer"}
    for roles, persona in ((("System Manager",), "close_lead"), (("EPM Analyst",), "group_accountant"),
                           (("Entity Accountant",), "entity_accountant"), (("EPM User",), "viewer")):
        result = _call(_Site(roles=roles, allowed={"ZZA"}))
        assert list(result["counts"]["by_screen"]) == screens[persona], persona


# --- failure paths ---------------------------------------------------------------

def test_undeclared_first_close_gives_only_the_gap_items():
    for unset in ((0, 0), (2025, 0), (None, None)):
        site = _Site()
        site.first_close = unset
        result = _call(site)
        ids = _ids(result)
        assert ids.count("gap:first_close") == 1, (unset, ids)
        assert all(i["id"].startswith("gap:") for i in result["items"]), ids
        assert site.rate_calls == [] and site.problem_calls == [], unset
        _assert_counts_add_up(result, "close_lead")


def test_undeclared_first_close_reaches_the_entity_accountant_too():
    site = _Site(roles=("Entity Accountant",), allowed={"ZZA"})
    site.first_close = (0, 0)
    assert "gap:first_close" in _ids(_call(site))


def test_rate_gate_error_is_a_blocking_item_with_the_error_text():
    site = _Site()
    site.problems[(2025, 8)] = {"config_gaps": [], "order": None, "completeness": None}
    site.rates[(2025, 8)] = (None, "ServerException UNKNOWN_TABLE", [])
    result = _call(site)
    item = next(i for i in result["items"] if i["id"] == "rates-error:2025-08")
    assert item["kind"] == "blocking"
    assert "ServerException UNKNOWN_TABLE" in item["title"] + item.get("detail", "")
    assert item["action"] == {"screen": "sign-off"}
    # Rates that cannot be checked never let the period be offered for sign-off.
    assert "signoff:2025-08" not in _ids(result)
    _assert_counts_add_up(result, "close_lead")


def test_clean_period_offers_sign_off():
    site = _Site()
    site.problems[(2025, 8)] = {"config_gaps": [], "order": None, "completeness": None}
    assert "signoff:2025-08" in _ids(_call(site))


def test_rate_blockers_count_as_missing_rates():
    site = _Site()
    site.rates[(2025, 9)] = ([], None, ["Consolidation Group ZZG has no reporting currency"])
    item = next(i for i in _call(site)["items"] if i["id"] == "rates:2025-09")
    assert item["title"] == "Rates missing (1)"


def test_rate_gate_error_item_goes_to_the_close_lead_only():
    site = _Site(roles=("EPM Analyst",))
    site.rates[(2025, 8)] = (None, "ServerException UNKNOWN_TABLE", [])
    assert not any(i.startswith("rates") for i in _ids(_call(site)))


def test_errored_run_is_failed_not_current():
    site = _Site()
    site.problems[(2025, 8)] = {"config_gaps": [], "order": None, "completeness": None}
    site.runs[1].update(status="Error", failed=0, errored=0)
    ids = _ids(_call(site))
    assert "signoff:2025-08" not in ids
    assert "checks-waiting:2025-08" in ids


# --- A53: items carry an age (`since` = the period's end date) -----------------


def test_period_items_carry_since_as_their_period_end_date():
    site = _Site()
    result = _call(site)
    ends = {fp: _month_end(2025, fp).isoformat() for fp in (7, 8, 9)}
    seen = set()
    for item in result["items"]:
        period = item.get("period")
        if period is None:
            continue
        fp = period["fiscal_period"]
        assert period["since"] == ends[fp], item
        seen.add(fp)
    assert seen == {7, 8, 9}


def test_rate_gate_error_item_also_carries_since():
    site = _Site()
    site.rates[(2025, 8)] = (None, "ServerException UNKNOWN_TABLE", [])
    result = _call(site)
    item = next(i for i in result["items"] if i["id"] == "rates-error:2025-08")
    assert item["period"]["since"] == _month_end(2025, 8).isoformat()


def test_gap_items_carry_since_none_and_a_reason():
    site = _Site()
    site.first_close = (0, 0)  # only gap items are returned
    result = _call(site)
    assert result["items"]
    for item in result["items"]:
        assert item["id"].startswith("gap:"), item
        assert item["since"] is None, item
        assert item["since_reason"] == "configuration gap", item


def test_period_with_no_end_date_raises_and_is_never_given_today():
    site = _Site()
    for row in site.rows:
        if (row["fiscal_year"], row["fiscal_period"]) == (2025, 7):
            row["end_date"] = None
    with pytest.raises(Exception) as info:
        _call(site)
    message = str(info.value)
    assert "end date" in message, message
    assert "P07" in message, message
