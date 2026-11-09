"""Consolidation worksheet model, pure: konsol/close/tb_worksheet_model.py
(konsol#494 row 1 — layer -> column rule).

``column_of(adjustment_type, data_area_id, shown, allowed)`` names the
worksheet column a ``gold_fully_consolidated_tb`` row lands in. Only the
``entity`` layer and its ``cta`` rows sit under an entity; every other layer
is one column whatever ``data_area_id`` it carries (D2-5). The layer grouping
is ``drill_model``'s, loaded by path (no copy of the label table).

Keys are tagged tuples so an entity code can never equal a layer or bucket
key: ``("entity", code)``, ``("bucket", "other_entities" | "outside_scope")``,
``("layer", key)`` (raw type for an unknown one).

Pure: no frappe, no konsol imports. ``drill_model`` is a sibling loaded by
path, as ``drill_model`` loads ``statement_model``.
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name):
    spec = importlib.util.spec_from_file_location(
        "konsol_close_tb_worksheet_model_" + name, os.path.join(_HERE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


drill_model = _load_sibling("drill_model")
statement_model = _load_sibling("statement_model")

OTHER_ENTITIES = ("bucket", "other_entities")
OUTSIDE_SCOPE = ("bucket", "outside_scope")

_ENTITY_LAYERS = ("entity", "cta")


def _layer_key(adjustment_type):
    """Drill group key for a type; the raw type when drill_model has no group."""
    label = drill_model._LAYER_LABELS.get(adjustment_type)
    if label is None:
        return adjustment_type
    return drill_model._LAYER_KEY_BY_LABEL.get(label, adjustment_type)


def column_of(adjustment_type, data_area_id, shown, allowed):
    """Tagged column key for one row.

    ``shown``: entity codes with their own column (None = all).
    ``allowed``: entity codes the caller may see (None = unrestricted).
    Scope wins over ``shown``: an entity outside ``allowed`` is
    ``outside_scope`` and never named.
    """
    if adjustment_type not in _ENTITY_LAYERS:
        return ("layer", _layer_key(adjustment_type))
    if allowed is not None and data_area_id not in allowed:
        return OUTSIDE_SCOPE
    if shown is not None and data_area_id not in shown:
        return OTHER_ENTITIES
    return ("entity", data_area_id)


# -- row 2: account placement and window ------------------------------------

CTA_UNDECLARED = ("cta_undeclared",)
CTA_UNDECLARED_LABEL = "CTA (no CTA account declared)"
NOT_IN_CHART_LABEL = "not in the published chart"
_CTA_CODE = "CTA"


def row_key(row, accounts, declared):
    """Line key for one row: ``("account", code)``, ``CTA_UNDECLARED`` or
    ``("not_in_chart", code)``.

    A cta-layer row (``main_account`` is the literal ``CTA``) is placed under
    ``declared["cta_account"]``; undeclared, it keeps a visible row of its own.
    A code missing from the Published chart is never dropped.
    """
    code = row["main_account"]
    if row.get("adjustment_type") == "cta" or code == _CTA_CODE:
        cta_account = (declared or {}).get("cta_account")
        return ("account", cta_account) if cta_account else CTA_UNDECLARED
    if code not in accounts:
        return ("not_in_chart", code)
    return ("account", code)


def in_window(row, accounts, period_rows, key):
    """True when the row's period counts at ``key`` (fiscal_year, fiscal_period).

    statement_model's rule: a chart account in the Profit and Loss section is
    year-to-date (``period_keys(...)["ytd"]``); everything else (Balance Sheet,
    CTA, not-in-chart) is cumulative (``_keys_up_to``).
    """
    period = (row["fiscal_year"], row["fiscal_period"])
    entry = accounts.get(row["main_account"])
    is_pl = (row.get("adjustment_type") != "cta" and entry is not None
             and not entry.get("is_group")
             and entry.get("statement_section") == statement_model.PL)
    if is_pl:
        return period in set(statement_model.period_keys(period_rows, key)["ytd"])
    return period in statement_model._keys_up_to(period_rows, key)


# -- row 3: the worksheet and its cross-foot ---------------------------------

from decimal import Decimal  # noqa: E402

ROUNDING = ("rounding",)
CONSOLIDATED = ("consolidated",)
ROUNDING_LABEL = statement_model.ROUNDING_LABEL
CONSOLIDATED_LABEL = "Consolidated"
OTHER_ENTITIES_LABEL = "Other entities"
OUTSIDE_SCOPE_LABEL = "Outside your scope"
_ELIMINATIONS = ("layer", "ic_elimination")
_ZERO = Decimal("0")


def _adjustment_order():
    """Group keys in drill_model's label order, eliminations excluded."""
    order = []
    for adjustment_type in drill_model._LAYER_LABELS:
        key = _layer_key(adjustment_type)
        if key != "ic_elimination" and key not in order:
            order.append(key)
    return order


def _layer_label(key):
    for adjustment_type, label in drill_model._LAYER_LABELS.items():
        if _layer_key(adjustment_type) == key:
            return label
    return key


def _columns(present, shown, allowed):
    """Ordered column keys before Rounding/Consolidated: entities by code
    (each present one, plus each shown one the caller may see), Other,
    Outside, eliminations, known adjustment groups in drill_model's order,
    then unknown types by raw code."""
    entities = {k[1] for k in present if k[0] == "entity"}
    if shown is not None:
        entities |= {c for c in shown if allowed is None or c in allowed}
    keys = [("entity", code) for code in sorted(entities)]
    keys += [k for k in (OTHER_ENTITIES, OUTSIDE_SCOPE) if k in present]
    keys.append(_ELIMINATIONS)
    known = _adjustment_order()
    keys += [("layer", k) for k in known if ("layer", k) in present]
    keys += sorted(k for k in present
                   if k[0] == "layer" and k != _ELIMINATIONS and k[1] not in known)
    return keys


def _label(key):
    if key[0] == "entity":
        return key[1]
    if key == OTHER_ENTITIES:
        return OTHER_ENTITIES_LABEL
    if key == OUTSIDE_SCOPE:
        return OUTSIDE_SCOPE_LABEL
    return _layer_label(key[1])


def _line_order(key, accounts):
    if key[0] == "account":
        return (0, (accounts.get(key[1]) or {}).get("lft") or 0, key[1])
    if key[0] == "not_in_chart":
        return (1, 0, key[1])
    return (2, 0, "")


def _line_head(key, accounts):
    if key == CTA_UNDECLARED:
        return _CTA_CODE, CTA_UNDECLARED_LABEL
    if key[0] == "not_in_chart":
        return key[1], NOT_IN_CHART_LABEL
    return key[1], (accounts.get(key[1]) or {}).get("account_name")


def worksheet(rows, accounts, period_rows, key, declared, minor_unit, shown, allowed):
    """The consolidation worksheet at ``key``: one line per account, one
    column per layer/entity, net debit (konsol#494).

    ``rows`` are the grouped ``gold_fully_consolidated_tb`` read (keys
    ``fiscal_year, fiscal_period, data_area_id, main_account,
    adjustment_type, amount, null_rows``); ``accounts`` is the Published
    chart; ``declared`` is ``statement_accounts()``; ``shown`` and
    ``allowed`` as in ``column_of``.

    Returns ``{"columns": [{"key", "label"}], "lines": [{"key", "account",
    "name", "cells", "consolidated"}], "total": {"cells", "consolidated"},
    "outside_count", "minor_unit"}``. ``cells`` maps every column key but
    Consolidated to a float.

    Sums are Decimal; each cell is rounded once from its exact sum, and
    Consolidated is the line's exact sum rounded once. The difference
    between Consolidated and the displayed cells goes in Rounding, so every
    line cross-foots exactly in displayed values. The Rounding column is
    present only when some line needs it. The Total line sums the displayed
    line values per column, so it foots both down and across.

    A row in the window with NULL amounts raises ``ValueError`` naming its
    account and layer; it is never read as 0.
    """
    minor_unit = statement_model.places(minor_unit)
    exact = {}
    present = set()
    outside = set()
    for row in rows:
        if not in_window(row, accounts, period_rows, key):
            continue
        code, layer = row["main_account"], row.get("adjustment_type")
        if row.get("null_rows") or row.get("amount") is None:
            raise ValueError(
                f"{code} ({layer}) {row['fiscal_year']}/{row['fiscal_period']}: "
                "has NULL amounts, cannot be read as 0")
        column = column_of(layer, row.get("data_area_id"), shown, allowed)
        if column == OUTSIDE_SCOPE:
            outside.add(row.get("data_area_id"))
        present.add(column)
        cells = exact.setdefault(row_key(row, accounts, declared), {})
        cells[column] = cells.get(column, _ZERO) + statement_model._dec(row["amount"])

    keys = _columns(present, shown, allowed)

    def rnd(amount):
        return statement_model._round(amount, minor_unit)

    lines = []
    for line_key in sorted(exact, key=lambda k: _line_order(k, accounts)):
        line_exact = exact[line_key]
        cells = {k: rnd(line_exact.get(k, _ZERO)) for k in keys}
        consolidated = rnd(sum(line_exact.values(), _ZERO))
        shown_sum = sum((statement_model._dec(v) for v in cells.values()), _ZERO)
        cells[ROUNDING] = rnd(statement_model._dec(consolidated) - shown_sum)
        account, name = _line_head(line_key, accounts)
        lines.append({"key": line_key, "account": account, "name": name,
                      "cells": cells, "consolidated": consolidated})

    if any(line["cells"][ROUNDING] for line in lines):
        keys.append(ROUNDING)
    else:
        for line in lines:
            del line["cells"][ROUNDING]

    total = {
        "cells": {k: rnd(sum((statement_model._dec(l["cells"][k]) for l in lines), _ZERO))
                  for k in keys},
        "consolidated": rnd(sum((statement_model._dec(l["consolidated"]) for l in lines), _ZERO)),
    }
    columns = [{"key": k, "label": ROUNDING_LABEL if k == ROUNDING else _label(k)} for k in keys]
    columns.append({"key": CONSOLIDATED, "label": CONSOLIDATED_LABEL})
    return {"columns": columns, "lines": lines, "total": total,
            "outside_count": len(outside), "minor_unit": minor_unit}
