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
