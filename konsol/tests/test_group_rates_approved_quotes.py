"""group_rates.approved_quotes: the one reader of an actuals period's rates
(konsol#592 T19).

Returns ``{(from, to): true_rate}`` for one rate type: from the approved
Statutory Rate Sheet (Average or Closing quote of each row, divided by its
Quoted Per), plus, until T26 retires Group Exchange Rate, the approved GER
rows for pairs no sheet covers (the sheet wins).

group_rates runs against a stub frappe; nothing here needs a site."""
import importlib.util
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(APP_DIR, "group_rates.py")


class Refused(Exception):
    pass


def _frappe(sheets=(), ger=(), actuals=("ACTUAL",)):
    """``sheets``: (name, basis, fy, fp, docstatus, supersedes,
    [(from, to, quoted_per, average, closing)]). ``ger``: (from, to, rate_type,
    fy, fp, rate_set, docstatus, quote, quoted_per)."""
    frappe = types.ModuleType("frappe")
    for name in ("ValidationError", "MandatoryError", "PermissionError"):
        setattr(frappe, name, type(name, (Exception,), {}))

    def throw(msg, exc=None, *a, **k):
        raise (exc or Refused)(msg)

    def get_all(doctype, filters=None, **k):
        assert doctype == "Rate Set"
        return list(actuals)

    def sql(query, params=(), *a, **k):
        if "`tabRate Sheet Row`" in query:
            assert "s.docstatus = 1" in query and "s.rate_basis = 'Statutory'" in query
            fy, fp = params
            return [(n, sup, f, t, per, avg, cls)
                    for n, basis, y, p, ds, sup, rows in sheets
                    if ds == 1 and basis == "Statutory" and (y, p) == (fy, fp)
                    for f, t, per, avg, cls in rows]
        if "`tabGroup Exchange Rate`" in query:
            ds, fy, fp, rate_type, rate_set = params
            return [(f, t, q, per) for f, t, rt, y, p, rs, d, q, per in ger
                    if (d, y, p, rt, rs) == (ds, fy, fp, rate_type, rate_set)]
        raise AssertionError(f"unexpected read: {query}")

    frappe.throw, frappe.get_all = throw, get_all
    frappe.db = types.SimpleNamespace(sql=sql)
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe._ = lambda s: s
    return frappe


def _rules(frappe):
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
    return module


def _sheet(rows, docstatus=1, basis="Statutory", fy=2099, fp=3, name="RS-S-2099-03", supersedes=None):
    return (name, basis, fy, fp, docstatus, supersedes, rows)


def test_a_sheet_row_gives_the_true_rate_of_the_asked_quote():
    r = _rules(_frappe([_sheet([("JPY", "CHF", "100", 0.65, 0.66), ("EUR", "CHF", "1", 0.94, 0.95)])]))
    assert r.approved_quotes(2099, 3, "Closing") == {("JPY", "CHF"): pytest.approx(0.0066),
                                                      ("EUR", "CHF"): 0.95}
    assert r.approved_quotes(2099, 3, "Average") == {("JPY", "CHF"): pytest.approx(0.0065),
                                                      ("EUR", "CHF"): 0.94}


def test_a_row_without_the_asked_quote_is_left_out():
    r = _rules(_frappe([_sheet([("EUR", "CHF", "1", 0.94, 0), ("JPY", "CHF", "1", None, 0.0066)])]))
    assert r.approved_quotes(2099, 3, "Closing") == {("JPY", "CHF"): 0.0066}
    assert r.approved_quotes(2099, 3, "Average") == {("EUR", "CHF"): 0.94}


def test_a_draft_a_cancelled_sheet_another_period_and_constant_give_nothing():
    rows = [("EUR", "CHF", "1", 0.94, 0.95)]
    for sheets in ([_sheet(rows, docstatus=0)], [_sheet(rows, docstatus=2)], [_sheet(rows, fp=4)],
                   [_sheet(rows, fy=2098)], [_sheet(rows, basis="Constant", fp=None)]):
        assert _rules(_frappe(sheets)).approved_quotes(2099, 3, "Closing") == {}


def test_a_superseded_sheet_gives_way_to_its_superseder():
    old = _sheet([("EUR", "CHF", "1", 0.90, 0.91), ("JPY", "CHF", "1", 0.0064, 0.0065)])
    new = _sheet([("EUR", "CHF", "1", 0.94, 0.95)], name="RS-S-2099-03-2", supersedes="RS-S-2099-03")
    assert _rules(_frappe([old, new])).approved_quotes(2099, 3, "Closing") == {("EUR", "CHF"): 0.95}


def test_ger_rows_fill_the_pairs_no_sheet_covers_and_the_sheet_wins():
    # The GER branch goes with T26 (konsol#592).
    ger = [("JPY", "CHF", "Closing", 2099, 3, "ACTUAL", 1, 0.66, "100"),
           ("EUR", "CHF", "Closing", 2099, 3, "ACTUAL", 1, 0.80, "1"),
           ("USD", "CHF", "Closing", 2099, 3, "ACTUAL", 0, 0.9, "1"),
           ("GBP", "CHF", "Closing", 2099, 3, "BUDGET", 1, 1.1, "1")]
    r = _rules(_frappe([_sheet([("EUR", "CHF", "1", 0.94, 0.95)])], ger))
    assert r.approved_quotes(2099, 3, "Closing") == {("EUR", "CHF"): 0.95,
                                                      ("JPY", "CHF"): pytest.approx(0.0066)}
    assert _rules(_frappe([], ger)).approved_quotes(2099, 3, "Closing")[("EUR", "CHF")] == 0.80


def test_an_unknown_rate_type_is_refused_by_name():
    with pytest.raises(ValueError, match="Rate type"):
        _rules(_frappe()).approved_quotes(2099, 3, "Spot")


def test_the_gate_and_the_quotes_share_one_sheet_read():
    # A sheet covers exactly the (from, to, type) the gate counts.
    r = _rules(_frappe([_sheet([("EUR", "CHF", "1", 0.94, 0.95), ("JPY", "CHF", "1", None, 0.0066)])]))
    keys = r._approved_keys(2099, 3)
    for rt in ("Average", "Closing"):
        assert {(f, t, rt) for f, t in r.approved_quotes(2099, 3, rt)} == {k for k in keys if k[2] == rt}
