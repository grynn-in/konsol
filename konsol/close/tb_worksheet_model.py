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
