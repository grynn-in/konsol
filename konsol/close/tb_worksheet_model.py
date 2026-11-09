"""Consolidation worksheet model, pure: konsol/close/tb_worksheet_model.py
(konsol#494 row 1 — layer -> column rule).

``column_of(adjustment_type, data_area_id, shown, allowed)`` names the
worksheet column a ``gold_fully_consolidated_tb`` row lands in. Only the
``entity`` layer and its ``cta`` rows sit under an entity; every other layer
is one column whatever ``data_area_id`` it carries (D2-5). The layer grouping
is ``drill_model``'s, loaded by path (no copy of the label table).

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

OTHER_ENTITIES = "other_entities"
OUTSIDE_SCOPE = "outside_scope"

_ENTITY_LAYERS = ("entity", "cta")


def _layer_key(adjustment_type):
    """Drill group key for a type; the raw type when drill_model has no group."""
    label = drill_model._LAYER_LABELS.get(adjustment_type)
    if label is None:
        return adjustment_type
    return drill_model._LAYER_KEY_BY_LABEL.get(label, adjustment_type)


def column_of(adjustment_type, data_area_id, shown, allowed):
    """Column key for one row.

    ``shown``: entity codes with their own column (None = all).
    ``allowed``: entity codes the caller may see (None = unrestricted).
    Scope wins over ``shown``: an entity outside ``allowed`` is
    ``outside_scope`` and never named.
    """
    if adjustment_type not in _ENTITY_LAYERS:
        return _layer_key(adjustment_type)
    if allowed is not None and data_area_id not in allowed:
        return OUTSIDE_SCOPE
    if shown is not None and data_area_id not in shown:
        return OTHER_ENTITIES
    return data_area_id
