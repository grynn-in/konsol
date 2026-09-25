"""Sign-off model, pure: konsol/close/signoff_model.py (konsol#305 A09, #303-3a).

Configuration gaps and the order gate. Loaded by path; the module imports
nothing from frappe or konsol.
"""
import ast
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(APP_DIR, "close", "signoff_model.py")
_spec = importlib.util.spec_from_file_location("close_signoff_model_under_test", MODEL_PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

FIRST = (2025, 7)
UNDECLARED = {
    "code": "first_close_undeclared",
    "message": "Declare the first close period in Close Settings (the Close Lead or System Manager) before signing off.",
}


def _state(fy, fp, status="Closed", signoff="Signed Off"):
    return {"key": (fy, fp), "code": "P%02d" % fp, "status": status, "signoff": signoff}


def _fy2025(overrides=None):
    """FY2025 P01-P12; Closed and signed unless overridden {fp: (status, signoff)}."""
    rows = []
    for fp in range(1, 13):
        status, signoff = (overrides or {}).get(fp, ("Closed", "Signed Off"))
        rows.append(_state(2025, fp, status, signoff))
    return rows


def _codes(gaps):
    return [g["code"] for g in gaps]


# --- config_gaps --------------------------------------------------------------

def test_undeclared_first_close_is_a_named_gap():
    assert M.config_gaps(None, (2025, 9), {"ZZA": "Monthly"}) == [UNDECLARED]


def test_first_close_read_back_as_zero_is_undeclared():
    # Close Settings Int fields read back as 0 when unset (coordinator note, A06).
    for first_close in ((0, 0), (2025, 0), (0, 7)):
        assert M.config_gaps(first_close, (2025, 9), {"ZZA": "Monthly"}) == [UNDECLARED], first_close


def test_target_before_first_close_is_history():
    gaps = M.config_gaps(FIRST, (2025, 6), {"ZZA": "Monthly"})
    assert _codes(gaps) == ["history_period"]
    assert "FY2025 P06" in gaps[0]["message"]
    assert "FY2025 P07" in gaps[0]["message"]


def test_the_first_close_period_itself_is_not_history():
    assert M.config_gaps(FIRST, FIRST, {"ZZA": "Monthly"}) == []


def test_history_across_a_year_boundary():
    assert _codes(M.config_gaps((2026, 1), (2025, 12), {})) == ["history_period"]
    assert M.config_gaps((2025, 12), (2026, 1), {}) == []


def test_blank_frequencies_are_one_gap_listing_every_entity():
    gaps = M.config_gaps(FIRST, (2025, 9), {"ZZB": "", "ZZA": None, "ZZC": "Quarterly"})
    assert _codes(gaps) == ["frequency_undeclared"]
    assert gaps[0]["entities"] == ["ZZA", "ZZB"]
    assert "ZZA, ZZB" in gaps[0]["message"]
    assert "Reporting Frequency" in gaps[0]["message"]


def test_every_gap_is_reported_together():
    gaps = M.config_gaps(None, (2025, 9), {"ZZA": ""})
    assert _codes(gaps) == ["first_close_undeclared", "frequency_undeclared"]


def test_all_declared_has_no_gaps():
    assert M.config_gaps(FIRST, (2025, 9), {"ZZA": "Monthly", "ZZB": "Quarterly"}) == []


def test_an_unknown_frequency_is_refused_not_skipped():
    try:
        M.config_gaps(FIRST, (2025, 9), {"ZZA": "Weekly"})
    except ValueError as exc:
        assert "Weekly" in str(exc) and "ZZA" in str(exc)
    else:
        raise AssertionError("an unknown frequency must raise ValueError")


# --- order_problem ------------------------------------------------------------

def test_open_earlier_periods_block_and_the_oldest_is_named():
    states = _fy2025({7: ("Open", "Not signed off"), 8: ("Open", "Not signed off"),
                      9: ("Open", "Not signed off")})
    assert M.order_problem(states, FIRST, (2025, 9)) == {
        "blocking": "P07",
        "periods": ["P07", "P08"],
        "message": "Sign off and close P07 first",
    }


def test_closed_but_re_sign_needed_blocks():
    states = _fy2025({8: ("Closed", "Re-sign Needed"), 9: ("Open", "Not signed off")})
    res = M.order_problem(states, FIRST, (2025, 9))
    assert res["blocking"] == "P08"
    assert res["periods"] == ["P08"]
    assert res["message"] == "Re-sign P08 first"


def test_history_periods_never_block():
    states = _fy2025({3: ("Open", "Not signed off"), 6: ("Closed", "Re-sign Needed"),
                      9: ("Open", "Not signed off")})
    assert M.order_problem(states, FIRST, (2025, 9)) is None


def test_later_periods_never_block():
    states = _fy2025({9: ("Open", "Not signed off"), 10: ("Open", "Not signed off")})
    assert M.order_problem(states, FIRST, (2025, 9)) is None


def test_everything_earlier_closed_and_signed_is_none():
    states = _fy2025({9: ("Open", "Not signed off")})
    assert M.order_problem(states, FIRST, (2025, 9)) is None


def test_the_first_close_period_has_nothing_before_it():
    states = _fy2025({6: ("Open", "Not signed off"), 7: ("Open", "Not signed off")})
    assert M.order_problem(states, FIRST, FIRST) is None


def test_open_p12_of_the_previous_year_blocks_p1_of_the_next():
    states = _fy2025({12: ("Open", "Not signed off")}) + [_state(2026, 1, "Open", "Not signed off")]
    res = M.order_problem(states, FIRST, (2026, 1))
    assert res["blocking"] == "P12"
    assert res["periods"] == ["P12"]


def test_previous_year_p12_before_first_close_does_not_block():
    states = _fy2025({12: ("Open", "Not signed off")}) + [_state(2026, 1, "Open", "Not signed off")]
    assert M.order_problem(states, (2026, 1), (2026, 1)) is None


def test_keys_as_lists_are_accepted():
    # States that went through JSON carry keys as lists.
    states = [dict(s, key=list(s["key"])) for s in _fy2025({8: ("Open", "Not signed off")})]
    assert M.order_problem(states, FIRST, (2025, 9))["blocking"] == "P08"


def test_order_with_undeclared_first_close_raises_not_guesses():
    states = _fy2025({8: ("Open", "Not signed off")})
    for first_close in (None, (0, 0), (2025, 0)):
        try:
            M.order_problem(states, first_close, (2025, 9))
        except ValueError as exc:
            assert "first_close_undeclared" in str(exc)
        else:
            raise AssertionError("undeclared first close must raise: %r" % (first_close,))


def test_module_imports_no_frappe():
    with open(MODEL_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith(("frappe", "konsol")) for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("frappe", "konsol"))
            assert node.level == 0
