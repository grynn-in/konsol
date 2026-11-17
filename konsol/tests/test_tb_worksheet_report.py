"""Consolidation Worksheet report: execute() (konsol#494 row 8,
konsol/consolidation/report/consolidation_worksheet).

The group filter is resolved by Consolidated Statement's own ``_group_code``
(imported, never copied). The worksheet is ``tb_worksheet_api.get_worksheet``'s;
the layout is the REAL ``tb_worksheet_report_model`` (loaded by path), so the
cross-foot test runs end to end through the real model.
"""
import ast
import importlib.util
import json
import os
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(APP_DIR, "consolidation", "report", "consolidation_worksheet")
PATH = os.path.join(DIR, "consolidation_worksheet.py")


class Refused(Exception):
    pass


def _load(name):
    spec = importlib.util.spec_from_file_location(
        "wsrep_" + name, os.path.join(APP_DIR, "close", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WM = _load("tb_worksheet_model")
LAYOUT = _load("tb_worksheet_report_model")

ACCOUNTS = {
    "4000": {"account_name": "Sales", "statement_section": "Profit and Loss", "is_group": 0, "lft": 1},
    "1000": {"account_name": "Cash", "statement_section": "Balance Sheet", "is_group": 0, "lft": 2},
}
PERIODS = [{"fiscal_year": 2025, "fiscal_period": p, "period_type": "Regular"} for p in range(1, 13)]


def _row(layer, area, account, amount):
    return {"adjustment_type": layer, "data_area_id": area, "main_account": account,
            "fiscal_year": 2025, "fiscal_period": 3, "amount": amount, "null_rows": 0}


def _ok_payload():
    rows = [_row("entity", "ZZA", "4000", -10.0049), _row("entity", "ZZB", "4000", -20.0049),
            _row("entity", "ZZA", "1000", 10.0049), _row("entity", "ZZB", "1000", 20.0049),
            _row("ic_elimination", "", "4000", 3.3333), _row("ic_elimination", "", "1000", -3.3333),
            _row("topside", "ZZA", "1000", 1.0), _row("topside", "ZZA", "4000", -1.0)]
    ws = WM.to_ids(WM.worksheet(rows, ACCOUNTS, PERIODS, (2025, 3), {"cta_account": None},
                                2, None, None))
    return {"state": "ok", "message": None,
            "period": {"fiscal_year": 2025, "fiscal_period": 3},
            "consolidation_group": "ZZG", "reporting_currency": "USD", "minor_unit": 2,
            "dimension": None, "worksheet": ws}


def _tree():
    return ast.parse(open(PATH).read())


def test_group_code_is_imported_from_consolidated_statement_not_copied():
    tree = _tree()
    imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)
               and n.module == "konsol.consolidation.report.consolidated_statement.consolidated_statement"]
    assert any(a.name == "_group_code" for n in imports for a in n.names)
    assert not any(isinstance(n, ast.FunctionDef) and n.name == "_group_code" for n in tree.body)


def _execute_with(payload):
    fns = [n for n in _tree().body if isinstance(n, (ast.FunctionDef, ast.Assign))]
    seen = {}

    def get_worksheet(*args, **kwargs):
        seen["call"] = (args, kwargs)
        return payload

    def throw(msg, *a, **k):
        raise Refused(msg)

    class _Dict(dict):
        __getattr__ = dict.get

    def group_code(value):
        if value == "CG-MEMBER-":
            raise Refused("CG-MEMBER- is a member of ZZG, not a root consolidation group.")
        return {"CG-ZZG-": "ZZG"}.get(value, value)

    frappe = types.SimpleNamespace(_dict=_Dict, throw=throw)
    env = {"frappe": frappe, "tb_worksheet_api": types.SimpleNamespace(get_worksheet=get_worksheet),
           "tb_worksheet_report_model": LAYOUT, "_group_code": group_code}
    exec(compile(ast.Module(body=fns, type_ignores=[]), PATH, "exec"), env)
    return env["execute"], seen


_FILTERS = {"fiscal_year": 2025, "fiscal_period": 3, "consolidation_group": "CG-ZZG-"}


def test_ok_payload_gives_columns_data_and_message():
    execute, seen = _execute_with(_ok_payload())
    columns, data, message = execute(dict(_FILTERS))
    assert [c["fieldname"] for c in columns][:2] == ["account", "name"]
    assert data[-1]["account"] == "Total" and data[-1]["bold"] == 1
    assert len(data) == 3
    assert "Consolidation worksheet" in message and "USD" in message
    args, kwargs = seen["call"]
    assert "ZZG" in args + tuple(kwargs.values())


def test_every_row_cross_foots_through_the_real_layout():
    execute, _ = _execute_with(_ok_payload())
    columns, data, _ = execute(dict(_FILTERS))
    amounts = [c["fieldname"] for c in columns if c.get("fieldtype") == "Currency"]
    assert "consolidated" in amounts and len(amounts) > 3
    for row in data:
        parts = sum(row[f] for f in amounts if f != "consolidated")
        assert round(parts - row["consolidated"], 6) == 0, row


def test_entities_and_dimension_are_passed_through():
    execute, seen = _execute_with(_ok_payload())
    execute(dict(_FILTERS, entities=["ZZA"], dimension="dim_region"))
    args, kwargs = seen["call"]
    flat = list(args) + list(kwargs.values())
    assert ["ZZA"] in flat and "dim_region" in flat


@pytest.mark.parametrize("state", ["choose_group", "no_chart", "setup_gap", "not_built", "error"])
def test_a_non_ok_state_is_refused_with_its_message(state):
    execute, _ = _execute_with({"state": state, "message": "because " + state, "worksheet": None})
    with pytest.raises(Refused, match="because " + state):
        execute(dict(_FILTERS))


def test_a_member_group_is_refused():
    execute, seen = _execute_with(_ok_payload())
    with pytest.raises(Refused, match="not a root"):
        execute(dict(_FILTERS, consolidation_group="CG-MEMBER-"))
    assert "call" not in seen


def test_missing_period_is_refused():
    execute, _ = _execute_with(_ok_payload())
    with pytest.raises(Refused):
        execute({"fiscal_year": 2025})


def test_report_json_and_js():
    r = json.load(open(os.path.join(DIR, "consolidation_worksheet.json")))
    assert r["report_type"] == "Script Report" and r["is_standard"] == "Yes"
    assert r["add_total_row"] == 0 and r["ref_doctype"] == "Trial Balance Submission"
    assert {x["role"] for x in r["roles"]} == {"EPM Admin", "EPM Analyst", "EPM User"}
    assert r["name"] == r["report_name"] == "Consolidation Worksheet"
    js = open(os.path.join(DIR, "consolidation_worksheet.js")).read()
    assert "frappe.call" not in js and "MultiSelectList" in js
    assert "get_link_options" in js and "in_trial_balance" in js


# --- Basis: Actual or Budget (konsol#348 S6 KB7; S6-Q5 a) ----------------------

_BUDGET_SENTENCE = ("Approved budget for ZZG FY2025: Budget Cycle BCYC-ZZB-2025-abc12345, all layers "
                    "(base), Rate Set ZZRS (Approved Budget APB-CG-ZZG--2025-0f0f0f0f, approved by "
                    "lead@example.com on 2026-11-17).")
_BUDGET_META = {"cycle": "BCYC-ZZB-2025-abc12345", "layers": ["base"], "rate_set": "ZZRS",
                "scenario_id": "ZZB", "declaration": "APB-CG-ZZG--2025-0f0f0f0f",
                "message": _BUDGET_SENTENCE}


def _budget_payload():
    """The Budget basis: FCB rows, the plug in its own ``unbalanced`` column."""
    rows = [_row("entity", "ZZA", "4000", -100.0), _row("entity", "ZZB", "4000", -50.0),
            _row("unbalanced", "ZZA", "UNBALANCED", 100.0),
            _row("unbalanced", "ZZB", "UNBALANCED", 50.0)]
    # The plug is never a chart account (PR #558 review m2): the chart is the
    # actuals' own, and the model labels the plug's line itself.
    ws = WM.to_ids(WM.worksheet(rows, ACCOUNTS, PERIODS, (2025, 3), {"cta_account": None},
                                2, None, None))
    return dict(_ok_payload(), worksheet=ws, basis="Budget", budget=dict(_BUDGET_META))


def _kwargs(seen):
    args, kwargs = seen["call"]
    return kwargs


def test_the_basis_defaults_to_actual_and_is_passed_by_name():
    execute, seen = _execute_with(_ok_payload())
    execute(dict(_FILTERS))
    assert _kwargs(seen).get("basis") == "Actual"
    execute, seen = _execute_with(_budget_payload())
    execute(dict(_FILTERS, basis="Budget"))
    assert _kwargs(seen).get("basis") == "Budget"


def test_the_actual_message_is_unchanged_and_names_no_budget():
    execute, _ = _execute_with(dict(_ok_payload(), basis="Actual", budget=None))
    _, _, message = execute(dict(_FILTERS))
    plain_execute, _ = _execute_with(_ok_payload())
    assert message == plain_execute(dict(_FILTERS))[2]
    assert "udget" not in message


def test_an_ok_budget_shows_the_unbalanced_column_and_the_budget_sentence():
    execute, _ = _execute_with(_budget_payload())
    columns, data, message = execute(dict(_FILTERS, basis="Budget"))
    labels = [c["label"] for c in columns]
    assert any(label.startswith("Unbalanced budget") for label in labels), labels
    [plug] = [row for row in data if row["account"] == "UNBALANCED"]
    assert plug["name"] == WM.statement_model.UNBALANCED_LABEL, "never 'not in the published chart'"
    assert "Basis: Budget" in message
    assert "BCYC-ZZB-2025-abc12345" in message
    assert LAYOUT.BUDGET_LIMITS in message
    assert "budgeted movements" in message
    assert data[-1]["account"] == "Total"


@pytest.mark.parametrize("state", ["no_approved_budget", "cycle_cancelled", "inputs_changed",
                                   "layer_missing"])
def test_a_budget_state_is_the_legend_with_no_columns_never_an_empty_grid(state):
    """BUD-4-2 (Deepak Pai, 7 Nov 2026): a visible state, never a blank column."""
    payload = dict(_ok_payload(), state=state, message="No approved budget because " + state,
                   worksheet=None, basis="Budget", budget=dict(_BUDGET_META, message="x"))
    execute, _ = _execute_with(payload)
    columns, data, message = execute(dict(_FILTERS, basis="Budget"))
    assert columns == [] and data == []
    assert "Basis: Budget" in message
    assert "Budget worksheet not shown: No approved budget because " + state in message


def test_a_non_budget_state_on_the_budget_basis_is_still_refused():
    execute, _ = _execute_with({"state": "not_built", "message": "because not_built",
                                "worksheet": None, "basis": "Budget", "budget": None})
    with pytest.raises(Refused, match="because not_built"):
        execute(dict(_FILTERS, basis="Budget"))


def test_the_limits_text_is_the_statement_reports():
    srm = _load("statement_report_model")
    assert LAYOUT.BUDGET_LIMITS == srm.BUDGET_LIMITS


def test_the_js_has_a_basis_select_actual_or_budget_defaulting_to_actual():
    js = open(os.path.join(DIR, "consolidation_worksheet.js")).read()
    block = js[js.index('fieldname: "basis"'):]
    block = block[:block.index("}")]
    assert 'fieldtype: "Select"' in block
    assert 'options: "Actual\\nBudget"' in block
    assert 'default: "Actual"' in block


def test_the_budget_states_are_approved_budget_models_states_but_ok():
    abm = _load("approved_budget_model")
    tree = _tree()
    assign = next(n for n in tree.body if isinstance(n, ast.Assign)
                  and any(getattr(t, "id", None) == "BUDGET_STATES" for t in n.targets))
    assert ast.literal_eval(assign.value) == tuple(s for s in abm.STATES if s != abm.OK)
