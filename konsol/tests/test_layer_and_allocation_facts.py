"""Structural tests for the budget-layer dimension and the allocated fact.

Site-free, mirroring test_fact_registry: parse doctype JSON / fixtures /
api.py / the Excel add-in without a live Frappe site. Live K.EPM behaviour is
exercised by bench run-tests against a site with data.
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
    with open(os.path.join(APP_DIR, "fixtures", name)) as f:
        return json.load(f)


def _fact(scenario_key):
    return next(r for r in _fixture("fact_table.json") if r["scenario_key"] == scenario_key)


# --- budget layer: Fact Table doctype + fixture ---------------------------

def test_fact_table_doctype_has_has_layer_field():
    fields = [f["fieldname"] for f in _doctype_json("fact_table")["fields"]]
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


# --- allocated fact: separate from actuals --------------------------------

def test_allocated_fact_registered():
    f = _fact("allocated")
    assert f["clickhouse_table"] == "epm_gold.gold_allocation_tb"
    assert f["dbt_model"] == "gold_allocation_tb"
    assert f["has_scenario_id"] == 1


def test_allocated_fact_measure_and_dimension():
    f = _fact("allocated")
    measures = [m["measure"] for m in f["fact_measures"]]
    dims = [d["dimension"] for d in f["fact_dimensions"]]
    assert "period_net_amount" in measures
    assert "dim_cost_center" in dims


def test_allocated_is_distinct_from_actuals_table():
    assert _fact("allocated")["clickhouse_table"] != _fact("actuals")["clickhouse_table"]


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
