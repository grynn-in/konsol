"""Product-owned report templates → Excel cell maps for add-in Apply / Snapshot."""

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

TB_LONG_HEADERS = [
    "Entity", "Year", "Period", "Account", "Account Name",
    "Acct Type", "BS/P&L", "Debit", "Credit", "Net",
]
TB_LONG_LAST_COL = "J"
TB_LONG_MAX_ROWS = 10000


def _month_row_range(row):
    return f"B{row}:{_MONTH_END_COL}{row}"


def list_templates():
    return [
        {
            "id": "pnl_monthly",
            "title": "Monthly P&L",
            "description": "12-month income statement (demo accounts 4010, 5010)",
            "mode": "formulas",
        },
        {
            "id": "trial_balance_long",
            "title": "Trial balance (long)",
            "description": "One row per account × period — snapshot values for Excel models",
            "mode": "snapshot",
        },
    ]


def build_cell_map(template_id, entity, year, scenario_id="actuals"):
    if template_id != "pnl_monthly":
        raise ValueError(f"Unknown template_id for formulas: {template_id!r}")
    return _build_pnl_monthly(entity, int(year), scenario_id)


def build_trial_balance_long_map(entity, year, rows, period_from=1, period_to=12):
    """Build a snapshot cell map (values only) from pre-fetched TB rows."""
    year = int(year)
    period_from = int(period_from)
    period_to = int(period_to)

    cells = [
        {"range": "A1", "values": [["Trial Balance (snapshot)"]]},
        {"range": "B1", "values": [["Entity:"]]},
        {"range": "C1", "values": [[entity]]},
        {"range": "E1", "values": [["Year:"]]},
        {"range": "F1", "values": [[year]]},
        {"range": "H1", "values": [["Periods:"]]},
        {"range": "I1", "values": [[f"{period_from}–{period_to}"]]},
        {"range": f"A3:{TB_LONG_LAST_COL}3", "values": [TB_LONG_HEADERS]},
    ]

    data_rows = []
    for row in rows:
        data_rows.append([
            row.get("entity", entity),
            row.get("year", year),
            row.get("period"),
            row.get("account", ""),
            row.get("account_name", ""),
            row.get("account_type", ""),
            row.get("bs_pnl", ""),
            _num(row.get("debit")),
            _num(row.get("credit")),
            _num(row.get("net")),
        ])

    if data_rows:
        last_row = 3 + len(data_rows)
        cells.append({
            "range": f"A4:{TB_LONG_LAST_COL}{last_row}",
            "values": data_rows,
        })

    return {
        "template_id": "trial_balance_long",
        "mode": "snapshot",
        "entity": entity,
        "year": year,
        "period_from": period_from,
        "period_to": period_to,
        "sheet_name": "TB",
        "row_count": len(data_rows),
        "cells": cells,
    }


def _num(value):
    if value is None:
        return 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


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
        "mode": "formulas",
        "entity": entity,
        "year": year,
        "scenario_id": scenario_id,
        "sheet_name": "P&L",
        "cells": cells,
    }