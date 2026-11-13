"""Bulk budget files (konsol/budget_bulk_model.py, konsol#348 M1): the header
contract. It is the trial balance contract (tb_bulk_model) plus required
`scenario` and `layer`, with no partner column (Q2-c, naming #427), and a
dim_* column only for a Dimension declared in_budget."""
import ast
import os
from decimal import Decimal

from konsol import budget_bulk_model as B
from konsol import tb_bulk_model as T

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HEADER = ["scenario", "layer", "data_area_id", "fiscal_year", "fiscal_period",
          "main_account", "debit", "credit", "currency"]


def _raises(fn, *args):
    try:
        fn(*args)
    except ValueError as e:
        return str(e)
    raise AssertionError("expected ValueError")


def _dim(name, in_budget=1, in_trial_balance=0, status="Published"):
    return {"dimension_name": name, "status": status,
            "in_budget": in_budget, "in_trial_balance": in_trial_balance}


def test_a_clean_file_splits_into_budget_entries_in_file_order():
    table = [
        HEADER,
        ["BUD", "base", "E1", "2026", "1", "4000", "0", "100", "EUR"],
        ["BUD", "base", "E1", "2026", "1", "6000", "100", "0", "EUR"],
        ["BUD", "base", "E1", "2026", "2", "4000", "0", "50", "EUR"],
        ["BUD", "mgmt", "E1", "2026", "1", "4000", "0", "7", "EUR"],
    ]
    groups = B.split_table(table, ())
    assert list(groups) == [("BUD", 2026, "E1", "base", 1), ("BUD", 2026, "E1", "base", 2),
                            ("BUD", 2026, "E1", "mgmt", 1)]
    key = next(iter(groups))
    assert (key.scenario, key.fiscal_year, key.data_area_id, key.layer, key.fiscal_period) == \
        ("BUD", 2026, "E1", "base", 1)
    first = groups[key][0]
    assert first["main_account"] == "4000"
    assert str(first["debit"]) == "0" and str(first["credit"]) == "100"
    assert first["currency"] == "EUR"
    assert first["amount_basis"] == ""
    assert first["line"] == 2
    assert "partner_data_area_id" not in first


def test_missing_layer_is_refused_by_name():
    table = [[h for h in HEADER if h != "layer"],
             ["BUD", "E1", "2026", "1", "4000", "0", "100", "EUR"]]
    msg = _raises(B.split_table, table, ())
    assert "Missing column(s) layer" in msg
    assert B.HEADER_HELP in msg


def test_missing_scenario_is_refused_by_name():
    table = [[h for h in HEADER if h != "scenario"],
             ["base", "E1", "2026", "1", "4000", "0", "100", "EUR"]]
    assert "Missing column(s) scenario" in _raises(B.split_table, table, ())


def test_debit_and_credit_are_both_required():
    table = [[h for h in HEADER if h != "credit"],
             ["BUD", "base", "E1", "2026", "1", "4000", "0", "EUR"]]
    assert "Missing column(s) credit" in _raises(B.split_table, table, ())


def test_missing_currency_says_what_to_add():
    table = [[h for h in HEADER if h != "currency"],
             ["BUD", "base", "E1", "2026", "1", "4000", "0", "100"]]
    msg = _raises(B.split_table, table, ())
    assert "Missing column(s) currency" in msg and "Functional Currency" in msg


def test_a_partner_header_is_refused_naming_427():
    for header in ("partner_data_area_id", "partner", "counterparty"):
        table = [HEADER + [header], ["BUD", "base", "E1", "2026", "1", "4000", "0", "100", "EUR", "E2"]]
        msg = _raises(B.split_table, table, ())
        assert "#427" in msg, header
        assert "partner_data_area_id" in msg, header
        # It is refused for what it is, not lumped in with the unknown headers.
        assert "Unrecognised" not in msg, header


def test_an_unknown_header_is_refused_by_name():
    table = [HEADER + ["cost_centre"], ["BUD", "base", "E1", "2026", "1", "4000", "0", "100", "EUR", "CC1"]]
    msg = _raises(B.split_table, table, ())
    assert "Unrecognised column(s) cost_centre" in msg
    assert B.HEADER_HELP in msg


def test_the_tb_aliases_and_header_forgiveness_apply():
    table = [["Scenario", "LAYER", "Entity", "Year", "Period", "Account", "Debit", "Credit", "Currency",
              "Amount Basis"],
             ["BUD", "base", "E1", "2026", "1", "4000", "0", "100", "EUR", "period movement"]]
    groups = B.split_table(table, ())
    rows = groups[("BUD", 2026, "E1", "base", 1)]
    assert rows[0]["main_account"] == "4000"
    assert rows[0]["amount_basis"] == "Period movement"


def test_amount_basis_is_optional_and_one_budget_entry_holds_one_basis():
    table = [HEADER + ["amount_basis"],
             ["BUD", "base", "E1", "2026", "1", "4000", "0", "100", "EUR", "Period movement"],
             ["BUD", "base", "E1", "2026", "1", "6000", "100", "0", "EUR", "Year-to-date movement"]]
    msg = _raises(B.split_table, table, ())
    assert "Line 3" in msg and "line 2" in msg and "one amount basis" in msg
    # Other layers are other budget entries, so they may differ.
    table[2][1] = "mgmt"
    assert len(B.split_table(table, ())) == 2


def test_a_blank_scenario_or_layer_is_refused_on_its_line():
    table = [HEADER,
             ["", "base", "E1", "2026", "1", "4000", "0", "100", "EUR"],
             ["BUD", "", "E1", "2026", "1", "4000", "0", "100", "EUR"]]
    msg = _raises(B.split_table, table, ())
    assert "Line 2: scenario is blank" in msg
    assert "Line 3: layer is blank" in msg


def test_a_dimension_declared_in_budget_is_carried():
    table = [HEADER + ["dim_project"], ["BUD", "base", "E1", "2026", "1", "4000", "0", "100", "EUR", "P1"]]
    groups = B.split_table(table, [_dim("dim_project")])
    assert next(iter(groups.values()))[0]["dim_project"] == "P1"


def test_a_dimension_on_the_trial_balance_but_not_in_budget_is_refused():
    table = [HEADER + ["dim_project"], ["BUD", "base", "E1", "2026", "1", "4000", "0", "100", "EUR", "P1"]]
    msg = _raises(B.split_table, table, [_dim("dim_project", in_budget=0, in_trial_balance=1)])
    assert "dim_project" in msg and "in_budget" in msg
    assert "in_trial_balance" not in msg


def test_an_undeclared_dimension_is_refused():
    table = [HEADER + ["dim_region"], ["BUD", "base", "E1", "2026", "1", "4000", "0", "100", "EUR", "R1"]]
    msg = _raises(B.split_table, table, [_dim("dim_project")])
    assert "dim_region" in msg and "create the Dimension" in msg


def test_the_tb_contract_is_imported_not_copied():
    """The TB's aliases, cell readers and basis rules are the budget's too: a
    copy would drift. budget_bulk_model defines none of them itself."""
    assert B.ALIASES is T.ALIASES
    assert B.table_from_csv is T.table_from_csv
    assert B.group_basis is T.group_basis
    assert B.resolve_basis is T.resolve_basis
    with open(os.path.join(APP_DIR, "budget_bulk_model.py")) as f:
        tree = ast.parse(f.read())
    defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert not defined & {"cell", "_header_name", "_amount", "_whole", "table_from_csv",
                          "group_basis", "resolve_basis"}


def test_the_model_is_pure():
    with open(os.path.join(APP_DIR, "budget_bulk_model.py")) as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert "frappe" not in imported


def test_a_repeated_column_is_refused_by_name_with_its_count():
    """Two layer (or scenario, or fiscal_period) columns used to read the first
    and drop the second without a word (konsol#348 M2). The rule is the TB's
    shared one, so case and aliases count as the same column."""
    table = [HEADER + ["Layer"], ["BUD", "base", "E1", "2026", "1", "4000", "0", "100", "EUR", "mgmt"]]
    msg = _raises(B.split_table, table, ())
    assert "Two layer columns" in msg and "keep one" in msg, msg
    table = [HEADER + ["SCENARIO", "scenario", "period"],
             ["BUD", "base", "E1", "2026", "1", "4000", "0", "100", "EUR", "FC", "FC2", "2"]]
    msg = _raises(B.split_table, table, ())
    assert "Three scenario columns" in msg, msg
    assert "Two fiscal_period columns" in msg, msg


# --- M2: the per-group check (BUD-10-1, Q4-b, Q8-a) ----------------------------

KEY = B.BudgetKey("BUD", 2026, "E1", "base", 3)
ROWS = [{"main_account": "4000", "debit": Decimal("0"), "credit": Decimal("100"), "currency": "EUR", "line": 2}]
CHART = {"4000": {"is_group": 0, "is_posting": 1}, "4": {"is_group": 1, "is_posting": 0},
         "4900": {"is_group": 0, "is_posting": 0}}
REGULAR = {"code": "P03", "type": "Regular", "status": "Open"}
BUDGET = {"scenario_type": "budget"}
LAYERS = ("base", "challenge")


def _check(key=KEY, period=REGULAR, layers=LAYERS, scenario=BUDGET, rows=ROWS, chart=CHART,
           functional_currency="EUR", minor_unit=2, form_basis="Period movement"):
    return B.check_group(key, rows, period=period, layers=layers, scenario=scenario, chart=chart,
                         functional_currency=functional_currency, minor_unit=minor_unit,
                         form_basis=form_basis)


def test_a_declared_regular_period_layer_and_budget_scenario_pass():
    item = _check()
    assert item["ok"] and item["errors"] == []
    assert (item["scenario"], item["fiscal_year"], item["entity"], item["layer"], item["fiscal_period"]) == \
        ("BUD", 2026, "E1", "base", 3)
    assert item["rows"] == 1


def test_a_closed_or_locked_regular_period_is_accepted():
    """Q4-b: the Cycle freezes a plan, not the period's close status."""
    for status in ("Closed", "Locked"):
        assert _check(period=dict(REGULAR, status=status))["ok"], status


def test_an_undeclared_period_is_refused_by_name():
    item = _check(period=None)
    assert not item["ok"]
    assert item["errors"] == ["FY2026 P03 is not declared: a budget goes into the declared Regular "
                              "periods of its year"]


def test_opening_closing_and_adjustment_periods_are_refused_by_name():
    for kind in ("Opening", "Closing", "Adjustment"):
        item = _check(period={"code": "X", "type": kind, "status": "Open"})
        assert not item["ok"], kind
        assert len(item["errors"]) == 1, item
        assert f"FY2026 P03 is an {kind} period" in item["errors"][0] or \
            f"FY2026 P03 is a {kind} period" in item["errors"][0], item
        assert "Regular" in item["errors"][0]


def test_an_undeclared_layer_is_refused_naming_the_declared_ones():
    item = _check(key=KEY._replace(layer="board"))
    assert not item["ok"]
    assert item["errors"] == ["Layer board is not declared. Declared layers: base, challenge. "
                              "Use one of them, or create board as a Budget Layer"]


def test_a_site_with_no_layer_is_told_the_fix():
    item = _check(layers=())
    assert not item["ok"]
    assert item["errors"] == ["Layer base is not declared: this site has no Budget Layer yet. "
                              "Create base (or the layers you plan in) as a Budget Layer first"]


def test_an_undeclared_scenario_is_refused_by_name():
    item = _check(scenario=None)
    assert item["errors"] == ["Scenario BUD is not declared: create it as a Scenario first"]


def test_an_actual_scenario_is_refused_by_name():
    item = _check(scenario={"scenario_type": "actual"})
    assert item["errors"] == ["Scenario BUD is an actual scenario: actuals come from the trial balance, "
                              "not a budget file. Use a budget, forecast or other plan scenario"]


def test_every_problem_of_a_group_is_reported_together():
    item = _check(key=KEY._replace(layer="board"), period=None, scenario=None)
    assert len(item["errors"]) == 3


def test_the_check_takes_its_facts_without_defaults():
    import inspect
    params = inspect.signature(B.check_group).parameters
    for name in ("period", "layers", "scenario", "chart", "functional_currency", "minor_unit", "form_basis"):
        assert params[name].default is inspect.Parameter.empty, name


# --- M3: currency, decimals, accounts, negatives, basis (Q15-c); no balance rule ------------

def _row(account="4000", debit="0", credit="100", currency="EUR", line=2, **more):
    return {"main_account": account, "debit": Decimal(debit), "credit": Decimal(credit),
            "currency": currency, "line": line, **more}


def test_an_unbalanced_group_is_accepted():
    """A budget need not balance: no balance rule."""
    item = _check(rows=[_row(credit="100"), _row(account="4000", debit="30", credit="0", line=3)])
    assert item["ok"], item["errors"]
    item = _check(rows=[_row(credit="100")])
    assert item["ok"], item["errors"]


def test_a_wrong_currency_is_refused_naming_both():
    item = _check(rows=[_row(currency="USD")])
    assert not item["ok"]
    assert len(item["errors"]) == 1
    assert "USD" in item["errors"][0] and "EUR" in item["errors"][0] and "E1" in item["errors"][0]


def test_an_entity_without_a_functional_currency_is_refused_by_name():
    for missing in (None, ""):
        item = _check(functional_currency=missing)
        assert not item["ok"]
        assert any("E1 has no Functional Currency" in e for e in item["errors"]), item["errors"]


def test_a_blank_currency_is_refused():
    item = _check(rows=[_row(currency="")])
    assert any("currency is blank" in e for e in item["errors"]), item["errors"]


def test_currency_case_is_forgiven():
    assert _check(rows=[_row(currency=" eur ")])["ok"]


def test_more_decimals_than_the_minor_unit_is_refused_naming_the_line():
    item = _check(rows=[_row(credit="100.005", line=7)])
    assert item["errors"] == ["Line 7: credit 100.005 has 3 decimal places; EUR has 2."]
    assert _check(rows=[_row(credit="100.50")])["ok"]


def test_a_missing_minor_unit_is_refused_not_defaulted():
    item = _check(minor_unit=None)
    assert not item["ok"]
    assert any("Minor Unit" in e for e in item["errors"]), item["errors"]


def test_a_wrong_currency_does_not_also_judge_decimals():
    item = _check(rows=[_row(currency="USD", credit="100.005")])
    assert len(item["errors"]) == 1


def test_an_unknown_heading_and_closed_account_are_refused():
    rows = [_row(account="9999", line=2), _row(account="4", line=3), _row(account="4900", line=4)]
    errors = _check(rows=rows)["errors"]
    assert any("Line 2" in e and "9999" in e and "not in the group chart" in e for e in errors), errors
    assert any("Line 3" in e and "heading" in e for e in errors), errors
    assert any("Line 4" in e and "not open for posting" in e for e in errors), errors


def test_a_site_with_no_chart_is_told_the_fix():
    item = _check(chart={})
    assert not item["ok"]
    assert any("No group chart" in e for e in item["errors"]), item["errors"]


def test_a_negative_amount_is_refused_as_on_the_trial_balance():
    item = _check(rows=[_row(credit="-100", line=5)])
    assert not item["ok"]
    assert any("Line 5" in e and "Negative credit" in e for e in item["errors"]), item["errors"]


def test_movement_and_year_to_date_are_accepted_from_the_upload_or_the_column():
    for basis in ("Period movement", "Year-to-date movement"):
        assert _check(form_basis=basis)["ok"], basis
        assert _check(form_basis=None, rows=[_row(amount_basis=basis)])["ok"], basis
        assert _check(form_basis="", rows=[_row(amount_basis=basis.upper())])["ok"], basis


def test_a_period_end_balance_is_refused_by_name_from_either_source():
    for item in (_check(form_basis="Period-end balance"),
                 _check(form_basis=None, rows=[_row(amount_basis="Period-end balance")])):
        assert not item["ok"]
        assert len(item["errors"]) == 1
        message = item["errors"][0]
        assert "Period-end balance" in message
        assert "not supported yet" in message and "opening" in message


def test_a_missing_basis_is_refused_never_defaulted():
    for form in (None, ""):
        item = _check(form_basis=form)
        assert not item["ok"]
        assert any("Amount Basis is required" in e for e in item["errors"]), item["errors"]


def test_an_unrecognised_basis_is_refused():
    item = _check(form_basis="Quarterly")
    assert not item["ok"]
    assert any('"Quarterly" is not one of' in e for e in item["errors"]), item["errors"]


def test_a_column_that_disagrees_with_the_upload_field_is_refused():
    item = _check(form_basis="Period movement", rows=[_row(amount_basis="Year-to-date movement")])
    assert not item["ok"]
    assert any("Year-to-date movement" in e and "Period movement" in e for e in item["errors"]), item["errors"]
    assert _check(form_basis="period movement", rows=[_row(amount_basis="Period movement")])["ok"]
