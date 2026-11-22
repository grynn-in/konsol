"""Copy rates from previous period, the pure rules: konsol/rate_copy_model.py
(konsol#584, decided by Deepak Pai on 22 Nov 2026).

Loaded by path; the module imports nothing from frappe or konsol.

The previous period comes from the declared fiscal calendar, never from month
arithmetic: the latest Regular period before the target, in the target's own
year or else in the year before. Opening, Closing and Adjustment periods are
skipped. What is copied is every APPROVED Closing and Average rate of the
chosen Rate Set in that period, except a key the target already has a draft
or an approved rate for.
"""
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "rcm_under_test", os.path.join(APP_DIR, "rate_copy_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _year(regular=12, opening=True, closing=True, adjustments=()):
    """A declared year: OPN (0), P1..P<regular>, then Adjustment and CLS rows."""
    rows = [{"period": 0, "type": "Opening"}] if opening else []
    rows += [{"period": p, "type": "Regular"} for p in range(1, regular + 1)]
    rows += [{"period": p, "type": "Adjustment"} for p in adjustments]
    if closing:
        rows.append({"period": max([regular, *adjustments]) + 1, "type": "Closing"})
    return rows


# -- the previous regular period --------------------------------------------------

def test_the_previous_period_inside_the_year():
    cal = {2025: _year()}
    assert M.previous_regular_period(2025, 7, cal) == ((2025, 6), None)
    assert M.previous_regular_period(2025, 2, cal) == ((2025, 1), None)


def test_p1_takes_the_previous_years_last_regular_period():
    cal = {2025: _year(), 2024: _year()}
    assert M.previous_regular_period(2025, 1, cal) == ((2024, 12), None)


def test_the_previous_years_own_calendar_decides_its_last_regular_period():
    """A 13-period year before a 12-period one: the last Regular period is the
    one that year declares, not 12."""
    cal = {2025: _year(), 2024: _year(regular=13)}
    assert M.previous_regular_period(2025, 1, cal) == ((2024, 13), None)


def test_opening_closing_and_adjustment_periods_are_skipped():
    cal = {2025: _year(adjustments=(13, 14)), 2024: _year(adjustments=(13,))}
    # P1 skips this year's Opening (0) and last year's Closing and Adjustment
    assert M.previous_regular_period(2025, 1, cal) == ((2024, 12), None)
    # an Opening target skips back to the year before as well
    assert M.previous_regular_period(2025, 0, cal) == ((2024, 12), None)
    # a Closing or Adjustment target copies from the year's last Regular period
    assert M.previous_regular_period(2025, 15, cal) == ((2025, 12), None)
    assert M.previous_regular_period(2025, 14, cal) == ((2025, 12), None)


def test_an_undeclared_previous_year_is_refused_by_name():
    prev, why = M.previous_regular_period(2025, 1, {2025: _year()})
    assert prev is None
    assert "FY2024 is not declared" in why and "FY2025 P1" in why, why


def test_a_previous_year_with_no_regular_period_is_refused_by_name():
    cal = {2025: _year(), 2024: [{"period": 0, "type": "Opening"}]}
    prev, why = M.previous_regular_period(2025, 1, cal)
    assert prev is None and "FY2024 declares no Regular period" in why, why
    prev, why = M.previous_regular_period(2025, 1, {2025: _year(), 2024: []})
    assert prev is None and "FY2024 declares no Regular period" in why, why


def test_an_undeclared_target_is_refused_by_name():
    prev, why = M.previous_regular_period(2025, 3, {})
    assert prev is None and "FY2025 is not declared" in why, why
    prev, why = M.previous_regular_period(2025, 20, {2025: _year()})
    assert prev is None and "FY2025 has no period 20" in why, why


def test_a_previous_year_is_never_skipped_over():
    """FY2023 is declared but FY2024 is not: the copy never jumps the gap."""
    prev, why = M.previous_regular_period(2025, 1, {2025: _year(), 2023: _year()})
    assert prev is None and "FY2024 is not declared" in why


# -- what is copied ------------------------------------------------------------------

def _rate(name, f="EUR", t="CHF", rt="Closing", docstatus=1, rate_set="ACTUAL", quote=0.9478, per="1"):
    return {"name": name, "from_currency": f, "to_currency": t, "rate_type": rt, "docstatus": docstatus,
            "rate_set": rate_set, "quote": quote, "quoted_per": per}


def test_every_approved_closing_and_average_rate_is_drafted():
    sources = [_rate("A1"), _rate("A2", rt="Average"), _rate("A3", f="JPY", quote=0.6, per="100")]
    drafts, skipped = M.plan_copy(sources, taken=set(), rate_set="ACTUAL")
    assert [d["name"] for d in drafts] == ["A1", "A2", "A3"] and skipped == []


def test_only_approved_sources_are_copied():
    sources = [_rate("DRAFT", docstatus=0), _rate("CANCELLED", docstatus=2),
               _rate("OK", rt="Average", docstatus=1)]
    drafts, skipped = M.plan_copy(sources, taken=set(), rate_set="ACTUAL")
    assert [d["name"] for d in drafts] == ["OK"] and skipped == []


def test_another_sets_rates_are_never_copied():
    sources = [_rate("BUD", rate_set="BUDGET-2025"), _rate("ACT", rate_set="ACTUAL")]
    drafts, _ = M.plan_copy(sources, taken=set(), rate_set="ACTUAL")
    assert [d["name"] for d in drafts] == ["ACT"]
    drafts, _ = M.plan_copy(sources, taken=set(), rate_set="BUDGET-2025")
    assert [d["name"] for d in drafts] == ["BUD"]


def test_a_key_with_a_draft_or_an_approved_rate_is_skipped_with_its_reason():
    sources = [_rate("A1"), _rate("A2", rt="Average"), _rate("A3", f="GBP")]
    taken = {("EUR", "CHF", "Closing"), ("GBP", "CHF", "Closing")}
    drafts, skipped = M.plan_copy(sources, taken=taken, rate_set="ACTUAL")
    assert [d["name"] for d in drafts] == ["A2"]
    assert skipped == [
        "EUR → CHF Closing: the target period already has a draft or an approved rate for this key",
        "GBP → CHF Closing: the target period already has a draft or an approved rate for this key",
    ], skipped


def test_a_rate_type_outside_closing_and_average_is_not_copied():
    drafts, _ = M.plan_copy([_rate("H", rt="Historical")], taken=set(), rate_set="ACTUAL")
    assert drafts == []


def test_one_draft_per_key_even_if_the_source_period_holds_two():
    """One approved rate per key is the controller's rule; the plan never
    drafts a key twice even if it were broken."""
    drafts, skipped = M.plan_copy([_rate("B"), _rate("A")], taken=set(), rate_set="ACTUAL")
    assert [d["name"] for d in drafts] == ["A"]
    assert skipped == ["EUR → CHF Closing: B is a second approved rate for this key in the source period"]


def test_the_note_names_the_rate_it_was_copied_from():
    note = M.copy_note(_rate("GER-2025-06-EUR-CHF-Closing-ACTUAL-01"), (2025, 6), "0.9478 CHF per EUR")
    assert "GER-2025-06-EUR-CHF-Closing-ACTUAL-01" in note and "FY2025 P6" in note
    assert "0.9478 CHF per EUR" in note and "Rate Set ACTUAL" in note
