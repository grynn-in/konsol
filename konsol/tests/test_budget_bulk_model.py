"""Bulk budget files (konsol/budget_bulk_model.py, konsol#348 M1): the header
contract. It is the trial balance contract (tb_bulk_model) plus required
`scenario` and `layer`, with no partner column (Q2-c, naming #427), and a
dim_* column only for a Dimension declared in_budget."""
import ast
import os

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
