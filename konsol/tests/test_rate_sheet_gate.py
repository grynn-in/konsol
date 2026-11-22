"""The close gate reads approved Rate Sheets (konsol#592 T11).

A period's rates are complete when every pair its ledgers translate has an
Average and a Closing rate. One approved Statutory sheet holds both quotes of a
pair on one row, so a row with both quotes covers both rate types; a row with
one quote covers that type only. The sheets are the only source: Group
Exchange Rate is retired (konsol#592 T26).

group_rates runs against a stub frappe; nothing here needs a site."""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(APP_DIR, "group_rates.py")


class Refused(Exception):
    pass


def _frappe(sheets=(), actuals=("ACTUAL",), calendar=()):
    """``sheets``: (name, basis, fy, fp, docstatus, [(from, to, average, closing)]).
    ``calendar``: (fy, fp, period type) of the declared periods."""
    frappe = types.ModuleType("frappe")
    for name in ("ValidationError", "MandatoryError", "PermissionError"):
        setattr(frappe, name, type(name, (Exception,), {}))
    frappe.queries = []

    def throw(msg, exc=None, *a, **k):
        raise (exc or Refused)(msg)

    def get_all(doctype, filters=None, **k):
        assert doctype == "Rate Set"
        return list(actuals)

    def sql(query, params=(), *a, **k):
        frappe.queries.append(query)
        if "`tabRate Sheet Row`" in query:
            assert "s.docstatus = %s" in query and params[0] == 1, "a draft or a cancelled sheet is not a rate"
            assert "s.rate_basis = 'Statutory'" in query, "a Constant sheet is not a period's rate"
            _ds, fy, fp = params
            return [(n, None, f, t, "1", avg, cls) for n, basis, y, p, ds, rows in sheets
                    if ds == 1 and basis == "Statutory" and (y, p) == (fy, fp) for f, t, avg, cls in rows]
        if "`tabEPM Fiscal Year Period`" in query:
            (fy,) = params
            return [(p, t) for y, p, t in calendar if y == fy]
        raise AssertionError(f"unexpected read: {query}")

    frappe.throw, frappe.get_all = throw, get_all
    frappe.db = types.SimpleNamespace(sql=sql)
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe._ = lambda s: s
    return frappe


def _rules(frappe, **overrides):
    period_status = types.ModuleType("konsol.period_status")
    period_status.period_dates = lambda fy, fp: (None, None)
    period_status.PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})
    period_status.assert_open = lambda *a, **k: None
    saved = {n: sys.modules.get(n) for n in ("frappe", "konsol.period_status")}
    sys.modules["frappe"], sys.modules["konsol.period_status"] = frappe, period_status
    try:
        spec = importlib.util.spec_from_file_location("konsol.group_rates", RULES)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    for k, v in overrides.items():
        setattr(module, k, v)
    return module


EUR = ("EUR", "CHF")
JPY = ("JPY", "CHF")
BOTH = {(f, t, rt) for f, t in (EUR, JPY) for rt in ("Average", "Closing")}


def _sheet(rows, docstatus=1, basis="Statutory", fy=2099, fp=3, name="RS-S-2099-03"):
    return (name, basis, fy, fp, docstatus, rows)


def test_one_statutory_sheet_covers_both_quotes_of_every_pair():
    r = _rules(_frappe([_sheet([("EUR", "CHF", 0.94, 0.95), ("JPY", "CHF", 0.0065, 0.0066)])]))
    assert r._approved_keys(2099, 3) == BOTH
    assert r.missing_rates(2099, 3, pairs={EUR, JPY}) == []


def test_a_row_missing_a_quote_covers_only_the_other_type():
    r = _rules(_frappe([_sheet([("EUR", "CHF", 0.94, 0), ("JPY", "CHF", None, 0.0066)])]))
    assert r._approved_keys(2099, 3) == {("EUR", "CHF", "Average"), ("JPY", "CHF", "Closing")}
    assert r.missing_rates(2099, 3, pairs={EUR, JPY}) == [
        ("EUR", "CHF", "Closing"), ("JPY", "CHF", "Average")]


def test_a_pair_with_no_row_is_named_with_both_types():
    r = _rules(_frappe([_sheet([("EUR", "CHF", 0.94, 0.95)])]))
    assert r.missing_rates(2099, 3, pairs={EUR, JPY}) == [
        ("JPY", "CHF", "Average"), ("JPY", "CHF", "Closing")]


def test_a_draft_a_cancelled_sheet_and_another_period_cover_nothing():
    rows = [("EUR", "CHF", 0.94, 0.95)]
    for sheets in ([_sheet(rows, docstatus=0)], [_sheet(rows, docstatus=2)], [_sheet(rows, fp=4)],
                   [_sheet(rows, fy=2098)], [_sheet(rows, basis="Constant", fp=None)]):
        assert _rules(_frappe(sheets))._approved_keys(2099, 3) == set()


def test_a_sheet_is_an_actuals_rate_only():
    r = _rules(_frappe([_sheet([("EUR", "CHF", 0.94, 0.95)])]))
    assert r._approved_keys(2099, 3, rate_set="BUDGET") == set()


def test_the_share_lock_is_taken_on_demand_only():
    frappe = _frappe([_sheet([("EUR", "CHF", 0.94, 0.95)])])
    r = _rules(frappe)
    r._approved_keys(2099, 3)
    assert frappe.queries and not any("LOCK IN SHARE MODE" in q for q in frappe.queries), frappe.queries
    frappe.queries.clear()
    r._approved_keys(2099, 3, lock=True)
    sheet_reads = [q for q in frappe.queries if "`tabRate Sheet" in q]
    assert sheet_reads and all(q.rstrip().endswith("LOCK IN SHARE MODE") for q in sheet_reads), frappe.queries


def test_the_close_gate_refuses_by_name_and_passes_when_the_sheet_is_complete():
    pairs = {EUR, JPY}
    gaps = lambda fy, fp: (pairs, [], [])  # noqa: E731
    ok = _rules(_frappe([_sheet([("EUR", "CHF", 0.94, 0.95), ("JPY", "CHF", 0.0065, 0.0066)])]),
                translation_gaps=gaps, ledgers_built=lambda: True)
    ok.assert_rates_complete(2099, 3)
    frappe = _frappe([_sheet([("EUR", "CHF", 0.94, 0.95)])])
    short = _rules(frappe, translation_gaps=gaps, ledgers_built=lambda: True)
    try:
        short.assert_rates_complete(2099, 3)
    except (Refused, frappe.ValidationError) as e:
        assert "JPY → CHF Average" in str(e) and "JPY → CHF Closing" in str(e), str(e)
    else:
        raise AssertionError("a pair with no rate must refuse the close")


# -- PR #595 review 1: Closing and Adjustment periods ------------------------------

def _year(fy=2099, adjustment=None):
    """(fy, fp, type): P0 Opening, P1-P12 Regular, an Adjustment period, P14 Closing."""
    rows = [(fy, 0, "Opening")] + [(fy, p, "Regular") for p in range(1, 13)]
    if adjustment:
        rows.append((fy, adjustment, "Adjustment"))
    return rows + [(fy, 14, "Closing")]


def test_a_closing_period_is_gated_on_the_last_regular_periods_sheet():
    """rate_period_map() (konsolidat governed_rates.sql) translates a Closing
    period at the year's last Regular period, and a Statutory sheet for a
    Closing period is refused: so closing P14 reads P12's sheet."""
    rows = [("EUR", "CHF", 0.94, 0.95), ("JPY", "CHF", 0.0065, 0.0066)]
    r = _rules(_frappe([_sheet(rows, fp=12, name="RS-S-2099-12")], calendar=_year(adjustment=13)))
    assert r._approved_keys(2099, 14) == BOTH
    assert r.missing_rates(2099, 14, pairs={EUR, JPY}) == []


def test_a_closing_period_with_no_sheet_at_the_last_regular_period_is_missing():
    r = _rules(_frappe([_sheet([("EUR", "CHF", 0.94, 0.95)], fp=11, name="RS-S-2099-11")],
                       calendar=_year()))
    assert r._approved_keys(2099, 14) == set()


def test_an_adjustment_period_is_gated_on_its_own_sheet():
    """An Adjustment period maps to itself: P12's sheet does not cover P13."""
    rows = [("EUR", "CHF", 0.94, 0.95)]
    r = _rules(_frappe([_sheet(rows, fp=12, name="RS-S-2099-12")], calendar=_year(adjustment=13)))
    assert r._approved_keys(2099, 13) == set()
    r = _rules(_frappe([_sheet(rows, fp=13, name="RS-S-2099-13")], calendar=_year(adjustment=13)))
    assert r._approved_keys(2099, 13) == {("EUR", "CHF", "Average"), ("EUR", "CHF", "Closing")}


def test_a_closing_period_in_a_year_with_no_regular_period_reads_itself():
    rows = [("EUR", "CHF", 0.94, 0.95)]
    r = _rules(_frappe([_sheet(rows, fp=14, name="RS-S-2099-14")], calendar=[(2099, 14, "Closing")]))
    assert r._approved_keys(2099, 14) == {("EUR", "CHF", "Average"), ("EUR", "CHF", "Closing")}
