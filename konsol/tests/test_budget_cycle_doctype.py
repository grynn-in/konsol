"""Budget Cycle's fields and permissions (konsol#348 S2-C W2a, BUD-9-3; #524).

The Cycle is the approval of a (scenario, fiscal year) budget: submittable,
with its state in ``workflow_state`` (the installed workflow sets it) and
``amended_from`` so a cancelled Cycle can be amended (#524). The deadline is a
date only; nothing locks at it. The old lock fields (``status``,
``locked_by``, ``locked_on``) are gone; W3's patch drops their columns.
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(APP_DIR, "epm", "doctype", "budget_cycle", "budget_cycle.json")
FLAGS = {"r": "read", "w": "write", "c": "create", "d": "delete", "s": "submit", "x": "cancel", "a": "amend"}


def _doctype():
    with open(PATH) as f:
        return json.load(f)


def _fields():
    return {f["fieldname"]: f for f in _doctype()["fields"]}


def test_is_submittable_and_tracks_changes():
    d = _doctype()
    assert d["is_submittable"] == 1
    assert d["track_changes"] == 1


def test_the_lock_fields_are_gone():
    fields = _fields()
    for gone in ("status", "locked_by", "locked_on"):
        assert gone not in fields, gone


def test_workflow_state_is_a_read_only_link_set_by_the_workflow():
    f = _fields()["workflow_state"]
    assert (f["fieldtype"], f["options"]) == ("Link", "Workflow State")
    assert f.get("read_only") == 1
    # An amendment starts in the workflow's first state, never the
    # cancelled one's; the cancel itself sets it on a submitted Cycle.
    assert f.get("no_copy") == 1
    assert f.get("allow_on_submit") == 1


def test_amended_from_lets_a_cancelled_cycle_be_amended():
    f = _fields()["amended_from"]
    assert (f["fieldtype"], f["options"]) == ("Link", "Budget Cycle")
    assert f.get("read_only") == 1
    assert f.get("no_copy") == 1


def test_entry_count_is_counted_not_typed():
    f = _fields()["entry_count"]
    assert f["fieldtype"] == "Int"
    assert f.get("read_only") == 1
    assert f.get("no_copy") == 1


def test_deadline_is_a_date_that_locks_nothing():
    f = _fields()["deadline"]
    assert f["fieldtype"] == "Date"
    assert not f.get("reqd")
    assert f["description"] == "A target date; it locks nothing."


def test_key_fields_unchanged():
    fields = _fields()
    assert fields["scenario_id"]["fieldtype"] == "Link" and fields["scenario_id"]["reqd"] == 1
    assert fields["fiscal_year"]["fieldtype"] == "Int" and fields["fiscal_year"]["reqd"] == 1
    assert fields["scenario_type"]["read_only"] == 1


# ---- S3 K1: a forecast round is (scenario, fiscal year, as-of date) (#348 Q23 a) ------

FORECAST_ONLY = "eval:doc.scenario_type=='forecast'"


def test_as_of_date_keys_the_forecast_round():
    f = _fields()["as_of_date"]
    assert f["fieldtype"] == "Date"
    # Required on a forecast, refused on every other type (QB8 a): never
    # `reqd`, which would demand it of a budget Cycle too.
    assert not f.get("reqd")
    assert f["mandatory_depends_on"] == FORECAST_ONLY
    assert f["depends_on"] == FORECAST_ONLY
    # Part of the Cycle's key and name (K4): set once, like scenario_id and fiscal_year.
    assert f.get("set_only_once") == 1
    assert f.get("in_list_view") == 1
    assert "forecast" in f["description"] and "round" in f["description"], f["description"]
    assert "one Cycle per fiscal year" in f["description"], f["description"]  # Q24 a


def test_horizon_periods_is_the_rounds_length():
    f = _fields()["horizon_periods"]
    assert f["fieldtype"] == "Int"
    assert not f.get("reqd")
    assert f["mandatory_depends_on"] == FORECAST_ONLY
    assert f["depends_on"] == FORECAST_ONLY
    assert "forecast" in f["description"] and "1 or more" in f["description"], f["description"]


def test_the_round_fields_sit_in_the_cycle_section_after_the_year():
    order = [f["fieldname"] for f in _doctype()["fields"]]
    assert order.index("fiscal_year") < order.index("as_of_date") < order.index("horizon_periods") \
        < order.index("col_break_1")


def test_as_of_date_is_searchable():
    assert _doctype()["search_fields"].split(",")[:3] == ["scenario_id", "fiscal_year", "as_of_date"]


def test_tabs_per_the_part_2_table():
    """Cycle tab (Cycle section) holds the key, the deadline and the state;
    the Entries tab (Summary section) holds the count."""
    order = [f["fieldname"] for f in _doctype()["fields"]]
    kinds = {f["fieldname"]: f["fieldtype"] for f in _doctype()["fields"]}
    tabs = [n for n in order if kinds[n] == "Tab Break"]
    assert len(tabs) == 2, tabs
    first, second = order.index(tabs[0]), order.index(tabs[1])
    for name in ("scenario_id", "scenario_type", "fiscal_year", "deadline", "workflow_state", "amended_from"):
        assert first < order.index(name) < second, name
    assert order.index("entry_count") > second
    labels = {f["fieldname"]: f.get("label") for f in _doctype()["fields"]}
    assert [labels[t] for t in tabs] == ["Cycle", "Entries"]
    sections = [labels[n] for n in order if kinds[n] == "Section Break"]
    assert sections == ["Cycle", "Summary"], sections


def _perms():
    out = {}
    for p in _doctype()["permissions"]:
        if p.get("permlevel"):
            continue
        out[p["role"]] = "".join(ch for ch, field in FLAGS.items() if p.get(field))
    return out


def test_permissions_per_the_part_2_table():
    assert _perms() == {
        "System Manager": "rwcdsxa",
        "EPM Admin": "rwcsxa",  # Q22 (#524): the role that submits and cancels also amends; amend inserts, so it needs create
        "EPM Analyst": "rwc",
        "Entity Accountant": "r",
        "EPM User": "r",
    }


def test_no_budget_role_is_named():
    assert not [r for r in _perms() if r.startswith("Budget ")]


# ---- S4 K5 (BUD-3): the Cycle declares its Rate Set --------------------------------

def test_rate_set_is_a_required_link_declared_per_cycle_with_no_default():
    f = _fields()["rate_set"]
    assert (f["fieldtype"], f["options"]) == ("Link", "Rate Set")
    # Required of every Cycle that can exist (budget, forecast, strategic,
    # operational; an actual Cycle is refused by validate), so plain reqd.
    assert f["reqd"] == 1
    assert not f.get("default"), "BUD-3: declared per Cycle, never defaulted"
    # Editable while a draft, frozen by the submit: not set-once (it is not
    # part of the Cycle's identity or name) and never allow_on_submit (the
    # fingerprint stamped at Approve covers it).
    assert not f.get("set_only_once")
    assert not f.get("allow_on_submit")
    assert f.get("in_list_view") == 1 and f.get("in_standard_filter") == 1
    assert "translated" in f["description"] and "default" in f["description"].lower(), f["description"]
    assert f["label"] == "Rate Set"


def test_rate_set_sits_in_the_cycle_section_after_the_year_before_the_round():
    order = [f["fieldname"] for f in _doctype()["fields"]]
    assert order.index("fiscal_year") < order.index("rate_set") < order.index("as_of_date")


def test_rate_set_is_searchable():
    assert "rate_set" in _doctype()["search_fields"].split(",")
