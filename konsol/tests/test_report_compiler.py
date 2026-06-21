"""Unit tests for report_compiler (no Frappe site required)."""
import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)

from report_compiler import build_cell_map, list_templates


def test_list_templates_includes_pnl_monthly():
    ids = [t["id"] for t in list_templates()]
    assert "pnl_monthly" in ids


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


def test_unknown_template_raises():
    try:
        build_cell_map("unknown", "AMUS", 2024)
        raised = False
    except ValueError:
        raised = True
    assert raised