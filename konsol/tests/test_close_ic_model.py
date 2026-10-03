"""konsol#305 C01 — ic_model part 1: state, W3-2 masking, counts (pure).

``konsol/close/ic_model.py`` is pure like fiscal_status_model.py: loaded by
path (test_fiscal_status_model.py:1-12), no frappe or konsol import.
Decisions: #305-W3-2 option B (own side, difference, partner code), #305-W3-7
option A (declared "none in this group" → not applicable), and the W3
engineering call (0 Published → "not configured — nothing was checked").
"""
import ast
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IC_MODEL_PY = os.path.join(APP_DIR, "close", "ic_model.py")
IC_ACCOUNT_PY = os.path.join(APP_DIR, "consolidation", "doctype", "intercompany_account",
                             "intercompany_account.py")

_spec = importlib.util.spec_from_file_location("ic_model_under_test", IC_MODEL_PY)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _raises(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type as e:
        return e
    raise AssertionError("%s not raised" % exc_type.__name__)


def _no_success_words(message, extra=()):
    low = message.lower()
    for word in ("reconciled", "within tolerance", "matched") + tuple(extra):
        assert word not in low, "%r must not say %r" % (message, word)


# --- constants ---------------------------------------------------------------

def test_states_and_match_statuses():
    assert M.STATES == ("not_configured", "not_applicable", "not_built", "error", "checked")
    assert M.MATCH_STATUSES == ("matched", "within_tolerance", "fx_difference", "over_tolerance")


def test_pinned_messages():
    assert M.NOT_CONFIGURED == "Intercompany not configured — nothing was checked."
    assert M.NOT_BUILT == ("The warehouse has not built the intercompany tables yet "
                           "— nothing was checked.")
    assert M.NOT_APPLICABLE == ("Intercompany: none in this group (declared in Close Settings) "
                                "— not applicable.")


def test_setup_help_carries_293_wording():
    assert M.SETUP_HELP.startswith(M.NOT_CONFIGURED)
    for part in ("Main Account", "Allow Intercompany", "not configured — nothing was checked",
                 "Set Allow Intercompany on those accounts before publishing this pairing."):
        assert part in M.SETUP_HELP, part


def test_setup_help_sentence_matches_intercompany_account_refusal():
    sentence = "Set Allow Intercompany on those accounts before publishing this pairing."
    with open(IC_ACCOUNT_PY, encoding="utf-8") as f:
        src = f.read()
    assert sentence in src, "#293 refusal wording changed in intercompany_account.py"
    assert sentence in M.SETUP_HELP


# --- state -------------------------------------------------------------------

def test_not_configured_never_reads_as_reconciled():
    s = M.state(0)
    assert s == {"state": "not_configured", "message": M.NOT_CONFIGURED}
    _no_success_words(s["message"])


def test_not_configured_is_decided_before_any_warehouse_answer():
    s = M.state(0, {"not_built": True, "text": "x"})
    assert s["state"] == "not_configured"
    assert s["message"] == M.NOT_CONFIGURED


def test_checked():
    assert M.state(3) == {"state": "checked", "message": None}


def test_not_built():
    s = M.state(3, {"not_built": True, "text": "(UNKNOWN_TABLE)"})
    assert s == {"state": "not_built", "message": M.NOT_BUILT}
    _no_success_words(s["message"])


def test_error_names_the_text():
    s = M.state(3, {"not_built": False, "text": "HTTPError (TIMEOUT_EXCEEDED)"})
    assert s["state"] == "error"
    assert s["message"] == ("Intercompany could not be checked: HTTPError (TIMEOUT_EXCEEDED). "
                            "Rebuild the consolidation, then open this again.")
    _no_success_words(s["message"])


def test_state_refuses_a_negative_count():
    _raises(ValueError, M.state, -1)


def test_state_refuses_a_non_int_count():
    _raises(ValueError, M.state, "2")


def test_state_refuses_a_bool_count():
    _raises(ValueError, M.state, True)


def test_not_applicable_never_reads_as_reconciled_or_as_a_gap():
    s = M.state(0, declared_none=True)
    assert s == {"state": "not_applicable", "message": M.NOT_APPLICABLE}
    _no_success_words(s["message"], extra=("not configured",))


def test_not_applicable_is_decided_before_the_warehouse():
    s = M.state(0, {"not_built": True, "text": "x"}, declared_none=True)
    assert s["state"] == "not_applicable"


def test_declared_none_with_published_accounts_is_an_error():
    s = M.state(2, declared_none=True)
    assert s["state"] == "error"
    assert s["message"] == ("Close Settings declares no intercompany in this group, but 2 "
                            "Intercompany Account(s) are Published: clear the declaration or "
                            "make them Inactive.")
    _no_success_words(s["message"], extra=("not applicable",))


def test_declared_none_must_be_a_bool():
    _raises(ValueError, M.state, 0, declared_none="yes")
    _raises(ValueError, M.state, 0, declared_none=1)
    _raises(ValueError, M.state, 0, declared_none=None)


def test_default_never_declares_none():
    assert M.state(0)["state"] == "not_configured"


# --- mask --------------------------------------------------------------------

def _row(ea, aa, eb, ab, status="over_tolerance", bal_a=1000.0, bal_b=-900.0):
    return {
        "consolidation_group": "GRP", "fiscal_year": 2025, "fiscal_period": 7,
        "entity_a": ea, "account_a": aa, "entity_b": eb, "account_b": ab,
        "basis": "balance", "pair_event": "", "currency_a": "GBP", "currency_b": "EUR",
        "local_a": bal_a * 0.8, "local_b": bal_b * 1.1,
        "balance_a": bal_a, "balance_b": bal_b,
        "group_balance_a": bal_a * 0.75, "group_balance_b": bal_b * 0.6,
        "share_a": 0.75, "share_b": 0.6, "matched_amount": min(abs(bal_a), abs(bal_b)),
        "difference": bal_a + bal_b, "net_balance": bal_a + bal_b,
        "residual_a": 100.0, "residual_b": -0.0,
        "difference_cause": "booking", "ic_difference_account": "", "tolerance": 10.0,
        "match_status": status,
    }


def _fixture():
    return [
        _row("UK01", "1810", "DE01", "2810", bal_a=1234.5, bal_b=-1111.25),
        _row("DE01", "1820", "FR01", "2820", bal_a=500.0, bal_b=-500.0, status="matched"),
        _row("UK01", "1830", "UK02", "2830", bal_a=70.0, bal_b=-60.0, status="within_tolerance"),
    ]


MASKED_COLUMNS = ("balance", "local", "group_balance", "share", "residual", "currency")


def test_mask_scoped_caller_keeps_own_side_and_partner_code():
    rows = _fixture()
    kept, hidden = M.mask(rows, {"UK01"})
    assert hidden == 1
    assert len(kept) == 2
    by = {(r["entity_a"], r["entity_b"]): r for r in kept}
    ukde = by[("UK01", "DE01")]
    for col in MASKED_COLUMNS:
        assert ukde[col + "_b"] is None, col + "_b"
    assert ukde["masked_b"] is True
    assert ukde["masked_a"] is False
    assert ukde["entity_b"] == "DE01"
    assert ukde["account_b"] == "2810"
    assert ukde["difference"] == rows[0]["difference"]
    assert ukde["match_status"] == "over_tolerance"
    assert ukde["difference_cause"] == "booking"
    assert ukde["balance_a"] == 1234.5
    assert ukde["currency_a"] == "GBP"
    ukuk = by[("UK01", "UK02")]
    assert ukuk["masked_a"] is False and ukuk["masked_b"] is True
    # UK02 is not allowed: its side is masked too
    assert ukuk["balance_b"] is None


def test_mask_both_sides_allowed_masks_nothing():
    rows = _fixture()
    kept, hidden = M.mask(rows, {"UK01", "UK02"})
    assert hidden == 1
    ukuk = [r for r in kept if r["entity_b"] == "UK02"][0]
    assert ukuk["masked_a"] is False and ukuk["masked_b"] is False
    assert ukuk["balance_b"] == -60.0
    assert ukuk["local_b"] == rows[2]["local_b"]


def test_mask_partner_side_a_masked():
    rows = _fixture()
    kept, hidden = M.mask(rows, {"FR01"})
    assert hidden == 2
    (r,) = kept
    assert r["masked_a"] is True and r["masked_b"] is False
    for col in MASKED_COLUMNS:
        assert r[col + "_a"] is None
    assert r["entity_a"] == "DE01" and r["balance_b"] == -500.0


def test_mask_partner_amount_never_leaks():
    rows = _fixture()
    secret = {rows[0]["balance_b"], rows[0]["local_b"], rows[0]["group_balance_b"]}
    kept, _ = M.mask(rows, {"UK01"})
    ukde = [r for r in kept if r["entity_b"] == "DE01"][0]
    for k, v in ukde.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            assert v not in secret, "partner amount leaked in %s" % k


def test_mask_does_not_mutate_input():
    rows = _fixture()
    M.mask(rows, {"UK01"})
    assert rows[0]["balance_b"] == -1111.25
    assert "masked_b" not in rows[0]


def test_mask_unscoped_keeps_everything():
    rows = _fixture()
    kept, hidden = M.mask(rows, None)
    assert hidden == 0
    assert len(kept) == 3
    for orig, r in zip(rows, kept):
        assert r["masked_a"] is False and r["masked_b"] is False
        for k, v in orig.items():
            assert r[k] == v


def test_mask_empty_allowed_hides_all():
    kept, hidden = M.mask(_fixture(), set())
    assert kept == []
    assert hidden == 3


def test_mask_unmatched():
    un = [
        {"consolidation_group": "GRP", "data_area_id": "UK01", "main_account": "1810",
         "unmatched_amount": 5.0, "unmatched_local_amount": 4.0},
        {"consolidation_group": "GRP", "data_area_id": "DE01", "main_account": "2810",
         "unmatched_amount": 6.0, "unmatched_local_amount": 7.0},
        {"consolidation_group": "GRP", "data_area_id": "FR01", "main_account": "2820",
         "unmatched_amount": 8.0, "unmatched_local_amount": 9.0},
    ]
    kept, hidden = M.mask_unmatched(un, {"UK01", "FR01"})
    assert [r["data_area_id"] for r in kept] == ["UK01", "FR01"]
    assert hidden == 1
    kept, hidden = M.mask_unmatched(un, None)
    assert len(kept) == 3 and hidden == 0
    kept, hidden = M.mask_unmatched(un, set())
    assert kept == [] and hidden == 3


# --- counts ------------------------------------------------------------------

def test_counts_one_of_each():
    rows = [_row("A", "1", "B", "2", status=s) for s in M.MATCH_STATUSES]
    un = [{"data_area_id": "A"}, {"data_area_id": "B"}]
    assert M.counts(rows, un) == {"pairs": 4, "matched": 1, "within_tolerance": 1,
                                  "fx_difference": 1, "over_tolerance": 1, "unmatched": 2}


def test_counts_empty():
    assert M.counts([], []) == {"pairs": 0, "matched": 0, "within_tolerance": 0,
                                "fx_difference": 0, "over_tolerance": 0, "unmatched": 0}


def test_counts_refuses_an_unknown_status():
    rows = [_row("A", "1", "B", "2", status="close_enough")]
    e = _raises(ValueError, M.counts, rows, [])
    assert "close_enough" in str(e)


# --- pair_key, sent_back (C02) ------------------------------------------------

def _event(name, at, actor="user@example.com", reason="because", detail=None, kind="ic_sent_back"):
    return {"name": name, "kind": kind, "at": at, "actor": actor, "reason": reason,
            "detail": detail or {}}


def _detail(ea, aa, eb, ab, extra=None):
    d = {"entity_a": ea, "account_a": aa, "entity_b": eb, "account_b": ab}
    if extra:
        d.update(extra)
    return d


def test_pair_key_from_row_and_from_detail():
    row = _row("UK01", "1810", "DE01", "2810")
    assert M.pair_key(row) == ("UK01", "1810", "DE01", "2810")
    assert M.pair_key(_detail("UK01", "1810", "DE01", "2810")) == ("UK01", "1810", "DE01", "2810")


def test_sent_back_keeps_the_later_event():
    d = _detail("UK01", "1810", "DE01", "2810")
    e1 = _event("CE-1", "2025-07-01 10:00:00", reason="first", detail=d)
    e2 = _event("CE-2", "2025-07-02 10:00:00", reason="second", detail=d)
    latest = M.sent_back([e1, e2])
    assert len(latest) == 1
    key = ("UK01", "1810", "DE01", "2810")
    assert latest[key]["name"] == "CE-2"
    assert latest[key]["reason"] == "second"


def test_sent_back_order_independent():
    d = _detail("UK01", "1810", "DE01", "2810")
    e1 = _event("CE-1", "2025-07-01 10:00:00", detail=d)
    e2 = _event("CE-2", "2025-07-02 10:00:00", detail=d)
    assert M.sent_back([e2, e1])[("UK01", "1810", "DE01", "2810")]["name"] == "CE-2"


def test_sent_back_refuses_a_non_sent_back_kind():
    e = _event("CE-3", "2025-07-01 10:00:00", kind="rejected", detail=_detail("A", "1", "B", "2"))
    _raises(ValueError, M.sent_back, [e])


def test_sent_back_refuses_a_detail_missing_a_key():
    bad = {"entity_a": "UK01", "account_a": "1810", "entity_b": "DE01"}
    e = _event("CE-4", "2025-07-01 10:00:00", detail=bad)
    err = _raises(ValueError, M.sent_back, [e])
    assert "CE-4" in str(err)


# --- open_fixes (C02) ----------------------------------------------------------

def test_open_fixes_over_tolerance_in_any_group_is_enough():
    pair = ("UK01", "1810", "DE01", "2810")
    d = _detail(*pair)
    events = [_event("CE-1", "2025-07-01 10:00:00", reason="chasing it", detail=d)]
    rows = [
        _row(*pair, status="over_tolerance"),
        {**_row(*pair, status="within_tolerance"), "consolidation_group": "SUBGRP"},
    ]
    fixes = M.open_fixes(events, rows)
    assert len(fixes) == 1
    fix = fixes[0]
    assert fix["entity_a"] == "UK01" and fix["account_a"] == "1810"
    assert fix["entity_b"] == "DE01" and fix["account_b"] == "2810"
    assert fix["state"] == "over_tolerance"
    assert fix["sent_by"] == "user@example.com"
    assert fix["sent_at"] == "2025-07-01 10:00:00"
    assert fix["reason"] == "chasing it"
    assert fix["error"] is None
    assert {g["consolidation_group"] for g in fix["groups"]} == {"GRP", "SUBGRP"}


def test_open_fixes_cleared_when_every_group_is_within_or_matched_or_fx():
    pair = ("UK01", "1810", "DE01", "2810")
    d = _detail(*pair)
    events = [_event("CE-1", "2025-07-01 10:00:00", detail=d)]
    for status in ("within_tolerance", "matched", "fx_difference"):
        rows = [_row(*pair, status=status)]
        assert M.open_fixes(events, rows) == []


def test_open_fixes_not_in_build_on_empty_rows():
    pair = ("UK01", "1810", "DE01", "2810")
    events = [_event("CE-1", "2025-07-01 10:00:00", detail=_detail(*pair))]
    fixes = M.open_fixes(events, [])
    assert len(fixes) == 1
    assert fixes[0]["state"] == "not_in_build"
    assert fixes[0]["groups"] == []


def test_open_fixes_cannot_check_on_error_whatever_rows_holds():
    pair = ("UK01", "1810", "DE01", "2810")
    events = [_event("CE-1", "2025-07-01 10:00:00", detail=_detail(*pair))]
    for rows in ([], [_row(*pair, status="matched")], [_row(*pair, status="over_tolerance")]):
        fixes = M.open_fixes(events, rows, error="HTTPError (TIMEOUT_EXCEEDED)")
        assert len(fixes) == 1
        assert fixes[0]["state"] == "cannot_check"
        assert fixes[0]["error"] == "HTTPError (TIMEOUT_EXCEEDED)"


def test_open_fixes_fx_difference_alone_does_not_hold_a_fix_open():
    pair = ("UK01", "1810", "DE01", "2810")
    events = [_event("CE-1", "2025-07-01 10:00:00", detail=_detail(*pair))]
    rows = [_row(*pair, status="fx_difference")]
    assert M.open_fixes(events, rows) == []


def test_open_fixes_sorted_by_pair_key():
    events = [
        _event("CE-1", "2025-07-01 10:00:00", detail=_detail("UK02", "1", "DE01", "2")),
        _event("CE-2", "2025-07-01 10:00:00", detail=_detail("UK01", "1", "DE01", "2")),
    ]
    rows = [
        _row("UK02", "1", "DE01", "2", status="over_tolerance"),
        _row("UK01", "1", "DE01", "2", status="over_tolerance"),
    ]
    fixes = M.open_fixes(events, rows)
    assert [f["entity_a"] for f in fixes] == ["UK01", "UK02"]


# --- group_view (C02) ----------------------------------------------------------

def test_group_view_groups_sorts_and_flags_can_send_back():
    pair_over = ("UK01", "1810", "DE01", "2810")
    pair_fx = ("UK01", "1820", "DE01", "2820")
    pair_within = ("UK01", "1830", "DE01", "2830")
    pair_matched = ("UK01", "1840", "DE01", "2840")
    rows = [
        _row(*pair_matched, status="matched"),
        _row(*pair_within, status="within_tolerance"),
        _row(*pair_fx, status="fx_difference"),
        _row(*pair_over, status="over_tolerance"),
        {**_row("DE01", "1", "FR01", "2", status="over_tolerance"), "consolidation_group": "AGRP",
         "tolerance": 5.0},
    ]
    events = [_event("CE-1", "2025-07-01 10:00:00", reason="holding it", detail=_detail(*pair_over))]
    currencies = {"GRP": "GBP"}
    groups = M.group_view(rows, events, currencies)
    assert [g["consolidation_group"] for g in groups] == ["AGRP", "GRP"]
    grp = groups[1]
    assert grp["reporting_currency"] == "GBP"
    assert grp["tolerance"] == 10.0
    assert grp["tolerance_declared"] is True
    order = [(r["entity_a"], r["account_a"]) for r in grp["pairs"]]
    assert order == [("UK01", "1810"), ("UK01", "1820"), ("UK01", "1830"), ("UK01", "1840")]
    over_row = grp["pairs"][0]
    assert over_row["can_send_back"] is True
    assert over_row["sent_back"] == {"by": "user@example.com", "at": "2025-07-01 10:00:00",
                                     "reason": "holding it"}
    assert grp["pairs"][1]["can_send_back"] is False
    assert grp["pairs"][1]["sent_back"] is None
    agrp = groups[0]
    assert agrp["reporting_currency"] is None
    assert agrp["ic_difference_account"] == ""


def test_group_view_disagreeing_tolerance_raises():
    rows = [
        _row("UK01", "1", "DE01", "2"),
        {**_row("UK01", "3", "DE01", "4"), "tolerance": 999.0},
    ]
    _raises(ValueError, M.group_view, rows, [], {})


def test_group_view_disagreeing_ic_difference_account_raises():
    rows = [
        _row("UK01", "1", "DE01", "2"),
        {**_row("UK01", "3", "DE01", "4"), "ic_difference_account": "9999"},
    ]
    _raises(ValueError, M.group_view, rows, [], {})


def test_group_view_undeclared_tolerance_refuses_can_send_back_for_everyone():
    rows = [
        {**_row("UK01", "1", "DE01", "2", status="over_tolerance"), "tolerance": 0.0},
        {**_row("UK01", "3", "DE01", "4", status="matched"), "tolerance": 0.0},
    ]
    groups = M.group_view(rows, [], {})
    assert groups[0]["tolerance_declared"] is False
    assert all(p["can_send_back"] is False for p in groups[0]["pairs"])


# --- signoff_line (C02) ---------------------------------------------------------

def test_signoff_line_not_configured_has_no_counts():
    line = M.signoff_line("not_configured", [], [], [])
    assert line == {"state": "not_configured", "message": M.NOT_CONFIGURED,
                    "counts": None, "sent_back_open": None}
    assert line["counts"] is None
    for v in line.values():
        assert v != "reconciled" and v != 0 and v != "0 pairs"


def test_signoff_line_not_applicable_has_no_counts():
    line = M.signoff_line("not_applicable", [], [], [])
    assert line == {"state": "not_applicable", "message": M.NOT_APPLICABLE,
                    "counts": None, "sent_back_open": None}


def test_signoff_line_checked_zero_pairs():
    line = M.signoff_line("checked", [], [], [])
    assert line["counts"] == {"pairs": 0, "matched": 0, "within_tolerance": 0,
                              "fx_difference": 0, "over_tolerance": 0, "unmatched": 0}
    assert line["message"] == "0 intercompany pairs in the last build for this period."
    assert line["sent_back_open"] == 0


def test_signoff_line_checked_counts_sent_back_open():
    pair = ("UK01", "1810", "DE01", "2810")
    rows = [_row(*pair, status="over_tolerance")]
    events = [_event("CE-1", "2025-07-01 10:00:00", detail=_detail(*pair))]
    line = M.signoff_line("checked", rows, [], events)
    assert line["counts"]["over_tolerance"] == 1
    assert line["sent_back_open"] == 1
    assert line["message"] is None


def test_signoff_line_refuses_an_unknown_state():
    _raises(ValueError, M.signoff_line, "reconciled", [], [], [])


# --- tolerance_gap (C02, #305-W3-6) ---------------------------------------------

def test_tolerance_gap_names_undeclared_groups():
    groups = [{"consolidation_group": "GRP", "ic_difference_tolerance": 0},
              {"consolidation_group": "SUBGRP", "ic_difference_tolerance": 5}]
    gap = M.tolerance_gap(3, False, groups)
    assert gap["code"] == M.TOLERANCE_UNDECLARED
    assert gap["groups"] == ["GRP"]
    assert "0 is undeclared" in gap["message"]
    assert "tiny positive" in gap["message"]
    assert "GRP" in gap["message"]


def test_tolerance_gap_none_when_every_group_declared():
    groups = [{"consolidation_group": "GRP", "ic_difference_tolerance": 0.01}]
    assert M.tolerance_gap(3, False, groups) is None


def test_tolerance_gap_none_when_not_published():
    groups = [{"consolidation_group": "GRP", "ic_difference_tolerance": 0}]
    assert M.tolerance_gap(0, False, groups) is None


def test_tolerance_gap_none_when_declared_none():
    groups = [{"consolidation_group": "GRP", "ic_difference_tolerance": 0}]
    assert M.tolerance_gap(3, True, groups) is None


def test_tolerance_gap_sorted_and_only_undeclared():
    groups = [{"consolidation_group": "Z", "ic_difference_tolerance": 0},
              {"consolidation_group": "A", "ic_difference_tolerance": 0},
              {"consolidation_group": "M", "ic_difference_tolerance": 5}]
    gap = M.tolerance_gap(3, False, groups)
    assert gap["groups"] == ["A", "Z"]


def test_tolerance_gap_refuses_a_negative_tolerance():
    groups = [{"consolidation_group": "GRP", "ic_difference_tolerance": -1}]
    err = _raises(ValueError, M.tolerance_gap, 3, False, groups)
    assert "GRP" in str(err)


# --- hygiene -----------------------------------------------------------------

def test_module_imports_no_frappe():
    with open(IC_MODEL_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert not a.name.startswith(("frappe", "konsol")), a.name
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert not mod.startswith(("frappe", "konsol")), mod
            assert node.level == 0, "relative import"
