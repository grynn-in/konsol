"""Close Event trail model, pure: konsol/close/trail_model.py (konsol#305 W2,
#298 story 10.1; amended 2 Oct by #305-W2-8, #305-W2-9).

Loaded by path; the module imports nothing from frappe or konsol (it loads
close_event_model as a sibling).
"""
import ast
import datetime
import importlib.util
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(APP_DIR, "close", "trail_model.py")
_spec = importlib.util.spec_from_file_location("trail_model_under_test", MODEL_PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _event(name="CE-0001", **overrides):
    """A clean, valid "approved" event; overrides flip one field."""
    base = {
        "name": name,
        "fiscal_year": 2025,
        "fiscal_period": 9,
        "kind": "approved",
        "entity": None,
        "reference_doctype": None,
        "reference_name": None,
        "actor": "zz-dana@example.com",
        "at": datetime.datetime(2026, 9, 30, 10, 0, 0),
        "reason": None,
        "detail": None,
        "source": "live",
    }
    base.update(overrides)
    return base


# --- summary: signoff, closed, locked -----------------------------------------

def test_acknowledged_signoff_then_close_then_lock_gives_full_summary():
    events = [
        _event("CE-0001", kind="signed_off", actor="dana",
               at=datetime.datetime(2026, 9, 30, 9, 0),
               reason="late TB",
               detail={"signoff_status": "Acknowledged", "run_status": "Red",
                       "warnings": ["w1"]}),
        _event("CE-0002", kind="period_closed", actor="dana",
               at=datetime.datetime(2026, 9, 30, 10, 0)),
        _event("CE-0003", kind="period_locked", actor="admin",
               at=datetime.datetime(2026, 9, 30, 11, 0)),
    ]
    s = M.summary(events)
    assert s["signoff"] == {
        "state": "signed", "by": "dana", "at": events[0]["at"],
        "result": "Acknowledged", "run_status": "Red",
        "reason": "late TB", "warnings": ["w1"],
    }
    assert s["closed"] == {"by": "dana", "at": events[1]["at"]}
    assert s["locked"] == {"by": "admin", "at": events[2]["at"]}
    assert s["counts"]["acknowledgements"] == 1


def test_close_then_reopen_gives_closed_none_and_counts_reopening():
    events = [
        _event("CE-0001", kind="period_closed", at=datetime.datetime(2026, 9, 30, 10, 0)),
        _event("CE-0002", kind="period_reopened", reason="late entry",
               at=datetime.datetime(2026, 9, 30, 11, 0)),
    ]
    s = M.summary(events)
    assert s["closed"] is None
    assert s["counts"]["reopenings"] == 1


def test_signoff_then_later_void_gives_voided_state():
    events = [
        _event("CE-0001", kind="signed_off", actor="dana",
               at=datetime.datetime(2026, 9, 30, 9, 0),
               detail={"signoff_status": "Signed Off"}),
        _event("CE-0002", kind="signoff_voided", actor="admin", reason="wrong run",
               at=datetime.datetime(2026, 9, 30, 10, 0)),
    ]
    s = M.summary(events)
    assert s["signoff"] == {
        "state": "voided", "by": "admin", "at": events[1]["at"], "reason": "wrong run",
    }


def test_year_locked_alone_sets_locked_for_the_period():
    events = [_event("CE-0001", kind="year_locked", fiscal_period=0,
                      at=datetime.datetime(2026, 9, 30, 10, 0))]
    s = M.summary(events)
    assert s["locked"] == {"by": "zz-dana@example.com", "at": events[0]["at"]}
    assert s["closed"] is None


# --- summary: counts ------------------------------------------------------------

def test_tb_submitted_on_behalf_yes_counts_an_upload():
    events = [_event("CE-0001", kind="tb_submitted", detail={"on_behalf": "Yes"})]
    assert M.summary(events)["counts"]["on_behalf_uploads"] == 1


def test_tb_submitted_on_behalf_no_counts_nothing():
    events = [_event("CE-0001", kind="tb_submitted", detail={"on_behalf": "No"})]
    assert M.summary(events)["counts"]["on_behalf_uploads"] == 0


def test_backfilled_self_approved_with_reason_not_recorded_counts_both():
    events = [_event("CE-0001", kind="self_approved", source="backfill",
                      detail={"reason_not_recorded": True})]
    counts = M.summary(events)["counts"]
    assert counts["recovered"] == 1
    assert counts["reasons_not_recorded"] == 1
    assert counts["self_approvals"] == 1
    assert counts["approvals"] == 1


def test_approval_cancelled_counts_as_a_cancellation():
    # W2-8: approval_cancelled is a new kind and counts() gains cancellations.
    events = [_event("CE-0001", kind="approval_cancelled")]
    assert M.summary(events)["counts"]["cancellations"] == 1


# --- ordered ---------------------------------------------------------------------

def test_ordered_puts_the_newest_first_tie_broken_by_name():
    same_at = datetime.datetime(2026, 9, 30, 10, 0)
    e1 = _event("CE-0001", at=same_at)
    e2 = _event("CE-0002", at=same_at)
    e3 = _event("CE-0003", at=datetime.datetime(2026, 9, 30, 11, 0))
    result = M.ordered([e1, e2, e3])
    assert [e["name"] for e in result] == ["CE-0003", "CE-0002", "CE-0001"]


# --- summary: failure path and the empty case -----------------------------------

def test_unknown_kind_raises_value_error_naming_it():
    events = [_event("CE-0001", kind="bogus")]
    try:
        M.summary(events)
    except ValueError as exc:
        assert "bogus" in str(exc)
    else:
        raise AssertionError("summary() with an unknown kind should raise ValueError")


def test_empty_events_gives_the_blank_summary():
    s = M.summary([])
    assert s["signoff"] == {"state": "none"}
    assert s["closed"] is None
    assert s["locked"] is None
    assert all(v == 0 for v in s["counts"].values())


# --- visible: #305-W2-9 ----------------------------------------------------------

def test_visible_scopes_entity_events_and_hides_the_rest_with_no_leak():
    events = [
        _event("CE-0001", kind="tb_submitted", entity="ZZA"),
        _event("CE-0002", kind="tb_submitted", entity="ZZX"),
        _event("CE-0003", kind="approved", entity=None),  # a GER: group-level
    ]
    kept, hidden = M.visible(events, {"ZZA"})
    assert [e["name"] for e in kept] == ["CE-0001", "CE-0003"]
    assert hidden == 1

    # Failure path: ZZX's activity must not leak through the summary or the
    # kept events, beyond the hidden count already surfaced above.
    blob = json.dumps(kept, default=str) + json.dumps(M.summary(kept), default=str)
    assert "ZZX" not in blob


def test_visible_with_allowed_none_keeps_everything():
    events = [
        _event("CE-0001", entity="ZZA"),
        _event("CE-0002", entity="ZZX"),
        _event("CE-0003", entity=None),
    ]
    kept, hidden = M.visible(events, None)
    assert len(kept) == 3
    assert hidden == 0


def test_visible_with_empty_allowed_keeps_only_group_level_events():
    events = [
        _event("CE-0001", entity="ZZA"),
        _event("CE-0002", entity="ZZX"),
        _event("CE-0003", entity=None),
    ]
    kept, hidden = M.visible(events, set())
    assert [e["name"] for e in kept] == ["CE-0003"]
    assert hidden == 2


# --- module hygiene ----------------------------------------------------------------

def test_module_imports_no_frappe():
    with open(MODEL_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith(("frappe", "konsol")) for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("frappe", "konsol"))
            assert node.level == 0
