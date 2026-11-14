"""S2-V1: konsol/budget_intake.py, the one entry every budget writer calls
(konsol#348).

It gathers the facts (chart, periods, entity currency, layers, scenarios,
Cycle), runs budget_bulk_model, then runs api._assert_budget_write_access on
every row and the layer's writer_role check. It returns a plan (rows grouped
by Budget Entry key, plus each group's problems) and writes nothing.

- A (scenario, year) with no Cycle is refused, naming the Cycle to create;
  nothing is auto-created (Q6-b). A Cycle that is not docstatus 0 is refused.
- An inactive Scenario is refused like an undeclared one.
- The same (entity, period, layer, account, dim_*) twice is refused, naming
  both lines.
- Layer codes are case-exact.

budget_intake imports frappe, so it is loaded against a stubbed frappe and
stubbed konsol modules for the facts it reads; budget_bulk_model is the real one.
"""
import importlib.util
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_PATH = os.path.join(APP_DIR, "budget_intake.py")

HEADER = ["scenario", "layer", "data_area_id", "fiscal_year", "fiscal_period",
          "main_account", "debit", "credit", "currency", "dim_cost_center"]
BASIS = "Period movement"


class Thrown(Exception):
    pass


class Denied(Exception):
    pass


def _matches(row, filters):
    for field, cond in (filters or {}).items():
        value = row.get(field)
        if isinstance(cond, (list, tuple)):
            op, arg = cond
            if op == "in" and value not in arg:
                return False
            if op != "in":
                raise AssertionError(f"stub does not know operator {op}")
        elif value != cond:
            return False
    return True


@pytest.fixture
def env(monkeypatch):
    data = {
        "Scenario": [
            {"name": "BUD", "scenario_type": "budget", "is_active": 1},
            {"name": "OLD", "scenario_type": "budget", "is_active": 0},
            {"name": "ACT", "scenario_type": "actual", "is_active": 1},
        ],
        "Budget Layer": [
            {"name": "base", "writer_role": None, "sort_order": 1},
            {"name": "board", "writer_role": "Budget Approver", "sort_order": 4},
        ],
        "Budget Cycle": [
            {"name": "BCYC-BUD-2027", "scenario_id": "BUD", "fiscal_year": 2027, "docstatus": 0,
             "status": "Open"},
            {"name": "BCYC-BUD-2028", "scenario_id": "BUD", "fiscal_year": 2028, "docstatus": 1},
            {"name": "BCYC-BUD-2029", "scenario_id": "BUD", "fiscal_year": 2029, "docstatus": 2},
            {"name": "BCYC-OLD-2027", "scenario_id": "OLD", "fiscal_year": 2027, "docstatus": 0},
        ],
        "Entity": [
            {"name": "E1", "functional_currency": "EUR"},
            {"name": "E2", "functional_currency": "EUR"},
        ],
        "ISO Currency": [{"name": "EUR", "minor_unit": 2}],
        "Dimension": [{"dimension_name": "dim_cost_center", "status": "Published", "in_budget": 1,
                       "in_trial_balance": 1}],
        # Rows the writer checks read (Q15-c, Q16-c), with a locking read.
        "Budget Entry": [],
        "Budget Entry Line": [],
        "Budget Annual Input": [],
    }
    state = types.SimpleNamespace(user="ann@example.com", roles=["Entity Accountant"],
                                  access_calls=[], denied_entities=set(), queries=[],
                                  locked_reads=[])

    def get_all(doctype, filters=None, fields=None, order_by=None, pluck=None, limit_page_length=None,
                **kw):
        state.queries.append((doctype, order_by))
        rows = [r for r in data[doctype] if _matches(r, filters)]
        if pluck:
            return [r[pluck] for r in rows]
        if fields:
            rows = [{f: r.get(f) for f in fields} for r in rows]
        return [types.SimpleNamespace(**r, get=r.get) for r in rows]

    def forbidden(*a, **k):
        raise AssertionError("budget_intake must not write")

    frappe = types.ModuleType("frappe")
    frappe.throw = lambda msg, exc=None, *a, **k: (_ for _ in ()).throw((exc or Thrown)(msg))
    frappe._ = lambda s: s
    frappe.ValidationError = Thrown
    frappe.PermissionError = Denied
    frappe.session = types.SimpleNamespace(user=None)
    frappe.get_all = get_all
    frappe.get_list = get_all
    frappe.get_roles = lambda user=None: list(state.roles)
    frappe.flags = types.SimpleNamespace(in_install=False, in_migrate=False)
    #: The dim_* fields each doctype's meta carries.
    state.meta_dims = {"Budget Annual Input": ["dim_cost_center"],
                       "Budget Entry Line": ["dim_cost_center"]}
    frappe.get_meta = lambda dt: types.SimpleNamespace(fields=[
        types.SimpleNamespace(fieldname=d, fieldtype="Data") for d in state.meta_dims[dt]] + [
        types.SimpleNamespace(fieldname="dim_section", fieldtype="Section Break")])
    frappe.new_doc = forbidden
    frappe.get_doc = forbidden
    def get_values(doctype, filters=None, fields=None, as_dict=False, order_by=None, for_update=False,
                   **kw):
        # The writer checks read what is committed now: under REPEATABLE READ
        # only a locking read does, so a plain read here is a test failure.
        state.locked_reads.append((doctype, dict(filters or {}), for_update, order_by))
        assert for_update, f"{doctype}: a writer check reads with a locking read"
        assert as_dict and order_by, (doctype, as_dict, order_by)
        rows = [r for r in data[doctype] if _matches(r, filters)]
        return [types.SimpleNamespace(**{f: r.get(f) for f in fields}, get=r.get) for r in rows]

    frappe.db = types.SimpleNamespace(set_value=forbidden, sql=forbidden, insert=forbidden,
                                      get_values=get_values)

    api = types.ModuleType("konsol.api")

    def access(row):
        state.access_calls.append(dict(row))
        if row.get("data_area_id") in state.denied_entities:
            raise Denied(f"Not permitted to write entity {row.get('data_area_id')}")
    api._assert_budget_write_access = access

    chart = types.ModuleType("konsol.group_chart")
    chart.chart_accounts = lambda: {"4000": {"is_group": 0, "is_posting": 1},
                                    "6000": {"is_group": 0, "is_posting": 1}}

    periods = types.ModuleType("konsol.period_status")

    class PeriodNotDeclared(Exception):
        pass

    def period_row(year, period):
        if int(year) in (2027, 2028, 2029) and 1 <= int(period) <= 12:
            return {"code": f"P{int(period):02d}", "type": "Regular", "status": "Open"}
        raise PeriodNotDeclared(f"{year} P{period} not declared")
    periods.PeriodNotDeclared = PeriodNotDeclared
    periods.period_row = period_row

    perms = types.ModuleType("konsol.entity_permissions")
    perms.BYPASS_ROLES = frozenset({"System Manager", "Administrator"})

    for name, mod in {"frappe": frappe, "konsol.api": api, "konsol.group_chart": chart,
                      "konsol.period_status": periods, "konsol.entity_permissions": perms}.items():
        monkeypatch.setitem(sys.modules, name, mod)

    spec = importlib.util.spec_from_file_location("_budget_intake_under_test", PY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def plan(table, form_basis=BASIS, name_lines=True, whole_slices=True):
        frappe.session.user = state.user
        return mod.plan(table, form_basis=form_basis, name_lines=name_lines, whole_slices=whole_slices)

    return types.SimpleNamespace(mod=mod, data=data, state=state, plan=plan)


def _row(entity="E1", period="1", account="4000", debit="100", credit="0", layer="base",
         scenario="BUD", year="2027", cc="CC1"):
    return [scenario, layer, entity, year, period, account, debit, credit, "EUR", cc]


def _errors(plan):
    return [e for item in plan["report"] for e in item["errors"]]


# ---- a clean file ------------------------------------------------------------

def test_a_clean_file_gives_a_plan_grouped_by_budget_entry_key(env):
    plan = env.plan([HEADER, _row(), _row(account="6000"), _row(period="2")])
    assert plan["ok"], _errors(plan)
    keys = list(plan["groups"])
    assert [(k.scenario, k.fiscal_year, k.data_area_id, k.layer, k.fiscal_period) for k in keys] == [
        ("BUD", 2027, "E1", "base", 1), ("BUD", 2027, "E1", "base", 2)]
    assert len(plan["groups"][keys[0]]) == 2
    first = plan["report"][0]
    assert first["cycle"] == "BCYC-BUD-2027"
    assert first["currency"] == "EUR"
    assert first["amount_basis"] == BASIS
    assert first["ok"] and first["rows"] == 2


def test_the_access_check_runs_once_per_row_with_entity_account_and_dimensions(env):
    env.plan([HEADER, _row(), _row(account="6000"), _row(period="2", cc="CC2")])
    calls = env.state.access_calls
    assert len(calls) == 3
    assert [(c["data_area_id"], c["main_account"], c["dim_cost_center"]) for c in calls] == [
        ("E1", "4000", "CC1"), ("E1", "6000", "CC1"), ("E1", "4000", "CC2")]


def test_an_ungranted_row_is_refused_naming_its_line(env):
    env.state.denied_entities = {"E2"}
    plan = env.plan([HEADER, _row(), _row(entity="E2")])
    assert not plan["ok"]
    ok, refused = plan["report"]
    assert ok["ok"], ok["errors"]
    assert not refused["ok"]
    assert any("Line 3" in e and "Not permitted to write entity E2" in e for e in refused["errors"]), \
        refused["errors"]


def test_an_access_denial_is_kept_apart_from_other_problems(env):
    # S2-X3: an API writer raises a PermissionError (403) for a denial, not a
    # ValidationError (417), so the plan says which refusals are denials. The
    # CSV report keeps naming them among the errors.
    env.state.denied_entities = {"E2"}
    plan = env.plan([HEADER, _row(), _row(entity="E2", layer="board")])
    ok, refused = plan["report"]
    assert ok["denials"] == []
    (denial,) = [d for d in refused["denials"] if "Not permitted" in d]
    assert "Not permitted to write entity E2" in denial
    assert denial in refused["errors"]
    # Review fix 7a: the layer's writer role is an access denial too (403);
    # the CSV report still names it among the errors.
    (role,) = [d for d in refused["denials"] if "Budget Approver" in d]
    assert role in refused["errors"]


def test_a_clean_group_has_no_denials(env):
    plan = env.plan([HEADER, _row()])
    assert [item["denials"] for item in plan["report"]] == [[]]


# ---- S2-X3: the caller chooses whether problems name file lines ------------------

def test_a_file_writer_gets_its_line_numbers(env):
    env.state.denied_entities = {"E2"}
    plan = env.plan([HEADER, _row(account="9999"), _row(entity="E2")], name_lines=True)
    first, second = plan["report"]
    assert any(e.startswith("Line 2: ") and "9999" in e for e in first["errors"]), first["errors"]
    assert second["denials"] == ["Line 3: Not permitted to write entity E2"]


def test_a_writer_without_lines_gets_no_line_prefix(env):
    # An Excel cell or an API row has no file line: "Line 2:" would point at
    # nothing (the one-row table's header is line 1).
    env.state.denied_entities = {"E2"}
    plan = env.plan([HEADER, _row(account="9999"), _row(entity="E2", debit="1.234")],
                    name_lines=False)
    first, second = plan["report"]
    errors = first["errors"] + second["errors"]
    assert errors, plan
    assert not [e for e in errors if "Line" in e or "line" in e], errors
    assert any("9999" in e for e in first["errors"]), first["errors"]
    assert second["denials"] == ["Not permitted to write entity E2"]
    assert any("1.234" in e for e in second["errors"]), second["errors"]


def test_a_duplicate_without_lines_names_the_account(env):
    plan = env.plan([HEADER, _row(), _row()], name_lines=False)
    (item,) = plan["report"]
    dupes = [e for e in item["errors"] if "4000" in e]
    assert dupes and not [e for e in dupes if "Line" in e or "None" in e], item["errors"]


def test_the_caller_must_say_whether_lines_are_named(env):
    with pytest.raises(TypeError):
        env.mod.plan([HEADER, _row()], form_basis=BASIS)


def test_the_plan_writes_nothing(env):
    # new_doc, get_doc and db writes raise in the stub
    plan = env.plan([HEADER, _row()])
    assert plan["ok"], _errors(plan)


# ---- the layer's writer role ------------------------------------------------

def test_a_user_without_the_layers_writer_role_is_refused(env):
    plan = env.plan([HEADER, _row(layer="board")])
    assert not plan["ok"]
    errors = _errors(plan)
    assert any("board" in e and "Budget Approver" in e for e in errors), errors


def test_a_holder_of_the_layers_writer_role_is_accepted(env):
    env.state.roles = ["Entity Accountant", "Budget Approver"]
    plan = env.plan([HEADER, _row(layer="board")])
    assert plan["ok"], _errors(plan)


@pytest.mark.parametrize("user, roles", [("Administrator", []), ("sam@example.com", ["System Manager"])])
def test_system_manager_and_administrator_bypass_the_writer_role(env, user, roles):
    env.state.user, env.state.roles = user, roles
    plan = env.plan([HEADER, _row(layer="board")])
    assert plan["ok"], _errors(plan)


def test_a_layer_without_a_writer_role_takes_anyone(env):
    plan = env.plan([HEADER, _row(layer="base")])
    assert plan["ok"], _errors(plan)


def test_layer_codes_are_case_exact_and_the_refusal_lists_the_declared_codes(env):
    plan = env.plan([HEADER, _row(layer="Base")])
    assert not plan["ok"]
    errors = _errors(plan)
    assert any("Layer Base is not declared" in e and "base, board" in e for e in errors), errors


# ---- the Cycle ----------------------------------------------------------------

def test_a_missing_cycle_is_refused_by_name_and_never_created(env):
    env.data["Budget Cycle"] = [c for c in env.data["Budget Cycle"] if c["fiscal_year"] != 2027]
    plan = env.plan([HEADER, _row()])
    assert not plan["ok"]
    item = plan["report"][0]
    assert item["cycle"] is None
    assert any("Budget Cycle" in e and "BUD" in e and "2027" in e and "Create" in e for e in item["errors"]), \
        item["errors"]
    assert env.data["Budget Cycle"] == [c for c in env.data["Budget Cycle"] if c["fiscal_year"] != 2027]


@pytest.mark.parametrize("year, word", [("2028", "approved"), ("2029", "cancelled")])
def test_a_cycle_that_is_not_a_draft_is_refused(env, year, word):
    plan = env.plan([HEADER, _row(year=year)])
    assert not plan["ok"]
    errors = _errors(plan)
    assert any(f"BCYC-BUD-{year}" in e and word in e for e in errors), errors


# ---- scenarios ----------------------------------------------------------------

def test_an_inactive_scenario_is_refused_as_inactive_not_undeclared(env):
    plan = env.plan([HEADER, _row(scenario="OLD")])
    assert not plan["ok"]
    errors = _errors(plan)
    assert any("Scenario OLD is inactive" in e for e in errors), errors
    assert not any("not declared" in e for e in errors), errors


def test_a_scenario_that_does_not_exist_is_still_not_declared(env):
    plan = env.plan([HEADER, _row(scenario="NOPE")])
    assert any("Scenario NOPE is not declared" in e for e in _errors(plan)), _errors(plan)


def test_an_actual_scenario_is_refused(env):
    env.data["Budget Cycle"].append({"name": "BCYC-ACT-2027", "scenario_id": "ACT", "fiscal_year": 2027,
                                     "docstatus": 0})
    plan = env.plan([HEADER, _row(scenario="ACT")])
    assert any("actual scenario" in e for e in _errors(plan)), _errors(plan)


# ---- duplicate rows -------------------------------------------------------------

def test_the_same_account_and_dimensions_twice_is_refused_naming_both_lines(env):
    plan = env.plan([HEADER, _row(), _row(account="6000"), _row(debit="5")])
    assert not plan["ok"]
    errors = _errors(plan)
    assert any("Lines 2 and 4" in e and "4000" in e for e in errors), errors


def test_the_same_account_with_other_dimensions_is_not_a_duplicate(env):
    plan = env.plan([HEADER, _row(), _row(cc="CC2")])
    assert plan["ok"], _errors(plan)


def test_the_same_account_in_another_layer_or_period_is_not_a_duplicate(env):
    env.state.roles = ["Budget Approver"]
    plan = env.plan([HEADER, _row(), _row(layer="board"), _row(period="2")])
    assert plan["ok"], _errors(plan)


# ---- facts are read in a stated order (v16 sorts by creation) ----------------------

def test_every_fact_query_states_its_order(env):
    env.plan([HEADER, _row()])
    for doctype in ("Scenario", "Budget Layer", "Budget Cycle", "Dimension"):
        orders = [o for d, o in env.state.queries if d == doctype]
        assert orders, f"{doctype} was not read"
        assert all(orders), f"{doctype} is read without order_by"


def test_inactive_scenarios_are_gathered_so_they_can_be_named(env):
    seen = {}

    original = sys.modules["frappe"].get_all

    def spy(doctype, filters=None, fields=None, **kw):
        if doctype == "Scenario":
            seen["filters"], seen["fields"] = filters, fields
        return original(doctype, filters=filters, fields=fields, **kw)
    sys.modules["frappe"].get_all = spy
    env.plan([HEADER, _row()])
    assert "is_active" not in (seen["filters"] or {})
    assert "is_active" in seen["fields"]


# ---- the annual entry (A6): Budget Annual Input's validate -------------------------

def _annual(**over):
    row = {"scenario_id": "BUD", "fiscal_year": 2027, "data_area_id": "E1", "main_account": "4000",
           "layer": "base", "currency": "EUR", "dim_cost_center": "CC1"}
    row.update(over)
    return row


def _annual_check(env, **over):
    env.plan  # fixture sets the frappe stub's user via plan(); do it here too
    sys.modules["frappe"].session.user = env.state.user
    env.mod.annual_check(_annual(**over))


def test_a_clean_annual_row_passes_and_runs_the_access_check_once(env):
    _annual_check(env)
    assert [(c["data_area_id"], c["main_account"], c["dim_cost_center"])
            for c in env.state.access_calls] == [("E1", "4000", "CC1")]


def test_an_annual_row_in_the_wrong_currency_is_refused(env):
    with pytest.raises(Thrown, match="USD"):
        _annual_check(env, currency="USD")


def test_an_annual_row_in_an_undeclared_layer_is_refused(env):
    with pytest.raises(Thrown, match="Layer nope is not declared"):
        _annual_check(env, layer="nope")


def test_an_annual_row_in_a_role_held_layer_needs_the_role(env):
    # An access denial (review fix 7a), as on the cell path.
    with pytest.raises(Denied, match="Budget Approver"):
        _annual_check(env, layer="board")
    env.state.roles = ["Budget Approver"]
    _annual_check(env, layer="board")


def test_an_annual_row_on_an_inactive_scenario_says_inactive(env):
    with pytest.raises(Thrown, match="Scenario OLD is inactive"):
        _annual_check(env, scenario_id="OLD")


def test_an_annual_row_on_an_actual_scenario_is_refused(env):
    with pytest.raises(Thrown, match="actual scenario"):
        _annual_check(env, scenario_id="ACT")


def test_an_ungranted_annual_row_raises_the_permission_error(env):
    env.state.denied_entities = {"E1"}
    with pytest.raises(Denied):
        _annual_check(env)


def test_the_annual_access_check_alone_runs_for_a_delete(env):
    env.mod.assert_annual_access(_annual(layer="nope", currency="USD"))
    assert len(env.state.access_calls) == 1


def test_a_writer_role_refusal_is_a_denial_even_when_access_is_granted(env):
    plan = env.plan([HEADER, _row(layer="board")])
    (item,) = plan["report"]
    (denial,) = item["denials"]
    assert "board" in denial and "Budget Approver" in denial
    assert item["errors"] == [denial]


# ---- re-review 6a: removing lines is a write to them ---------------------------------

def test_removing_lines_runs_the_access_check_on_each(env):
    sys.modules["frappe"].session.user = env.state.user
    env.mod.assert_may_remove("E1", "base", [{"main_account": "4000", "dim_cost_center": "CC1"}])
    assert [(c["data_area_id"], c["main_account"], c["dim_cost_center"]) for c in env.state.access_calls] == [
        ("E1", "4000", "CC1")]


def test_removing_a_line_of_a_denied_entity_is_refused(env):
    sys.modules["frappe"].session.user = env.state.user
    env.state.denied_entities = {"E2"}
    with pytest.raises(Denied):
        env.mod.assert_may_remove("E2", "base", [{"main_account": "4000"}])


def test_removing_a_line_needs_the_layers_writer_role(env):
    sys.modules["frappe"].session.user = env.state.user
    with pytest.raises(Denied, match="Budget Approver"):
        env.mod.assert_may_remove("E1", "board", [{"main_account": "4000"}])
    env.state.roles = ["Budget Approver"]
    env.mod.assert_may_remove("E1", "board", [{"main_account": "4000"}])


# ---- Q15-c: one amount basis per (cycle, entity, layer) ------------------------

YTD = "Year-to-date movement"


def _entry_row(name, period, basis, cycle="BCYC-BUD-2027", entity="E1", layer="base"):
    return {"name": name, "cycle": cycle, "data_area_id": entity, "layer": layer,
            "fiscal_period": period, "amount_basis": basis}


def test_a_second_basis_in_one_cycle_entity_and_layer_is_refused_naming_the_entry(env):
    env.data["Budget Entry"] = [_entry_row("BENT-P1", 1, YTD)]
    with pytest.raises(Thrown) as raised:
        env.mod.assert_one_basis("BCYC-BUD-2027", "E1", "base", BASIS, name="BENT-P2")
    msg = str(raised.value)
    assert "Budget Entry BENT-P1" in msg and "period 1" in msg and YTD in msg and BASIS in msg, msg


def test_the_same_basis_in_another_period_is_accepted(env):
    env.data["Budget Entry"] = [_entry_row("BENT-P1", 1, BASIS)]
    env.mod.assert_one_basis("BCYC-BUD-2027", "E1", "base", BASIS, name="BENT-P2")


@pytest.mark.parametrize("other", [dict(entity="E2"), dict(layer="board"), dict(cycle="BCYC-OLD-2027")])
def test_another_basis_in_another_entity_layer_or_cycle_is_accepted(env, other):
    env.data["Budget Entry"] = [_entry_row("BENT-X", 1, YTD, **other)]
    env.mod.assert_one_basis("BCYC-BUD-2027", "E1", "base", BASIS, name="BENT-P2")


def test_an_entry_does_not_conflict_with_itself(env):
    # A Desk change of the only entry's basis is not a second basis.
    env.data["Budget Entry"] = [_entry_row("BENT-P1", 1, YTD)]
    env.mod.assert_one_basis("BCYC-BUD-2027", "E1", "base", BASIS, name="BENT-P1")


def test_entries_being_replaced_in_the_same_write_do_not_conflict(env):
    # A Budget Upload replaces a slice whole: its old entries are rewritten in
    # the same transaction, so their old basis is not a second one.
    env.data["Budget Entry"] = [_entry_row("BENT-P1", 1, YTD), _entry_row("BENT-P2", 2, YTD)]
    env.mod.assert_one_basis("BCYC-BUD-2027", "E1", "base", BASIS, name="BENT-P1",
                             replacing={"BENT-P1", "BENT-P2"})


def test_the_basis_check_reads_the_entries_with_a_locking_read(env):
    env.mod.assert_one_basis("BCYC-BUD-2027", "E1", "base", BASIS, name="BENT-P2")
    ((doctype, filters, for_update, _order),) = env.state.locked_reads
    assert doctype == "Budget Entry" and for_update
    assert (filters["cycle"], filters["data_area_id"], filters["layer"]) == ("BCYC-BUD-2027", "E1", "base")


def test_a_file_giving_two_bases_to_one_entity_and_layer_is_refused_in_both_periods(env):
    header = HEADER + ["amount_basis"]
    plan = env.plan([header, _row(period="1") + [BASIS], _row(period="2") + [YTD],
                     _row(period="1", entity="E2") + [YTD]], form_basis="")
    p1, p2, other = plan["report"]
    assert other["ok"], other["errors"]
    for item, mine, theirs, period in ((p1, BASIS, YTD, 2), (p2, YTD, BASIS, 1)):
        assert not item["ok"]
        assert any(f"period {period}" in e and theirs in e and mine in e for e in item["errors"]), item["errors"]


# ---- Q16-c: a grain takes its numbers from one writer -----------------------------

def _annual_input(name="BAI-1", layer="base", account="4000", cc="CC1", entity="E1", year=2027,
            scenario="BUD"):
    return {"name": name, "scenario_id": scenario, "fiscal_year": year, "data_area_id": entity,
            "layer": layer, "main_account": account, "dim_cost_center": cc}


def _line(parent="BENT-P3", period=3, layer="base", account="4000", cc="CC1", entity="E1", year=2027,
          scenario="BUD", cycle="BCYC-BUD-2027"):
    return {"parent": parent, "parenttype": "Budget Entry", "scenario_id": scenario, "cycle": cycle,
            "fiscal_year": year, "data_area_id": entity, "layer": layer, "fiscal_period": period,
            "main_account": account, "dim_cost_center": cc}


def _entry_side(env, lines, **kw):
    args = dict(scenario_id="BUD", fiscal_year=2027, entity="E1", layer="base", entry="BENT-P3",
                period=3)
    args.update(kw)
    env.mod.assert_no_annual_input(args.pop("scenario_id"), args.pop("fiscal_year"),
                                   args.pop("entity"), args.pop("layer"), lines, **args)


def test_an_entry_line_on_an_annual_input_grain_is_refused_naming_it_and_the_way_out(env):
    env.data["Budget Annual Input"] = [_annual_input()]
    with pytest.raises(Thrown) as raised:
        _entry_side(env, [{"main_account": "4000", "dim_cost_center": "CC1"}])
    msg = str(raised.value)
    assert "Budget Annual Input BAI-1" in msg and "4000" in msg and "period 3" in msg, msg
    assert "Delete Budget Annual Input BAI-1" in msg, msg
    assert "enter the year as monthly entries" in msg, msg


@pytest.mark.parametrize("other", [dict(layer="board"), dict(cc="CC2"), dict(account="6000"),
                                   dict(entity="E2"), dict(year=2028), dict(scenario="OLD")])
def test_an_annual_input_on_another_grain_does_not_refuse_the_entry(env, other):
    env.data["Budget Annual Input"] = [_annual_input(**other)]
    _entry_side(env, [{"main_account": "4000", "dim_cost_center": "CC1"}])


def test_a_blank_dimension_matches_an_unset_one_across_the_writers(env):
    env.data["Budget Annual Input"] = [_annual_input(cc=None)]
    with pytest.raises(Thrown, match="BAI-1"):
        _entry_side(env, [{"main_account": "4000", "dim_cost_center": ""}])


def test_the_entry_side_reads_the_annual_inputs_with_a_locking_read(env):
    _entry_side(env, [{"main_account": "4000", "dim_cost_center": "CC1"}])
    ((doctype, filters, for_update, _order),) = env.state.locked_reads
    assert doctype == "Budget Annual Input" and for_update
    assert (filters["scenario_id"], filters["fiscal_year"], filters["data_area_id"], filters["layer"]) == (
        "BUD", 2027, "E1", "base")


def test_an_entry_without_lines_reads_nothing(env):
    _entry_side(env, [])
    assert env.state.locked_reads == []


def _annual_side(env, **over):
    row = {"scenario_id": "BUD", "fiscal_year": 2027, "data_area_id": "E1", "layer": "base",
           "main_account": "4000", "dim_cost_center": "CC1"}
    row.update(over)
    env.mod.assert_no_entry_lines(row, name="BAI-NEW")


def test_an_annual_input_on_a_budget_entry_grain_is_refused_naming_the_entry_and_period(env):
    env.data["Budget Entry Line"] = [_line(parent="BENT-P3", period=3)]
    with pytest.raises(Thrown) as raised:
        _annual_side(env)
    msg = str(raised.value)
    assert "Budget Entry BENT-P3" in msg and "period 3" in msg and "4000" in msg, msg


@pytest.mark.parametrize("other", [dict(layer="board"), dict(cc="CC2"), dict(account="6000"),
                                   dict(entity="E2"), dict(year=2028), dict(scenario="OLD")])
def test_a_budget_entry_line_on_another_grain_does_not_refuse_the_annual_input(env, other):
    env.data["Budget Entry Line"] = [_line(**other)]
    _annual_side(env)


def test_the_annual_side_matches_a_blank_dimension_to_an_unset_one(env):
    env.data["Budget Entry Line"] = [_line(cc="")]
    with pytest.raises(Thrown, match="BENT-P3"):
        _annual_side(env, dim_cost_center=None)


def test_the_annual_side_reads_the_entry_lines_with_a_locking_read(env):
    _annual_side(env)
    ((doctype, filters, for_update, _order),) = env.state.locked_reads
    assert doctype == "Budget Entry Line" and for_update
    assert filters["parenttype"] == "Budget Entry"


def test_lock_cycles_locks_every_cycle_of_the_scenario_and_year(env):
    cycles = env.mod.lock_cycles("BUD", 2027)
    # PR #522 review 2: the lock returns each Cycle's state as it reads it, so
    # the caller judges the Cycle by what is committed now, not its snapshot.
    assert cycles == [{"name": "BCYC-BUD-2027", "docstatus": 0, "status": "Open"}]
    ((doctype, filters, for_update, order_by),) = env.state.locked_reads
    assert doctype == "Budget Cycle" and for_update and order_by == "name asc"
    assert filters == {"scenario_id": "BUD", "fiscal_year": 2027}


@pytest.mark.parametrize("flag", ["in_install", "in_migrate"])
def test_lock_cycles_is_skipped_in_install_and_migrate(env, flag):
    setattr(env.mod.frappe.flags, flag, True)
    assert env.mod.lock_cycles("BUD", 2027) == []
    assert env.state.locked_reads == []


# ---- 19-b: a cancelled Cycle's lines do not block ---------------------------------

def test_lines_under_a_cancelled_cycle_do_not_block_an_annual_input(env):
    # An amended pair: the cancelled original's entries stay as the audit trail.
    env.data["Budget Cycle"].append({"name": "BCYC-BUD-2027-OLD", "scenario_id": "BUD",
                                     "fiscal_year": 2027, "docstatus": 2})
    env.data["Budget Entry Line"] = [_line(cycle="BCYC-BUD-2027-OLD")]
    _annual_side(env)


@pytest.mark.parametrize("docstatus", [0, 1])
def test_lines_under_a_draft_or_approved_cycle_still_block(env, docstatus):
    env.data["Budget Cycle"].append({"name": "BCYC-BUD-2027-X", "scenario_id": "BUD",
                                     "fiscal_year": 2027, "docstatus": docstatus})
    env.data["Budget Entry Line"] = [_line(cycle="BCYC-BUD-2027-X")]
    with pytest.raises(Thrown, match="BENT-P3"):
        _annual_side(env)


# ---- PR #522 review 1: every locking read runs on an index -------------------------
# Without an index a FOR UPDATE scans the table and InnoDB next-key-locks every
# row it scans: two cell saves in different Cycles each hold their own entry and
# wait on the other's (1213), and every budget writer queues behind the scan.

EXPECTED_LOCK_INDEXES = {
    "Budget Cycle": ("scenario_id", "fiscal_year"),
    "Budget Entry": ("cycle", "data_area_id", "layer"),
    "Budget Entry Line": ("scenario_id", "fiscal_year", "data_area_id", "layer", "main_account"),
    "Budget Annual Input": ("scenario_id", "fiscal_year", "data_area_id", "layer", "main_account"),
}

CONTROLLERS = {
    "Budget Cycle": "budget_cycle",
    "Budget Entry": "budget_entry",
    "Budget Entry Line": "budget_entry_line",
    "Budget Annual Input": "budget_annual_input",
}


def test_the_lock_indexes_are_declared_once(env):
    assert env.mod.LOCK_INDEXES == EXPECTED_LOCK_INDEXES


def test_add_lock_index_adds_the_doctypes_index_by_name(env):
    added = []
    env.mod.frappe.db.add_index = lambda dt, fields, index_name=None: added.append((dt, fields, index_name))
    env.mod.ensure_lock_indexes()
    assert added == [(dt, list(cols), "budget_lock") for dt, cols in EXPECTED_LOCK_INDEXES.items()]


@pytest.mark.parametrize("doctype", sorted(CONTROLLERS))
def test_each_doctype_adds_its_lock_index_on_doctype_update(doctype):
    """Frappe runs a controller module's on_doctype_update on doctype sync."""
    import ast
    folder = CONTROLLERS[doctype]
    path = os.path.join(os.path.dirname(PY_PATH), "epm", "doctype", folder, f"{folder}.py")
    with open(path) as f:
        tree = ast.parse(f.read())
    (fn,) = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "on_doctype_update"]
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", getattr(n.func, "id", None)) == "add_lock_index"]
    assert [c.args[0].value for c in calls] == [doctype]


def test_migrate_adds_the_lock_indexes_on_existing_sites():
    """on_doctype_update runs only when a doctype's JSON changes; after_migrate
    runs _ensure_indexes every time."""
    with open(os.path.join(os.path.dirname(PY_PATH), "install.py")) as f:
        src = f.read()
    body = src.split("def _ensure_indexes")[1].split("\ndef ")[0]
    assert "ensure_lock_indexes()" in body


def _assert_on_index(env, doctype, filters):
    cols = EXPECTED_LOCK_INDEXES[doctype]
    assert set(cols) <= set(filters), (doctype, filters)
    # Equality on every column but the last, which may be an IN list.
    for col in cols[:-1]:
        assert not isinstance(filters[col], (list, tuple)), (doctype, col, filters[col])


def test_every_locking_read_in_the_intake_uses_its_index(env):
    env.data["Budget Entry"] = [{"name": "BENT-1", "cycle": "BCYC-BUD-2027", "data_area_id": "E1",
                                 "layer": "base", "fiscal_period": 1, "amount_basis": BASIS}]
    env.mod.assert_one_basis("BCYC-BUD-2027", "E1", "base", BASIS, name="BENT-2")
    _entry_side(env, [{"main_account": "4000", "dim_cost_center": "CC1"}])
    _annual_side(env)
    env.mod.lock_cycles("BUD", 2027)
    reads = [(dt, f) for dt, f, _u, _o in env.state.locked_reads if not (dt == "Budget Cycle" and "name" in f)]
    assert {dt for dt, _ in reads} == set(EXPECTED_LOCK_INDEXES), reads
    for doctype, filters in reads:
        _assert_on_index(env, doctype, filters)



# ---- PR #522 review 4: the check names a saved Annual Input on the file's grain ---
# Load refuses such a slice (Q16-c, Budget Entry's controller); the check must
# not call it ready.

def test_the_check_refuses_a_row_on_a_saved_annual_input_grain_naming_it(env):
    env.data["Budget Annual Input"] = [_annual_input()]
    plan = env.plan([HEADER, _row(), _row(account="6000")])
    (item,) = plan["report"]
    assert not item["ok"]
    (error,) = [e for e in item["errors"] if "Budget Annual Input" in e]
    assert error.startswith("Line 2: ") and "Budget Annual Input BAI-1" in error and "4000" in error, error
    assert "Delete Budget Annual Input BAI-1" in error, error


@pytest.mark.parametrize("other", [dict(layer="board"), dict(cc="CC2"), dict(account="6000"),
                                   dict(entity="E2"), dict(year=2028), dict(scenario="OLD")])
def test_an_annual_input_on_another_grain_leaves_the_check_ready(env, other):
    env.data["Budget Annual Input"] = [_annual_input(**other)]
    plan = env.plan([HEADER, _row()])
    assert plan["ok"], _errors(plan)


def test_the_check_matches_a_blank_dimension_to_an_unset_one(env):
    env.data["Budget Annual Input"] = [_annual_input(cc=None)]
    plan = env.plan([HEADER, _row(cc="")])
    assert any("BAI-1" in e for e in _errors(plan)), _errors(plan)


def test_the_checks_annual_input_pass_is_a_report_not_a_lock(env):
    # plan judges a file; the locking read is the controller's, at save.
    env.data["Budget Annual Input"] = [_annual_input()]
    env.plan([HEADER, _row()])
    assert not [r for r in env.state.locked_reads if r[0] == "Budget Annual Input"]


def test_a_one_writer_refusal_raises_its_own_error_class(env):
    assert issubclass(env.mod.SecondWriterError, Thrown)
    env.data["Budget Annual Input"] = [_annual_input()]
    with pytest.raises(env.mod.SecondWriterError):
        _entry_side(env, [{"main_account": "4000", "dim_cost_center": "CC1"}])
    env.data["Budget Entry Line"] = [_line()]
    with pytest.raises(env.mod.SecondWriterError):
        _annual_side(env)


# ---- Q21 (a): a Year-to-date slice states every period from 1 to its last --------

def _ytd_plan(env, periods, entity="E1", layer="base", whole_slices=True):
    table = [HEADER] + [_row(entity=entity, layer=layer, period=str(p)) for p in periods]
    return env.plan(table, form_basis=YTD, whole_slices=whole_slices)


def test_a_ytd_slice_that_starts_late_is_refused_naming_the_slice_and_periods(env):
    errors = _errors(_ytd_plan(env, range(7, 13)))
    assert errors and all("Year-to-date lines for E1 base state periods 7\u201312 but not 1\u20136. "
                          "State every period from 1, or use the Movement basis." in e for e in errors), errors


def test_a_ytd_slice_that_skips_a_period_is_refused(env):
    errors = _errors(_ytd_plan(env, [1, 2, 4]))
    assert errors and all("state periods 1\u20132, 4 but not 3." in e for e in errors), errors


def test_a_ytd_slice_from_period_1_without_gaps_is_accepted(env):
    assert _errors(_ytd_plan(env, [1, 2, 3])) == []


def test_a_movement_slice_may_start_late_and_skip(env):
    table = [HEADER, _row(period="7"), _row(period="9")]
    assert _errors(env.plan(table, form_basis=BASIS, whole_slices=True)) == []


def test_each_slice_is_judged_alone(env):
    table = [HEADER, _row(period="1"), _row(period="2"), _row(entity="E2", period="2")]
    errors = _errors(env.plan(table, form_basis=YTD, whole_slices=True))
    assert len(errors) == 1 and "for E2 base" in errors[0], errors


def test_a_single_entry_writer_is_not_judged_by_the_plan(env):
    # One cell is one period: the slice's other periods are read under lock
    # by assert_ytd_complete, not known to the plan.
    assert _errors(_ytd_plan(env, [7], whole_slices=False)) == []


def _saved(env, *periods, basis=YTD, **kw):
    env.data["Budget Entry"] = [_entry_row(f"BENT-P{p}", p, basis, **kw) for p in periods]


def test_a_write_that_leaves_a_saved_slice_with_a_gap_is_refused(env):
    _saved(env, 1, 2)
    with pytest.raises(Thrown) as raised:
        env.mod.assert_ytd_complete("BCYC-BUD-2027", "E1", "base", YTD, 4, name="BENT-P4")
    assert "Year-to-date lines for E1 base state periods 1\u20132, 4 but not 3." in str(raised.value)


def test_the_next_period_after_the_saved_ones_is_accepted(env):
    _saved(env, 1, 2)
    env.mod.assert_ytd_complete("BCYC-BUD-2027", "E1", "base", YTD, 3, name="BENT-P3")


def test_a_first_write_must_be_period_1(env):
    env.mod.assert_ytd_complete("BCYC-BUD-2027", "E1", "base", YTD, 1, name="BENT-P1")
    with pytest.raises(Thrown, match="but not 1\u20136"):
        env.mod.assert_ytd_complete("BCYC-BUD-2027", "E1", "base", YTD, 7, name="BENT-P7")


def test_rewriting_a_saved_period_does_not_count_it_twice(env):
    _saved(env, 1, 2, 3)
    env.mod.assert_ytd_complete("BCYC-BUD-2027", "E1", "base", YTD, 2, name="BENT-P2")


def test_a_movement_write_is_not_judged(env):
    env.mod.assert_ytd_complete("BCYC-BUD-2027", "E1", "base", BASIS, 7, name="BENT-P7")
    assert env.state.locked_reads == []


def test_the_completeness_check_reads_the_slice_with_a_locking_read(env):
    env.mod.assert_ytd_complete("BCYC-BUD-2027", "E1", "base", YTD, 1, name="BENT-P1")
    ((doctype, filters, for_update, _order),) = env.state.locked_reads
    assert doctype == "Budget Entry" and for_update


# ---- a failed lock-index setup is not silent --------------------------------------

def _ensure_indexes_fn(monkeypatch, tb_error, lock_error):
    """install._ensure_indexes, run alone against stubbed imports. Returns
    (function, warnings logged)."""
    import ast

    with open(os.path.join(os.path.dirname(PY_PATH), "install.py")) as f:
        tree = ast.parse(f.read())
    (fn,) = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_ensure_indexes"]
    warnings = []
    logger = types.SimpleNamespace(warning=lambda *a, **k: warnings.append(a))
    frappe = types.SimpleNamespace(logger=lambda: logger)

    def raiser(error):
        def run():
            if error:
                raise error
        return run

    tb = types.ModuleType("konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission")
    tb.on_doctype_update = raiser(tb_error)
    intake = types.ModuleType("konsol.budget_intake")
    intake.ensure_lock_indexes = raiser(lock_error)
    for name, mod in {tb.__name__: tb, "konsol.budget_intake": intake}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    namespace = {"frappe": frappe}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "install.py", "exec"), namespace)
    return namespace["_ensure_indexes"], warnings


def test_a_failed_lock_index_setup_raises_from_ensure_indexes(monkeypatch):
    run, _ = _ensure_indexes_fn(monkeypatch, None, RuntimeError("index failed"))
    with pytest.raises(RuntimeError, match="index failed"):
        run()


def test_another_index_failure_still_only_logs(monkeypatch):
    run, warnings = _ensure_indexes_fn(monkeypatch, RuntimeError("tb index failed"), None)
    run()
    assert len(warnings) == 1
