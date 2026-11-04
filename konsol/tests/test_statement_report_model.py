"""Consolidated Statement Desk report, pure: konsol/close/statement_report_model.py.

The Desk Script Report "Consolidated Statement" shows the Close app's
Numbers statement (``statement_api.get_statement``'s payload) as report
rows. This module maps the payload to rows; it never recomputes an amount.

The statement under test is built by ``statement_model.statement`` itself,
from the fixtures in ``test_close_statement_model.py`` (loaded by path), so
the test exercises the real payload shape, not a hand-written copy of it.

Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, *parts):
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, *parts))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = _load("statement_report_model_under_test", "close", "statement_report_model.py")
F = _load("close_statement_model_fixtures", "tests", "test_close_statement_model.py")
SM = F.M


def _payload(rows=None, declared=None, **overrides):
    stmt = SM.statement(
        rows if rows is not None else F._s_rows(), F._s_accounts(), F._S_CALENDAR,
        F._S_KEY, declared if declared is not None else F._s_declared(),
    )
    payload = {
        "state": "ok", "message": None, "statement": stmt,
        "period": {"fiscal_year": 2025, "fiscal_period": 7, "code": "P07",
                   "status": "Open", "period_type": "Regular"},
        "consolidation_group": "ZZ_GROUP", "reporting_currency": "USD",
        "gap": None, "not_included": None,
    }
    payload.update(overrides)
    return payload


def _by_label(data, label):
    found = [row for row in data if row["label"] == label]
    assert len(found) == 1, f"{label!r}: {len(found)} rows"
    return found[0]


def test_module_imports_nothing_from_frappe_or_konsol():
    with open(os.path.join(APP_DIR, "close", "statement_report_model.py")) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert not name.startswith(("frappe", "konsol")), name


def test_columns_are_label_code_four_amounts_and_currency():
    fieldnames = [c["fieldname"] for c in R.columns()]
    assert fieldnames == ["label", "code", "current", "comparison", "variance", "ytd", "currency"]
    for column in R.columns():
        if column["fieldname"] in ("current", "comparison", "variance", "ytd"):
            assert column["fieldtype"] == "Currency"
            assert column["options"] == "currency"


def test_a_section_header_row_per_section_in_statement_order():
    data = R.rows(_payload())
    headers = [row["label"] for row in data if row.get("is_section")]
    assert headers == ["Profit and Loss", "Balance Sheet"]
    assert data[0]["label"] == "Profit and Loss"


def test_every_statement_line_has_a_row_with_the_payloads_own_amounts():
    payload = _payload()
    data = R.rows(payload)
    pl, bs = payload["statement"]["sections"]
    for line in pl["lines"] + bs["lines"]:
        label = line.get("heading_name") or line["label"]
        row = _by_label(data, label)
        assert row["current"] == line["current"]
        assert row["comparison"] == line.get("comparison")
        if line["kind"] != "residual":
            assert row["variance"] == line.get("variance")


def test_heading_rows_carry_their_code():
    data = R.rows(_payload())
    assert _by_label(data, "ASSETS")["code"] == "1"
    assert _by_label(data, "EQUITY")["code"] == "3"


def test_pl_rows_carry_ytd_and_bs_rows_do_not():
    payload = _payload()
    data = R.rows(payload)
    net = _by_label(data, SM.NET_RESULT_LABEL)
    net_line = payload["statement"]["sections"][0]["lines"][-1]
    assert net["ytd"] == net_line["ytd"]
    assert net["current"] == 241.43  # profit positive (fixture: P&L -241.43 net debit)
    for label in ("ASSETS", "LIABILITIES", "EQUITY", "Unmatched residual"):
        assert _by_label(data, label)["ytd"] is None


def test_balanced_fixture_bs_rows_and_zero_residual():
    data = R.rows(_payload())
    assert _by_label(data, "ASSETS")["current"] == 1735.10
    assert _by_label(data, "LIABILITIES")["current"] == 803.70
    assert _by_label(data, "Unmatched residual")["current"] == 0.0


def test_equity_includes_are_indented_rows_right_after_equity():
    data = R.rows(_payload())
    labels = [row["label"] for row in data]
    at = labels.index("EQUITY")
    includes = data[at + 1: at + 3]
    assert [row["label"] for row in includes] == [
        "of which currency translation (CTA)", "of which current-year result",
    ]
    for row in includes:
        assert row["indent"] == 1
    assert data[at]["indent"] == 0
    assert includes[0]["current"] == 5.07
    assert includes[1]["current"] == 241.43


def test_every_row_carries_the_reporting_currency():
    data = R.rows(_payload())
    assert data and all(row["currency"] == "USD" for row in data)


def test_unbalanced_residual_lists_its_explanation_indented():
    """A non-zero residual (CTA account undeclared): its explanation rows
    follow it, indented, with the Unexplained remainder — never hidden."""
    declared = F._s_declared(cta_account=None)
    data = R.rows(_payload(declared=declared))
    labels = [row["label"] for row in data]
    at = labels.index("Unmatched residual")
    assert data[at]["current"] != 0
    tail = data[at + 1:]
    assert tail and all(row["indent"] == 1 for row in tail)
    assert tail[-1]["label"] == "Unexplained"


@pytest.mark.parametrize("state, message", [
    ("choose_group", "Choose a consolidation group."),
    ("no_chart", "Publish the group chart (Main Account) first."),
    ("not_built", "The warehouse table is not built yet."),
    ("setup_gap", "statement_heading_side_undeclared: 2 have no normal_balance declared"),
])
def test_a_non_ok_state_raises_its_own_message_never_an_empty_table(state, message):
    payload = {"state": state, "message": message, "statement": None}
    with pytest.raises(ValueError) as exc:
        R.rows(payload)
    assert str(exc.value) == message


def test_a_non_ok_state_with_no_message_still_raises_naming_the_state():
    with pytest.raises(ValueError, match="error"):
        R.rows({"state": "error", "message": None, "statement": None})


def test_an_unknown_line_kind_raises_naming_it():
    payload = _payload()
    payload["statement"]["sections"][1]["lines"].append({"kind": "mystery", "label": "?"})
    with pytest.raises(ValueError, match="mystery"):
        R.rows(payload)


def test_message_names_period_group_currency_and_legend():
    text = R.message(_payload())
    assert "FY2025 P07" in text
    assert "ZZ_GROUP" in text
    assert "USD" in text
    assert SM.LEGEND in text


def test_message_carries_the_comparison_note_when_there_is_one():
    payload = _payload()
    payload["statement"]["periods"]["comparison_note"] = "No rows in the warehouse for FY2025 P06"
    assert "No rows in the warehouse for FY2025 P06" in R.message(payload)


def test_message_names_entities_not_included():
    payload = _payload(not_included={"count": 2, "entities": ["ZZA", "ZZB"], "hidden": 0})
    text = R.message(payload)
    assert "ZZA" in text and "ZZB" in text
