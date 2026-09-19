"""Structural tests for the budget-layer dimension.

Site-free, mirroring test_fact_registry: parse doctype JSON / fixtures /
api.py / the Excel add-in without a live Frappe site. Live K.EPM behaviour is
exercised by bench run-tests against a site with data.

The allocated-fact tests this file used to carry (konsol#264: allocation is
retired, and its `allocated` Dataset row is removed in row K5) moved out;
budget-layer coverage is unrelated to allocation and stays.
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(path) as f:
        return f.read()


def _doctype_json(name):
    with open(os.path.join(APP_DIR, "epm", "doctype", name, f"{name}.json")) as f:
        return json.load(f)


def _fixture(name):
    # konsol#230: shipped from fixtures/ or defaults/ depending on mutability.
    from konsol.tests.shipped import shipped
    return shipped(name)


def _fact(scenario_key):
    return next(r for r in _fixture("dataset.json") if r["scenario_key"] == scenario_key)


# --- budget layer: Dataset doctype + fixture -------------------------------

def test_dataset_doctype_has_has_layer_field():
    fields = [f["fieldname"] for f in _doctype_json("dataset")["fields"]]
    assert "has_layer" in fields


def test_budget_fact_has_layer_enabled():
    assert _fact("budget")["has_layer"] == 1


# --- budget layer: API reads + filters the column -------------------------

def test_api_fact_fields_include_has_layer():
    src = _read(os.path.join(APP_DIR, "api.py"))
    # _FACT_FIELDS must load has_layer so fact.has_layer is available
    fields_block = src.split("_FACT_FIELDS")[1].split("]")[0]
    assert '"has_layer"' in fields_block


def test_api_batch_applies_layer_filter():
    src = _read(os.path.join(APP_DIR, "api.py"))
    assert "layer_clause" in src
    assert "AND layer = {layer:String}" in src
    # gated on the fact capability, like scenario_id
    assert "fact.has_layer" in src


def test_epm_value_get_accepts_layer():
    # single-value GET endpoint must accept layer too (parity with the batch path)
    src = _read(os.path.join(APP_DIR, "api.py"))
    sig = src.split("def epm_value(")[1].split(")")[0]
    assert "layer" in sig


# --- Excel add-in: layer passable from a worksheet ------------------------

def test_addin_js_threads_layer():
    js = _read(os.path.join(APP_DIR, "public", "excel-addin", "functions.js"))
    assert "if (layer) req.layer = String(layer);" in js
    # both the generic and budget helpers forward layer
    assert "function epmBudget(entity, year, period, account, costCenter, " \
           "department, scenarioId, hierarchy, node, layer)" in js


def test_addin_metadata_declares_layer_param():
    meta = json.load(
        open(os.path.join(APP_DIR, "public", "excel-addin", "functions.json"))
    )
    budget = next(f for f in meta["functions"] if f["id"] == "EPM_BUDGET")
    assert "layer" in [p["name"] for p in budget["parameters"]]
