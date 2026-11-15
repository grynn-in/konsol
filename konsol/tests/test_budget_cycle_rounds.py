"""S3 K3/K4: a forecast Cycle is one round (konsol#348, Q23 a / Q24 a / QB8 a).

A forecast round is (scenario, fiscal year, as-of date). The Cycle controller

- refuses a forecast Cycle without an As-of Date or a Horizon of 1 or more
  periods, and a Cycle of any other type that carries either
  (budget_bulk_model.cycle_round_problems);
- holds one live Cycle (docstatus 0 or 1) per ROUND: two forecast rounds of
  one pair live side by side, a second Cycle of one round is refused by name.
  A budget pair is its own round, so its rule is unchanged
  (test_budget_cycle_amend.py pins the sentence);
- names a forecast Cycle with its as-of date in the digest (K4), so two rounds
  of one pair get different base names; the ``-N`` scheme stays.

Site-free: the controller is loaded against stubbed frappe, budget_grain,
scenario and budget_intake modules, as test_budget_cycle_amend.py does. The
digest stub records its parts, so the tests pin what the name is made of.
"""
import datetime
import importlib.util
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CYCLE_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_cycle", "budget_cycle.py")


class _Thrown(Exception):
    pass


@pytest.fixture
def env(monkeypatch):
    names = set()
    pair = []            # what lock_cycles returns for the pair
    locked = []          # the (scenario, year) pairs lock_cycles was asked for
    kind = ["forecast"]  # what locked_scenario_type answers
    digests = []         # the parts each digest_name call was given

    class Document:
        def __init__(self, **kw):
            self.flags = types.SimpleNamespace(name_set=False)
            self.__dict__.update(kw)

        def get(self, key, default=None):
            return self.__dict__.get(key, default)

    def throw(msg, exc=None, *a, **k):
        raise _Thrown(msg)

    frappe = types.ModuleType("frappe")
    frappe._ = lambda s: s
    frappe.throw = throw
    frappe.ValidationError = _Thrown
    frappe.flags = types.SimpleNamespace(in_install=False, in_migrate=False)
    frappe.db = types.SimpleNamespace(exists=lambda dt, name: name in names, count=lambda *a, **k: 0)
    model = types.ModuleType("frappe.model")
    docmod = types.ModuleType("frappe.model.document")
    docmod.Document = Document
    grain = types.ModuleType("konsol.epm.budget_grain")

    def digest_name(prefix, parts):
        digests.append(list(parts))
        return prefix + "-" + "-".join(str(p) for p in parts)

    grain.digest_name = digest_name
    scenario = types.ModuleType("konsol.epm.doctype.scenario.scenario")
    scenario.locked_scenario_type = lambda s: kind[0]
    intake = types.ModuleType("konsol.budget_intake")

    def lock_cycles(scenario_id, fiscal_year, *, wait=True):
        locked.append((scenario_id, fiscal_year))
        return list(pair)

    intake.lock_cycles = lock_cycles
    for name, mod in {
        "frappe": frappe, "frappe.model": model, "frappe.model.document": docmod,
        "konsol.epm.budget_grain": grain, "konsol.epm.doctype.scenario.scenario": scenario,
        "konsol.budget_intake": intake,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(sys.modules["konsol"], "budget_intake", intake, raising=False)
    spec = importlib.util.spec_from_file_location("_budget_cycle_rounds_under_test", CYCLE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def cycle(name=None, scenario_id="ZZFC", **kw):
        return mod.BudgetCycle(name=name, scenario_id=scenario_id, fiscal_year=2027, doctype="Budget Cycle",
                               **kw)

    return types.SimpleNamespace(cycle=cycle, names=names, pair=pair, locked=locked, kind=kind,
                                 digests=digests)


def _row(name, docstatus, as_of_date=""):
    return {"name": name, "docstatus": docstatus, "workflow_state": "", "as_of_date": as_of_date}


# ---- validate calls the round rule (K2) ---------------------------------------------

def test_a_forecast_cycle_with_as_of_and_horizon_is_accepted(env):
    doc = env.cycle("FC-A", as_of_date="2027-03-31", horizon_periods=12)
    doc.validate()
    assert doc.scenario_type == "forecast"


def test_a_forecast_cycle_without_an_as_of_date_is_refused(env):
    with pytest.raises(_Thrown) as raised:
        env.cycle("FC-A", horizon_periods=12).validate()
    assert "A forecast Cycle needs an As-of Date" in str(raised.value)


def test_a_forecast_cycle_without_a_horizon_is_refused_and_both_problems_are_told_together(env):
    with pytest.raises(_Thrown) as raised:
        env.cycle("FC-A", as_of_date="2027-03-31", horizon_periods=0).validate()
    assert "A forecast Cycle needs a Horizon of 1 or more periods" in str(raised.value)
    with pytest.raises(_Thrown) as raised:
        env.cycle("FC-A").validate()
    message = str(raised.value)
    assert "As-of Date" in message and "Horizon" in message


def test_a_budget_cycle_with_a_round_field_is_refused_naming_the_type(env):
    env.kind[0] = "budget"
    with pytest.raises(_Thrown) as raised:
        env.cycle("B-A", scenario_id="ZZB", as_of_date="2027-03-31").validate()
    assert "A budget Cycle has no forecast round: leave As-of Date blank" in str(raised.value)
    with pytest.raises(_Thrown) as raised:
        env.cycle("B-A", scenario_id="ZZB", horizon_periods=3).validate()
    assert "leave Horizon blank" in str(raised.value)
    env.cycle("B-A", scenario_id="ZZB").validate()  # blank both: accepted


def test_the_round_rule_runs_before_the_live_cycle_rule(env):
    """A Cycle refused for its round fields is not also judged against the
    pair: the sentence names what to fix first, and nothing is locked for it."""
    env.pair[:] = [_row("FC-A", 0, "2027-03-31")]
    with pytest.raises(_Thrown) as raised:
        env.cycle("FC-B", as_of_date="2027-03-31").validate()
    assert "Horizon" in str(raised.value)
    assert env.locked == []


# ---- one live Cycle per round -----------------------------------------------------

def test_two_forecast_rounds_of_one_pair_live_side_by_side(env):
    env.pair[:] = [_row("FC-Q1", 1, "2027-03-31"), _row("FC-Q2", 0, "2027-06-30")]
    doc = env.cycle("FC-Q3", as_of_date="2027-09-30", horizon_periods=12)
    doc.validate()
    assert env.locked == [("ZZFC", 2027)], "QB9 a: the lock stays on the pair"


@pytest.mark.parametrize("docstatus, word", [(0, "draft"), (1, "approved")])
def test_a_second_live_cycle_of_one_round_is_refused_by_name(env, docstatus, word):
    env.pair[:] = [_row("FC-Q1", docstatus, datetime.date(2027, 3, 31))]
    with pytest.raises(_Thrown) as raised:
        env.cycle("FC-Q1-again", as_of_date="2027-03-31", horizon_periods=6).validate()
    assert str(raised.value) == (
        f"Budget Cycle FC-Q1 is already the {word} Cycle of ZZFC 2027 as of 2027-03-31. A forecast "
        "scenario, year and as-of date hold one live Cycle: work in it, or cancel it and amend it."), raised.value


def test_a_cancelled_round_can_be_amended_beside_other_rounds(env):
    env.pair[:] = [_row("FC-Q1", 2, "2027-03-31"), _row("FC-Q1-1", 0, "2027-03-31"), _row("FC-Q2", 1, "2027-06-30")]
    env.cycle("FC-Q1-1", as_of_date="2027-03-31", horizon_periods=12, amended_from="FC-Q1").validate()


def test_a_budget_pair_still_holds_one_live_cycle_whatever_the_rows_date_says(env):
    env.kind[0] = "budget"
    env.pair[:] = [_row("B-A", 0)]
    with pytest.raises(_Thrown) as raised:
        env.cycle("B-B", scenario_id="ZZB").validate()
    assert str(raised.value) == (
        "Budget Cycle B-A is already the draft Cycle of ZZB 2027. A scenario and year hold one "
        "live Cycle: work in it, or cancel it and amend it."), raised.value


# ---- K4: the name digest carries the as-of date for a forecast ------------------------
#
# Two rounds of one pair must get different base names, or the second would
# take "<digest>-1" and read as an amendment of the first. The digest stub
# returns "BCYC-<parts joined>", so the tests pin the parts the digest is made of.

def test_a_forecast_cycles_digest_is_made_of_scenario_year_and_as_of(env):
    doc = env.cycle(scenario_type="forecast", as_of_date=datetime.date(2027, 3, 31), horizon_periods=12)
    doc.autoname()
    assert env.digests == [["ZZFC", 2027, "2027-03-31"]]
    assert doc.name == "BCYC-ZZFC-2027-2027-03-31"


def test_two_rounds_of_one_pair_get_different_base_names(env):
    first = env.cycle(scenario_type="forecast", as_of_date="2027-03-31", horizon_periods=12)
    first.autoname()
    env.names.add(first.name)
    second = env.cycle(scenario_type="forecast", as_of_date="2027-06-30", horizon_periods=12)
    second.autoname()
    assert second.name == "BCYC-ZZFC-2027-2027-06-30"
    assert not second.name.startswith(first.name)


def test_a_budget_cycles_digest_is_the_pair_alone(env):
    env.kind[0] = "budget"
    doc = env.cycle(scenario_id="ZZB", scenario_type="budget")
    doc.autoname()
    assert env.digests == [["ZZB", 2027]]
    assert doc.name == "BCYC-ZZB-2027"


def test_autoname_reads_the_scenarios_type_when_the_copy_is_not_set_yet(env):
    """A Cycle inserted through the API may reach autoname before scenario_type
    is copied: the type is read from the Scenario, not guessed from the date."""
    doc = env.cycle(as_of_date="2027-03-31", horizon_periods=12)
    doc.autoname()
    assert env.digests == [["ZZFC", 2027, "2027-03-31"]]


def test_the_suffix_scheme_still_applies_within_a_round(env):
    """#524: a cancelled round keeps its name; its replacement takes -N."""
    env.names.update({"BCYC-ZZFC-2027-2027-03-31", "BCYC-ZZFC-2027-2027-03-31-1"})
    doc = env.cycle(scenario_type="forecast", as_of_date="2027-03-31", horizon_periods=12)
    doc.autoname()
    assert doc.name == "BCYC-ZZFC-2027-2027-03-31-2"
