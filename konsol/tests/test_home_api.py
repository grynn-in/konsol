"""Close home dashboard endpoint, against stubbed sources (trial, 8 Nov 2026).

``home_api.get_home`` is loaded by path against a stub ``frappe`` and stub
readers (period_api, statement_api, signoff_gate, ic_api, mywork_api,
approvals_api, freshness_api, setup); ``home_model``, ``charts_model`` and
``overview_model`` are the real ones. What this pins:

- with every source answering, every section is filled from it;
- one failing source makes only the sections that read it "unavailable"
  with that source's reason; every other section is still filled, and the
  failure's ``frappe.throw`` message is dropped (no dialog);
- a failing IC read is a gap with its reason, never 0;
- setup is read only for the setup roles; for anyone else it is absent;
- the endpoint is a gated GET that bypasses no permission and adds no query.
"""
import ast
import importlib.util
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.join(APP_DIR, "close", "home_api.py")
ROLES = {"EPM Admin", "EPM Analyst", "EPM User", "System Manager"}
P05, P06, P07 = (2025, 5), (2025, 6), (2025, 7)


class _ValidationError(Exception):
    pass


def _load_path(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, rel))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOME_MODEL = _load_path("test_home_api_home_model", os.path.join("close", "home_model.py"))
CHARTS_MODEL = _load_path("test_home_api_charts_model", os.path.join("close", "charts_model.py"))
OVERVIEW_MODEL = _load_path("test_home_api_overview_model", os.path.join("close", "overview_model.py"))


def _state(key, end):
    return {"key": list(key), "status": "Open", "end_date": end, "checks": "Amber",
            "signoff": "Not Signed Off", "is_signed": False}


def _statement(rows, accounts, period_rows, key, declared, minor_unit):
    return {"sections": [{"section": "Profit and Loss", "lines": [
        {"kind": "heading", "heading": "Net Sales", "current": 1000.0 * key[1]},
        {"kind": "net_result", "current": 100.0 * key[1]}]}]}


def _pairs(fp):
    return [{"match_status": "over_tolerance", "entity_a": "A", "entity_b": "B",
             "masked_a": False, "masked_b": False}] * (fp - 5) + [
        {"match_status": "matched", "entity_a": "A", "entity_b": "B",
         "masked_a": False, "masked_b": False}]


def _sources():
    """Every reader home_api calls, answering."""
    def choose_group(groups, group=None):
        if group:
            return group, None, "ok", None
        if len(groups) == 1:
            return groups[0]["consolidation_group"], "The only consolidation group.", "ok", None
        return None, None, "choose_group", "Choose a consolidation group."

    lists = {
        "Trial Balance Submission": [
            {"data_area_id": "A", "fiscal_year": 2025, "fiscal_period": p} for p in (5, 6, 7)] + [
            {"data_area_id": "B", "fiscal_year": 2025, "fiscal_period": p} for p in (5, 6)],
        "TB Exception": [],
        "Entity": [{"name": "A", "entity_name": "Alpha"}, {"name": "B", "entity_name": "Beta"}],
    }
    return {
        "period_api.get_context": lambda: {
            "landing": {"period": [2025, 7]},
            "periods": [_state(P05, "2025-05-31"), _state(P06, "2025-06-30"),
                        _state(P07, "2025-07-31")]},
        "statement_api.root_groups": lambda: [
            {"consolidation_group": "G1", "reporting_currency": "EUR"}],
        "statement_api.choose_group": choose_group,
        "statement_api._statement": lambda fy, fp, group: (
            {"state": "ok", "message": None, "statement": {"minor_unit": 2}},
            {"tb_rows": [], "accounts": {}, "period_rows": [], "declared": {}}),
        "statement_model.statement": _statement,
        "signoff_gate.trial_balance_progress": lambda fy, fp: {
            "expected": ["A", "B"], "missing": ["B"] if fp == 7 else []},
        "signoff_gate.latest_signed_runs": lambda fields=(): {
            P05: {"signed_off_at": "2025-06-12 10:00:00"},
            P06: {"signed_off_at": "2025-07-08 10:00:00"}},
        "ic_api.get_ic": lambda fy, fp: {
            "state": "checked", "message": None, "unmatched": [],
            "groups": [{"consolidation_group": "G1", "pairs": _pairs(fp)}]},
        "approvals_api.queue_for": lambda user, roles: {"waiting": {"count": 2}},
        "freshness_api.current_freshness": lambda: {
            "state": "fresh", "as_of": "2025-08-01T10:00:00", "pending": 0},
        "mywork_api.get_my_work": lambda: {"counts": {"blocking": 2, "todo": 1, "waiting": 0}},
        "setup.status": lambda: {"done": 3, "total": 11, "steps": []},
        "frappe.get_list": lambda doctype, **kw: [dict(r) for r in lists[doctype]],
        "frappe.get_roles": lambda user=None: ["EPM Admin"],
        "frappe.has_permission": lambda *a, **kw: True,
        "frappe.db.get_single_value": lambda doctype, field: "Net Sales",
        "entity_permissions.allowed_entity_codes": lambda: None,
    }


def _home(**over):
    """``(home_api module, frappe stub)`` with ``over`` replacing sources."""
    sources = _sources()
    for name, fn in over.items():
        sources[name.replace("__", ".")] = fn

    frappe = types.ModuleType("frappe")
    frappe.ValidationError = _ValidationError
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.local = types.SimpleNamespace(message_log=[])
    frappe.session = types.SimpleNamespace(user="zz@example.com")
    frappe.only_for_calls = []

    def throw(msg, exc=_ValidationError, *a, **kw):
        frappe.local.message_log.append(msg)
        raise exc(msg)

    def whitelist(methods=None, **kw):
        return lambda fn: fn

    frappe.throw = throw
    frappe.whitelist = whitelist
    frappe.only_for = lambda roles: frappe.only_for_calls.append(tuple(roles))
    frappe.db = types.SimpleNamespace()

    modules = {name: types.ModuleType(name) for name in (
        "period_api", "statement_api", "statement_model", "signoff_gate", "ic_api",
        "approvals_api", "freshness_api", "mywork_api", "setup", "entity_permissions")}
    modules["setup"].SETUP_ROLES = ("EPM Admin", "System Manager")
    targets = dict(modules, frappe=frappe, **{"frappe.db": frappe.db})
    for name, fn in sources.items():
        owner, _, attr = name.rpartition(".")
        setattr(targets[owner], attr, fn)

    close = types.ModuleType("konsol.close")
    for name in ("period_api", "statement_api", "statement_model", "signoff_gate", "ic_api",
                 "approvals_api", "freshness_api", "mywork_api"):
        setattr(close, name, modules[name])
    close.home_model = HOME_MODEL
    close.charts_model = CHARTS_MODEL
    close.overview_model = OVERVIEW_MODEL
    konsol = types.ModuleType("konsol")
    konsol.setup = modules["setup"]
    konsol.close = close
    stubs = {"frappe": frappe, "konsol": konsol, "konsol.close": close,
             "konsol.setup": modules["setup"],
             "konsol.entity_permissions": modules["entity_permissions"]}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("home_api_under_test", API)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return module, frappe


def _fails(frappe_ref, message):
    """A source that fails the way a reader does: frappe.throw."""
    def fn(*a, **kw):
        frappe_ref[0].throw(message)
    return fn


# --- every source answering ----------------------------------------------------

def test_every_section_is_filled_from_its_source():
    api, frappe = _home()
    home = api.get_home()
    assert [set(c) for c in frappe.only_for_calls] == [ROLES]
    assert home["header"]["period"] == "FY2025 P07" and home["header"]["group"] == "G1"
    assert home["header"]["periods"] == 6 and home["header"]["choices"] == [3, 6, 12]
    assert home["headline"]["count"] == 2 and home["headline"]["text"] == "2 items need you"
    tb, ic, days, result = home["tiles"]
    assert tb["state"] == "ok" and tb["display"] == "1 / 2" and tb["delta"] == -1
    assert tb["breakdown"] == "1 submitted · 0 exceptions · 1 missing"
    assert ic["state"] == "ok" and [p["value"] for p in ic["points"]] == [0, 1, 2]
    assert days["state"] == "empty" and "not signed off" in days["empty"]["text"]
    assert [p["value"] for p in days["points"]] == [12, 8, None]
    assert result["state"] == "ok" and result["value"] == 700.0
    assert result["display"] == "EUR 700" and result["breakdown"] == "Net sales 7K"
    assert home["period"]["close"]["text"] == "FY2025 P07 · Amber"
    assert home["period"]["waiting"] == 2
    rows = home["entities"]["rows"]
    assert [(r["entity"], r["tb"], r["ic"], r["open"]) for r in rows] == [
        ("A", "submitted", "over", True), ("B", "missing", "over", True)]
    assert [b["kind"] for b in home["banners"]] == ["setup"]
    assert home["setup"] == {"done": 3, "total": 11, "hidden": False, "route": "/desk/konsol-setup"}
    assert frappe.local.message_log == []


def test_the_periods_selector_drives_the_window():
    api, _ = _home()
    home = api.get_home(periods="3")
    assert [p["label"] for p in home["tiles"][1]["points"]] == [
        "FY2025 P05", "FY2025 P06", "FY2025 P07"]


def test_a_period_count_outside_the_choices_is_refused():
    api, _ = _home()
    with pytest.raises(_ValidationError):
        api.get_home(periods=5)


# --- one failing source ----------------------------------------------------------

#: source -> the sections that read it (index into tiles as "tiles.N").
FAILURES = {
    "mywork_api.get_my_work": {"headline"},
    "signoff_gate.trial_balance_progress": {"tiles.0", "entities"},
    "signoff_gate.latest_signed_runs": {"tiles.2"},
    "statement_api._statement": {"tiles.3"},
    "approvals_api.queue_for": {"period"},
    "setup.status": {"setup"},
}
SECTIONS = ("headline", "tiles.0", "tiles.1", "tiles.2", "tiles.3", "period", "entities", "setup")


def _get(home, section):
    if section.startswith("tiles."):
        return home["tiles"][int(section[6:])]
    return home[section]


@pytest.mark.parametrize("source", sorted(FAILURES))
def test_one_failing_source_blanks_only_its_own_sections(source):
    ref = []
    api, frappe = _home(**{source.replace(".", "__"): _fails(ref, "boom from " + source)})
    ref.append(frappe)
    home = api.get_home()
    for section in SECTIONS:
        value = _get(home, section)
        if section in FAILURES[source]:
            assert value == {"state": "unavailable", "reason": "boom from " + source}, section
        else:
            assert value is not None and value.get("state") != "unavailable", (section, value)
    assert frappe.local.message_log == []  # no dialog: the reason is in place


def test_no_current_period_names_the_reason_on_every_period_section():
    api, _ = _home(period_api__get_context=lambda: {
        "landing": {"period": None, "reason": "Declare the first close period."},
        "periods": []})
    home = api.get_home()
    assert home["header"]["period"] is None
    assert home["header"]["period_reason"] == "Declare the first close period."
    for section in ("tiles.0", "tiles.1", "tiles.2", "tiles.3", "period", "entities"):
        assert _get(home, section) == {"state": "unavailable",
                                       "reason": "Declare the first close period."}, section
    assert home["headline"]["state"] == "ok"


def test_a_failing_ic_read_is_a_gap_with_its_reason_never_zero():
    ref = []
    api, frappe = _home(ic_api__get_ic=_fails(ref, "The warehouse is not built."))
    ref.append(frappe)
    home = api.get_home()
    ic = home["tiles"][1]
    assert ic["state"] == "empty" and ic["value"] is None
    assert ic["empty"]["text"] == "The warehouse is not built."
    assert all(p["value"] is None for p in ic["points"]) and ic["spark"] == []
    assert {r["ic"] for r in home["entities"]["rows"]} == {"unknown"}
    assert frappe.local.message_log == []


def test_a_failing_freshness_read_is_named_on_the_period_card_only():
    ref = []
    api, frappe = _home(freshness_api__current_freshness=_fails(ref, "Redis is down"))
    ref.append(frappe)
    home = api.get_home()
    assert home["period"]["state"] == "ok"
    assert home["period"]["freshness"]["text"] == "Unavailable: Redis is down"
    assert [b["kind"] for b in home["banners"]] == ["setup"]


def test_a_running_build_shows_its_banner():
    api, _ = _home(freshness_api__current_freshness=lambda: {
        "state": "pending", "as_of": None, "pending": 1})
    home = api.get_home()
    assert [b["kind"] for b in home["banners"]] == ["build", "setup"]


def test_setup_is_not_read_for_a_role_outside_setup_roles():
    called = []
    api, _ = _home(frappe__get_roles=lambda user=None: ["EPM Analyst"],
                   setup__status=lambda: called.append(1))
    home = api.get_home()
    assert home["setup"] is None and not called
    assert home["banners"] == []


def test_several_groups_and_none_chosen_says_choose_on_the_net_result_only():
    api, _ = _home(statement_api__root_groups=lambda: [
        {"consolidation_group": "G1", "reporting_currency": "EUR"},
        {"consolidation_group": "G2", "reporting_currency": "USD"}])
    home = api.get_home()
    assert home["header"]["groups"] == ["G1", "G2"] and home["header"]["group"] is None
    assert home["tiles"][3] == {"state": "unavailable", "reason": "Choose a consolidation group."}
    assert home["tiles"][1]["value"] == 2  # every group's pairs
    home = api.get_home(group="G2")
    assert home["header"]["group"] == "G2"
    assert home["tiles"][1]["value"] == 0  # G2 has no pair
    assert home["tiles"][3]["display"] == "USD 700"


# --- the endpoint's source -----------------------------------------------------------

def _source():
    with open(API) as f:
        return f.read()


def test_get_home_is_a_gated_get():
    tree = ast.parse(_source())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_home")
    (deco,) = fn.decorator_list
    assert ast.unparse(deco) == "frappe.whitelist(methods=['GET'])"
    call = next(n for n in ast.walk(fn) if isinstance(n, ast.Call)
                and ast.unparse(n.func) == "frappe.only_for")
    assert set(ast.literal_eval(call.args[0])) == ROLES


def test_home_api_bypasses_no_permission_and_adds_no_query():
    source = _source()
    assert "ignore_permissions" not in source
    assert "frappe.get_all(" not in source
    assert "frappe.db.sql" not in source and "ch_read" not in source
    assert "frappe.db.commit" not in source
