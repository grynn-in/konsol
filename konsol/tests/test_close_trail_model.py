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


# --- story 10.2: filters (kind, actor, entity, date range) --------------------
#
# The events are already scoped (visible()'s first element); the filters cut
# that scoped list further and never widen it.

def _four():
    return [
        _event("CE-0001", kind="tb_submitted", entity="ZZA", actor="zz-a@example.com",
               at=datetime.datetime(2026, 9, 1, 9, 0)),
        _event("CE-0002", kind="approved", entity=None, actor="zz-b@example.com",
               at=datetime.datetime(2026, 9, 2, 23, 59)),
        _event("CE-0003", kind="rejected", entity="ZZB", actor="zz-a@example.com",
               at=datetime.datetime(2026, 9, 3, 0, 0), reason="wrong"),
        _event("CE-0004", kind="approved", entity="ZZA", actor="zz-b@example.com",
               at=datetime.datetime(2026, 9, 4, 12, 0)),
    ]


def _names(events):
    return [e["name"] for e in events]


def test_parse_filters_with_nothing_given_is_no_filter():
    f = M.parse_filters()
    assert f == {"kinds": None, "actors": None, "entities": None,
                 "date_from": None, "date_to": None}
    assert _names(M.filtered(_four(), f)) == ["CE-0001", "CE-0002", "CE-0003", "CE-0004"]


def test_parse_filters_reads_json_lists_and_iso_dates_as_a_get_sends_them():
    f = M.parse_filters(kinds='["approved"]', actors='["zz-b@example.com"]',
                        entities='["ZZA"]', date_from="2026-09-01", date_to="2026-09-30")
    assert f == {"kinds": {"approved"}, "actors": {"zz-b@example.com"},
                 "entities": {"ZZA"},
                 "date_from": datetime.date(2026, 9, 1),
                 "date_to": datetime.date(2026, 9, 30)}


def test_parse_filters_accepts_python_lists_too():
    f = M.parse_filters(kinds=["approved", "rejected"])
    assert f["kinds"] == {"approved", "rejected"}


def test_an_empty_list_is_no_filter_on_that_field():
    f = M.parse_filters(kinds="[]", actors=[], entities="")
    assert f["kinds"] is None and f["actors"] is None and f["entities"] is None


def test_filter_by_kind():
    f = M.parse_filters(kinds=["approved"])
    assert _names(M.filtered(_four(), f)) == ["CE-0002", "CE-0004"]


def test_filter_by_actor():
    f = M.parse_filters(actors=["zz-a@example.com"])
    assert _names(M.filtered(_four(), f)) == ["CE-0001", "CE-0003"]


def test_filter_by_entity_keeps_only_that_entitys_events():
    f = M.parse_filters(entities=["ZZA"])
    assert _names(M.filtered(_four(), f)) == ["CE-0001", "CE-0004"]


def test_filter_by_entity_group_level_token_selects_blank_entity_events():
    f = M.parse_filters(entities=[M.GROUP_LEVEL, "ZZB"])
    assert _names(M.filtered(_four(), f)) == ["CE-0002", "CE-0003"]


def test_date_range_is_inclusive_on_both_days():
    f = M.parse_filters(date_from="2026-09-02", date_to="2026-09-03")
    assert _names(M.filtered(_four(), f)) == ["CE-0002", "CE-0003"]


def test_open_ended_date_ranges():
    assert _names(M.filtered(_four(), M.parse_filters(date_from="2026-09-03"))) == ["CE-0003", "CE-0004"]
    assert _names(M.filtered(_four(), M.parse_filters(date_to="2026-09-01"))) == ["CE-0001"]


def test_filters_combine_with_and():
    f = M.parse_filters(kinds=["approved"], entities=["ZZA"], actors=["zz-b@example.com"],
                        date_from="2026-09-04", date_to="2026-09-04")
    assert _names(M.filtered(_four(), f)) == ["CE-0004"]


def test_filtered_keeps_the_input_order():
    events = list(reversed(_four()))
    f = M.parse_filters(kinds=["approved"])
    assert _names(M.filtered(events, f)) == ["CE-0004", "CE-0002"]


# Failure paths: a bad filter is refused, never silently ignored.

def _value_error(fn):
    try:
        fn()
    except ValueError as e:
        return str(e)
    raise AssertionError("expected ValueError")


def test_an_unknown_kind_filter_is_refused_naming_it():
    msg = _value_error(lambda: M.parse_filters(kinds=["approvd"]))
    assert "approvd" in msg


def test_a_bad_date_is_refused_naming_it():
    msg = _value_error(lambda: M.parse_filters(date_from="2026-13-01"))
    assert "2026-13-01" in msg


def test_a_reversed_date_range_is_refused():
    msg = _value_error(lambda: M.parse_filters(date_from="2026-09-05", date_to="2026-09-01"))
    assert "2026-09-05" in msg and "2026-09-01" in msg


def test_a_filter_that_is_not_a_list_is_refused():
    _value_error(lambda: M.parse_filters(kinds='"approved"'))
    _value_error(lambda: M.parse_filters(actors="{bad"))
    _value_error(lambda: M.parse_filters(entities=[1]))


def test_an_entity_filter_never_widens_the_scope():
    # ZZX was dropped by visible(); asking for it by name gives nothing back.
    events = _four() + [_event("CE-0009", entity="ZZX")]
    kept, _hidden = M.visible(events, {"ZZA", "ZZB"})
    f = M.parse_filters(entities=["ZZX"])
    assert M.filtered(kept, f) == []


# --- story 10.2: the filter choices -----------------------------------------------

def test_options_are_the_distinct_kinds_actors_and_entities_sorted():
    opts = M.options(_four())
    assert opts == {
        "kinds": ["approved", "rejected", "tb_submitted"],
        "actors": ["zz-a@example.com", "zz-b@example.com"],
        "entities": [M.GROUP_LEVEL, "ZZA", "ZZB"],
    }


def test_options_from_a_scoped_list_name_no_hidden_entity():
    events = _four() + [_event("CE-0009", entity="ZZX", actor="zz-x@example.com")]
    kept, _hidden = M.visible(events, {"ZZA"})
    opts = M.options(kept)
    assert "ZZX" not in opts["entities"] and "ZZB" not in opts["entities"]
    assert "zz-x@example.com" not in opts["actors"]


# --- story 10.2: the CSV -----------------------------------------------------------

def _row(**overrides):
    """One event as trail_api hands it to csv_text: _event_out's shape plus
    the event's fiscal_year and fiscal_period."""
    base = {
        "name": "CE-0001", "kind": "approved", "entity": "ZZA",
        "reference_doctype": "Journal Entry", "reference_name": "JE-1",
        "actor": "zz-a@example.com", "actor_name": "A Accountant",
        "actor_missing": False, "actor_persona": "close_lead",
        "at": "2026-09-01T09:00:00+01:00", "reason": None,
        "detail": {"preparer": "zz-p@example.com", "actor_persona": "close_lead"},
        "source": "live", "fiscal_year": 2026, "fiscal_period": 9,
    }
    base.update(overrides)
    return base


def _parse_csv(text):
    import csv
    import io
    return list(csv.reader(io.StringIO(text)))


def test_csv_header_is_the_documented_columns_in_order():
    rows = _parse_csv(M.csv_text([]))
    assert rows == [list(M.CSV_COLUMNS)]
    assert M.CSV_COLUMNS == (
        "event", "at", "fiscal_year", "fiscal_period", "kind", "entity",
        "actor", "actor_name", "actor_persona", "reference_doctype",
        "reference_name", "reason", "source", "detail",
    )


def test_csv_one_row_per_event_in_the_given_order():
    rows = _parse_csv(M.csv_text([_row(name="CE-2"), _row(name="CE-1", entity=None)]))
    assert [r[0] for r in rows[1:]] == ["CE-2", "CE-1"]
    first = dict(zip(rows[0], rows[1]))
    assert first == {
        "event": "CE-2", "at": "2026-09-01T09:00:00+01:00",
        "fiscal_year": "2026", "fiscal_period": "9", "kind": "approved",
        "entity": "ZZA", "actor": "zz-a@example.com", "actor_name": "A Accountant",
        "actor_persona": "close_lead", "reference_doctype": "Journal Entry",
        "reference_name": "JE-1", "reason": "", "source": "live",
        "detail": '{"actor_persona": "close_lead", "preparer": "zz-p@example.com"}',
    }
    assert dict(zip(rows[0], rows[2]))["entity"] == ""


def test_csv_quotes_commas_quotes_and_newlines_in_a_reason():
    reason = 'late, "very" late\nsecond line'
    rows = _parse_csv(M.csv_text([_row(reason=reason)]))
    assert dict(zip(rows[0], rows[1]))["reason"] == reason


def test_csv_neutralises_a_spreadsheet_formula_in_free_text():
    rows = _parse_csv(M.csv_text([_row(reason="=HYPERLINK(\"x\")", actor_name="+1 Bad", reference_name="@SUM(A1)")]))
    got = dict(zip(rows[0], rows[1]))
    assert got["reason"] == "'=HYPERLINK(\"x\")"
    assert got["actor_name"] == "'+1 Bad"
    assert got["reference_name"] == "'@SUM(A1)"


def test_csv_refuses_a_row_missing_a_column_rather_than_writing_a_blank():
    row = _row()
    del row["source"]
    try:
        M.csv_text([row])
    except KeyError as e:
        assert "source" in str(e)
        return
    raise AssertionError("expected KeyError")

# --- summary: a rejected sign-off (konsol#305 story 9.4, #157, #305-W5-1) ------

def _signed(name, at):
    return _event(name, kind="signed_off", actor="dana", at=at,
                  detail={"signoff_status": "Signed Off", "run_status": "Green"})


def _rejected(name, at):
    return _event(name, kind="signoff_rejected", actor="lead", reason="ZZA's TB is the draft",
                  at=at, reference_doctype="Assertion Run", reference_name="AR-1",
                  detail={"signoff_status": "Signed Off", "preparer": "ana"})


def test_signoff_then_later_reject_gives_rejected_state():
    events = [_signed("CE-0001", datetime.datetime(2026, 9, 30, 9, 0)),
              _rejected("CE-0002", datetime.datetime(2026, 9, 30, 10, 0))]
    assert M.summary(events)["signoff"] == {
        "state": "rejected", "by": "lead", "at": events[1]["at"],
        "reason": "ZZA's TB is the draft",
    }


def test_a_signature_after_the_reject_reads_signed_again():
    events = [_signed("CE-0001", datetime.datetime(2026, 9, 30, 9, 0)),
              _rejected("CE-0002", datetime.datetime(2026, 9, 30, 10, 0)),
              _signed("CE-0003", datetime.datetime(2026, 9, 30, 11, 0))]
    signoff = M.summary(events)["signoff"]
    assert signoff["state"] == "signed" and signoff["at"] == events[2]["at"]


def test_a_void_after_the_reject_reads_as_the_later_event():
    events = [_signed("CE-0001", datetime.datetime(2026, 9, 30, 9, 0)),
              _rejected("CE-0002", datetime.datetime(2026, 9, 30, 10, 0)),
              _event("CE-0003", kind="signoff_voided", actor="admin", reason="data changed",
                     at=datetime.datetime(2026, 9, 30, 11, 0))]
    assert M.summary(events)["signoff"]["state"] == "voided"
