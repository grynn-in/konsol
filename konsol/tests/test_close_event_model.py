"""Close Event rules, pure: konsol/close/close_event_model.py (konsol#305 W2,
#298 story 10.1, #305-W2-1).

The rule an event dict must meet before any writer inserts it. Loaded by
path; the module imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(APP_DIR, "close", "close_event_model.py")
_spec = importlib.util.spec_from_file_location("close_event_model_under_test", MODEL_PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _event(**overrides):
    """A clean, valid live "approved" event; overrides flip one field."""
    base = {
        "kind": "approved",
        "source": "live",
        "fiscal_year": 2025,
        "fiscal_period": 9,
        "actor": "zz-dana@example.com",
        "at": "2026-09-30T10:00:00",
        "reference_doctype": None,
        "reference_name": None,
        "detail": None,
        "reason": None,
    }
    base.update(overrides)
    return base


def _field_named(problems, *names):
    assert len(problems) == 1, problems
    assert any(name in problems[0] for name in names), problems


# --- event_problems: the clean case -------------------------------------------

def test_a_clean_live_approved_event_gives_no_problems():
    assert M.event_problems(_event()) == []


# --- event_problems: failure paths --------------------------------------------

def test_unknown_kind_is_named():
    _field_named(M.event_problems(_event(kind="bogus")), "kind")


def test_unknown_source_is_named():
    _field_named(M.event_problems(_event(source="bogus")), "source")


def test_fiscal_year_zero_is_named():
    _field_named(M.event_problems(_event(fiscal_year=0)), "fiscal_year")


def test_fiscal_period_negative_is_named():
    _field_named(M.event_problems(_event(fiscal_period=-1)), "fiscal_period")


def test_period_closed_with_period_zero_is_named():
    _field_named(
        M.event_problems(_event(kind="period_closed", fiscal_period=0)),
        "fiscal_period",
    )


def test_year_closed_with_nonzero_period_is_named():
    _field_named(
        M.event_problems(_event(kind="year_closed", fiscal_period=3)),
        "fiscal_period",
    )


def test_blank_actor_is_named():
    _field_named(M.event_problems(_event(actor="")), "actor")


def test_only_reference_name_given_is_named():
    _field_named(
        M.event_problems(_event(reference_doctype=None, reference_name="TB-0001")),
        "reference_doctype", "reference_name",
    )


def test_detail_a_list_is_named():
    _field_named(M.event_problems(_event(detail=[1, 2, 3])), "detail")


def test_detail_holding_a_set_is_not_json_serialisable():
    _field_named(M.event_problems(_event(detail={"x": {1, 2}})), "detail")


def test_live_rejected_with_blank_reason_is_named():
    _field_named(
        M.event_problems(_event(kind="rejected", reason="")),
        "reason",
    )


def test_live_signed_off_overridden_with_blank_reason_is_named():
    _field_named(
        M.event_problems(_event(
            kind="signed_off", reason="",
            detail={"signoff_status": "Overridden"},
        )),
        "reason",
    )


def test_backfill_self_approved_blank_reason_no_flag_is_named():
    _field_named(
        M.event_problems(_event(
            kind="self_approved", source="backfill", reason="", detail=None,
        )),
        "reason",
    )


# --- event_problems: the declared exceptions ----------------------------------

def test_backfill_self_approved_blank_reason_with_flag_gives_no_problems():
    assert M.event_problems(_event(
        kind="self_approved", source="backfill", reason="",
        detail={"reason_not_recorded": True},
    )) == []


def test_live_signed_off_plain_signed_off_with_blank_reason_gives_no_problems():
    assert M.event_problems(_event(
        kind="signed_off", reason="",
        detail={"signoff_status": "Signed Off"},
    )) == []


# --- KINDS: approval_cancelled (T02c, #305-W2-8) -------------------------------

def test_approval_cancelled_is_a_declared_kind():
    assert "approval_cancelled" in M.KINDS


def test_live_approval_cancelled_with_no_reason_gives_no_problems():
    # Frappe's cancel takes no reason, and a Reverse is a cancel (top Problems
    # W2-P5): approval_cancelled is not in REASON_REQUIRED.
    assert M.event_problems(_event(kind="approval_cancelled")) == []


def test_approval_cancelled_with_period_zero_is_named():
    # Not a year kind: fiscal_period must not be 0.
    _field_named(
        M.event_problems(_event(kind="approval_cancelled", fiscal_period=0)),
        "fiscal_period",
    )


# --- approval_kind --------------------------------------------------------------

def test_approval_kind_approver_in_preparers_is_self_approved():
    assert M.approval_kind({"a", "b"}, "b") == "self_approved"


def test_approval_kind_approver_not_in_preparers_is_approved():
    assert M.approval_kind({"a"}, "b") == "approved"


def test_approval_kind_preparers_a_string_raises_type_error():
    # Failure path: a str passed by mistake would match by substring
    # ("b" in "ab" is True), silently calling a non-preparer self-approved.
    try:
        M.approval_kind("ab", "b")
    except TypeError:
        pass
    else:
        raise AssertionError("approval_kind('ab', 'b') should raise TypeError")


# --- detail_json -----------------------------------------------------------------

def test_detail_json_sorts_keys():
    assert M.detail_json({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'


def test_detail_json_blank_is_none():
    assert M.detail_json({}) is None
    assert M.detail_json(None) is None


# --- ic_sent_back (konsol#305 X02, #305-W3-1) ---------------------------------------

def _ic_sent_back_detail():
    return {
        "entity_a": "UK01",
        "account_a": "1810",
        "entity_b": "DE01",
        "account_b": "2810",
        "groups": [
            {
                "consolidation_group": "GRP",
                "difference": 120.5,
                "tolerance": 50,
                "match_status": "over_tolerance",
            }
        ],
    }


def test_ic_sent_back_is_a_declared_kind_that_needs_a_reason():
    assert "ic_sent_back" in M.KINDS
    assert "ic_sent_back" in M.REASON_REQUIRED


def test_a_clean_ic_sent_back_event_gives_no_problems():
    event = _event(
        kind="ic_sent_back",
        entity="UK01",
        reason="Our side agrees to INV-5531",
        detail=_ic_sent_back_detail(),
    )
    assert M.event_problems(event) == []


def test_ic_sent_back_without_a_reason_is_refused():
    event = _event(
        kind="ic_sent_back",
        entity="UK01",
        detail=_ic_sent_back_detail(),
    )
    assert M.event_problems(event) == ["reason is required for ic_sent_back."]


def test_ic_sent_back_fiscal_period_zero_is_named():
    event = _event(
        kind="ic_sent_back",
        entity="UK01",
        reason="Our side agrees to INV-5531",
        detail=_ic_sent_back_detail(),
        fiscal_period=0,
    )
    _field_named(M.event_problems(event), "fiscal_period")


# --- commentary_saved (konsol#305 M41, #305-W4-6) -----------------------------------

def _commentary_saved_detail():
    return {
        "consolidation_group": "GRP",
        "heading": "4",
        "heading_name": "NET SALES",
        "text": "Volume down 4%",
    }


def test_commentary_saved_is_a_declared_kind_not_reason_required():
    assert "commentary_saved" in M.KINDS
    assert "commentary_saved" not in M.REASON_REQUIRED


def test_a_clean_commentary_saved_event_with_no_reason_gives_no_problems():
    event = _event(
        kind="commentary_saved",
        entity=None,
        detail=_commentary_saved_detail(),
    )
    assert M.event_problems(event) == []


def test_commentary_saved_fiscal_period_zero_is_named():
    # Not a year kind: fiscal_period must not be 0.
    event = _event(
        kind="commentary_saved",
        entity=None,
        detail=_commentary_saved_detail(),
        fiscal_period=0,
    )
    _field_named(M.event_problems(event), "fiscal_period")


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
