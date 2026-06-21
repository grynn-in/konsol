"""Unit tests for report_compiler (no Frappe site required)."""
import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)

from report_compiler import (
    TB_LONG_HEADERS,
    build_cell_map,
    build_trial_balance_long_map,
    list_templates,
)


def test_list_templates_includes_pnl_monthly():
    ids = [t["id"] for t in list_templates()]
    assert "pnl_monthly" in ids
    assert "trial_balance_long" in ids


def test_build_pnl_monthly_demo_shape():
    spec = build_cell_map("pnl_monthly", "AMUS", 2024)

    assert spec["template_id"] == "pnl_monthly"
    assert spec["entity"] == "AMUS"
    assert spec["year"] == 2024
    assert spec["sheet_name"] == "P&L"
    assert len(spec["cells"]) >= 10

    formula_cells = [c for c in spec["cells"] if "formulas" in c]
    assert formula_cells
    assert formula_cells[0]["range"] == "B3:M3"
    assert len(formula_cells[0]["formulas"][0]) == 12
    first = formula_cells[0]["formulas"][0][0]
    assert first.startswith('=K.EPM("AMUS", 2024, 1, "4010")')

    month_header = next(c for c in spec["cells"] if c.get("range") == "B2:M2")
    assert len(month_header["values"][0]) == 12


def test_build_trial_balance_long_snapshot_shape():
    rows = [
        {
            "entity": "AMUS",
            "year": 2024,
            "period": 6,
            "account": "4010",
            "account_name": "Product Revenue",
            "account_type": "Revenue",
            "bs_pnl": "P&L",
            "debit": 0.0,
            "credit": 861245.0,
            "net": 861245.0,
        },
        {
            "entity": "AMUS",
            "year": 2024,
            "period": 6,
            "account": "5010",
            "account_name": "COGS",
            "account_type": "Expense",
            "bs_pnl": "P&L",
            "debit": 420100.0,
            "credit": 0.0,
            "net": -420100.0,
        },
    ]
    spec = build_trial_balance_long_map("AMUS", 2024, rows, 1, 12)

    assert spec["template_id"] == "trial_balance_long"
    assert spec["mode"] == "snapshot"
    assert spec["sheet_name"] == "TB"
    assert spec["row_count"] == 2

    header = next(c for c in spec["cells"] if c.get("range") == "A3:J3")
    assert header["values"][0] == TB_LONG_HEADERS

    data = next(c for c in spec["cells"] if c.get("range") == "A4:J5")
    assert data["values"][0][3] == "4010"
    assert data["values"][0][9] == 861245.0
    assert "formulas" not in data


def test_unknown_template_raises():
    try:
        build_cell_map("unknown", "AMUS", 2024)
        raised = False
    except ValueError:
        raised = True
    assert raised