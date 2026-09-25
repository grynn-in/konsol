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
    "message": "Declare the first close period in EPM Settings (System Manager) before signing off.",
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
    # EPM Settings Int fields read back as 0 when unset (coordinator note, A06).
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


# --- A10: expected entities, quarter-end ---------------------------------------
# Rows have the fiscal_calendar.fiscal_period_rows() shape. Quarter-end is read
# from the declared ``quarter`` field, never from ``fiscal_period % 3``.


def _row(fy, fp, quarter="", period_type="Regular", code=None):
    return {
        "fiscal_year": fy,
        "fiscal_period": fp,
        "period_code": code or "P%02d" % fp,
        "period_type": period_type,
        "quarter": quarter,
    }


def _monthly_year(fy=2025):
    """P00 Opening, P01-P12 Regular with Q1 = P01-P03 ... Q4 = P10-P12, P13 Closing."""
    rows = [_row(fy, 0, period_type="Opening")]
    for fp in range(1, 13):
        rows.append(_row(fy, fp, "Q%d" % ((fp - 1) // 3 + 1)))
    rows.append(_row(fy, 13, period_type="Closing"))
    return rows


FREQ = {"ZZM": "Monthly", "ZZQ": "Quarterly"}


def test_monthly_entities_are_always_expected():
    rows = _monthly_year()
    for fp in range(1, 13):
        res = M.expected_entities(FREQ, (2025, fp), rows)
        assert "ZZM" in res["expected"], fp


def test_quarterly_entity_expected_at_quarter_end_only():
    rows = _monthly_year()
    p03 = M.expected_entities(FREQ, (2025, 3), rows)
    assert p03 == {"expected": ["ZZM", "ZZQ"], "not_expected": [], "frequency_undeclared": [], "gaps": []}
    p02 = M.expected_entities(FREQ, (2025, 2), rows)
    assert p02 == {"expected": ["ZZM"], "not_expected": ["ZZQ"], "frequency_undeclared": [], "gaps": []}


def test_quarter_end_follows_the_declared_quarter_not_period_mod_3():
    # A 13-period year whose Close Lead declared Q1 = P01-P04: P03 is NOT quarter-end, P04 is.
    rows = [_row(2025, fp, q) for fp, q in
            [(1, "Q1"), (2, "Q1"), (3, "Q1"), (4, "Q1"), (5, "Q2"), (6, "Q2"), (7, "Q2"),
             (8, "Q3"), (9, "Q3"), (10, "Q3"), (11, "Q4"), (12, "Q4"), (13, "Q4")]]
    assert M.expected_entities(FREQ, (2025, 3), rows)["not_expected"] == ["ZZQ"]
    assert M.expected_entities(FREQ, (2025, 4), rows)["expected"] == ["ZZM", "ZZQ"]
    assert M.expected_entities(FREQ, (2025, 12), rows)["not_expected"] == ["ZZQ"]
    assert M.expected_entities(FREQ, (2025, 13), rows)["expected"] == ["ZZM", "ZZQ"]


def test_non_regular_rows_never_make_a_quarter_end():
    # A Closing P13 carrying Q4 must not push the quarter-end past P12.
    rows = _monthly_year()
    rows[-1]["quarter"] = "Q4"
    assert M.expected_entities(FREQ, (2025, 12), rows)["expected"] == ["ZZM", "ZZQ"]


def test_quarterly_entity_and_blank_target_quarter_is_a_gap():
    # A 13-period year: Generate Periods leaves quarter blank.
    rows = [_row(2025, fp) for fp in range(1, 14)]
    res = M.expected_entities(FREQ, (2025, 3), rows)
    assert res["expected"] == ["ZZM"]
    assert res["not_expected"] == []
    assert [g["code"] for g in res["gaps"]] == ["quarter_undeclared"]
    gap = res["gaps"][0]
    assert gap["entities"] == ["ZZQ"]
    assert "P03" in gap["message"] and "Quarter" in gap["message"]


def test_blank_quarter_elsewhere_in_the_year_is_a_gap_not_a_guess():
    # P01 declares Q1 but P02-P03 are blank: whether P01 ends Q1 is unknown.
    rows = [_row(2025, 1, "Q1")] + [_row(2025, fp) for fp in range(2, 13)]
    res = M.expected_entities(FREQ, (2025, 1), rows)
    assert [g["code"] for g in res["gaps"]] == ["quarter_undeclared"]
    assert "ZZQ" not in res["expected"] and "ZZQ" not in res["not_expected"]


def test_blank_quarter_is_no_gap_without_quarterly_entities():
    rows = [_row(2025, fp) for fp in range(1, 14)]
    res = M.expected_entities({"ZZM": "Monthly"}, (2025, 3), rows)
    assert res == {"expected": ["ZZM"], "not_expected": [], "frequency_undeclared": [], "gaps": []}


def test_blank_frequency_is_neither_expected_nor_excused():
    rows = _monthly_year()
    res = M.expected_entities({"ZZM": "Monthly", "ZZB": "", "ZZN": None}, (2025, 3), rows)
    assert res["expected"] == ["ZZM"]
    assert res["not_expected"] == []
    assert res["frequency_undeclared"] == ["ZZB", "ZZN"]
    # config_gaps already reports the blank frequency; it is not a second gap here.
    assert res["gaps"] == []


def test_expected_entities_refuses_what_it_cannot_decide():
    rows = _monthly_year()
    cases = [
        ({"ZZX": "Weekly"}, (2025, 3), "Weekly"),       # unknown frequency
        (FREQ, (2025, 14), "not in the calendar"),      # target missing from rows
        (FREQ, (2025, 13), "Regular"),                  # non-Regular target (P5)
    ]
    for frequencies, target, needle in cases:
        try:
            M.expected_entities(frequencies, target, rows)
        except ValueError as exc:
            assert needle in str(exc), (target, str(exc))
        else:
            raise AssertionError("must raise for %r %r" % (frequencies, target))


def test_expected_entities_accepts_list_keys():
    assert M.expected_entities(FREQ, [2025, 3], _monthly_year())["expected"] == ["ZZM", "ZZQ"]


# --- A10: completeness gate -----------------------------------------------------


def _doc(entity, fp=9, fy=2025, docstatus=1):
    return {"data_area_id": entity, "fiscal_year": fy, "fiscal_period": fp, "docstatus": docstatus}


def test_completeness_names_the_missing_entities_sorted():
    res = M.completeness_problem(["ZZC", "ZZB", "ZZA"], [_doc("ZZC")], [])
    assert res == {
        "missing": ["ZZA", "ZZB"],
        "message": "No trial balance from ZZA, ZZB. Upload them or declare an exception.",
    }


def test_completeness_one_missing_entity():
    res = M.completeness_problem(["ZZA"], [], [])
    assert res == {
        "missing": ["ZZA"],
        "message": "No trial balance from ZZA. Upload it or declare an exception.",
    }


def test_completeness_tb_or_exception_satisfies():
    assert M.completeness_problem(["ZZA", "ZZB"], [_doc("ZZA")], [_doc("ZZB")]) is None


def test_completeness_nothing_expected_is_none():
    assert M.completeness_problem([], [], []) is None


def test_cancelled_or_draft_documents_cover_nothing():
    for docstatus in (0, 2):
        res = M.completeness_problem(["ZZA", "ZZB"], [_doc("ZZA", docstatus=docstatus)],
                                     [_doc("ZZB", docstatus=docstatus)])
        assert res["missing"] == ["ZZA", "ZZB"], docstatus


def test_completeness_needs_a_docstatus_not_a_default():
    try:
        M.completeness_problem(["ZZA"], [{"data_area_id": "ZZA"}], [])
    except KeyError:
        pass
    else:
        raise AssertionError("a record without docstatus must not count as submitted")


# --- A10: covers notes ----------------------------------------------------------


def test_exception_then_tb_covers_both_periods():
    notes = M.covers_notes((2025, 9), _monthly_year(), [_doc("ZZA", 9)], [_doc("ZZA", 8)])
    assert notes == ["ZZA: covers P08–P09"]


def test_a_run_of_exceptions_is_covered_from_its_first_period():
    exc = [_doc("ZZA", 7), _doc("ZZA", 8)]
    notes = M.covers_notes((2025, 9), _monthly_year(), [_doc("ZZA", 9)], exc)
    assert notes == ["ZZA: covers P07–P09"]


def test_the_run_stops_at_a_period_with_a_tb():
    exc = [_doc("ZZA", 6), _doc("ZZA", 8)]
    tbs = [_doc("ZZA", 7), _doc("ZZA", 9)]
    assert M.covers_notes((2025, 9), _monthly_year(), tbs, exc) == ["ZZA: covers P08–P09"]


def test_no_note_without_a_tb_in_the_target_or_an_exception_before_it():
    rows = _monthly_year()
    assert M.covers_notes((2025, 9), rows, [], [_doc("ZZA", 8)]) == []
    assert M.covers_notes((2025, 9), rows, [_doc("ZZA", 9)], []) == []
    assert M.covers_notes((2025, 9), rows, [_doc("ZZA", 9)], [_doc("ZZA", 7)]) == []


def test_a_cancelled_exception_covers_nothing():
    notes = M.covers_notes((2025, 9), _monthly_year(), [_doc("ZZA", 9)], [_doc("ZZA", 8, docstatus=2)])
    assert notes == []


def test_a_cancelled_tb_in_the_target_covers_nothing():
    notes = M.covers_notes((2025, 9), _monthly_year(), [_doc("ZZA", 9, docstatus=2)], [_doc("ZZA", 8)])
    assert notes == []


def test_covers_notes_skip_non_regular_rows_and_stop_at_the_year():
    rows = _monthly_year(2025) + _monthly_year(2026)
    # FY2026 P01 does not cover FY2025 P12: the year-end needs its own trial balance.
    notes = M.covers_notes((2026, 1), rows, [_doc("ZZA", 1, fy=2026)], [_doc("ZZA", 12)])
    assert notes == []


def test_covers_notes_are_sorted_by_entity():
    tbs = [_doc("ZZB", 9), _doc("ZZA", 9)]
    exc = [_doc("ZZB", 8), _doc("ZZA", 8)]
    assert M.covers_notes((2025, 9), _monthly_year(), tbs, exc) == [
        "ZZA: covers P08–P09", "ZZB: covers P08–P09"]
