"""Close Event backfill model, pure: konsol/close/close_event_backfill_model.py
(konsol#305 T06a, #298 story 10.1, #305-W2-1; amended by #305-W2-5, W2-7,
W2-8 and W2-14).

Plain rows in (Versions, Comments, sign-off fields, journal fields), backfill
events out, with everything that cannot be placed counted under a named
reason. Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(APP_DIR, "close", "close_event_backfill_model.py")
EVENT_MODEL_PATH = os.path.join(APP_DIR, "close", "close_event_model.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = _load("close_event_backfill_model_under_test", MODEL_PATH)
EM = _load("close_event_model_for_backfill_test", EVENT_MODEL_PATH)

APPROVAL_DOCTYPES = (
    "Consolidation Journal", "Business Combination", "Business Disposal",
    "Group Exchange Rate", "Ownership Period", "Historical Equity Rate", "IC Balance",
)
STATE_FIELDS = {"Consolidation Journal": "status", "Business Combination": "status",
                "Business Disposal": "status"}

A = "zz-a@example.com"
B = "zz-b@example.com"


def _v(name, doctype, docname, owner, creation, data, as_text=True):
    return {"name": name, "ref_doctype": doctype, "docname": docname, "owner": owner,
            "creation": creation, "data": json.dumps(data, indent=1) if as_text else data}


def _submit(name, doctype, docname, owner, creation):
    return _v(name, doctype, docname, owner, creation, {"changed": [["docstatus", 0, 1]]})


def _cancel(name, doctype, docname, owner, creation):
    return _v(name, doctype, docname, owner, creation, {"changed": [["docstatus", 1, 2]]})


def _run(versions, docs, periods=None, placements=None, reasons=None):
    return M.events_from_versions(
        versions, docs, periods or {}, placements or {},
        approval_doctypes=APPROVAL_DOCTYPES, state_fields=STATE_FIELDS, reasons=reasons)


def _assert_valid(events):
    for event in events:
        assert EM.event_problems(event) == [], (event, EM.event_problems(event))
        assert event["source"] == "backfill"


GER = ("Group Exchange Rate", "GER-1")
GER_DOCS = {GER: {"owner": A}}
GER_PLACED = {GER: (2025, 3)}


# --- approvals (#305-W2-7, W2-14) --------------------------------------

def test_ger_self_submit_is_self_approved_with_reason_not_recorded():
    events, unplaced = _run([_submit("V1", *GER, A, "2025-03-05 10:00:00")],
                            GER_DOCS, placements=GER_PLACED)
    assert unplaced == {}
    assert len(events) == 1
    e = events[0]
    assert e["kind"] == "self_approved"
    assert (e["fiscal_year"], e["fiscal_period"]) == (2025, 3)
    assert e["actor"] == A and e["at"] == "2025-03-05 10:00:00"
    assert (e["reference_doctype"], e["reference_name"]) == GER
    assert e["reason"] is None
    assert e["detail"]["reason_not_recorded"] is True
    assert e["detail"]["preparer"] == A and e["detail"]["preparers"] == [A]
    _assert_valid(events)


def test_self_approval_comment_supplies_the_reason_and_no_flag():
    comments = [{"reference_doctype": GER[0], "reference_name": GER[1], "owner": A,
                 "creation": "2025-03-05 09:59:59",
                 "content": "Self-approved by %s under Close Settings (Allowed with reason): "
                            "only Close Lead on leave" % A}]
    reasons = M.reasons_from_comments(comments)
    assert reasons == {(GER[0], GER[1], A): [("2025-03-05 09:59:59", "only Close Lead on leave")]}
    events, _ = _run([_submit("V1", *GER, A, "2025-03-05 10:00:00")],
                     GER_DOCS, placements=GER_PLACED, reasons=reasons)
    e = events[0]
    assert e["kind"] == "self_approved"
    assert e["reason"] == "only Close Lead on leave"
    assert "reason_not_recorded" not in e["detail"]
    _assert_valid(events)


def test_owner_other_than_approver_is_approved_with_preparer():
    events, _ = _run([_submit("V1", *GER, B, "2025-03-05 10:00:00")],
                     GER_DOCS, placements=GER_PLACED)
    e = events[0]
    assert e["kind"] == "approved" and e["actor"] == B
    assert e["detail"]["preparer"] == A
    assert "reason_not_recorded" not in e["detail"]
    _assert_valid(events)


def test_draft_edited_by_b_then_submitted_by_b_is_self_approved():
    versions = [
        _v("V1", *GER, B, "2025-03-04 10:00:00", {"changed": [["exchange_rate", 1.1, 1.2]]}),
        _submit("V2", *GER, B, "2025-03-05 10:00:00"),
    ]
    events, _ = _run(versions, GER_DOCS, placements=GER_PLACED)
    assert len(events) == 1
    e = events[0]
    assert e["kind"] == "self_approved"
    assert e["detail"]["preparers"] == sorted([A, B])
    assert e["detail"]["reason_not_recorded"] is True
    _assert_valid(events)


def test_submitting_version_that_also_edits_a_field_is_self_approved():
    """A 0->1 Version whose own ``data`` also changes a field (an edit made
    in the same request as the submit) makes its owner a preparer too
    (konsol#305 R01b), matching the live rule
    (close_policy_model.submit_carries_edit, konsol#305 R01a)."""
    versions = [_v("V1", *GER, B, "2025-03-05 10:00:00",
                   {"changed": [["quote", 1.1, 1.5], ["docstatus", 0, 1]]})]
    events, _ = _run(versions, GER_DOCS, placements=GER_PLACED)
    assert len(events) == 1
    e = events[0]
    assert e["kind"] == "self_approved"
    assert e["actor"] == B
    assert e["detail"]["preparers"] == sorted([A, B])
    _assert_valid(events)


def test_an_edit_after_the_submit_does_not_count_as_preparing():
    versions = [
        _submit("V1", *GER, B, "2025-03-05 10:00:00"),
        _v("V2", *GER, B, "2025-03-06 10:00:00", {"changed": [["remarks", "", "x"]]}),
    ]
    events, _ = _run(versions, GER_DOCS, placements=GER_PLACED)
    assert [e["kind"] for e in events] == ["approved"]


def test_journal_reject_alone_is_not_an_edit():
    """Failure path: a Reject (state field only) counted as an edit would
    make the Close Lead a preparer."""
    cj = ("Consolidation Journal", "CJ-00001")
    versions = [
        _v("V1", *cj, B, "2025-03-04 10:00:00",
           {"changed": [["status", "Pending Approval", "Draft"]]}),
        _submit("V2", *cj, B, "2025-03-05 10:00:00"),
    ]
    events, _ = _run(versions, {cj: {"owner": A}}, placements={cj: (2025, 3)})
    assert [e["kind"] for e in events] == ["approved"]
    assert events[0]["detail"]["preparers"] == [A]


def test_ger_cancel_is_one_approval_cancelled():
    versions = [
        _submit("V1", *GER, A, "2025-03-05 10:00:00"),
        _cancel("V2", *GER, B, "2025-03-07 10:00:00"),
        _v("V3", *GER, A, "2025-03-08 10:00:00", {"changed": [["docstatus", 2, 0]]}),
    ]
    events, _ = _run(versions, GER_DOCS, placements=GER_PLACED)
    kinds = [e["kind"] for e in events]
    assert kinds.count("approval_cancelled") == 1
    assert len(events) == 2  # the 2->0 amendment save is not an event
    cancelled = [e for e in events if e["kind"] == "approval_cancelled"][0]
    assert cancelled["actor"] == B and cancelled["detail"]["preparer"] == A
    _assert_valid(events)


def test_approval_event_carries_the_entity_from_docs():
    op = ("Ownership Period", "OP-1")
    events, _ = _run([_submit("V1", *op, A, "2025-12-20 10:00:00")],
                     {op: {"owner": A, "entity": "ZZE1"}}, placements={op: (2025, 13)})
    assert events[0]["entity"] == "ZZE1"
    assert (events[0]["fiscal_year"], events[0]["fiscal_period"]) == (2025, 13)


def test_a_doctype_outside_the_lists_gives_no_event():
    events, unplaced = _run([_submit("V1", "Entity", "E1", A, "2025-03-05 10:00:00")], {})
    assert events == [] and unplaced == {}


def test_a_draft_edit_alone_gives_no_event():
    events, unplaced = _run(
        [_v("V1", *GER, B, "2025-03-04 10:00:00", {"changed": [["exchange_rate", 1, 2]]})],
        GER_DOCS, placements=GER_PLACED)
    assert events == [] and unplaced == {}


def test_data_as_a_dict_gives_the_same_answer_as_text():
    as_text, _ = _run([_submit("V1", *GER, A, "2025-03-05 10:00:00")], GER_DOCS,
                      placements=GER_PLACED)
    as_dict, _ = _run([_v("V1", *GER, A, "2025-03-05 10:00:00",
                          {"changed": [["docstatus", 0, 1]]}, as_text=False)],
                      GER_DOCS, placements=GER_PLACED)
    assert as_text == as_dict


# --- trial balances and TB Exceptions --------------------------------------

def test_tb_submit_then_cancel():
    tb = ("Trial Balance Submission", "TB-1")
    docs = {tb: {"owner": A, "data_area_id": "ZZE1", "uploaded_on_behalf": 1,
                 "amended_from": "TB-0"}}
    versions = [_cancel("V2", *tb, A, "2025-03-06 10:00:00"),
                _submit("V1", *tb, A, "2025-03-05 10:00:00")]
    events, _ = _run(versions, docs, placements={tb: (2025, 3)})
    assert [e["kind"] for e in events] == ["tb_submitted", "tb_cancelled"]
    assert events[0]["entity"] == "ZZE1"
    assert events[0]["detail"]["on_behalf"] == 1
    assert events[0]["detail"]["replaces"] == "TB-0"
    _assert_valid(events)


def test_tb_exception_declared_carries_its_reason_and_cancel():
    tbe = ("TB Exception", "TBE-1")
    docs = {tbe: {"owner": A, "data_area_id": "ZZE1", "reason": "dormant entity"}}
    versions = [_submit("V1", *tbe, A, "2025-03-05 10:00:00"),
                _cancel("V2", *tbe, A, "2025-03-06 10:00:00")]
    events, _ = _run(versions, docs, placements={tbe: (2025, 3)})
    assert [e["kind"] for e in events] == ["tb_exception_declared", "tb_exception_cancelled"]
    assert events[0]["reason"] == "dormant entity"
    _assert_valid(events)


# --- EPM Fiscal Year ------------------------------------------------------

FY = ("EPM Fiscal Year", "FY-2025")
PERIODS = {"r7": (2025, 7, "2025-P07")}


def _row(row_name, old, new):
    return {"row_changed": [["periods", 7, row_name, [["status", old, new],
                                                       ["closed_by", None, A]]]]}


def test_period_closed_takes_the_closing_note_text():
    note = "2025-P07 closed on 2025-08-03 by %s: all TBs in" % A
    docs = {FY: {"owner": A, "fiscal_year": 2025, "closing_note": note}}
    events, unplaced = _run([_v("V1", *FY, A, "2025-08-03 11:00:00", _row("r7", "Open", "Closed"))],
                            docs, periods=PERIODS)
    assert unplaced == {}
    assert len(events) == 1
    e = events[0]
    assert e["kind"] == "period_closed"
    assert (e["fiscal_year"], e["fiscal_period"]) == (2025, 7)
    assert e["reason"] == "all TBs in"
    assert e["detail"]["period_code"] == "2025-P07" and e["detail"]["from"] == "Open"
    _assert_valid(events)


def test_period_reopen_with_no_note_is_reason_not_recorded():
    docs = {FY: {"owner": A, "fiscal_year": 2025, "closing_note": None}}
    events, _ = _run([_v("V1", *FY, A, "2025-08-03 11:00:00", _row("r7", "Closed", "Open"))],
                     docs, periods=PERIODS)
    e = events[0]
    assert e["kind"] == "period_reopened"
    assert e["reason"] is None and e["detail"]["reason_not_recorded"] is True
    _assert_valid(events)


def test_period_lock_with_no_note_has_no_reason_and_no_flag():
    docs = {FY: {"owner": A, "fiscal_year": 2025, "closing_note": ""}}
    events, _ = _run([_v("V1", *FY, A, "2025-08-03 11:00:00", _row("r7", "Closed", "Locked"))],
                     docs, periods=PERIODS)
    e = events[0]
    assert e["kind"] == "period_locked" and e["reason"] is None
    assert "reason_not_recorded" not in e["detail"]


def test_year_status_change_is_year_event_with_period_zero():
    note = "FY2025 closed on 2025-12-31 by %s: year end" % A
    docs = {FY: {"owner": A, "fiscal_year": 2025, "closing_note": note}}
    data = {"changed": [["status", "Open", "Closed"]]}
    data.update(_row("r7", "Open", "Closed"))
    events, _ = _run([_v("V1", *FY, A, "2025-12-31 11:00:00", data)], docs, periods=PERIODS)
    by_kind = {e["kind"]: e for e in events}
    assert set(by_kind) == {"year_closed", "period_closed"}
    year = by_kind["year_closed"]
    assert (year["fiscal_year"], year["fiscal_period"]) == (2025, 0)
    assert year["reason"] == "year end"
    assert by_kind["period_closed"]["detail"]["via"] == "year"
    assert by_kind["period_closed"]["reason"] == "year end"
    _assert_valid(events)


def test_period_row_gone_is_unplaced_not_an_event():
    """Failure path: a Version naming a row Generate Periods replaced."""
    docs = {FY: {"owner": A, "fiscal_year": 2025, "closing_note": None}}
    events, unplaced = _run(
        [_v("V1", *FY, A, "2025-08-03 11:00:00", _row("r-gone", "Open", "Closed"))],
        docs, periods=PERIODS)
    assert events == []
    assert unplaced == {M.UNPLACED_ROW_GONE: 1}
    assert M.UNPLACED_ROW_GONE == "period row no longer exists"


def test_reasons_from_closing_note_parses_lines():
    note = ("2025-P07 closed on 2025-08-03 by %s: all TBs in\n"
            "FY2025 reopened on 2026-01-04 by %s: late audit: adjustment" % (A, B))
    assert M.reasons_from_closing_note(note) == [
        ("2025-P07", "closed", "2025-08-03", A, "all TBs in"),
        ("FY2025", "reopened", "2026-01-04", B, "late audit: adjustment"),
    ]
    assert M.reasons_from_closing_note(None) == []


# --- placement failure paths ---------------------------------------------

def test_placement_none_is_unplaced_under_no_declared_period():
    events, unplaced = _run([_submit("V1", *GER, A, "2025-03-05 10:00:00")],
                            GER_DOCS, placements={GER: None})
    assert events == []
    assert unplaced == {"no declared period (#305-W2-5)": 1}
    assert M.UNPLACED_NO_PERIOD == "no declared period (#305-W2-5)"


def test_placement_with_its_own_reason_is_counted_under_it():
    events, unplaced = _run([_submit("V1", *GER, A, "2025-03-05 10:00:00")],
                            GER_DOCS, placements={GER: "some other reason"})
    assert events == [] and unplaced == {"some other reason": 1}


def test_deleted_document_is_unplaced():
    events, unplaced = _run([_submit("V1", *GER, A, "2025-03-05 10:00:00")], {})
    assert events == [] and unplaced == {"document deleted": 1}


def test_text_that_is_not_json_raises_naming_the_version():
    bad = {"name": "VER-LIKE-1", "ref_doctype": GER[0], "docname": GER[1], "owner": A,
           "creation": "2025-03-05 10:00:00", "data": "%docstatus%: 0, 1"}
    try:
        _run([bad], GER_DOCS, placements=GER_PLACED)
    except ValueError as exc:
        assert "VER-LIKE-1" in str(exc)
    else:
        raise AssertionError("non-JSON Version data must raise")


# --- comments: rejections ------------------------------------------------

def test_rejected_comment_becomes_a_rejected_event():
    comments = [{"reference_doctype": GER[0], "reference_name": GER[1], "owner": B,
                 "creation": "2025-03-04 12:00:00", "content": "Rejected: wrong entity"}]
    assert M.reasons_from_comments(comments) == {}
    events, unplaced = M.events_from_rejections(comments, GER_DOCS, GER_PLACED)
    assert unplaced == {}
    e = events[0]
    assert e["kind"] == "rejected" and e["reason"] == "wrong entity"
    assert e["actor"] == B and e["at"] == "2025-03-04 12:00:00"
    assert e["detail"]["preparer"] == A
    _assert_valid(events)


def test_unrelated_comment_is_ignored():
    comments = [{"reference_doctype": GER[0], "reference_name": GER[1], "owner": B,
                 "creation": "2025-03-04 12:00:00", "content": "looks fine"}]
    assert M.reasons_from_comments(comments) == {}
    assert M.events_from_rejections(comments, GER_DOCS, GER_PLACED) == ([], {})


# --- sign-offs ----------------------------------------------------------

def _signoff_run(**overrides):
    run = {"name": "AR-1", "fiscal_year": 2025, "fiscal_period": 7, "signed_off_by": A,
           "signed_off_at": "2025-08-02 10:00:00", "signoff_status": "Acknowledged",
           "status": "Amber", "override_reason": None, "acknowledgement": "known FX warning",
           "affected_by": None, "versions": []}
    run.update(overrides)
    return run


def test_acknowledged_run_gives_signed_off_acknowledged():
    events, unplaced = M.events_from_signoffs([_signoff_run()])
    assert unplaced == {}
    assert len(events) == 1
    e = events[0]
    assert e["kind"] == "signed_off" and e["actor"] == A
    assert e["detail"]["signoff_status"] == "Acknowledged"
    assert e["reason"] == "known FX warning"
    assert (e["reference_doctype"], e["reference_name"]) == ("Assertion Run", "AR-1")
    _assert_valid(events)


def test_resign_needed_run_without_version_is_unknown_and_voided():
    run = _signoff_run(signoff_status="Re-sign Needed", acknowledgement=None,
                       affected_by="FY2025 2025-P07 reopened on 2025-08-09 by x: late TB")
    events, unplaced = M.events_from_signoffs([run])
    assert unplaced == {}
    by_kind = {e["kind"]: e for e in events}
    assert set(by_kind) == {"signed_off", "signoff_voided"}
    signed = by_kind["signed_off"]
    assert signed["detail"]["signoff_status"] == "unknown"
    assert signed["detail"]["reason_not_recorded"] is True
    voided = by_kind["signoff_voided"]
    assert voided["reason"] == run["affected_by"]
    _assert_valid(events)


def test_resign_needed_run_with_version_recovers_the_signed_state_and_voider():
    run = _signoff_run(
        signoff_status="Re-sign Needed",
        affected_by="late TB at 2025-08-09 10:00:00 by zz-b@example.com",
        versions=[{"name": "V9", "owner": B, "creation": "2025-08-09 10:00:01",
                   "data": json.dumps({"changed": [
                       ["signoff_status", "Acknowledged", "Re-sign Needed"]]})}])
    events, unplaced = M.events_from_signoffs([run])
    assert unplaced == {}
    by_kind = {e["kind"]: e for e in events}
    assert by_kind["signed_off"]["detail"]["signoff_status"] == "Acknowledged"
    assert by_kind["signed_off"]["reason"] == "known FX warning"
    assert by_kind["signoff_voided"]["actor"] == B
    assert by_kind["signoff_voided"]["at"] == "2025-08-09 10:00:01"
    _assert_valid(events)


def test_run_never_signed_gives_nothing():
    events, unplaced = M.events_from_signoffs([_signoff_run(signed_off_by=None, signoff_status="")])
    assert events == [] and unplaced == {}


# --- T06c: a deleted run's own Versions are counted, not dropped ----------

def _orphan_version(name, owner, creation, old, new):
    return {"name": name, "owner": owner, "creation": creation,
            "data": json.dumps({"changed": [["signoff_status", old, new]]})}


def test_orphan_signoff_version_into_signed_state_is_unplaced_document_deleted():
    v = _orphan_version("V9", B, "2025-08-09 10:00:00", "Not Signed Off", "Signed Off")
    events, unplaced = M.events_from_signoffs([], orphan_versions=[v])
    assert events == []
    assert unplaced == {"document deleted": 1}


def test_orphan_signoff_version_into_resign_needed_is_unplaced_document_deleted():
    v = _orphan_version("V10", B, "2025-08-09 10:00:01", "Signed Off", "Re-sign Needed")
    events, unplaced = M.events_from_signoffs([], orphan_versions=[v])
    assert events == []
    assert unplaced == {"document deleted": 1}


def test_both_orphan_transitions_of_a_deleted_run_count_separately():
    # Measured on live (T06c, 2 Oct): the 2 transitions of a run that no
    # longer exists both count, matching T06b's expected unplaced {"document
    # deleted": 2}.
    v1 = _orphan_version("V9", B, "2025-08-09 10:00:00", "Not Signed Off", "Signed Off")
    v2 = _orphan_version("V10", B, "2025-08-09 10:00:01", "Signed Off", "Re-sign Needed")
    events, unplaced = M.events_from_signoffs([], orphan_versions=[v1, v2])
    assert events == []
    assert unplaced == {"document deleted": 2}


def test_orphan_version_moving_away_from_a_signed_state_is_not_counted():
    # Failure path: today's code (no orphan_versions parameter) cannot
    # express this at all, and counting every orphan version unconditionally
    # would over-count a transition that is neither a sign-off nor a void.
    v = _orphan_version("V11", B, "2025-08-09 10:00:02", "Acknowledged", "Not Signed Off")
    events, unplaced = M.events_from_signoffs([], orphan_versions=[v])
    assert events == [] and unplaced == {}


def test_orphan_versions_default_to_none_without_error():
    events, unplaced = M.events_from_signoffs([_signoff_run()])
    assert unplaced == {}
    assert len(events) == 1


# --- journals -------------------------------------------------------------

def _journal(**overrides):
    j = {"name": "CJ-00002", "owner": A, "approved_by": B, "approved_at": "2025-03-05 10:00:00",
         "fiscal_year": 2025, "fiscal_period": 3}
    j.update(overrides)
    return j


def test_journal_approved_fields_give_an_approval():
    events = M.events_from_journals([_journal()], [])
    assert [e["kind"] for e in events] == ["approved"]
    assert events[0]["actor"] == B and events[0]["detail"]["preparer"] == A
    _assert_valid(events)


def test_journal_self_approved_without_reason_is_flagged():
    events = M.events_from_journals([_journal(approved_by=A)], [])
    assert events[0]["kind"] == "self_approved"
    assert events[0]["detail"]["reason_not_recorded"] is True
    _assert_valid(events)


def test_journal_skipped_when_a_version_already_gave_its_approval():
    cj = ("Consolidation Journal", "CJ-00002")
    from_versions, _ = _run([_submit("V1", *cj, B, "2025-03-05 10:00:00")],
                            {cj: {"owner": A}}, placements={cj: (2025, 3)})
    assert M.events_from_journals([_journal()], from_versions) == []


# --- new_events -------------------------------------------------------------

def _candidates():
    events, _ = _run([_submit("V1", *GER, A, "2025-03-05 10:00:00"),
                      _cancel("V2", *GER, A, "2025-03-07 10:00:00")],
                     GER_DOCS, placements=GER_PLACED)
    return events


def test_new_events_is_idempotent():
    first = M.new_events(_candidates(), set(), None)
    assert len(first) == 2
    assert [e["at"] for e in first] == sorted(e["at"] for e in first)
    existing = {M.event_key(e) for e in first}
    assert M.new_events(first, existing, None) == []


def test_candidate_at_or_after_cutoff_is_dropped():
    kept = M.new_events(_candidates(), set(), "2025-03-07 10:00:00")
    assert [e["kind"] for e in kept] == ["self_approved"]


# --- the whole plan and the constants ------------------------------------

def test_backfill_combines_every_source():
    comments = [{"reference_doctype": GER[0], "reference_name": GER[1], "owner": B,
                 "creation": "2025-03-04 12:00:00", "content": "Rejected: wrong entity"}]
    events, unplaced = M.backfill(
        versions=[_submit("V1", *GER, A, "2025-03-05 10:00:00")], docs=GER_DOCS,
        periods={}, placements=GER_PLACED, comments=comments, runs=[_signoff_run()],
        journals=[], existing=set(), cutoff=None,
        approval_doctypes=APPROVAL_DOCTYPES, state_fields=STATE_FIELDS)
    assert [e["kind"] for e in events] == ["rejected", "self_approved", "signed_off"]
    assert unplaced == {}
    _assert_valid(events)


def test_backfill_counts_a_deleted_runs_orphan_signoff_versions():
    orphan = [
        _orphan_version("V9", B, "2025-08-09 10:00:00", "Not Signed Off", "Signed Off"),
        _orphan_version("V10", B, "2025-08-09 10:00:01", "Signed Off", "Re-sign Needed"),
    ]
    events, unplaced = M.backfill(
        versions=[], docs={}, periods={}, placements={}, comments=[], runs=[],
        journals=[], existing=set(), cutoff=None, approval_doctypes=APPROVAL_DOCTYPES,
        state_fields=STATE_FIELDS, orphan_run_versions=orphan)
    assert events == []
    assert unplaced == {"document deleted": 2}


def test_not_recoverable_names_every_known_loss():
    text = " ".join(M.NOT_RECOVERABLE).lower()
    for phrase in ("14 sep 2026", "db.set_value", "close or lock", "on-behalf",
                   "re-sign needed", "no tracked version"):
        assert phrase in text, phrase


def test_module_imports_no_frappe():
    with open(MODEL_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith(("frappe", "konsol")) for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("frappe", "konsol"))
            assert node.level == 0
