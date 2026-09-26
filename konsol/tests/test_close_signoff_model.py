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


def test_more_than_five_blank_frequencies_are_counted_not_named():
    entities = ["ZZ%d" % i for i in range(1, 7)]  # 6 entities
    frequencies = {e: "" for e in entities}
    gaps = M.config_gaps(FIRST, (2025, 9), frequencies)
    assert _codes(gaps) == ["frequency_undeclared"]
    gap = gaps[0]
    assert gap["entities"] == sorted(entities)
    assert gap["message"] == (
        "6 entities have no reporting frequency. Declare it on each Entity (Monthly or Quarterly)."
    )


def test_exactly_five_blank_frequencies_are_still_named():
    entities = ["ZZ%d" % i for i in range(1, 6)]  # 5 entities
    frequencies = {e: "" for e in entities}
    gaps = M.config_gaps(FIRST, (2025, 9), frequencies)
    gap = gaps[0]
    assert gap["entities"] == sorted(entities)
    assert gap["message"] == (
        "Set the Reporting Frequency (Monthly or Quarterly) on ZZ1, ZZ2, ZZ3, ZZ4, ZZ5 "
        "before signing off."
    )
    assert "entities have no reporting frequency" not in gap["message"]


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


# --- A41: a quarterly entity's quarter-end TB is labelled with its quarter -----


def test_quarterly_entity_quarter_end_tb_notes_the_full_quarter():
    # Q1 = P01-P03 (_monthly_year); ZZQ's single P03 TB covers the whole quarter.
    freq = {"ZZQ": "Quarterly"}
    notes = M.covers_notes((2025, 3), _monthly_year(), [_doc("ZZQ", 3)], [], frequencies=freq)
    assert notes == ["ZZQ: quarterly — covers P01–P03"]


def test_monthly_entity_gets_no_quarterly_note():
    freq = {"ZZM": "Monthly"}
    notes = M.covers_notes((2025, 3), _monthly_year(), [_doc("ZZM", 3)], [], frequencies=freq)
    assert notes == []


def test_quarterly_entity_not_at_quarter_end_gets_no_quarterly_note():
    freq = {"ZZQ": "Quarterly"}
    notes = M.covers_notes((2025, 2), _monthly_year(), [_doc("ZZQ", 2)], [], frequencies=freq)
    assert notes == []


def test_quarterly_note_needs_a_declared_quarter_not_a_guess():
    # Generate Periods leaves quarter blank: the gap stays A10's quarter_undeclared,
    # covers_notes never guesses a quarter span.
    rows = [_row(2025, fp) for fp in range(1, 14)]
    freq = {"ZZQ": "Quarterly"}
    notes = M.covers_notes((2025, 3), rows, [_doc("ZZQ", 3)], [], frequencies=freq)
    assert notes == []


def test_quarterly_note_omitted_when_frequencies_not_passed():
    # Existing 4-argument callers keep working unchanged; no quarterly note is guessed.
    notes = M.covers_notes((2025, 3), _monthly_year(), [_doc("ZZQ", 3)], [])
    assert notes == []


# --- A21: the sign-off summary (story 9.1) -----------------------------------

NO_PROBLEMS = {"config_gaps": [], "order": None, "completeness": None}
ORDER_P07 = {"config_gaps": [], "completeness": None, "order": {
    "blocking": "P07", "periods": ["P07", "P08"], "message": "Sign off and close P07 first"}}


def _run(status="Green", signoff="Not Signed Off", **extra):
    run = {"name": "ZZRUN-1", "status": status, "signoff_status": signoff, "failed": 0, "errored": 0}
    run.update(extra)
    return run


def _summary(run=None, warned=(), on_behalf=(), exceptions=(), covers=(), previous=(),
             problems=None, can_override=False, period_status="Open"):
    return M.summary(run, list(warned), list(on_behalf), list(exceptions), list(covers),
                     list(previous), problems if problems is not None else NO_PROBLEMS, can_override,
                     period_status=period_status)


# --- A59: no checks and no signing on a Closed or Locked period ----------------

def test_a_closed_or_locked_period_offers_neither_run_nor_sign():
    """A59: a Closed or Locked period's unsigned run is not signed, and no
    new run is offered: the only way on is to reopen the period."""
    runs = [None, _run("Green"), _run("Amber", warned=1), _run("Red"), _run("Error"),
            _run("Queued"), _run("Running"), _run("Green", "Re-sign Needed")]
    for state in ("Closed", "Locked"):
        for run in runs:
            s = _summary(run=run, can_override=True, period_status=state)
            what = (state, run and run["status"], run and run["signoff_status"])
            assert s["action"] == "blocked", (what, s["action"])
            assert s["label"] == "The period is %s; reopen it to run the checks or sign off" % state, (
                what, s["label"])


def test_a_closed_period_blocks_before_the_other_gates():
    s = _summary(run=_run("Green"), problems=ORDER_P07, period_status="Closed")
    assert s["action"] == "blocked"
    assert s["label"].startswith("The period is Closed")


def test_a_signed_run_on_a_closed_period_stays_signed():
    for state in ("Signed Off", "Acknowledged", "Overridden"):
        for period_status in ("Closed", "Locked"):
            s = _summary(run=_run("Green", state), period_status=period_status)
            assert (s["action"], s["label"]) == ("signed", state), (state, period_status)


def test_an_open_period_is_unchanged():
    assert _summary(run=_run("Green"), period_status="Open")["action"] == "sign"
    assert _summary(run=None, period_status="Open")["action"] == "run_checks"


def test_an_unknown_period_status_is_refused_not_guessed():
    for bad in (None, "", "open", "Frozen"):
        try:
            _summary(run=_run("Green"), period_status=bad)
        except ValueError as e:
            assert "period status" in str(e).lower(), str(e)
            continue
        raise AssertionError("summary accepted period status %r" % (bad,))


def test_an_open_earlier_period_blocks_with_the_button_text():
    s = _summary(run=_run("Green"), problems=ORDER_P07)
    assert s["action"] == "blocked"
    assert s["label"] == "Sign off P07 first"
    assert s["gates"]["order"] == ORDER_P07["order"]
    assert s["gates"]["messages"] == ["Sign off and close P07 first"]


def test_the_order_gate_blocks_whatever_the_run_status():
    for status in ("Green", "Amber", "Red", "Error", "Queued", "Running"):
        s = _summary(run=_run(status), problems=ORDER_P07, can_override=True)
        assert (status, s["action"], s["label"]) == (status, "blocked", "Sign off P07 first")
    assert _summary(run=None, problems=ORDER_P07)["action"] == "blocked"


def test_configuration_gaps_block_first_and_are_listed_first():
    gap = {"code": "first_close_undeclared", "message": UNDECLARED["message"]}
    problems = {"config_gaps": [gap], "order": ORDER_P07["order"],
                "completeness": {"missing": ["ZZA"], "message": "No trial balance from ZZA. Upload it or declare an exception."}}
    s = _summary(run=_run("Green"), problems=problems)
    assert s["action"] == "blocked"
    assert s["label"] == UNDECLARED["message"]
    assert s["gates"]["messages"] == [
        UNDECLARED["message"], "Sign off and close P07 first",
        "No trial balance from ZZA. Upload it or declare an exception."]


def test_a_missing_trial_balance_blocks():
    msg = "No trial balance from ZZA, ZZB. Upload them or declare an exception."
    s = _summary(run=_run("Green"), problems={"config_gaps": [], "order": None,
                                               "completeness": {"missing": ["ZZA", "ZZB"], "message": msg}})
    assert (s["action"], s["label"]) == ("blocked", msg)


def test_no_run_means_run_the_checks():
    s = _summary(run=None)
    assert s["action"] == "run_checks"
    assert s["label"] == "Run the checks"
    assert s["checks"] == {"run": None, "status": None, "signoff_status": None, "failed": None, "errored": None}


def test_queued_or_running_means_wait():
    for status in ("Queued", "Running"):
        s = _summary(run=_run(status))
        assert (status, s["action"]) == (status, "wait")
        assert status.lower() in s["label"].lower()


def test_green_means_sign():
    s = _summary(run=_run("Green"))
    assert (s["action"], s["label"]) == ("sign", "Sign off")
    assert s["checks"]["run"] == "ZZRUN-1" and s["checks"]["status"] == "Green"


def test_amber_means_acknowledge_with_the_warned_names():
    s = _summary(run=_run("Amber", warned=2), warned=["assert_a", "assert_b"])
    assert s["action"] == "acknowledge"
    assert s["acknowledgements"] == {"names": ["assert_a", "assert_b"], "total": 2, "unlisted": 0}


def test_a_capped_name_list_says_how_many_more():
    names = ["assert_%02d" % i for i in range(50)]
    s = _summary(run=_run("Amber", warned=57), warned=names)
    assert s["acknowledgements"]["total"] == 57
    assert s["acknowledgements"]["unlisted"] == 7


def test_an_unknown_warning_count_is_unknown_not_zero():
    # latest_close_run does not return `warned`; the summary must not claim 0.
    run = _run("Amber")
    s = _summary(run=run, warned=["assert_a"])
    assert s["acknowledgements"] == {"names": ["assert_a"], "total": None, "unlisted": None}
    assert _summary(run=None)["acknowledgements"] == {"names": [], "total": None, "unlisted": None}


def test_red_or_error_means_override_for_the_close_lead():
    for status in ("Red", "Error"):
        s = _summary(run=_run(status, failed=3), can_override=True)
        assert (status, s["action"]) == (status, "override")
        assert s["checks"]["failed"] == 3


def test_red_without_the_override_role_is_blocked():
    for status in ("Red", "Error"):
        s = _summary(run=_run(status), can_override=False)
        assert (status, s["action"]) == (status, "blocked")
        assert "Close Lead" in s["label"]


def test_an_already_signed_run_is_signed():
    for state in ("Signed Off", "Acknowledged", "Overridden"):
        s = _summary(run=_run("Green", state))
        assert (state, s["action"], s["label"]) == (state, "signed", state)


def test_re_sign_needed_means_run_the_checks_again():
    s = _summary(run=_run("Green", "Re-sign Needed"))
    assert s["action"] == "rerun"
    assert s["label"] == "Run the checks again"


def test_an_unknown_run_status_is_refused_not_guessed():
    for bad in (_run("Purple"), _run("Green", "Maybe")):
        try:
            _summary(run=bad)
        except ValueError:
            continue
        raise AssertionError("summary accepted %r" % bad)


def test_on_behalf_uploads_are_labelled_and_unknowns_are_shown_as_unknown():
    tbs = [
        {"data_area_id": "ZZB", "owner": "zz-admin@example.com", "uploaded_on_behalf": 1},
        {"data_area_id": "ZZA", "owner": "zz-lead@example.com", "uploaded_on_behalf": 1},
        {"data_area_id": "ZZC", "owner": "zz-ea@example.com", "uploaded_on_behalf": 0},
        {"data_area_id": "ZZD", "owner": "zz-old@example.com", "uploaded_on_behalf": None},
    ]
    s = _summary(run=_run(), on_behalf=tbs)
    assert s["on_behalf"]["labels"] == [
        "by zz-lead@example.com for ZZA", "by zz-admin@example.com for ZZB"]
    assert s["on_behalf"]["unknown"] == [
        "ZZD: by zz-old@example.com; on-behalf not recorded (uploaded before it was tracked)"]


def test_an_on_behalf_row_without_the_flag_is_refused():
    try:
        _summary(run=_run(), on_behalf=[{"data_area_id": "ZZA", "owner": "zz@example.com"}])
    except KeyError:
        return
    raise AssertionError("a row with no uploaded_on_behalf key was read as not on behalf")


def test_exceptions_covers_and_previous_periods_are_listed():
    exceptions = [
        {"data_area_id": "ZZB", "reason": "Dormant", "declared_by": "zz-lead@example.com", "docstatus": 1},
        {"data_area_id": "ZZA", "reason": "Quarterly", "declared_by": "zz-lead@example.com", "docstatus": 1},
    ]
    previous = [_state(2025, 8, "Closed", "Signed Off"), _state(2025, 7, "Closed", "Acknowledged")]
    s = _summary(run=_run(), exceptions=exceptions, covers=["ZZA: covers P08–P09"], previous=previous)
    assert s["exceptions"] == [
        {"entity": "ZZA", "reason": "Quarterly", "declared_by": "zz-lead@example.com"},
        {"entity": "ZZB", "reason": "Dormant", "declared_by": "zz-lead@example.com"},
    ]
    assert s["covers"] == ["ZZA: covers P08–P09"]
    assert s["previous"] == [
        {"code": "P07", "status": "Closed", "signoff": "Acknowledged"},
        {"code": "P08", "status": "Closed", "signoff": "Signed Off"},
    ]


def test_empty_sections_are_empty_lists():
    s = _summary(run=_run())
    assert s["on_behalf"] == {"labels": [], "unknown": []}
    assert s["exceptions"] == [] and s["covers"] == [] and s["previous"] == []
    assert s["gates"]["messages"] == []


def test_signed_states_match_the_assertion_run_controller():
    # One source of truth: the pure model mirrors assertion_run.SIGNED_STATES.
    path = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run", "assertion_run.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    found = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
             and any(getattr(t, "id", None) == "SIGNED_STATES" for t in n.targets)]
    assert found == [M.SIGNED_STATES]
