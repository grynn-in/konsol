"""konsol#354 (rescoped 5 Nov 2026 to the Desk): the Consolidated Statement
rounds to the reporting currency's ISO minor unit, never a fixed 2 places.

Chosen under Deepak Pai's standing rule of 5 Nov 2026 (product, configurable,
not opinionated); the HMD table is on the issue. The minor unit is
``ISO Currency.minor_unit`` of the group's reporting currency (konsol#330 ties
it to the shipped ISO 4217 list). The statement and drill models take it as a
required argument; an undeclared one is refused (a setup gap), never 2.

Loaded by path: the models import nothing from frappe or konsol.
"""
import importlib.util
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE = os.path.join(APP_DIR, "close")


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(CLOSE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SM = _load("statement_model_354", "statement_model.py")
DM = _load("drill_model_354", "drill_model.py")
CPM = _load("close_policy_model_354", "close_policy_model.py")
RM = _load("statement_report_model_354", "statement_report_model.py")

_KEY = (2025, 7)


def _calendar():
    rows = []
    for fy in (2024, 2025):
        rows.append({"fiscal_year": fy, "fiscal_period": 0, "period_type": "Opening"})
        rows += [{"fiscal_year": fy, "fiscal_period": p, "period_type": "Regular"} for p in range(1, 13)]
        rows.append({"fiscal_year": fy, "fiscal_period": 13, "period_type": "Closing"})
    return rows


_CALENDAR = _calendar()

_DECLARED_ROWS = {
    "3300": {"is_group": False, "status": "Published", "statement_section": SM.BS,
             "account_name": "AOCI - CTA", "parent_account": "3"},
    "3100": {"is_group": False, "status": "Published", "statement_section": SM.BS,
             "account_name": "Retained earnings", "parent_account": "3"},
}


def _declared():
    return CPM.statement_accounts("3300", "3100", _DECLARED_ROWS)


def _accounts():
    return {
        "1": {"account_name": "ASSETS", "parent_account": None, "is_group": True,
              "statement_section": SM.BS, "lft": 1, "normal_balance": "Debit"},
        "2": {"account_name": "LIABILITIES", "parent_account": None, "is_group": True,
              "statement_section": SM.BS, "lft": 5, "normal_balance": "Credit"},
        "3": {"account_name": "EQUITY", "parent_account": None, "is_group": True,
              "statement_section": SM.BS, "lft": 9, "normal_balance": "Credit"},
        "4": {"account_name": "COST OF SALES", "parent_account": None, "is_group": True,
              "statement_section": SM.PL, "lft": 13},
        "1110": {"account_name": "Cash", "parent_account": "1", "is_group": False,
                 "statement_section": SM.BS, "lft": 2},
        "2100": {"account_name": "Payables", "parent_account": "2", "is_group": False,
                 "statement_section": SM.BS, "lft": 6},
        "3200": {"account_name": "Share capital", "parent_account": "3", "is_group": False,
                 "statement_section": SM.BS, "lft": 10},
        "3300": {"account_name": "AOCI - CTA", "parent_account": "3", "is_group": False,
                 "statement_section": SM.BS, "lft": 11},
        "3100": {"account_name": "Retained earnings", "parent_account": "3", "is_group": False,
                 "statement_section": SM.BS, "lft": 12},
        "4100": {"account_name": "Net sales", "parent_account": "4", "is_group": False,
                 "statement_section": SM.PL, "lft": 14},
    }


def _row(code, amount, adjustment_type=None, data_area_id="ZZA"):
    return {"fiscal_year": _KEY[0], "fiscal_period": _KEY[1], "main_account": code,
            "data_area_id": data_area_id, "adjustment_type": adjustment_type,
            "amount": amount, "null_rows": 0}


def _bhd_rows():
    """A balanced BHD group (3 decimal places): assets 1,735.105, liabilities
    -803.701, share capital -684.902, CTA -5.071, net sales -241.431."""
    return [
        _row("1110", 1735.105, "entity"),
        _row("2100", -803.701, "entity"),
        _row("3200", -684.902, "entity"),
        _row("CTA", -5.071, "cta"),
        _row("4100", -241.431, "entity"),
    ]


def _line(result, section_index, heading):
    return next(line for line in result["sections"][section_index]["lines"] if line.get("heading") == heading)


def test_a_three_decimal_currency_keeps_its_third_decimal():
    result = SM.statement(_bhd_rows(), _accounts(), _CALENDAR, _KEY, _declared(), 3)
    assert _line(result, 1, "1")["current"] == 1735.105
    assert _line(result, 1, "2")["current"] == 803.701
    assert _line(result, 1, "3")["current"] == 931.404      # 684.902 + 5.071 + 241.431
    assert _line(result, 0, "4")["current"] == 241.431
    residual = result["sections"][1]["lines"][-1]
    assert residual["kind"] == "residual" and residual["current"] == 0.0
    assert result["minor_unit"] == 3


def test_a_zero_decimal_currency_rounds_to_whole_units():
    rows = [_row("1110", 1000.4, "entity"), _row("3200", -1000.4, "entity")]
    result = SM.statement(rows, _accounts(), _CALENDAR, _KEY, _declared(), 0)
    assert _line(result, 1, "1")["current"] == 1000.0
    assert result["minor_unit"] == 0


def test_two_places_is_unchanged_for_a_two_decimal_currency():
    result = SM.statement(_bhd_rows(), _accounts(), _CALENDAR, _KEY, _declared(), 2)
    assert _line(result, 1, "1")["current"] == 1735.10 or _line(result, 1, "1")["current"] == 1735.11


@pytest.mark.parametrize("minor_unit", [None, "", -1, True, 2.5, "2"])
def test_an_undeclared_minor_unit_is_refused_never_two(minor_unit):
    with pytest.raises(ValueError) as info:
        SM.statement(_bhd_rows(), _accounts(), _CALENDAR, _KEY, _declared(), minor_unit)
    assert str(info.value).startswith(SM.STATEMENT_CURRENCY_PRECISION_UNDECLARED)


def test_the_drill_total_matches_the_statement_at_three_places():
    rows = _bhd_rows()
    stmt = SM.statement(rows, _accounts(), _CALENDAR, _KEY, _declared(), 3)
    line = _line(stmt, 1, "1")
    keys = SM._keys_up_to(_CALENDAR, _KEY)
    drill = DM.drill(rows, [], _accounts(), "1", keys, _declared(), None, line, 3)
    assert drill["total"] == 1735.105
    assert drill["minor_unit"] == 3


def test_the_drill_refuses_an_undeclared_minor_unit():
    rows = _bhd_rows()
    line = _line(SM.statement(rows, _accounts(), _CALENDAR, _KEY, _declared(), 3), 1, "1")
    with pytest.raises(ValueError):
        DM.drill(rows, [], _accounts(), "1", SM._keys_up_to(_CALENDAR, _KEY), _declared(), None, line, None)


def test_the_desk_report_columns_carry_the_minor_unit_as_precision():
    amounts = [c for c in RM.columns(3) if c["fieldtype"] == "Currency"]
    assert amounts and all(c["precision"] == 3 for c in amounts)
    assert all("precision" not in c for c in RM.columns(None) if c["fieldtype"] == "Currency"), \
        "no statement, no precision: never a guessed 2"


def test_the_prior_year_change_rounds_at_the_minor_unit():
    this = SM.statement(_bhd_rows(), _accounts(), _CALENDAR, _KEY, _declared(), 3)
    prior_rows = [dict(r, amount=r["amount"] / 2) for r in _bhd_rows()]
    prior = SM.statement(prior_rows, _accounts(), _CALENDAR, _KEY, _declared(), 3)
    payload = {"state": "ok", "statement": this, "reporting_currency": "BHD",
               "commentary": {}, "can_comment": False, "consolidation_group": "ZZG"}
    out = RM.rows(payload, {"state": "ok", "statement": prior})
    assets = next(r for r in out if r.get("code") == "1")
    assert assets["prior_year"] == 867.553 or assets["prior_year"] == 867.552
    assert assets["prior_year_change"] == round(1735.105 - assets["prior_year"], 3)
    assert assets["prior_year_change"] != round(assets["prior_year_change"], 2)
