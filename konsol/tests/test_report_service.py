"""report_service: the frappe side of report templates (konsol#548 v1, T9).

The service loads the template files, validates every submitted param through
report_choices (refused by name, never a fallback), checks entity access as
the endpoints always did, resolves the site context (Regular periods, the
chart's accounts) and calls the pure interpreter. The endpoints in api.py only
parse the body and delegate.

Loaded by path under a stub frappe; report_choices is a stub whose lists stand
in for the site. The interpreter and the template files are the real ones of
this worktree. No pytest fixtures (the host runner skips those).
"""
import ast
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_PATH = os.path.join(APP_DIR, "report_service.py")
API_PATH = os.path.join(APP_DIR, "api.py")
GOLDEN = os.path.join(APP_DIR, "tests", "fixtures", "report_golden")


class Refused(Exception):
    pass


class Forbidden(Exception):
    pass


class _Row(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


def _calendar(year):
    return [{"number": m, "code": f"P{m:02d}", "label": f"P{m:02d}",
             "start_date": f"{year}-{m:02d}-01", "end_date": f"{year}-{m:02d}-28",
             "quarter": f"Q{(m - 1) // 3 + 1}"} for m in range(1, 13)]


#: Golden accounts (scripts/capture_report_golden.py A1, A2), in code order.
MAIN_ACCOUNTS = [
    {"main_account": "5000", "account_name": "Cost of sales", "status": "Published", "is_group": 0,
     "statement_section": "Profit and Loss", "chart_of_accounts": "ZZ_CHART"},
    {"main_account": "4000", "account_name": "Revenue", "status": "Published", "is_group": 0,
     "statement_section": "Profit and Loss", "chart_of_accounts": "ZZ_CHART"},
    {"main_account": "1000", "account_name": "Cash", "status": "Published", "is_group": 0,
     "statement_section": "Balance Sheet", "chart_of_accounts": "ZZ_CHART"},
]

#: report_choices.hierarchy_nodes("ZZH", year): tree order, parent first.
NODES = [
    {"code": "ZZROOT", "label": "ZZ Group", "depth": 0, "is_group": True, "parent": None},
    {"code": "ZZEU", "label": "ZZ Europe", "depth": 1, "is_group": True, "parent": "ZZROOT"},
    {"code": "ZZDE", "label": "ZZ Germany", "depth": 2, "is_group": False, "parent": "ZZEU"},
    {"code": "ZZUS", "label": "ZZ America", "depth": 1, "is_group": False, "parent": "ZZROOT"},
]

DATASETS = [
    {"fact_name": "zz_actuals", "scenario_key": "actuals", "status": "Published"},
    {"fact_name": "zz_budget", "scenario_key": "budget", "status": "Draft"},
]


def _matches(row, filters):
    for key, cond in (filters or {}).items():
        if isinstance(cond, (list, tuple)):
            op, val = cond
            assert op == "in", op
            if row.get(key) not in val:
                return False
        elif row.get(key) != cond:
            return False
    return True


def _frappe(site):
    fr = types.ModuleType("frappe")
    fr.ValidationError = Refused
    fr.PermissionError = Forbidden
    fr.session = types.SimpleNamespace(user="zz@example.com")
    fr.whitelist = lambda *a, **k: (lambda f: f)

    def throw(msg, exc=None, *a, **k):
        raise (exc or Refused)(msg)

    def get_all(doctype, filters=None, fields=None, order_by=None, **kw):
        site["calls"].append({"doctype": doctype, "filters": filters, "fields": fields,
                              "order_by": order_by})
        rows = {"Main Account": site["accounts"], "Dataset": site["datasets"]}[doctype]
        out = [r for r in rows if _matches(r, filters)]
        if order_by:
            key = order_by.split()[0].strip("`")
            out.sort(key=lambda r: r[key], reverse=order_by.lower().endswith("desc"))
        return [_Row({f: r.get(f) for f in fields}) for r in out]

    fr.throw = throw
    fr.get_all = get_all
    fr._dict = _Row
    return fr


def _choices(site):
    """report_choices as the site sees it. is_allowed mirrors the real one."""
    rc = types.ModuleType("konsol.report_choices")
    log = site["choice_calls"]

    def rec(name, value):
        def fn(*a, **k):
            log.append((name, a))
            return value(*a, **k) if callable(value) else value
        return fn

    def named(code):
        return dict({"code": code}, **({"name": site["names"][code]} if code in site["names"] else {}))

    rc.entities = rec("entities", lambda: [named(c) for c in site["entities"]])
    rc.groups = rec("groups", lambda: [named(c) for c in site["groups"]])
    rc.fiscal_years = rec("fiscal_years", lambda: sorted(site["years"]))
    rc.periods = rec("periods", lambda year: site["periods"].get(int(year), []))
    rc.hierarchies = rec("hierarchies", lambda: [{"name": "ZZH"}])
    rc.hierarchy_nodes = rec("hierarchy_nodes", lambda h, year: (
        [dict(n) for n in NODES] if h == "ZZH" else []))
    rc.dimension_values = rec("dimension_values", lambda d: (
        [{"code": "ZZDACH"}] if d == "Region" else []))
    rc.accounts = rec("accounts", lambda section=None: [
        {"code": "4000", "name": "Revenue"}, {"code": "5000", "name": "Cost of sales"}])
    rc.account_headings = rec("account_headings", lambda section=None: [{"code": "4"}])

    def scenarios(year, scenario_type):
        if scenario_type not in ("budget", "forecast"):
            raise ValueError(f"Unknown scenario type '{scenario_type}'")
        return site["scenarios"].get((int(year), scenario_type),
                                     {"options": [], "default": None, "why": "ZZ no budget"})

    rc.scenarios = rec("scenarios", scenarios)

    def is_allowed_scenario(year, scenario_type, scenario_id):
        return any(o["id"] == scenario_id for o in scenarios(year, scenario_type)["options"])

    rc.is_allowed_scenario = is_allowed_scenario

    def is_allowed(kind, code):
        lists = {"entity": rc.entities, "group": rc.groups, "hierarchy": rc.hierarchies,
                 "account": rc.accounts, "account_heading": rc.account_headings}
        if kind == "fiscal_year":
            try:
                return int(code) in rc.fiscal_years()
            except (TypeError, ValueError):
                return False
        if kind == "dimension_value":
            dim, sep, value = str(code).partition(":")
            return bool(sep) and any(i["code"] == value for i in rc.dimension_values(dim))
        key = "name" if kind == "hierarchy" else "code"
        return any(i[key] == code for i in lists[kind]())

    rc.is_allowed = is_allowed
    return rc


def _site(**over):
    site = {
        "accounts": [dict(r) for r in MAIN_ACCOUNTS],
        "datasets": [dict(r) for r in DATASETS],
        "entities": ["ZZ1", "ZZE"],
        "groups": ["ZZG"],
        "years": {2024, 2026},
        "periods": {2024: _calendar(2024), 2026: _calendar(2026)},
        "scenarios": {},
        "names": {},
        "calls": [],
        "choice_calls": [],
        "access": [],
        "fetched": [],
        "denied": set(),
        "tb_rows": [],
    }
    site.update(over)
    return site


def _service(site):
    """report_service loaded by path under the stub frappe, its site hooks patched."""
    fr = _frappe(site)
    saved = {k: sys.modules.get(k) for k in ("frappe", "konsol.report_choices")}
    sys.modules["frappe"] = fr
    sys.modules["konsol.report_choices"] = _choices(site)
    try:
        spec = importlib.util.spec_from_file_location("_report_service_under_test", SERVICE_PATH)
        svc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(svc)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    svc.frappe = fr
    svc.report_choices = _choices(site)

    def access(entity):
        site["access"].append(entity)
        if entity in site["denied"]:
            raise Forbidden(f"No access to entity {entity}")

    def fetch(entity, year, period_from, period_to):
        site["fetched"].append((entity, year, period_from, period_to))
        return [dict(r) for r in site["tb_rows"]]

    svc._assert_entity_access = access
    svc._fetch_trial_balance_rows = fetch
    return svc


def _refusal(fn, exc=Refused):
    try:
        fn()
    except exc as e:
        return str(e)
    raise AssertionError(f"expected {exc.__name__}")


def _golden(name):
    with open(os.path.join(GOLDEN, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def _no_report(out):
    out = dict(out)
    out.pop("report", None)
    return out


# -- legacy body: identical to the golden outputs ----------------------------

def test_legacy_cell_map_body_matches_the_golden_outputs():
    for tag, accounts in (("1acct", MAIN_ACCOUNTS[1:]), ("2acct", MAIN_ACCOUNTS)):
        for scenario in ("actuals", "BUD"):
            for year in (2024, 2026):
                site = _site(accounts=[dict(r) for r in accounts])
                svc = _service(site)
                tid, params = svc.request(
                    {"template_id": "pnl_monthly", "entity": "ZZ1", "year": year,
                     "scenario_id": scenario}, svc.LEGACY_BUILD_KEYS)
                got = svc.build(tid, params)
                want = _golden(f"pnl_monthly__{tag}__{scenario}__{year}")
                assert _no_report(got) == want, (tag, scenario, year)
                assert site["access"] == ["ZZ1"]


def test_legacy_snapshot_body_matches_the_golden_outputs():
    cap_spec = importlib.util.spec_from_file_location(
        "_capture_golden_svc", os.path.join(os.path.dirname(APP_DIR), "scripts",
                                            "capture_report_golden.py"))
    cap = importlib.util.module_from_spec(cap_spec)
    cap_spec.loader.exec_module(cap)
    for tag, rows in (("0rows", []), ("2rows", cap.TB_ROWS_FULL), ("odd_rows", cap.TB_ROWS_ODD)):
        for pf, pt in ((1, 12), (3, 5)):
            site = _site(tb_rows=rows)
            svc = _service(site)
            tid, params = svc.request(
                {"template_id": "trial_balance_long", "entity": "ZZ1", "year": 2024,
                 "period_from": pf, "period_to": pt}, svc.LEGACY_SNAPSHOT_KEYS)
            got = svc.snapshot(tid, params)
            assert _no_report(got) == _golden(f"tb_long__{tag}__p{pf}_{pt}"), (tag, pf, pt)
            assert site["fetched"] == [("ZZ1", 2024, pf, pt)]
            assert site["access"] == ["ZZ1"]


def test_legacy_body_scenario_defaults_to_actuals_and_strips_text():
    svc = _service(_site())
    tid, params = svc.request({"template_id": " pnl_monthly ", "entity": " ZZ1 ", "year": 2024},
                              svc.LEGACY_BUILD_KEYS)
    assert tid == "pnl_monthly"
    assert params == {"entity": "ZZ1", "year": 2024}
    assert svc.build(tid, params)["scenario_id"] == "actuals"


def test_legacy_body_without_entity_or_year_is_refused_as_today():
    svc = _service(_site())
    for body in ({"template_id": "pnl_monthly", "year": 2024},
                 {"template_id": "pnl_monthly", "entity": "ZZ1"},
                 {"entity": "ZZ1", "year": 2024}):
        msg = _refusal(lambda: svc.request(body, svc.LEGACY_BUILD_KEYS))
        assert msg == "template_id, entity, and year are required", msg


def test_legacy_snapshot_body_ignores_keys_the_template_does_not_declare():
    svc = _service(_site())
    _, params = svc.request({"template_id": "trial_balance_long", "entity": "ZZ1", "year": 2024,
                             "scenario_id": "actuals"}, svc.LEGACY_SNAPSHOT_KEYS)
    assert params == {"entity": "ZZ1", "year": 2024}


# -- new body: the same output ----------------------------------------------

def test_new_body_gives_the_same_cell_map_as_the_legacy_body():
    svc = _service(_site())
    legacy = svc.build(*svc.request({"template_id": "pnl_monthly", "entity": "ZZ1",
                                     "year": 2026, "scenario_id": "BUD"}, svc.LEGACY_BUILD_KEYS))
    svc = _service(_site())
    tid, params = svc.request({"template_id": "pnl_monthly",
                               "params": {"entity": "ZZ1", "year": 2026, "scenario_id": "BUD"}},
                              svc.LEGACY_BUILD_KEYS)
    assert params == {"entity": "ZZ1", "year": 2026, "scenario_id": "BUD"}
    assert svc.build(tid, params) == legacy
    assert legacy["report"]["params"] == {"entity": "ZZ1", "year": 2026, "scenario_id": "BUD"}


def test_new_body_gives_the_same_snapshot_as_the_legacy_body():
    rows = [{"period": 3, "account": "4000", "debit": 1, "credit": 0, "net": 1}]
    svc = _service(_site(tb_rows=rows))
    legacy = svc.snapshot(*svc.request({"template_id": "trial_balance_long", "entity": "ZZ1",
                                        "year": 2024, "period_from": 3, "period_to": 5},
                                       svc.LEGACY_SNAPSHOT_KEYS))
    svc = _service(_site(tb_rows=rows))
    new = svc.snapshot(*svc.request({"template_id": "trial_balance_long", "params": {
        "entity": "ZZ1", "year": 2024, "period_from": 3, "period_to": 5}},
        svc.LEGACY_SNAPSHOT_KEYS))
    assert new == legacy


def test_new_body_params_must_be_an_object():
    svc = _service(_site())
    msg = _refusal(lambda: svc.request({"template_id": "pnl_monthly", "params": ["ZZ1"]},
                                       svc.LEGACY_BUILD_KEYS))
    assert "params" in msg and "object" in msg, msg
    msg = _refusal(lambda: svc.request({"params": {}}, svc.LEGACY_BUILD_KEYS))
    assert "template_id" in msg, msg


# -- the context: Regular periods from report_choices -------------------------

def test_the_columns_are_the_years_regular_periods_from_report_choices():
    site = _site(periods={2026: _calendar(2026)[:4] + [
        {"number": 5, "code": "P05", "label": "Wk 17-20", "start_date": "2026-04-26",
         "end_date": "2026-05-23", "quarter": "Q2"}]}, years={2026})
    svc = _service(site)
    out = svc.build("pnl_monthly", {"entity": "ZZ1", "year": 2026})
    assert ("periods", (2026,)) in site["choice_calls"]
    header = next(c for c in out["cells"] if c["range"] == "B2:F2")
    assert len(header["values"][0]) == 5


# -- refusals, by name -------------------------------------------------------

def test_unknown_template_is_refused_and_reads_nothing():
    site = _site()
    svc = _service(site)
    msg = _refusal(lambda: svc.build("zz_unknown", {"entity": "ZZ1", "year": 2024}))
    assert msg == "Unknown template_id for formulas: 'zz_unknown'", msg
    msg = _refusal(lambda: svc.snapshot("zz_unknown", {"entity": "ZZ1", "year": 2024}))
    assert msg == "Snapshot not supported for template 'zz_unknown'", msg
    assert site["calls"] == [] and site["access"] == [] and site["fetched"] == []


def test_the_wrong_mode_is_refused_with_todays_text():
    svc = _service(_site())
    msg = _refusal(lambda: svc.build("trial_balance_long", {"entity": "ZZ1", "year": 2024}))
    assert msg == "Unknown template_id for formulas: 'trial_balance_long'", msg
    msg = _refusal(lambda: svc.snapshot("pnl_monthly", {"entity": "ZZ1", "year": 2024}))
    assert msg == "Snapshot not supported for template 'pnl_monthly'", msg


def test_an_undeclared_param_is_refused_by_name():
    svc = _service(_site())
    msg = _refusal(lambda: svc.build("pnl_monthly", {"entity": "ZZ1", "year": 2024, "zz": 1}))
    assert "'zz'" in msg and "pnl_monthly" in msg, msg


def test_a_missing_required_param_is_refused_by_name():
    svc = _service(_site())
    msg = _refusal(lambda: svc.build("pnl_monthly", {"year": 2024}))
    assert "entity is required" in msg, msg


def test_an_entity_not_in_the_choices_is_refused_by_name():
    svc = _service(_site())
    msg = _refusal(lambda: svc.build("pnl_monthly", {"entity": "ZZNOPE", "year": 2024}))
    assert "entity" in msg and "'ZZNOPE'" in msg, msg


def test_a_year_without_regular_periods_is_refused_naming_them():
    site = _site()
    svc = _service(site)
    msg = _refusal(lambda: svc.build("pnl_monthly", {"entity": "ZZ1", "year": 2025}))
    assert "2025" in msg and "Regular period" in msg, msg
    assert not [c for c in site["calls"] if c["doctype"] == "Main Account"]


def test_a_listed_year_whose_periods_vanished_is_refused_by_the_interpreter():
    # fiscal_years and periods are two reads; the interpreter refuses an
    # empty calendar itself, never Jan-Dec.
    svc = _service(_site(years={2024, 2027}))
    msg = _refusal(lambda: svc.build("pnl_monthly", {"entity": "ZZ1", "year": 2027}))
    assert "No Regular periods are declared for FY2027" in msg, msg


def test_a_bad_year_is_refused_by_name_not_as_a_python_error():
    svc = _service(_site())
    msg = _refusal(lambda: svc.build("pnl_monthly", {"entity": "ZZ1", "year": "twenty"}))
    assert "year" in msg and "'twenty'" in msg, msg


def test_no_access_is_refused_before_anything_is_read():
    site = _site(denied={"ZZE"})
    svc = _service(site)
    _refusal(lambda: svc.build("pnl_monthly", {"entity": "ZZE", "year": 2024}), Forbidden)
    assert not [c for c in site["calls"] if c["doctype"] == "Main Account"]
    site = _site(denied={"ZZE"})
    svc = _service(site)
    _refusal(lambda: svc.snapshot("trial_balance_long", {"entity": "ZZE", "year": 2024}),
             Forbidden)
    assert site["fetched"] == []


def test_snapshot_periods_keep_todays_refusals():
    svc = _service(_site())
    for pf, pt in ((0, 12), (1, 13), (5, 3)):
        msg = _refusal(lambda: svc.snapshot("trial_balance_long", {
            "entity": "ZZ1", "year": 2024, "period_from": pf, "period_to": pt}))
        assert msg == "period_from and period_to must be between 1 and 12", msg
    msg = _refusal(lambda: svc.snapshot("trial_balance_long", {
        "entity": "ZZ1", "year": 2024, "period_from": "x"}))
    assert msg == "period_from and period_to must be integers", msg


# -- every param type, on a synthetic template --------------------------------

SYNTH = {
    "id": "zz_synth", "mode": "formulas", "title": "ZZ", "params": [
        {"name": "entity", "type": "entity", "required": True},
        {"name": "grp", "type": "group"},
        {"name": "year", "type": "fiscal_year", "required": True},
        {"name": "month", "type": "period", "depends_on": ["year"]},
        {"name": "bud", "type": "scenario", "scenario_type": "budget",
         "depends_on": ["year"], "default": "rule"},
        {"name": "h", "type": "hierarchy"},
        {"name": "node", "type": "hierarchy_node", "depends_on": ["h"]},
        {"name": "dim", "type": "text"},
        {"name": "dv", "type": "dimension_value", "depends_on": ["dim"]},
        {"name": "acct", "type": "account"},
        {"name": "head", "type": "account_heading"},
        {"name": "list", "type": "account_list"},
        {"name": "basis", "type": "choice", "choices": ["a", "b"], "default": "a"},
        {"name": "n", "type": "int", "default": 3},
    ],
}

BUDGETS = {(2024, "budget"): {"options": [{"id": "ZZB1"}, {"id": "ZZB2"}],
                              "default": "ZZB1", "why": None}}


def _validate(params, **site):
    svc = _service(_site(**{"scenarios": BUDGETS, **site}))
    return svc.validate_params(SYNTH, params)


def test_every_param_type_accepts_a_listed_value():
    got = _validate({"entity": "ZZ1", "grp": "ZZG", "year": "2024", "month": "P03",
                     "bud": "ZZB2", "h": "ZZH", "node": "ZZEU", "dim": "Region",
                     "dv": "ZZDACH", "acct": "4000", "head": "4",
                     "list": ["5000", "4000"], "basis": "b", "n": "7"})
    assert got["year"] == 2024 and got["n"] == 7 and got["bud"] == "ZZB2"
    assert got["month"] == "P03" and got["list"] == ["5000", "4000"]


def test_the_scenario_rule_default_is_the_sites_rule():
    got = _validate({"entity": "ZZ1", "year": 2024})
    assert got["bud"] == "ZZB1"
    assert got["basis"] == "a" and got["n"] == 3


def test_no_scenario_rule_default_is_refused_with_the_rules_reason():
    msg = _refusal(lambda: _validate({"entity": "ZZ1", "year": 2024}, scenarios={}))
    assert "bud" in msg and "ZZ no budget" in msg, msg


def _bad(name, value, expect=None):
    params = {"entity": "ZZ1", "year": 2024, "h": "ZZH", "dim": "Region", name: value}
    msg = _refusal(lambda: _validate(params))
    assert name in msg and repr(value) in msg, msg
    if expect:
        assert expect in msg, msg


def test_every_param_type_refuses_an_unlisted_value_by_name():
    _bad("grp", "ZZNOG")
    _bad("month", "P99")
    _bad("bud", "ZZB9")
    _bad("h", "ZZNOH")
    _bad("node", "ZZNON")
    _bad("dv", "ZZNODV")
    _bad("acct", "9999")
    _bad("head", "9")
    _bad("list", ["4000", "9999"])
    _bad("list", "4000")
    _bad("basis", "c")
    _bad("n", "x")


def test_a_dependent_param_without_its_parent_is_refused():
    msg = _refusal(lambda: _validate({"entity": "ZZ1", "year": 2024, "node": "ZZEU"}))
    assert "node" in msg and "h" in msg, msg


# -- accounts: the moved _pnl_accounts logic ---------------------------------

def test_pnl_accounts_are_published_pnl_leaves_in_code_order():
    site = _site()
    svc = _service(site)
    ctx = svc.resolve_context(svc.template("pnl_monthly"), {"entity": "ZZ1", "year": 2024})
    assert ctx["accounts"] == {"lines": [("4000", "Revenue"), ("5000", "Cost of sales")]}
    assert ctx["charts"] == {"lines": ["ZZ_CHART"]}
    (call,) = [c for c in site["calls"] if c["doctype"] == "Main Account"]
    assert call["filters"] == {"status": "Published", "is_group": 0,
                               "statement_section": "Profit and Loss"}


# -- report_catalog ----------------------------------------------------------

def test_the_catalog_carries_the_full_spec_in_order():
    svc = _service(_site())
    out = svc.catalog()
    ids = [t["id"] for t in out["templates"]]
    assert ids == ["pnl_monthly", "trial_balance_long", "pnl_actual_budget_monthly"]
    pnl = out["templates"][0]
    for key in ("id", "title", "description", "mode", "sheet_name", "params", "version",
                "available", "why"):
        assert key in pnl, key
    assert pnl["description"].endswith("(debit positive; a negative total is a profit)")
    assert [p["name"] for p in pnl["params"]] == ["entity", "year", "scenario_id"]
    entity = pnl["params"][0]
    assert entity["type"] == "entity" and entity["required"] is True
    assert entity["label"] and entity["depends_on"] == []
    scen = pnl["params"][2]
    assert scen["default"] == "actuals" and scen["echo_only"] is True
    assert pnl["available"] is True and pnl["why"] is None
    tb = out["templates"][1]
    assert tb["mode"] == "snapshot" and tb["available"] is True
    assert [p["default"] for p in tb["params"]][2:] == [1, 12]


def test_a_template_whose_dataset_word_is_not_published_is_unavailable_and_says_why():
    site = _site(datasets=[{"fact_name": "zz_actuals", "scenario_key": "actuals",
                            "status": "Draft"}])
    svc = _service(site)
    pnl = svc.catalog()["templates"][0]
    assert pnl["available"] is False
    assert "actuals" in pnl["why"] and "Published Dataset" in pnl["why"], pnl["why"]
    (call,) = [c for c in site["calls"] if c["doctype"] == "Dataset"]
    assert call["filters"] == {"status": "Published"}


def test_list_templates_keeps_its_four_keys():
    svc = _service(_site())
    for t in svc.list_templates():
        assert set(t) == {"id", "title", "description", "mode"}


# -- report_choices dispatch -------------------------------------------------

def test_choices_dispatch_to_report_choices():
    site = _site(scenarios=BUDGETS)
    svc = _service(site)
    assert svc.choices("entities") == {"kind": "entities",
                                       "choices": [{"code": "ZZ1"}, {"code": "ZZE"}]}
    assert len(svc.choices("periods", year="2024")["choices"]) == 12
    assert ("periods", (2024,)) in site["choice_calls"]
    got = svc.choices("scenarios", year="2024", scenario_type="budget")["choices"]
    assert got["default"] == "ZZB1"
    svc.choices("hierarchy_nodes", year="2024", hierarchy="ZZH")
    assert ("hierarchy_nodes", ("ZZH", 2024)) in site["choice_calls"]
    svc.choices("dimension_values", dimension="Region")
    svc.choices("accounts", section="Profit and Loss")
    assert ("accounts", ("Profit and Loss",)) in site["choice_calls"]


def test_an_unknown_choice_kind_is_refused_by_name():
    svc = _service(_site())
    msg = _refusal(lambda: svc.choices("zz_kind"))
    assert "'zz_kind'" in msg and "entities" in msg, msg


def test_a_choice_kind_without_its_argument_is_refused_by_name():
    svc = _service(_site())
    msg = _refusal(lambda: svc.choices("periods"))
    assert "periods" in msg and "year" in msg, msg
    msg = _refusal(lambda: svc.choices("scenarios", year=2024))
    assert "scenario_type" in msg, msg


# -- api.py: thin endpoints --------------------------------------------------

def _api_functions(names, service, body=None):
    tree = ast.parse(open(API_PATH).read())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names), names
    ns = {"frappe": types.SimpleNamespace(whitelist=lambda *a, **k: (lambda f: f)),
          "_get_json_body": lambda: dict(body or {})}
    saved = sys.modules.get("konsol.report_service")
    sys.modules["konsol.report_service"] = service
    exec(compile(ast.Module(body=nodes, type_ignores=[]), API_PATH, "exec"), ns)
    return ns, lambda: (sys.modules.pop("konsol.report_service", None) if saved is None
                        else sys.modules.__setitem__("konsol.report_service", saved))


def test_the_endpoints_only_parse_and_delegate():
    seen = []
    fake = types.ModuleType("konsol.report_service")
    fake.LEGACY_BUILD_KEYS = ("b",)
    fake.LEGACY_SNAPSHOT_KEYS = ("s",)
    fake.request = lambda body, keys: (seen.append(("request", body, keys)) or ("T", {"p": 1}))
    fake.build = lambda tid, params: seen.append(("build", tid, params)) or "BUILT"
    fake.snapshot = lambda tid, params: seen.append(("snapshot", tid, params)) or "SNAP"
    fake.catalog = lambda: "CAT"
    fake.list_templates = lambda: ["L"]
    fake.choices = lambda kind, **kw: seen.append(("choices", kind, kw)) or "CH"
    names = ("build_cell_map", "build_snapshot", "report_catalog", "report_choices",
             "list_report_templates")
    ns, restore = _api_functions(names, fake, body={"template_id": "T"})
    try:
        assert ns["build_cell_map"]() == "BUILT"
        assert ns["build_snapshot"]() == "SNAP"
        assert ns["report_catalog"]() == "CAT"
        assert ns["list_report_templates"]() == {"templates": ["L"]}
        assert ns["report_choices"]("periods", year="2024") == "CH"
    finally:
        restore()
    assert seen[:4] == [("request", {"template_id": "T"}, ("b",)), ("build", "T", {"p": 1}),
                        ("request", {"template_id": "T"}, ("s",)), ("snapshot", "T", {"p": 1})]
    assert seen[4] == ("choices", "periods", {"year": "2024", "hierarchy": None,
                                              "dimension": None, "section": None,
                                              "scenario_type": None})


def test_the_endpoints_keep_their_http_methods():
    tree = ast.parse(open(API_PATH).read())
    deco = {n.name: [ast.unparse(d) for d in n.decorator_list] for n in tree.body
            if isinstance(n, ast.FunctionDef)}
    assert deco["build_cell_map"] == ["frappe.whitelist(methods=['POST'])"]
    assert deco["build_snapshot"] == ["frappe.whitelist(methods=['POST'])"]
    for name in ("list_report_templates", "report_catalog", "report_choices"):
        assert deco[name] == ["frappe.whitelist(methods=['GET'])"], (name, deco[name])


def test_pnl_accounts_keeps_its_name_and_refusal():
    svc = _service(_site())
    assert svc._pnl_accounts() == [("4000", "Revenue"), ("5000", "Cost of sales")]
    rows = [dict(r) for r in MAIN_ACCOUNTS] + [{
        "main_account": "4100", "account_name": "Other", "status": "Published", "is_group": 0,
        "statement_section": "Profit and Loss", "chart_of_accounts": "ZZ_CHART_B"}]
    svc = _service(_site(accounts=rows))
    msg = _refusal(svc._pnl_accounts)
    assert msg == ("The monthly P&L reads one chart, and Published Profit and Loss accounts "
                   "exist in charts ZZ_CHART, ZZ_CHART_B. Make one chart Inactive or Draft."), msg


def test_api_keeps_the_moved_helpers_as_aliases():
    tree = ast.parse(open(API_PATH).read())
    defs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name in ("_pnl_accounts", "_fetch_trial_balance_rows"):
        src = ast.unparse(defs[name])
        assert "konsol.report_service" in src and name in src, src


def test_the_trial_balance_fetch_moved_unchanged():
    with open(SERVICE_PATH) as f:
        src = f.read()
    assert "sum(period_debit) - sum(period_credit) AS net" in src
    assert "FROM epm_gold.gold_trial_balance" in src
    assert 'f"LIMIT {int(TB_LONG_MAX_ROWS)}"' in src
    with open(API_PATH) as f:
        assert "gold_trial_balance" not in f.read()


# -- T12: context for every block type ----------------------------------------

def _acct(code, name, parent=None, is_group=0, section="Profit and Loss", chart="ZZ_CHART",
          status="Published"):
    return {"main_account": code, "account_name": name, "parent_account": parent,
            "is_group": is_group, "statement_section": section, "chart_of_accounts": chart,
            "status": status}


#: A P&L tree, deliberately not in code order: 4 > (4000, 41 > 4100), 5000
#: under no heading, 6 an empty heading, 1000 on the Balance Sheet, 4200 Draft.
TREE_ACCOUNTS = [
    _acct("5000", "Costs"),
    _acct("4100", "", parent="41"),
    _acct("41", "Other income", parent="4", is_group=1),
    _acct("1000", "Cash", section="Balance Sheet"),
    _acct("4000", "Sales", parent="4"),
    _acct("4", "Revenue", is_group=1),
    _acct("6", "Empty heading", is_group=1),
    _acct("4200", "Draft line", parent="4", status="Draft"),
]

ACTBUD = "pnl_actual_budget_monthly"
#: Every scenario word the actual-vs-budget report reads, Published: build() refuses a kind without them.
ALL_DATASETS = [{"fact_name": f"zz_{w}", "scenario_key": w, "status": "Published"}
                for w in ("actuals", "budget", "consolidated", "consolidated_budget")]
BUDGETS_2026 = {(2026, "budget"): {"options": [{"id": "ZZB1"}], "default": "ZZB1", "why": None}}


def _tree_template(svc, **block):
    t = json.loads(json.dumps(svc.template(ACTBUD)))
    tree = next(b for b in t["rows"]["blocks"] if b["type"] == "account_tree")
    tree.update(block)
    return t, tree.get("id")


def _actbud_params(**over):
    return dict({"scope": {"kind": "group", "code": "ZZG"}, "year": 2026, "budget": "ZZB1",
                 "through": "P03", "sign": "debit_positive"}, **over)


def test_an_account_tree_is_the_sections_published_accounts_in_dfs_order():
    svc = _service(_site(accounts=[dict(r) for r in TREE_ACCOUNTS]))
    t, key = _tree_template(svc)
    ctx = svc.resolve_context(t, _actbud_params())
    assert ctx["trees"][key] == [
        {"code": "4", "caption": "Revenue", "depth": 0, "is_group": True, "parent": None},
        {"code": "4000", "caption": "Sales", "depth": 1, "is_group": False, "parent": "4"},
        {"code": "41", "caption": "Other income", "depth": 1, "is_group": True, "parent": "4"},
        {"code": "4100", "caption": "4100", "depth": 2, "is_group": False, "parent": "41"},
        {"code": "5000", "caption": "Costs", "depth": 0, "is_group": False, "parent": None},
    ]
    assert ctx["charts"][key] == ["ZZ_CHART"]


def test_a_depth_cut_drops_a_heading_with_its_subtree_never_leaving_it_empty():
    svc = _service(_site(accounts=[dict(r) for r in TREE_ACCOUNTS]))
    t, key = _tree_template(svc, depth=2)
    codes = [n["code"] for n in svc.resolve_context(t, _actbud_params())["trees"][key]]
    # 41 is at depth 1, its only child 4100 at depth 2 is cut: 41 goes too.
    assert codes == ["4", "4000", "5000"]
    t, key = _tree_template(svc, depth=1)
    codes = [n["code"] for n in svc.resolve_context(t, _actbud_params())["trees"][key]]
    assert codes == ["5000"]


def test_an_account_tree_under_a_heading_param_starts_at_that_heading():
    svc = _service(_site(accounts=[dict(r) for r in TREE_ACCOUNTS]))
    t, key = _tree_template(svc, root="head")
    t["params"].append({"name": "head", "type": "account_heading"})
    ctx = svc.resolve_context(t, dict(_actbud_params(), head="41"))
    assert [(n["code"], n["depth"], n["parent"]) for n in ctx["trees"][key]] == [
        ("41", 0, None), ("4100", 1, "41")]


def test_an_account_tree_over_several_charts_is_refused_with_the_templates_text():
    rows = [dict(r) for r in TREE_ACCOUNTS] + [_acct("4900", "Other chart", chart="ZZ_CHART_B")]
    site = _site(accounts=rows, scenarios=BUDGETS_2026, datasets=ALL_DATASETS)
    svc = _service(site)
    msg = _refusal(lambda: svc.build(ACTBUD, dict(_actbud_params(), budget=None)))
    assert "ZZ_CHART, ZZ_CHART_B" in msg and "actual vs budget" in msg, msg


def test_the_actual_budget_report_builds_for_a_group_with_the_rule_budget():
    site = _site(accounts=[dict(r) for r in TREE_ACCOUNTS], scenarios=BUDGETS_2026, datasets=ALL_DATASETS)
    svc = _service(site)
    out = svc.build(ACTBUD, dict(_actbud_params(), budget=None))
    assert out["budget"] == "ZZB1"
    formulas = [f for c in out["cells"] for line in c.get("formulas", []) for f in line]
    assert '=K.EPM("ZZG", 2026, 1, "4000", , "consolidated_budget", , "ZZB1")' in formulas
    # A group is checked with K.EPM's own rule on the group code (finding 1).
    assert site["access"] == ["ZZG"]


def test_no_budget_rule_default_is_refused_with_the_rules_reason():
    site = _site(accounts=[dict(r) for r in TREE_ACCOUNTS])
    svc = _service(site)
    msg = _refusal(lambda: svc.build(ACTBUD, dict(_actbud_params(), budget=None)))
    assert "budget" in msg and "ZZ no budget" in msg, msg
    assert not [c for c in site["calls"] if c["doctype"] == "Main Account"]


def test_an_account_list_block_reads_the_param_in_the_users_order():
    svc = _service(_site())
    t = {"id": "zz_list", "params": [{"name": "entity", "type": "entity"},
                                     {"name": "year", "type": "fiscal_year"},
                                     {"name": "picks", "type": "account_list"}],
         "rows": {"blocks": [{"id": "picked", "type": "account_list", "param": "picks",
                              "caption": "{account.name}"}]}}
    ctx = svc.resolve_context(t, {"entity": "ZZ1", "year": 2024, "picks": ["5000", "4000"]})
    assert ctx["account_lists"] == {"picked": [("5000", "Cost of sales"), ("4000", "Revenue")]}


def _node_template(node=None):
    params = [{"name": "entity", "type": "entity"}, {"name": "year", "type": "fiscal_year"},
              {"name": "h", "type": "hierarchy"},
              {"name": "acct", "type": "account"}]
    block = {"type": "hierarchy_nodes", "hierarchy": "h", "account": "acct", "caption": "x"}
    if node:
        params.append({"name": "n", "type": "hierarchy_node", "depends_on": ["h"]})
        block["node"] = "n"
    return {"id": "zz_nodes", "params": params, "rows": {"blocks": [block]}}


def test_a_hierarchy_nodes_block_reads_the_hierarchys_nodes_for_the_year():
    site = _site()
    svc = _service(site)
    ctx = svc.resolve_context(_node_template(), {"entity": "ZZ1", "year": 2024, "h": "ZZH",
                                                 "acct": "4000"})
    assert ctx["nodes"]["blocks[0]"] == NODES
    assert ("hierarchy_nodes", ("ZZH", 2024)) in site["choice_calls"]
    ctx = svc.resolve_context(_node_template(node=True), {
        "entity": "ZZ1", "year": 2024, "h": "ZZH", "acct": "4000", "n": "ZZEU"})
    assert [n["code"] for n in ctx["nodes"]["blocks[0]"]] == ["ZZEU", "ZZDE"]


def test_scope_name_is_the_entitys_name_the_groups_name_or_the_nodes_label():
    svc = _service(_site(names={"ZZ1": "ZZ One Ltd", "ZZG": "ZZ Holdings"}))
    t = svc.template(ACTBUD)
    base = {"year": 2026}
    ctx = svc.resolve_context({"id": "x", "params": t["params"], "rows": {"blocks": []}},
                              dict(base, scope={"kind": "entity", "code": "ZZ1"}))
    assert ctx["scope_name"] == "ZZ One Ltd"
    ctx = svc.resolve_context({"id": "x", "params": t["params"], "rows": {"blocks": []}},
                              dict(base, scope={"kind": "group", "code": "ZZG"}))
    assert ctx["scope_name"] == "ZZ Holdings"
    node_t = {"id": "x", "params": [{"name": "year", "type": "fiscal_year"},
                                    {"name": "h", "type": "hierarchy"},
                                    {"name": "scope", "type": "scope", "kinds": ["node"]}],
              "rows": {"blocks": []}}
    ctx = svc.resolve_context(node_t, {"year": 2026, "h": "ZZH",
                                       "scope": {"kind": "node", "code": "ZZEU"}})
    assert ctx["scope_name"] == "ZZ Europe"
    # A name the site does not declare is left out: {scope.name} is then
    # refused by the interpreter, never filled with the code.
    svc = _service(_site())
    ctx = svc.resolve_context({"id": "x", "params": t["params"], "rows": {"blocks": []}},
                              dict(base, scope={"kind": "entity", "code": "ZZ1"}))
    assert "scope_name" not in ctx


def test_actual_vs_budget_without_a_consolidated_budget_is_offered_for_an_entity_only():
    """Availability is per scope kind (review finding 4, a product rule): a
    kind is offered when every read series has a Published Dataset for that
    kind's word. A site without consolidated_budget keeps the report for an
    entity; the group kind is withheld with its why."""
    published = [{"fact_name": f"zz_{w}", "scenario_key": w, "status": "Published"}
                 for w in ("actuals", "budget", "consolidated")]
    svc = _service(_site(datasets=published))
    t = next(x for x in svc.catalog()["templates"] if x["id"] == ACTBUD)
    assert t["available"] is True and t["why"] is None, t
    assert t["available_kinds"] == ["entity"], t
    assert set(t["unavailable_kinds"]) == {"group"}, t
    why = t["unavailable_kinds"]["group"]
    assert "'consolidated_budget'" in why and "'bud'" in why and "group" in why, why
    assert "'consolidated'" not in why, why
    svc = _service(_site(datasets=published + [{"fact_name": "zz_cb", "status": "Published",
                                                "scenario_key": "consolidated_budget"}]))
    t = next(x for x in svc.catalog()["templates"] if x["id"] == ACTBUD)
    assert t["available"] is True and t["why"] is None
    assert t["available_kinds"] == ["entity", "group"] and t["unavailable_kinds"] == {}, t


def test_a_template_with_no_available_kind_is_unavailable_with_every_kinds_why():
    published = [{"fact_name": "zz_a", "scenario_key": "actuals", "status": "Published"}]
    svc = _service(_site(datasets=published))
    t = next(x for x in svc.catalog()["templates"] if x["id"] == ACTBUD)
    assert t["available"] is False and t["available_kinds"] == [], t
    assert set(t["unavailable_kinds"]) == {"entity", "group"}, t
    for kind, why in t["unavailable_kinds"].items():
        assert why in t["why"], (kind, t["why"])
    assert "'budget'" in t["unavailable_kinds"]["entity"], t
    pnl = next(x for x in svc.catalog()["templates"] if x["id"] == "pnl_monthly")
    assert pnl["available"] is True and pnl["available_kinds"] == ["entity"], pnl
    tb = next(x for x in svc.catalog()["templates"] if x["id"] == "trial_balance_long")
    assert tb["available"] is True and tb["available_kinds"] == ["entity"], tb


# -- review findings (PR #626) -----------------------------------------------

def test_a_group_the_reader_may_not_read_is_refused_by_name_before_any_read():
    """Finding 1: a group is checked with K.EPM's rule (entity_read_scope on
    the group code), before any read, as an entity is."""
    site = _site(accounts=[dict(r) for r in TREE_ACCOUNTS], scenarios=BUDGETS_2026, datasets=ALL_DATASETS,
                 denied={"ZZG"})
    svc = _service(site)
    msg = _refusal(lambda: svc.build(ACTBUD, _actbud_params()), Forbidden)
    assert "ZZG" in msg, msg
    assert site["access"] == ["ZZG"]
    assert not [c for c in site["calls"] if c["doctype"] == "Main Account"]
    assert not [c for c in site["choice_calls"] if c[0] == "groups"], site["choice_calls"]
    # A group param, not only a scope of kind group.
    site = _site(denied={"ZZG"})
    svc = _service(site)
    _refusal(lambda: svc.validate_params(SYNTH, {"entity": "ZZ1", "grp": "ZZG", "year": 2024}),
             Forbidden)
    assert "ZZG" in site["access"]


def test_a_readable_group_code_builds_even_when_another_code_is_denied():
    site = _site(accounts=[dict(r) for r in TREE_ACCOUNTS], scenarios=BUDGETS_2026, datasets=ALL_DATASETS,
                 denied={"ZZ1"})
    svc = _service(site)
    out = svc.build(ACTBUD, _actbud_params())
    assert out["report"]["params"]["scope"] == {"kind": "group", "code": "ZZG"}
    assert site["access"] == ["ZZG"]


def test_list_templates_renders_a_param_signs_default_sentence():
    """Finding 3: a template whose sign is a param has ``sentences``; the
    catalog text is the default choice's sentence, never ``()``."""
    svc = _service(_site())
    by_id = {t["id"]: t for t in svc.list_templates()}
    d = by_id[ACTBUD]["description"]
    assert "()" not in d, d
    assert d.endswith("(debit positive; a negative total is a profit)"), d
    cat = {t["id"]: t for t in svc.catalog()["templates"]}
    assert cat[ACTBUD]["description"] == d
    assert by_id["pnl_monthly"]["description"].endswith(
        "(debit positive; a negative total is a profit)")


def _type_refusal(svc, fn):
    try:
        fn()
    except Refused as e:
        return str(e)
    except Exception as e:  # noqa: BLE001 - the test names what escaped
        raise AssertionError(f"{type(e).__name__} escaped instead of a refusal: {e}")
    raise AssertionError("expected a refusal")


def test_a_param_value_of_the_wrong_type_is_refused_by_name():
    """Finding 8a: a dict or list for a code reached assert_entity_access and
    raised TypeError (a 500). Each is refused by name, before any check."""
    site = _site(scenarios=BUDGETS)
    svc = _service(site)

    def real_access(entity):  # entity_read_scope does `entity in allowed` (a set)
        site["access"].append(entity)
        if entity in {"ZZ1", "ZZE", "ZZG"}:
            return

    svc._assert_entity_access = real_access
    cases = [("entity", {"code": "ZZ1"}), ("entity", ["ZZ1"]), ("grp", {"x": 1}),
             ("h", ["ZZH"]), ("acct", {"a": 1}), ("month", {"p": 3}), ("bud", ["ZZB1"])]
    for name, value in cases:
        params = {"entity": "ZZ1", "year": 2024, name: value}
        msg = _type_refusal(svc, lambda: svc.validate_params(SYNTH, params))
        assert name in msg and repr(value) in msg, (name, msg)
    assert all(isinstance(a, str) for a in site["access"]), site["access"]
    msg = _type_refusal(svc, lambda: svc.validate_params(
        SYNTH, {"entity": "ZZ1", "year": 2024, "list": ["4000", {"x": 1}]}))
    assert "list" in msg, msg
    msg = _type_refusal(svc, lambda: svc.validate_params(SYNTH, {"entity": "ZZ1", "year": [2024]}))
    assert "year" in msg, msg
    t = svc.template(ACTBUD)
    msg = _type_refusal(svc, lambda: svc.validate_params(t, {
        "scope": {"kind": "entity", "code": {"c": 1}}, "year": 2026, "through": "P01"}))
    assert "scope" in msg, msg
    msg = _type_refusal(svc, lambda: svc.validate_params(t, {
        "scope": {"kind": ["entity"], "code": "ZZ1"}, "year": 2026, "through": "P01"}))
    assert "scope" in msg, msg


def test_a_bad_scenario_type_for_choices_is_refused_naming_the_allowed_values():
    """Finding 8b: report_choices(kind=scenarios, scenario_type=x) was a 500."""
    svc = _service(_site())
    msg = _refusal(lambda: svc.choices("scenarios", year="2024", scenario_type="x"))
    assert "'x'" in msg and "budget" in msg and "forecast" in msg, msg


def _node_build_template(svc):
    t = json.loads(json.dumps(svc.template(ACTBUD)))
    t["id"] = "zz_node_build"
    t["params"][0]["kinds"] = ["entity", "group", "node"]
    t["params"][0]["depends_on"] = ["h", "year"]
    t["params"][1:1] = [{"name": "h", "type": "hierarchy", "required": True},
                        {"name": "acct", "type": "account", "required": True}]
    for name, word in (("act", "actuals"), ("bud", "budget")):
        t["series"][name]["read"]["node"] = word
    t["rows"]["blocks"] = [
        {"id": "nodes", "type": "hierarchy_nodes", "hierarchy": "h", "account": "acct",
         "caption": "{node.label}"},
    ]
    t["header"] = [c for c in t["header"] if c.get("param") != "scope"] + [
        {"at": "J1", "text": "{scope.name}"}]
    return t


def test_the_hierarchy_tree_is_read_once_per_build():
    """Finding 8d: the scope check, the hierarchy_nodes block and the scope
    name each read the tree; one build reads it once."""
    site = _site(scenarios=BUDGETS_2026, datasets=ALL_DATASETS)
    svc = _service(site)
    t = _node_build_template(svc)
    svc.templates()[t["id"]] = t
    out = svc.build(t["id"], {"scope": {"kind": "node", "code": "ZZEU"}, "h": "ZZH",
                              "acct": "4000", "year": 2026, "through": "P03"})
    reads = [c for c in site["choice_calls"] if c[0] == "hierarchy_nodes"]
    assert reads == [("hierarchy_nodes", ("ZZH", 2026))], reads
    values = {c["range"]: c["values"][0][0] for c in out["cells"] if "values" in c}
    assert values["J1"] == "ZZ Europe", values


def test_build_refuses_a_kind_the_site_cannot_read_and_still_builds_the_other():
    """Per-kind availability binds the API, not only the form (PR #626 re-review)."""
    published = [{"fact_name": f"zz_{w}", "scenario_key": w, "status": "Published"}
                 for w in ("actuals", "budget", "consolidated")]
    site = _site(accounts=[dict(r) for r in TREE_ACCOUNTS], scenarios=BUDGETS_2026, datasets=published)
    svc = _service(site)
    msg = _refusal(lambda: svc.build(ACTBUD, _actbud_params()))
    assert "'consolidated_budget'" in msg and "group" in msg, msg
    out = svc.build(ACTBUD, _actbud_params(scope={"kind": "entity", "code": "ZZ1"}))
    assert out["cells"]
