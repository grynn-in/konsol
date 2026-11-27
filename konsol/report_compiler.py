"""Product-owned report templates → Excel cell maps for add-in Apply / Snapshot."""

import os

from konsol.report_template_model import block_key, compile_formulas, load_templates

_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_templates")

TB_LONG_HEADERS = [
    "Entity", "Year", "Period", "Account", "Account Name",
    "Acct Type", "BS/P&L", "Debit", "Credit", "Net",
]
TB_LONG_LAST_COL = "J"
TB_LONG_MAX_ROWS = 10000

# konsol#107 decision 1 (Deepak Pai, 7 Nov 2026): the snapshot is the entity's
# own trial balance at account level in its local currency, read from the same
# table consolidation starts from, before FX translation, ownership and
# eliminations, with dimensions summed away. It reconciles to a group figure
# at account level in local currency only. The title and the note row say so,
# so nobody reads it as the TB the group was consolidated from.
TB_LONG_TITLE = "Entity TB — local currency, account level (pre-consolidation)"
TB_LONG_NOTE = (
    "Note: the entity's own trial balance in its local currency, one row per "
    "account and period, dimensions summed. Before FX translation, ownership "
    "and eliminations: it reconciles to group figures at account level in "
    "local currency only."
)


def list_templates():
    """The template files, in `order`: {id, title, description, mode}."""
    templates = load_templates(_TEMPLATE_DIR)
    return [
        {
            "id": t["id"],
            "title": t["title"],
            "description": t.get("description", "").replace(
                "{sign.sentence}", t.get("sign", {}).get("sentence", "")
            ),
            "mode": t["mode"],
        }
        for t in templates.values()
    ]


def build_cell_map(template_id, entity, year, scenario_id="actuals", accounts=None, *,
                   periods=None, params=None, ctx=None):
    """Compile a formulas template file into a cell map.

    `accounts`: list of `(main_account, caption)` in display order, for the
    template's one `accounts` block. `periods`: the year's Regular periods
    ({number, code, label, start_date}); required, a year without them is
    refused. `params` / `ctx` add to or override what the positional
    arguments supply (report_template_model.compile_formulas has the ctx
    contract).
    """
    template = load_templates(_TEMPLATE_DIR).get(template_id)
    if template is None or template["mode"] != "formulas":
        raise ValueError(f"Unknown template_id for formulas: {template_id!r}")
    merged = {"entity": entity, "year": year, "scenario_id": scenario_id}
    merged.update(params or {})
    context = {"periods": periods}
    if accounts is not None:
        keys = [block_key(b, i) for i, b in enumerate(template["rows"]["blocks"])
                if b["type"] == "accounts"]
        if len(keys) != 1:
            raise ValueError(
                f"Report template {template_id!r} has {len(keys)} accounts blocks: "
                "pass ctx['accounts'] by block, not accounts."
            )
        context["accounts"] = {keys[0]: list(accounts)}
    context.update(ctx or {})
    return compile_formulas(template, merged, context)


def build_trial_balance_long_map(entity, year, rows, period_from=1, period_to=12):
    """Build a snapshot cell map (values only) from pre-fetched TB rows."""
    year = int(year)
    period_from = int(period_from)
    period_to = int(period_to)

    cells = [
        {"range": "A1", "values": [[TB_LONG_TITLE]]},
        {"range": "A2", "values": [[TB_LONG_NOTE]]},
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
