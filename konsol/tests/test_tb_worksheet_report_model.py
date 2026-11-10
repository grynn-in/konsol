"""Consolidation worksheet Desk layout, pure: konsol/close/tb_worksheet_report_model.py
(konsol#494 row 5). Fixtures come from the real tb_worksheet_model.worksheet,
loaded by path; no frappe."""
import importlib.util
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, label):
    spec = importlib.util.spec_from_file_location(
        label, os.path.join(APP_DIR, "close", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WM = _load("tb_worksheet_model", "tb_worksheet_model_for_report")
M = _load("tb_worksheet_report_model", "tb_worksheet_report_model_under_test")

ACCOUNTS = {
    "4000": {"account_name": "Sales", "statement_section": "Profit and Loss", "is_group": 0, "lft": 1},
    "1000": {"account_name": "Cash", "statement_section": "Balance Sheet", "is_group": 0, "lft": 2},
    "3100": {"account_name": "Translation reserve", "statement_section": "Balance Sheet",
             "is_group": 0, "lft": 3},
}
PERIODS = [{"fiscal_year": 2025, "fiscal_period": p, "period_type": "Regular"} for p in range(1, 13)]
KEY = (2025, 3)


def _row(layer, area, account, amount):
    return {"adjustment_type": layer, "data_area_id": area, "main_account": account,
            "fiscal_year": 2025, "fiscal_period": 3, "amount": amount, "null_rows": 0}


def sheet(entities=("A-1", "A_1"), extra=(), shown=None, allowed=None, minor_unit=2):
    rows = [_row("entity", e, "4000", -10.0049 * (i + 1)) for i, e in enumerate(entities)]
    rows += [_row("entity", e, "1000", 10.0049 * (i + 1)) for i, e in enumerate(entities)]
    rows += [_row("ic_elimination", "", "4000", 3.3333), _row("ic_elimination", "", "1000", -3.3333)]
    rows += list(extra)
    return WM.to_ids(WM.worksheet(rows, ACCOUNTS, PERIODS, KEY, {"cta_account": "3100"},
                                  minor_unit, shown, allowed))


def cols(ws, currency="USD", minor_unit=2, dimension=None):
    return M.columns(ws, currency, minor_unit, dimension)


def test_fieldnames_are_unique_and_frappe_safe():
    ws = sheet(extra=[_row("topside", "A-1", "1000", 1.0), _row("zz_new_type", "", "1000", 1.0),
                      _row("entity", "topside", "1000", 1.0), _row("entity", "a-1", "1000", 1.0)])
    names = [c["fieldname"] for c in cols(ws)]
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", n) for n in names)


def test_entity_codes_a_dash_1_and_a_underscore_1_differ():
    ws = sheet()
    fm = M.fieldnames(ws)
    assert fm["entity:A-1"] != fm["entity:A_1"]
    assert fm["entity:A-1"].startswith("e_") and fm["entity:A_1"].startswith("e_")


def test_entity_named_like_a_layer_or_bucket_keeps_its_own_column():
    ws = sheet(extra=[_row("entity", "ic_elimination", "1000", 1.0),
                      _row("entity", "consolidated", "1000", 1.0),
                      _row("entity", "rounding", "1000", 1.0)])
    names = [c["fieldname"] for c in cols(ws)]
    assert len(names) == len(set(names))
    fm = M.fieldnames(ws)
    assert fm["entity:consolidated"] != fm["consolidated"]


def test_fieldnames_are_deterministic_and_fixed_for_non_entities():
    ws = sheet(extra=[_row("topside", "A-1", "1000", 1.0),
                      _row("entity", "ic_elimination", "1000", 1.0)])
    fm = M.fieldnames(ws)
    assert fm == M.fieldnames(ws)
    assert fm["consolidated"] == "consolidated"
    assert fm["layer:ic_elimination"] != fm["entity:ic_elimination"]


def test_column_order_and_leading_columns():
    ws = sheet()
    c = cols(ws)
    assert [x["fieldname"] for x in c[:2]] == ["account", "name"]
    assert c[-2]["fieldname"] == "consolidated" and c[-1]["fieldname"] == "currency"
    keys = [x["key"] for x in ws["columns"]]
    amount_names = [M.fieldnames(ws)[k] for k in keys]
    assert [x["fieldname"] for x in c if x["fieldname"] in amount_names] == amount_names


def test_amount_columns_are_currency_with_minor_unit_precision():
    ws = sheet()
    amount = [x for x in cols(ws, minor_unit=3) if x.get("fieldtype") == "Currency"]
    assert len(amount) == len(ws["columns"])
    assert all(x["options"] == "currency" and x["precision"] == 3 for x in amount)


def test_no_minor_unit_sets_no_precision():
    ws = sheet()
    amount = [x for x in cols(ws, minor_unit=None) if x.get("fieldtype") == "Currency"]
    assert amount and all("precision" not in x for x in amount)


def test_labels_carry_the_currency():
    ws = sheet()
    amount = [x for x in cols(ws, currency="EUR") if x.get("fieldtype") == "Currency"]
    assert all("EUR" in x["label"] for x in amount)
    assert any(x["label"].startswith("A-1") for x in amount)
    assert any(x["label"].startswith("Consolidated") for x in amount)


def test_dimension_column_after_name_only_when_given():
    ws = sheet()
    assert "dimension" not in [x["fieldname"] for x in cols(ws)]
    c = cols(ws, dimension="Cost centre")
    assert [x["fieldname"] for x in c[:3]] == ["account", "name", "dimension"]
    assert c[2]["label"] == "Cost centre"


def test_rows_one_per_line_then_bold_total():
    ws = sheet()
    out = M.rows(ws, "USD")
    assert len(out) == len(ws["lines"]) + 1
    assert out[-1]["account"] == "Total" and out[-1]["bold"] == 1
    assert not any(r.get("bold") for r in out[:-1])
    assert all(r["currency"] == "USD" for r in out)


def test_rows_carry_every_cell_and_consolidated_under_the_fieldnames():
    ws = sheet()
    fm = M.fieldnames(ws)
    out = M.rows(ws, "USD")
    for line, row in zip(ws["lines"], out):
        assert row["account"] == line["account"] and row["name"] == line["name"]
        assert row["consolidated"] == line["consolidated"]
        for key in (c["key"] for c in ws["columns"] if c["key"] != "consolidated"):
            assert row[fm[key]] == line["cells"][key]
    total = out[-1]
    assert total["consolidated"] == ws["total"]["consolidated"]


def test_rows_cross_foot_on_what_is_displayed():
    ws = sheet()
    fm = M.fieldnames(ws)
    from decimal import Decimal
    for row in M.rows(ws, "USD"):
        parts = sum((Decimal(str(row[fm[c["key"]]])) for c in ws["columns"]
                     if c["key"] != "consolidated"), Decimal("0"))
        assert parts == Decimal(str(row["consolidated"]))


def test_rows_dimension_value_filled_when_given():
    ws = sheet()
    ws["lines"][0]["dimension_value"] = "CC1"
    out = M.rows(ws, "USD", dimension="Cost centre")
    assert out[0]["dimension"] == "CC1"


PAYLOAD = {"consolidation_group": "GRP", "period": {"fiscal_year": 2025, "fiscal_period": 3},
           "reporting_currency": "USD"}


def test_message_names_period_group_currency_sign_and_window():
    msg = M.message(sheet(), PAYLOAD)
    assert "FY2025 P03" in msg and "GRP" in msg and "USD" in msg
    assert "net debit" in msg.lower()
    assert "year to date" in msg.lower() and "cumulative" in msg.lower()


def test_message_says_nothing_about_totals_when_all_zero():
    ws = sheet()
    for k in ws["total"]["cells"]:
        ws["total"]["cells"][k] = 0.0
    ws["total"]["consolidated"] = 0.0
    ws["total_rounded_exact"] = {k: 0.0 for k in ws["total_rounded_exact"]}
    assert "not zero" not in M.message(ws, PAYLOAD).lower()


def test_message_lists_non_zero_column_totals():
    ws = sheet(extra=[_row("topside", "A-1", "1000", 5.5)])
    msg = M.message(ws, PAYLOAD)
    assert "not zero" in msg.lower()
    assert "5.5" in msg


def test_message_counts_outside_scope_without_naming():
    ws = sheet(entities=("A-1", "SECRET-X", "SECRET-Y"), allowed={"A-1"})
    assert ws["outside_count"] == 2
    msg = M.message(ws, PAYLOAD)
    assert "2 entities outside your scope" in msg
    assert "SECRET" not in msg
    assert "SECRET" not in repr(M.columns(ws, "USD", 2, None))
    assert "SECRET" not in repr(M.rows(ws, "USD"))


def test_message_singular_and_absent_outside_scope():
    one = M.message(sheet(entities=("A-1", "SECRET-X"), allowed={"A-1"}), PAYLOAD)
    assert "1 entity outside your scope" in one
    assert "outside your scope" not in M.message(sheet(), PAYLOAD).replace("Outside your scope", "")


def test_message_is_html_escaped():
    msg = M.message(sheet(), dict(PAYLOAD, consolidation_group="<b>G</b>"))
    assert "<b>" not in msg and "&lt;b&gt;" in msg


def test_rows_read_the_dimension_value_the_real_worksheet_writes():
    # #494 row 5b: row 4's worksheet(dimension=...) puts the value on
    # line["dimension_value"]; the layout must show that, not a guessed key.
    rows = [dict(_row("entity", "A-1", "4000", -10.0), dimension_value="CC1"),
            dict(_row("entity", "A-1", "4000", -5.0), dimension_value="CC2"),
            dict(_row("ic_elimination", "", "4000", 3.0), dimension_value="")]
    ws = WM.to_ids(WM.worksheet(rows, ACCOUNTS, PERIODS, KEY, {"cta_account": "3100"},
                                2, None, None, dimension="dim_cost_center"))
    out = M.rows(ws, "USD", dimension="dim_cost_center")
    values = [r["dimension"] for r in out if r.get("account") != "Total"]
    assert values == ["CC1", "CC2", WM.NO_DIMENSION_VALUE]


def test_entity_code_with_a_colon_keeps_its_own_column():
    ws = sheet(entities=("A:1", "A-1"))
    fm = M.fieldnames(ws)
    assert "entity:A:1" in fm and fm["entity:A:1"].startswith("e_")
    assert fm["entity:A:1"] != fm["entity:A-1"]
    out = M.rows(ws, "USD")
    assert out[0][fm["entity:A:1"]] == ws["lines"][0]["cells"]["entity:A:1"]


# -- #494 row 10b: totals are judged on exact sums, not on rounded lines --------

def _exact_sheet(rows, minor_unit=2):
    return WM.to_ids(WM.worksheet(rows, ACCOUNTS, PERIODS, KEY, {"cta_account": "3100"},
                                  minor_unit, None, None))


def test_message_ignores_a_column_whose_rounded_lines_sum_to_a_cent_but_exact_is_zero():
    ws = _exact_sheet([_row("entity", "ZA", "4000", 0.004), _row("entity", "ZA", "1000", 0.004),
                       _row("entity", "ZA", "3100", -0.008)])
    assert ws["total"]["cells"]["entity:ZA"] == -0.01       # displayed total is off by a cent
    assert "not zero" not in M.message(ws, PAYLOAD).lower()


def test_message_still_lists_a_genuinely_unbalanced_column():
    ws = _exact_sheet([_row("entity", "ZA", "4000", 0.004), _row("entity", "ZA", "1000", 0.004),
                       _row("entity", "ZA", "3100", -0.008), _row("topside", "ZA", "1000", 0.05)])
    msg = M.message(ws, PAYLOAD)
    assert "not zero" in msg.lower() and "0.05" in msg


def test_message_never_lists_rounding_as_an_unbalanced_column():
    ws = _exact_sheet([_row("entity", "ZA", "4000", 0.004), _row("topside", "ZA", "4000", 0.004),
                       _row("entity", "ZA", "1000", -0.008)])
    assert ws["total"]["cells"]["rounding"] == 0.01
    msg = M.message(ws, PAYLOAD)
    assert "Rounding" not in msg and "not zero" not in msg.lower()


# -- #494 PR review V1: layout of a scoped, dimensioned worksheet ---------------

def test_layout_of_a_scoped_dimensioned_sheet_never_names_a_hidden_value():
    hidden = "ZZA-ONLY-PROJECT"
    rows = [dict(_row("entity", "ZZA", "4000", -10.0), dimension_value=hidden),
            dict(_row("entity", "ZZB", "4000", -4.0), dimension_value="CC1")]
    ws = WM.to_ids(WM.worksheet(rows, ACCOUNTS, PERIODS, KEY, {"cta_account": "3100"},
                                2, None, {"ZZB"}, dimension="dim_cost_center"))
    text = repr((M.columns(ws, "USD", 2, "dim_cost_center"),
                 M.rows(ws, "USD", dimension="dim_cost_center"), M.message(ws, PAYLOAD)))
    assert hidden not in text
    assert "ZZA" not in text


# -- #494 PR review V2: the message rounds the exact total as every cell is rounded ----------------

def test_message_rounds_an_exact_half_cent_total_half_even_to_nothing():
    ws = _exact_sheet([_row("topside", "", "1000", 0.005)])
    assert ws["total"]["cells"]["layer:topside"] == 0.0
    assert "not zero" not in M.message(ws, PAYLOAD).lower()


def test_message_prints_an_exact_0_015_total_rounded_half_even_to_0_02():
    ws = _exact_sheet([_row("topside", "", "1000", 0.015)])
    assert ws["total"]["cells"]["layer:topside"] == 0.02
    msg = M.message(ws, PAYLOAD)
    assert "0.02" in msg and "0.01" not in msg


def test_message_may_differ_from_the_total_row_by_accumulated_rounding():
    # Two 0.004 lines: the Total row sums displayed 0.00 + 0.00; the exact
    # column sum 0.008 rounds to 0.01 (by design, #494 row 10b).
    ws = _exact_sheet([_row("topside", "", "1000", 0.004), _row("topside", "", "4000", 0.004)])
    assert ws["total"]["cells"]["layer:topside"] == 0.0
    assert "0.01" in M.message(ws, PAYLOAD)
