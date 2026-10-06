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
REAL_MODELS = ("mywork_model", "checks_model", "period_model", "signoff_model",
               "close_policy_model", "scope_model")

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
        self.policies = ("Blocked", 50)
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
        self.ownership_queries = 0
        self.ic_gap = None
        self.ic_fixes = {}
        self.ic_calls = []
        self.ic_tolerance_gap = None
        #: #305 5.4: the stubbed `ic_balance_api.open_rule_gap` returns this.
        self.ic_rule_gap = None
        self.ic_rule_gap_calls = 0
        self.statement_gap = None
        #: W5-2 (8.4): the stubbed signoff_gate.commentary_gap() answer.
        self.commentary_gap = None
        #: S9: every signoff_gate.commentary_gap / shared_reads call, the
        #: shared reads each sign_off_problems call got, and each
        #: open_rule_gap call's reads.
        self.commentary_gap_calls = 0
        self.shared_reads_calls = 0
        self.shared = None
        self.problem_shared = []
        self.ic_rule_gap_reads = []
        self.approvals_waiting = {"count": 0}
        self.approvals_calls = []
        self.approvals_error = None
        self.sent_back_rows = []
        self.sent_back_calls = []
        self.sent_back_error = None
        #: #305-W5-1 (story 9.4): Close Event rows as the database holds them
        #: (detail JSON text, a naive ``at``), and every Close Event read.
        self.close_events = []
        self.close_event_reads = []


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
            site.ownership_queries += 1
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
        elif doctype == "Close Event":
            site.close_event_reads.append(dict(filters))
            rows = [e for e in site.close_events
                    if all(e.get(f) == c if not isinstance(c, list) else e.get(f) in c[1]
                           for f, c in filters.items())]
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
        self_approval, rate_move_threshold = site.policies
        return {"first_close_fiscal_year": fy, "first_close_fiscal_period": fp,
                "self_approval": self_approval, "rate_move_threshold": rate_move_threshold}[field]

    def forbidden(*a, **k):
        raise AssertionError("get_my_work must not write")

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.only_for = only_for
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.get_all = get_all
    frappe.get_roles = lambda user=None: sorted(site.roles)
    frappe.session = types.SimpleNamespace(user=site.user)
    frappe.utils = types.SimpleNamespace(getdate=lambda *a: TODAY,
                                         get_system_timezone=lambda: "Europe/London")
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

    # L01a: _aware (A47) lazily imports timefmt (pure, mirrors checks_api).
    tspec = importlib.util.spec_from_file_location(
        "konsol.close.timefmt", os.path.join(APP_DIR, "close", "timefmt.py"))
    timefmt = importlib.util.module_from_spec(tspec)
    tspec.loader.exec_module(timefmt)
    mods["konsol.close.timefmt"] = timefmt
    mods["konsol.close"].timefmt = timefmt

    real_covered = mods["konsol.close.scope_model"].covered

    def _spy_covered(rows, start_date):
        site.__dict__.setdefault("scope_calls", []).append(start_date)
        return real_covered(rows, start_date)

    mods["konsol.close.scope_model"].covered = _spy_covered

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

    def sign_off_problems(fy, fp, shared=None):
        row = next(r for r in site.rows if (r["fiscal_year"], r["fiscal_period"]) == (fy, fp))
        assert row["period_type"] == "Regular", "only Regular periods are gated"
        site.problem_calls.append((fy, fp))
        site.problem_shared.append(shared)
        return site.problems.get((fy, fp), {"config_gaps": [], "order": None, "completeness": None})

    signoff_gate.sign_off_problems = sign_off_problems
    # N46t: a stub `statement_gap`, unused by mywork_api until N47. It exists
    # so N47's `signoff_gate.statement_gap()` call resolves to this stub
    # rather than the real frappe-bound function.
    signoff_gate.statement_gap = lambda: site.statement_gap
    def commentary_gap():
        site.commentary_gap_calls += 1
        return site.commentary_gap

    signoff_gate.commentary_gap = commentary_gap

    def shared_reads():
        # S9: the real shape (signoff_gate.shared_reads): the commentary
        # threshold as close_policy_model.commentary_threshold gives it, and
        # ic_balance_api.open_reads' output (opaque here).
        site.shared_reads_calls += 1
        site.shared = {"commentary_threshold": {"threshold": None, "gap": site.commentary_gap},
                       "ic_balances": {"keys": frozenset(), "balances": [], "rules": []}}
        return site.shared

    signoff_gate.shared_reads = shared_reads
    freshness_api = types.ModuleType("konsol.close.freshness_api")
    freshness_api.current_freshness = lambda: {"state": "fresh", "as_of": site.as_of,
                                               "pending": 0, "changed_since": [],
                                               "last_failed": None}
    assertion_run = types.ModuleType("konsol.consolidation.doctype.assertion_run.assertion_run")
    assertion_run.latest_close_run = _latest_close_run(site)
    assertion_run.TERMINAL_STATUSES = ("Green", "Amber", "Red", "Error")

    # C06t: a stub `konsol.close.ic_api`, unused by mywork_api until C08. It
    # exists so C08's `from konsol.close import ic_api` resolves to this stub
    # rather than a real module that would otherwise run against this fake
    # frappe (and whose get_all would reject every doctype it does not know).
    ic_api = types.ModuleType("konsol.close.ic_api")
    ic_api.setup_gap = lambda: site.ic_gap
    ic_api.tolerance_gap = lambda: site.ic_tolerance_gap

    def open_fixes(keys):
        site.ic_calls.append(list(keys))
        return dict(site.ic_fixes)

    ic_api.open_fixes = open_fixes

    # #305 5.4: mywork_api imports konsol.close.ic_balance_api lazily.
    ic_balance_api = types.ModuleType("konsol.close.ic_balance_api")

    def open_rule_gap(reads=None):
        site.ic_rule_gap_calls += 1
        site.ic_rule_gap_reads.append(reads)
        return site.ic_rule_gap

    ic_balance_api.open_rule_gap = open_rule_gap

    # A12: a stub `konsol.close.approvals_api` with a recording `queue_for`, so
    # My work reads the same queue A10 builds without running it for real.
    approvals_api = types.ModuleType("konsol.close.approvals_api")

    def queue_for(user, roles):
        site.approvals_calls.append((user, tuple(roles)))
        if site.approvals_error:
            raise site.approvals_error
        return {"waiting": dict(site.approvals_waiting)}

    approvals_api.queue_for = queue_for

    # A22: a stub `sent_back_for`, extending A12's `approvals_api` stub, so My
    # work reads the same preparer-owned rows A21 builds without running it.
    def sent_back_for(user):
        site.sent_back_calls.append(user)
        if site.sent_back_error:
            raise site.sent_back_error
        return [dict(r) for r in site.sent_back_rows]

    approvals_api.sent_back_for = sent_back_for

    stubs = {
        "konsol.fiscal_calendar": fiscal_calendar,
        "konsol.group_chart": group_chart,
        "konsol.group_rates": group_rates,
        "konsol.entity_permissions": entity_permissions,
        "konsol.close.signoff_gate": signoff_gate,
        "konsol.close.freshness_api": freshness_api,
        "konsol.consolidation.doctype.assertion_run.assertion_run": assertion_run,
        "konsol.close.ic_api": ic_api,
        "konsol.close.ic_balance_api": ic_balance_api,
        "konsol.close.approvals_api": approvals_api,
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
        # F02: "approvals" is one My work item for the whole queue, so its
        # displayed count is the queue's real size, not len(on) (always 0 or 1).
        if screen != "approvals":
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
    # konsol#305 E412 (#305-W2-11): the Analyst gets "Rates missing" too.
    rates = next(i for i in result["items"] if i["id"] == "rates:2025-07")
    assert rates["kind"] == "blocking" and rates["action"] == {"screen": "rates"}
    assert not any(i["id"].startswith("rates-error") for i in result["items"])
    _assert_counts_add_up(result, "group_accountant")


def test_stale_run_asks_for_a_rerun():
    site = _Site(roles=("EPM Analyst",))
    site.as_of = "2025-09-12T00:00:00"  # rebuilt after P08's run
    assert "checks-run:2025-08" in _ids(_call(site))


def test_a_zoned_as_of_and_a_naive_completed_at_compare_without_error():
    """L01a (A47): freshness_api's as_of is zoned (A16b gives it the site's
    UTC offset); the newest Assertion Run's completed_at is read back naive
    from the database. get_my_work compared them directly and 500'd with
    "can't compare offset-naive and offset-aware datetimes". Same fix
    checks_api.py already applies (its own A47 test); same verdict as
    test_stale_run_asks_for_a_rerun's all-naive pair for the same instant,
    here written zoned."""
    site = _Site(roles=("EPM Analyst",))
    site.as_of = "2025-09-12T00:00:00+01:00"  # same instant as the naive pair above
    assert "checks-run:2025-08" in _ids(_call(site))  # no TypeError, same verdict


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
    # konsol#305 E412 (#305-W2-11): the Analyst still gets the P07 "Rates
    # missing" item; only the rate-gate error item stays Close Lead only.
    assert not any(i.startswith("rates-error") for i in _ids(_call(site)))


def test_errored_run_is_failed_not_current():
    site = _Site()
    site.problems[(2025, 8)] = {"config_gaps": [], "order": None, "completeness": None}
    site.runs[1].update(status="Error", failed=0, errored=0)
    ids = _ids(_call(site))
    assert "signoff:2025-08" not in ids
    assert "checks-waiting:2025-08" in ids


# --- konsol#305 E206, #289: a blocking item for a valid TB with no ownership ---


def test_close_lead_gets_the_unowned_tb_item():
    site = _Site()
    site.problems[(2025, 9)]["config_gaps"] = [
        {"code": "tb_without_ownership", "entities": ["ZZX"], "message": "…"}]
    result = _call(site)
    item = next(i for i in result["items"] if i["id"] == "unowned:2025-09")
    assert item["kind"] == "blocking"
    assert item["action"] == {"desk": "/app/ownership-period"}
    assert item["entities"] == ["ZZX"]
    _assert_counts_add_up(result, "close_lead")


def test_entity_accountant_does_not_get_the_unowned_tb_item():
    site = _Site(roles=("Entity Accountant",), user="zz-ea@example.com", allowed={"ZZA"})
    site.problems[(2025, 9)]["config_gaps"] = [
        {"code": "tb_without_ownership", "entities": ["ZZX"], "message": "…"}]
    result = _call(site)
    assert not any(i["id"].startswith("unowned:") for i in result["items"])


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


# --- konsol#305 P02/P06: the undeclared policies are setup-gap items -----------


def test_undeclared_policies_are_gap_items_for_the_close_lead():
    site = _Site()
    site.policies = ("", 0)
    result = _call(site)
    ids = _ids(result)
    assert "gap:self_approval" in ids and "gap:rate_move" in ids
    for gap in ("gap:self_approval", "gap:rate_move"):
        item = next(i for i in result["items"] if i["id"] == gap)
        assert item["owner"] == "EPM Admin"
        assert item["action"] == {"desk": "/app/close-settings"}
    _assert_counts_add_up(result, "close_lead")


def test_declared_policies_give_no_policy_gap_items():
    site = _Site()
    site.policies = ("Blocked", 50)
    ids = _ids(_call(site))
    assert "gap:self_approval" not in ids and "gap:rate_move" not in ids


def test_entity_accountant_never_sees_the_policy_gaps():
    # The Entity Accountant cannot declare Close Settings, and neither policy
    # touches a trial balance (mirrors gap:accountants).
    site = _Site(roles=("Entity Accountant",), user="zz-ea@example.com", allowed={"ZZA"})
    site.policies = ("", 0)
    ids = _ids(_call(site))
    assert "gap:self_approval" not in ids and "gap:rate_move" not in ids


# --- A54: entities_assigned for the Entity Accountant ---------------------------


def test_entity_accountant_with_no_entities_gets_entities_assigned_false():
    site = _Site(roles=("Entity Accountant",), user="zz-ea@example.com", allowed=set())
    result = _call(site)
    assert result["entities_assigned"] is False


def test_entity_accountant_assigned_but_out_of_scope_this_period_gets_entities_assigned_true():
    # ZZQ is assigned to the accountant but is not one of this period's
    # in-scope entities (not in site.entities at all): entities_assigned
    # answers "is anything assigned", not "is anything in scope this period".
    site = _Site(roles=("Entity Accountant",), user="zz-ea@example.com", allowed={"ZZQ"})
    result = _call(site)
    assert result["entities_assigned"] is True


def test_other_personas_get_entities_assigned_null():
    for roles in (("EPM Admin",), ("EPM Analyst",), ("EPM User",)):
        site = _Site(roles=roles)
        result = _call(site)
        assert result["entities_assigned"] is None, roles


# --- A56: the ownership gap is judged for the open periods, not for today -------
#
# An Active leaf entity is named in "Ownership missing" only when no submitted
# Ownership Period covers the start of ANY Open Regular period from the first
# close on (coordinator, 25 Sep: an entity uncovered at a period's start is out
# of scope for it under P7, not missing). The detail names the judged periods.
# Measured in C1: an entity owned from 2099-01-01, with the open periods in
# 2099, was named because the gap was judged as of today.


def _gap(result, gap):
    return next((i for i in result["items"] if i["id"] == "gap:" + gap), None)


def _next_year_site():
    """First close FY2026 P01; FY2026 P01-P03 Open (not started on TODAY).
    ZZOP is owned from 2026-01-01; ZZA-ZZD as in _Site (ZZC uncovered)."""
    site = _Site()
    site.first_close = (2026, 1)
    for fp in (1, 2, 3):
        site.rows.append({"fiscal_year": 2026, "fiscal_period": fp, "period_code": "P%02d" % fp,
                          "period_label": "P%02d" % fp, "period_type": "Regular",
                          "start_date": date(2026, fp, 1), "end_date": _month_end(2026, fp),
                          "quarter": "Q1", "status": "Open"})
    site.entities.append(_entity("ZZOP", ""))
    site.owners.append(_D(data_area_id="ZZOP", end_date=None,
                          effective_date=date(2026, 1, 1), docstatus=1))
    return site


def test_ownership_from_next_year_covers_open_periods_next_year():
    result = _call(_next_year_site())
    gap = _gap(result, "ownership")
    assert gap is not None and "ZZOP" not in gap["entities"], gap
    assert gap["entities"] == ["ZZC"], gap
    # In scope for the open periods, so its blank frequency is a gap.
    assert "ZZOP" in _gap(result, "frequency")["entities"]


def test_ownership_gap_names_the_uncovered_open_periods():
    gap = _gap(_call(_Site()), "ownership")
    assert gap["entities"] == ["ZZC"], gap
    # ZZC is uncovered in every open period from the first close on (P07-P11);
    # P06 is history and P05 is Closed, so neither is named.
    assert gap["detail"] == "ZZC: FY2025 P07, FY2025 P08, FY2025 P09, FY2025 P10, FY2025 P11", \
        gap["detail"]


def test_mid_year_acquisition_is_not_an_ownership_gap():
    # Coordinator on A56: ZZN is owned from P09 on. By P7 it is simply out of
    # scope for P07 and P08, so it is missing nothing; naming it is noise.
    site = _Site()
    site.entities.append(_entity("ZZN"))
    site.owners.append(_D(data_area_id="ZZN", end_date=None,
                          effective_date=date(2025, 9, 1), docstatus=1))
    gap = _gap(_call(site), "ownership")
    assert gap["entities"] == ["ZZC"], gap
    assert "ZZN" not in gap["detail"], gap["detail"]


def test_ownership_that_ended_before_the_open_periods_is_a_gap():
    site = _Site()
    site.owners[0] = _D(data_area_id="ZZA", end_date=date(2025, 6, 30),
                        effective_date=date(2020, 1, 1), docstatus=1)
    gap = _gap(_call(site), "ownership")
    assert "ZZA" in gap["entities"], gap
    assert "ZZA: FY2025 P07" in gap["detail"], gap["detail"]


def test_no_open_period_from_the_first_close_on_means_no_ownership_gap():
    site = _Site()
    for row in site.rows:
        if row["period_type"] == "Regular" and row["fiscal_period"] >= 7:
            row["status"] = "Closed"
    assert _gap(_call(site), "ownership") is None


def test_undeclared_first_close_judges_ownership_over_every_open_regular_period():
    site = _Site()
    site.first_close = (0, 0)
    gap = _gap(_call(site), "ownership")
    assert gap["entities"] == ["ZZC"], gap
    # No history without a first close: P06 is judged too; P05 (Closed) and
    # P00 (not Regular) are not.
    assert "ZZC: FY2025 P06, FY2025 P07" in gap["detail"], gap["detail"]
    assert "P05" not in gap["detail"] and "P00" not in gap["detail"], gap["detail"]


# --- A61: ownership is read once per judged period, not once per leaf --------
#
# Review #7: `_covered(start)` sat inside the per-leaf comprehension, so a load
# made (leaves x judged periods) Ownership Period queries (~329 x 12 on live).


def _three_periods_five_leaves():
    """First close 2025 P07; P07-P09 Open (judged), P10-P11 Closed. Five leaves:
    ZZA-ZZD as in _Site (ZZC uncovered) plus ZZE, owned."""
    site = _Site()
    for row in site.rows:
        if row["period_type"] == "Regular" and row["fiscal_period"] >= 10:
            row["status"] = "Closed"
    site.entities.append(_entity("ZZE"))
    site.owners.append(_owner("ZZE"))
    return site


def test_ownership_is_queried_once_per_judged_period():
    site = _three_periods_five_leaves()
    result = _call(site)
    assert site.ownership_queries == 3, \
        "%d Ownership Period queries for 3 periods x 5 leaves" % site.ownership_queries
    # Same result as before: only ZZC is uncovered, over the three judged periods.
    gap = _gap(result, "ownership")
    assert gap["entities"] == ["ZZC"], gap
    assert gap["detail"] == "ZZC: FY2025 P07, FY2025 P08, FY2025 P09", gap["detail"]


# --- G03: coverage is computed through scope_model, not a second copy -------


def test_coverage_is_computed_by_scope_model():
    site = _three_periods_five_leaves()
    result = _call(site)
    assert site.scope_calls == [date(2025, 7, 1), date(2025, 8, 1), date(2025, 9, 1)], \
        site.scope_calls
    gap = _gap(result, "ownership")
    assert gap["entities"] == ["ZZC"], gap


def test_the_inline_rule_is_gone():
    with open(API_PY) as fh:
        assert "end >= start" not in fh.read()


def test_no_leaves_still_answers_without_error():
    site = _three_periods_five_leaves()
    site.entities = []
    result = _call(site)
    assert site.ownership_queries <= 3, site.ownership_queries
    assert _gap(result, "ownership") is None


# --- C06t: the loader carries a stub konsol.close.ic_api, for C08 ----------


def test_the_ic_api_stub_is_installed():
    # C08: only the Entity Accountant reads the open IC fixes; the group
    # personas read the setup gaps only (no open_fixes call).
    site = _Site()
    _call(site)
    assert site.ic_calls == []
    ea = _Site(roles=("Entity Accountant",), allowed={"ZZA"})
    _call(ea)
    assert len(ea.ic_calls) == 1, ea.ic_calls


# --- C08: the IC gaps for the group personas, IC fix items for the EA ------

_GROUP_ROLES = (("EPM Admin",), ("System Manager",), ("EPM Analyst",))
_OPEN_KEYS = [(2025, 7), (2025, 8), (2025, 9)]


_IC_EVENT = {
    "kind": "ic_sent_back", "name": "ZZ-EVT-1",
    # Already zoned: this stub replaces ic_api.open_fixes, which is where
    # the real _iso(sent_at) happens (S1); feeding a zoned string here
    # stands in for that.
    "at": "2025-08-15T10:00:00+01:00",
    "actor": "zz-lead@example.com", "reason": "Please review the booking.",
    "detail": {"entity_a": "UK01", "account_a": "140000",
               "entity_b": "DE01", "account_b": "240000"},
}

_IC_ROW = {
    "entity_a": "UK01", "account_a": "140000", "entity_b": "DE01", "account_b": "240000",
    "consolidation_group": "EMEA Group", "match_status": "over_tolerance",
    "difference": 360.65, "tolerance": 5.0, "balance_a": 1250.75, "balance_b": 890.10,
}


def _ic_fix():
    """S1: built through the real producer (``ic_model.open_fixes``, loaded
    by path), never a hand-built dict."""
    return _model("ic_model").open_fixes([dict(_IC_EVENT)], [dict(_IC_ROW)])[0]


def _ic_items(result):
    return [i for i in result["items"] if i["id"].startswith("ic:")]


def test_ic_accounts_gap_reaches_the_close_lead_and_the_group_accountant():
    # Failure path: "not configured" is never silent on My work.
    for roles in _GROUP_ROLES:
        site = _Site(roles=roles)
        site.ic_gap = "No Published Intercompany Account: declare the pairings."
        result = _call(site)
        gap = _gap(result, "ic_accounts")
        assert gap is not None, (roles, _ids(result))
        assert gap["detail"] == site.ic_gap, gap
        assert gap["kind"] == "blocking", gap
        _assert_counts_add_up(result, "group_accountant" if roles == ("EPM Analyst",) else "close_lead")


def test_ic_accounts_gap_absent_when_ic_api_says_none():
    for roles in _GROUP_ROLES:
        result = _call(_Site(roles=roles))
        assert _gap(result, "ic_accounts") is None, roles
        assert _gap(result, "ic_tolerance") is None, roles


def test_entity_accountant_never_gets_the_ic_setup_gaps():
    # Failure path: the Entity Accountant cannot declare either, so neither is theirs.
    site = _Site(roles=("Entity Accountant",), allowed={"ZZA"})
    site.ic_gap = "No Published Intercompany Account: declare the pairings."
    site.ic_tolerance_gap = {"code": "ic_tolerance_undeclared", "groups": ["G"], "message": "m"}
    result = _call(site)
    assert _gap(result, "ic_accounts") is None, _ids(result)
    assert _gap(result, "ic_tolerance") is None, _ids(result)


def test_ic_tolerance_gap_reaches_the_group_personas():
    # Failure path (W3-6): a tolerance of 0 is never silently "exact".
    for roles in _GROUP_ROLES:
        site = _Site(roles=roles)
        site.ic_tolerance_gap = {"code": "ic_tolerance_undeclared", "groups": ["G"],
                                 "message": "Declare the IC tolerance on G."}
        result = _call(site)
        gap = _gap(result, "ic_tolerance")
        assert gap is not None, (roles, _ids(result))
        assert gap["detail"] == "Declare the IC tolerance on G.", gap
        assert gap["kind"] == "blocking", gap


def _real_ic_rule_gap():
    """#305 5.4: the real producer's gap (ic_balance_model.rule_gap)."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "close", "ic_balance_model.py")
    spec = importlib.util.spec_from_file_location("ic_balance_model_for_mywork_api", path)
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    return model.rule_gap([{"name": "B", "selling_entity": "UK01", "buying_entity": "DE01",
                            "docstatus": 0}], [])


def test_ic_rule_gap_reaches_the_group_personas():
    for roles in _GROUP_ROLES:
        site = _Site(roles=roles)
        site.ic_rule_gap = _real_ic_rule_gap()
        result = _call(site)
        gap = _gap(result, "ic_rule")
        assert gap is not None, (roles, _ids(result))
        assert "UK01 → DE01" in gap["detail"], gap
        assert gap["kind"] == "blocking" and gap["action"] == {"desk": "/app/ic-elimination-rule"}


def test_entity_accountant_never_gets_the_ic_rule_gap_and_it_is_not_read():
    site = _Site(roles=("Entity Accountant",), allowed={"UK01"})
    site.ic_rule_gap = _real_ic_rule_gap()
    result = _call(site)
    assert _gap(result, "ic_rule") is None, _ids(result)
    assert site.ic_rule_gap_calls == 0


def test_entity_accountant_gets_an_ic_fix_item_on_trial_balances():
    site = _Site(roles=("Entity Accountant",), allowed={"UK01"})
    site.ic_fixes = {(2025, 7): [_ic_fix()]}
    result = _call(site)
    items = _ic_items(result)
    assert len(items) == 1, items
    item = items[0]
    assert item["action"] == {"screen": "trial-balances", "entity": "UK01"}, item
    assert item["kind"] == "blocking", item  # P07 has ended
    assert item["period"]["since"] == "2025-07-31", item
    assert "890.1" not in item["detail"], item["detail"]  # W3-2: no partner balance
    assert site.ic_calls == [_OPEN_KEYS], site.ic_calls
    by_screen = result["counts"]["by_screen"]["trial-balances"]
    assert by_screen["count"] == sum(
        1 for i in result["items"] if i["action"].get("screen") == "trial-balances")
    assert by_screen["count"] >= 1 and by_screen["blocking"] >= 1, by_screen
    _assert_counts_add_up(result, "entity_accountant")


def test_entity_accountant_unrestricted_gets_both_sides():
    site = _Site(roles=("Entity Accountant",), allowed=None)
    site.ic_fixes = {(2025, 9): [_ic_fix()]}
    items = _ic_items(_call(site))
    assert sorted(i["action"]["entity"] for i in items) == ["DE01", "UK01"], items
    assert all(i["kind"] == "todo" for i in items), items  # P09 has not ended


def test_group_personas_never_get_ic_fix_items_and_never_read_them():
    # Failure path: fix items are the Entity Accountant's (R1).
    for roles in _GROUP_ROLES:
        site = _Site(roles=roles)
        site.ic_fixes = {(2025, 7): [_ic_fix()]}
        result = _call(site)
        assert _ic_items(result) == [], roles
        assert site.ic_calls == [], (roles, site.ic_calls)


def test_ic_fix_for_a_period_that_is_not_open_gives_no_item():
    # Failure path: P07 closed -> not one of My work's open periods.
    site = _Site(roles=("Entity Accountant",), allowed={"UK01"})
    for row in site.rows:
        if (row["fiscal_year"], row["fiscal_period"]) == (2025, 7):
            row["status"] = "Closed"
    site.ic_fixes = {(2025, 7): [_ic_fix()]}
    result = _call(site)
    assert _ic_items(result) == [], _ic_items(result)
    assert site.ic_calls == [[(2025, 8), (2025, 9)]], site.ic_calls


def test_entity_accountant_with_undeclared_first_close_reads_no_ic_fixes():
    site = _Site(roles=("Entity Accountant",), allowed={"UK01"})
    site.first_close = (0, 0)
    site.ic_fixes = {(2025, 7): [_ic_fix()]}
    result = _call(site)
    assert _ic_items(result) == []
    assert site.ic_calls == []


def test_viewer_reads_no_ic_facts():
    site = _Site(roles=("EPM User",))
    site.ic_gap = "x"
    site.ic_fixes = {(2025, 7): [_ic_fix()]}
    result = _call(site)
    assert result["items"] == [] and site.ic_calls == []


# --- A12: the Close Lead's "approvals" My work item -----------------------


def test_close_lead_gets_the_approvals_item_from_the_queue():
    site = _Site()
    site.approvals_waiting = {"count": 2, "oldest": "2025-08-15T10:00:00+01:00"}
    result = _call(site)
    item = next(i for i in result["items"] if i["id"] == "approvals")
    assert item["title"] == "Approve 2 items"
    assert item["action"] == {"screen": "approvals"}
    # F02: the approvals screen is one My work item for the whole queue, so
    # its badge must read the queue's real count (waiting["count"]), never
    # the number of My work items that point at it (always 1 or 0).
    assert result["counts"]["by_screen"]["approvals"]["count"] == 2
    assert site.approvals_calls == [(site.user, ("EPM Admin",))]
    _assert_counts_add_up(result, "close_lead")


def test_close_lead_with_six_waiting_sees_badge_six():
    """F02 (live: showed badge 1 against "6 waiting")."""
    site = _Site()
    site.approvals_waiting = {"count": 6, "oldest": "2025-08-15T10:00:00+01:00"}
    result = _call(site)
    assert result["counts"]["by_screen"]["approvals"]["count"] == 6


def test_zero_waiting_gives_no_approvals_item():
    site = _Site()
    site.approvals_waiting = {"count": 0}
    result = _call(site)
    assert "approvals" not in _ids(result)
    assert result["counts"]["by_screen"]["approvals"]["count"] == 0


def test_group_accountant_never_calls_the_approvals_queue():
    site = _Site(roles=("EPM Analyst",), user="zz-ga@example.com")
    site.approvals_waiting = {"count": 2}
    result = _call(site)
    assert "approvals" not in _ids(result)
    assert site.approvals_calls == []


def test_a_queue_failure_is_not_swallowed():
    site = _Site()
    site.approvals_error = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        _call(site)


def test_close_lead_gets_the_approvals_item_with_an_undeclared_first_close():
    site = _Site()
    site.first_close = (0, 0)
    site.approvals_waiting = {"count": 1, "oldest": "2025-08-15T10:00:00+01:00"}
    result = _call(site)
    ids = _ids(result)
    assert "approvals" in ids
    assert all(i["id"] == "approvals" or i["id"].startswith("gap:") for i in result["items"]), ids


# --- A22: the preparer's "sent back" My work item -----------------------------

def _sb_row(doctype, name, kind_label, title, fiscal_year=None, fiscal_period=None,
           actor="alice@example.com", at="2025-08-20T10:00:00+01:00",
           reason="Fix the amount."):
    return {
        "doctype": doctype, "name": name, "kind_label": kind_label, "title": title,
        "fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
        "rejection": {"reason": reason, "actor": actor, "at": at},
    }


def test_group_accountant_gets_one_sent_back_item_counted_under_rates():
    site = _Site(roles=("EPM Analyst",), user="zz-ga@example.com")
    site.rates = {}  # isolate: no base "Rates missing" item also on the rates screen
    site.sent_back_rows = [_sb_row("Group Exchange Rate", "GER-1", "Group rate · USD→EUR Closing",
                                   "1.1000", fiscal_year=2025, fiscal_period=7)]
    result = _call(site)
    ids = _ids(result)
    assert "sent-back:Group Exchange Rate:GER-1" in ids
    assert result["counts"]["by_screen"]["rates"]["count"] == 1
    assert site.sent_back_calls == [site.user]
    _assert_counts_add_up(result, "group_accountant")


def test_viewer_gets_no_sent_back_item_and_sent_back_for_is_not_called():
    site = _Site(roles=("EPM User",))
    site.sent_back_rows = [_sb_row("Group Exchange Rate", "GER-1", "x", "x",
                                   fiscal_year=2025, fiscal_period=7)]
    result = _call(site)
    assert result["items"] == []
    assert site.sent_back_calls == []


def test_a_sent_back_failure_is_not_swallowed():
    site = _Site()
    site.sent_back_error = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        _call(site)


def test_sent_back_item_shown_with_an_undeclared_first_close():
    site = _Site()
    site.first_close = (0, 0)
    site.sent_back_rows = [_sb_row("Historical Equity Rate", "HER-1", "Historical equity rate",
                                   "UK01 2024-12-31")]
    result = _call(site)
    ids = _ids(result)
    assert "sent-back:Historical Equity Rate:HER-1" in ids
    assert all(i["id"].startswith(("sent-back:", "gap:")) for i in result["items"]), ids


def test_entity_accountant_can_get_a_sent_back_item_too():
    site = _Site(roles=("Entity Accountant",), user="zz-ea@example.com", allowed={"ZZA"})
    site.sent_back_rows = [_sb_row("Historical Equity Rate", "HER-1", "Historical equity rate",
                                   "UK01 2024-12-31")]
    result = _call(site)
    assert "sent-back:Historical Equity Rate:HER-1" in _ids(result)


# --- N46t: the loader carries a stub signoff_gate.statement_gap, for N47 ---


def test_the_statement_gap_stub_is_installed():
    site = _Site()
    assert site.statement_gap is None
    # N47 now reads it (None here means no gap); _call must build and tear
    # down the stub without error either way.
    _call(site)
    assert site.statement_gap is None


# --- N47: the statement-accounts setup gap (konsol#305-W4-1 1c) ---------------
#
# signoff_gate.statement_gap() is appended to policy_gaps for group personas
# only (mywork_api._gap_facts); mywork_model resolves it into one item. The
# gap fed in is the REAL gap close_policy_model.statement_accounts returns,
# never a hand-built dict.


def test_statement_gap_reaches_the_group_personas():
    gap = _model("close_policy_model").statement_accounts("", "", {})["gap"]
    for roles in _GROUP_ROLES:
        site = _Site(roles=roles)
        site.statement_gap = gap
        result = _call(site)
        item = _gap(result, "statement_accounts")
        assert item is not None, (roles, _ids(result))
        assert item["detail"] == gap["message"], item
        assert item["kind"] == "blocking", item
        assert item["action"] == {"desk": "/app/close-settings"}, item
        _assert_counts_add_up(result, "group_accountant" if roles == ("EPM Analyst",) else "close_lead")


def test_entity_accountant_never_sees_the_statement_gap():
    # Failure path: the Entity Accountant cannot declare Close Settings.
    gap = _model("close_policy_model").statement_accounts("", "", {})["gap"]
    site = _Site(roles=("Entity Accountant",), user="zz-ea@example.com", allowed={"ZZA"})
    site.statement_gap = gap
    result = _call(site)
    assert _gap(result, "statement_accounts") is None, _ids(result)


def test_statement_gap_none_gives_no_item():
    for roles in _GROUP_ROLES:
        site = _Site(roles=roles)
        site.statement_gap = None
        result = _call(site)
        assert _gap(result, "statement_accounts") is None, (roles, _ids(result))
        _assert_counts_add_up(result, "group_accountant" if roles == ("EPM Analyst",) else "close_lead")


# --- #305-W5-1 (story 9.4, #157): the preparer's "sent back" sign-off item ------
#
# The rows are the real producers' events (test_assertion_run_reject.py's
# reject_signoff and sign_off_close), stored as the Close Event table holds
# them: detail as JSON text (close_event_model.detail_json), a naive ``at``.

_REJ_SPEC = importlib.util.spec_from_file_location(
    "assertion_run_reject_for_mywork_api",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_assertion_run_reject.py"))
_REJ = importlib.util.module_from_spec(_REJ_SPEC)
_REJ_SPEC.loader.exec_module(_REJ)


def _stored_event(event, name, at, fiscal_year=2025, fiscal_period=7):
    return _D(name=name, kind=event["kind"], fiscal_year=fiscal_year,
              fiscal_period=fiscal_period, actor=event.get("actor") or "acct@example.com",
              at=at, reason=event["reason"],
              detail=json.dumps(event["detail"], sort_keys=True))


def _rejected_row(name="ZZ-CE-2", at=datetime(2025, 8, 20, 10, 0), **kw):
    reject, frappe, _doc = _REJ.load()
    reject("AR-1", "ZZA's TB is the draft")
    return _stored_event(frappe.events[0], name, at, **kw)


def _signed_row(name="ZZ-CE-3", at=datetime(2025, 8, 21, 9, 0), **kw):
    module, frappe, _doc, _ = _REJ._amber._load(status="Green", warned=0)
    module.sign_off_close("AR-1")
    return _stored_event(frappe.events[0], name, at, **kw)


def test_the_preparer_gets_the_sent_back_signoff_item_counted_under_checks():
    site = _Site(roles=("EPM Analyst",), user=_REJ.PREPARER)
    site.close_events = [_rejected_row()]
    result = _call(site)
    item = next(i for i in result["items"] if i["id"] == "sent-back:signoff:2025-07")
    assert item["kind"] == "todo" and item["action"] == {"screen": "checks"}
    assert item["period"]["code"] == "P07"
    assert item["period"]["since"] == "2025-08-20"
    assert "ZZA's TB is the draft" in item["detail"]
    checks = [i for i in result["items"] if (i.get("action") or {}).get("screen") == "checks"]
    assert result["counts"]["by_screen"]["checks"]["count"] == len(checks)
    _assert_counts_add_up(result, "group_accountant")


def test_a_later_signature_clears_it():
    site = _Site(roles=("EPM Analyst",), user=_REJ.PREPARER)
    site.close_events = [_rejected_row(), _signed_row()]
    assert "sent-back:signoff:2025-07" not in _ids(_call(site))


def test_another_user_does_not_get_it():
    site = _Site(roles=("EPM Admin",), user="zz-lead@example.com")
    site.close_events = [_rejected_row()]
    assert "sent-back:signoff:2025-07" not in _ids(_call(site))


def test_the_viewer_reads_no_close_events():
    site = _Site(roles=("EPM User",), user=_REJ.PREPARER)
    site.close_events = [_rejected_row()]
    result = _call(site)
    assert result["items"] == []
    assert site.close_event_reads == []


def test_signatures_are_read_only_when_the_caller_has_a_rejection():
    site = _Site(roles=("EPM Analyst",), user="zz-ga@example.com")
    site.close_events = [_rejected_row()]
    _call(site)
    assert site.close_event_reads == [{"kind": "signoff_rejected"}]


# --- W5-2 (story 8.4): the commentary-threshold setup gap ---------------------
#
# signoff_gate.commentary_gap() is appended to policy_gaps for group personas
# only, after the statement gap. The gap fed in is the REAL one
# close_policy_model.commentary_threshold returns.


def test_commentary_gap_reaches_the_group_personas():
    gap = _model("close_policy_model").commentary_threshold(0, 0, "")["gap"]
    for roles in _GROUP_ROLES:
        site = _Site(roles=roles)
        site.commentary_gap = gap
        result = _call(site)
        item = _gap(result, "commentary_threshold")
        assert item is not None, (roles, _ids(result))
        assert item["detail"] == gap["message"], item
        assert item["action"] == {"desk": "/app/close-settings"}, item
        _assert_counts_add_up(result, "group_accountant" if roles == ("EPM Analyst",) else "close_lead")


def test_entity_accountant_never_sees_the_commentary_gap():
    gap = _model("close_policy_model").commentary_threshold(0, 0, "")["gap"]
    site = _Site(roles=("Entity Accountant",), user="zz-ea@example.com", allowed={"ZZA"})
    site.commentary_gap = gap
    result = _call(site)
    assert _gap(result, "commentary_threshold") is None, _ids(result)


# --- review-w5 S9: the shared settings are read once per request -------------


def test_one_request_reads_the_shared_settings_once_for_every_open_period():
    site = _Site()  # P07-P09 Open and started
    _call(site)
    assert site.problem_calls == [(2025, 7), (2025, 8), (2025, 9)]
    assert site.shared_reads_calls == 1
    assert site.problem_shared == [site.shared] * 3
    assert site.ic_rule_gap_reads == [site.shared["ic_balances"]]
    assert site.commentary_gap_calls == 0


def test_the_commentary_gap_comes_from_the_shared_reads():
    gap = _model("close_policy_model").commentary_threshold(0, 0, "")["gap"]
    site = _Site()
    site.commentary_gap = gap
    item = _gap(_call(site), "commentary_threshold")
    assert item is not None and item["detail"] == gap["message"]
    assert site.commentary_gap_calls == 0 and site.shared_reads_calls == 1
