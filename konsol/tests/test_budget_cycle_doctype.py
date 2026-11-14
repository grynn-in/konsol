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
