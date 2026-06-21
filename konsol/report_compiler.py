"""Product-owned report templates → Excel cell maps for add-in Apply."""

_MONTH_LABELS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

_PNL_MONTHLY_ACCOUNTS = [
    ("4010", "Product Revenue"),
    ("5010", "COGS"),
]

_MONTH_COLS = 12
_MONTH_END_COL = "M"  # B..M


def _month_row_range(row):
    return f"B{row}:{_MONTH_END_COL}{row}"


def list_templates():
    return [
        {
            "id": "pnl_monthly",
            "title": "Monthly P&L",
            "description": "12-month income statement (demo accounts 4010, 5010)",
        },
    ]


def build_cell_map(template_id, entity, year, scenario_id="actuals"):
    if template_id != "pnl_monthly":
        raise ValueError(f"Unknown template_id: {template_id!r}")
    return _build_pnl_monthly(entity, int(year), scenario_id)


def _epm_formula(entity, year, period, account):
    ent = str(entity).replace('"', '""')
    acc = str(account).replace('"', '""')
    return f'=K.EPM("{ent}", {int(year)}, {int(period)}, "{acc}")'


def _build_pnl_monthly(entity, year, scenario_id):
    cells = [
        {"range": "A1", "values": [["Monthly P&L"]]},
        {"range": "B1", "values": [["Entity:"]]},
        {"range": "C1", "values": [[entity]]},
        {"range": "E1", "values": [["Year:"]]},
        {"range": "F1", "values": [[year]]},
        {"range": "A2", "values": [["Account"]]},
        {"range": _month_row_range(2), "values": [_MONTH_LABELS]},
    ]

    row = 3
    first_data_row = row
    for account, label in _PNL_MONTHLY_ACCOUNTS:
        cells.append({"range": f"A{row}", "values": [[label]]})
        cells.append({
            "range": _month_row_range(row),
            "formulas": [[_epm_formula(entity, year, p, account) for p in range(1, _MONTH_COLS + 1)]],
        })
        row += 1

    letters = [chr(ord("B") + i) for i in range(_MONTH_COLS)]
    gp_row = row
    cells.append({"range": f"A{gp_row}", "values": [["Gross Profit"]]})
    cells.append({
        "range": _month_row_range(gp_row),
        "formulas": [[
            f"={letters[i]}{first_data_row}-{letters[i]}{first_data_row + 1}"
            for i in range(_MONTH_COLS)
        ]],
    })

    return {
        "template_id": "pnl_monthly",
        "entity": entity,
        "year": year,
        "scenario_id": scenario_id,
        "sheet_name": "P&L",
        "cells": cells,
    }