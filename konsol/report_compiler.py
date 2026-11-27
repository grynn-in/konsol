"""Product-owned report templates → Excel cell maps for add-in Apply / Snapshot."""

import os

from konsol.report_template_model import (
    block_key, column_letters, compile_formulas, compile_snapshot, description, load_templates,
)

_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_templates")

_TB_LONG = load_templates(_TEMPLATE_DIR)["trial_balance_long"]

# konsol#107 decision 1 (Deepak Pai, 7 Nov 2026): the snapshot is the entity's
# own trial balance at account level in its local currency, before FX
# translation, ownership and eliminations, dimensions summed. The title and
# the note row (in the template file) say so. These names are derived from
# the file; other modules import them.
TB_LONG_HEADERS = [f["h"] for f in _TB_LONG["fields"]]
TB_LONG_LAST_COL = column_letters(len(_TB_LONG["fields"]))
TB_LONG_MAX_ROWS = _TB_LONG["max_rows"]
TB_LONG_TITLE = _TB_LONG["title"]
TB_LONG_NOTE = next(c["text"] for c in _TB_LONG["header"] if c["at"] == "A2")


def list_templates():
    """The template files, in `order`: {id, title, description, mode}."""
    templates = load_templates(_TEMPLATE_DIR)
    return [
        {
            "id": t["id"],
            "title": t["title"],
            "description": description(t),
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
    return compile_snapshot(
        _TB_LONG,
        {"entity": entity, "year": int(year),
         "period_from": int(period_from), "period_to": int(period_to)},
        rows,
    )
