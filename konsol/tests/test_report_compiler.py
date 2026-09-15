"""Unit tests for report_compiler (no Frappe site required)."""
import os
import re
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)

from report_compiler import (
    TB_LONG_HEADERS,
    build_cell_map,
    build_trial_balance_long_map,
    list_templates,
)

ZZ_ACCOUNTS = [
    ("ZZ4000", "ZZ Sales"),
    ("ZZ5000", "ZZ Costs"),
    ("ZZ6000", "ZZ Other"),
]


def test_list_templates_includes_pnl_monthly():
    ids = [t["id"] for t in list_templates()]
    assert "pnl_monthly" in ids
    assert "trial_balance_long" in ids


def test_pnl_monthly_description_names_the_role_not_codes():
    pnl = next(t for t in list_templates() if t["id"] == "pnl_monthly")
    assert pnl["description"] == (
        "12-month income statement: every Published Profit and Loss account "
        "of the group chart"
    )


def test_build_pnl_monthly_shape():
    spec = build_cell_map("pnl_monthly", "ZZE", 2024, accounts=ZZ_ACCOUNTS[:2])

    assert spec["template_id"] == "pnl_monthly"
    assert spec["entity"] == "ZZE"
    assert spec["year"] == 2024
    assert spec["sheet_name"] == "P&L"
    assert len(spec["cells"]) >= 10

    formula_cells = [c for c in spec["cells"] if "formulas" in c]
    assert formula_cells
    assert formula_cells[0]["range"] == "B3:M3"
    assert len(formula_cells[0]["formulas"][0]) == 12
    first = formula_cells[0]["formulas"][0][0]
    assert first.startswith('=K.EPM("ZZE", 2024, 1, "ZZ4000")')

    month_header = next(c for c in spec["cells"] if c.get("range") == "B2:M2")
    assert len(month_header["values"][0]) == 12


def test_build_pnl_monthly_one_row_per_caller_account():
    spec = build_cell_map("pnl_monthly", "ZZE", 2026, accounts=ZZ_ACCOUNTS)
    by_range = {c["range"]: c for c in spec["cells"]}

    for offset, (account, caption) in enumerate(ZZ_ACCOUNTS):
        row = 3 + offset
        assert by_range[f"A{row}"]["values"] == [[caption]]
        formulas = by_range[f"B{row}:M{row}"]["formulas"][0]
        assert formulas == [
            f'=K.EPM("ZZE", 2026, {p}, "{account}")' for p in range(1, 13)
        ]


_LETTERS = [chr(ord("B") + i) for i in range(12)]


def test_build_pnl_monthly_total_row_sums_every_account():
    spec = build_cell_map("pnl_monthly", "ZZE", 2026, accounts=ZZ_ACCOUNTS)
    by_range = {c["range"]: c for c in spec["cells"]}

    assert by_range["A6"]["values"] == [["Net Profit and Loss"]]
    assert by_range["B6:M6"]["formulas"] == [
        [f"=SUM({col}3:{col}5)" for col in _LETTERS]
    ]


def test_build_pnl_monthly_total_row_with_one_account_is_not_circular():
    spec = build_cell_map("pnl_monthly", "ZZE", 2026, accounts=ZZ_ACCOUNTS[:1])
    by_range = {c["range"]: c for c in spec["cells"]}

    assert by_range["A4"]["values"] == [["Net Profit and Loss"]]
    formulas = by_range["B4:M4"]["formulas"][0]
    assert formulas == [f"=SUM({col}3:{col}3)" for col in _LETTERS]
    assert not any(re.search(r"[A-M]4\b", f) for f in formulas), formulas


def test_build_pnl_monthly_has_no_gross_profit_caption():
    for accounts in (ZZ_ACCOUNTS[:1], ZZ_ACCOUNTS[:2], ZZ_ACCOUNTS):
        spec = build_cell_map("pnl_monthly", "ZZE", 2026, accounts=accounts)
        captions = [
            v
            for c in spec["cells"]
            for line in c.get("values", [])
            for v in line
        ]
        assert "Gross Profit" not in captions, captions


def test_build_pnl_monthly_without_accounts_raises():
    message = (
        "The group chart has no Published Profit and Loss accounts, "
        "so the monthly P&L has no lines."
    )
    for kwargs in ({}, {"accounts": None}, {"accounts": []}):
        try:
            build_cell_map("pnl_monthly", "ZZE", 2026, **kwargs)
            raised = None
        except ValueError as exc:
            raised = str(exc)
        assert raised == message, (kwargs, raised)


def test_report_compiler_has_no_account_code_literal():
    path = os.path.join(APP_DIR, "report_compiler.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    hits = re.findall(r"""["'][0-9]{4}["']""", source)
    assert not hits, f"account code literals in report_compiler.py: {hits}"


def test_build_trial_balance_long_snapshot_shape():
    rows = [
        {
            "entity": "ZZE",
            "year": 2024,
            "period": 6,
            "account": "ZZ4000",
            "account_name": "ZZ Sales",
            "account_type": "Revenue",
            "bs_pnl": "P&L",
            "debit": 0.0,
            "credit": 861245.0,
            "net": 861245.0,
        },
        {
            "entity": "ZZE",
            "year": 2024,
            "period": 6,
            "account": "ZZ5000",
            "account_name": "ZZ Costs",
            "account_type": "Expense",
            "bs_pnl": "P&L",
            "debit": 420100.0,
            "credit": 0.0,
            "net": -420100.0,
        },
    ]
    spec = build_trial_balance_long_map("ZZE", 2024, rows, 1, 12)

    assert spec["template_id"] == "trial_balance_long"
    assert spec["mode"] == "snapshot"
    assert spec["sheet_name"] == "TB"
    assert spec["row_count"] == 2

    header = next(c for c in spec["cells"] if c.get("range") == "A3:J3")
    assert header["values"][0] == TB_LONG_HEADERS

    data = next(c for c in spec["cells"] if c.get("range") == "A4:J5")
    assert data["values"][0][3] == "ZZ4000"
    assert data["values"][0][9] == 861245.0
    assert "formulas" not in data


def test_unknown_template_raises():
    try:
        build_cell_map("unknown", "ZZE", 2024)
        raised = False
    except ValueError:
        raised = True
    assert raised
