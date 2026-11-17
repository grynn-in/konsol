"""Budget S4b K9 (konsol#348): the consolidated budget models are governed.

konsolidat's `gold_budget_entity_ownership`, `gold_consolidated_budget_tb` and `gold_fully_consolidated_budget`
sit beside `gold_scenario_trial_balance` and the `gold_variance_*` models in
the `scenarios` Build Governance domain. A gold model with no Build Model row
gets no `domain:` tag, is selected by no scoped build and goes stale; CI's
`scripts/check_gold_domains.py` in konsolidat reads the models on disk and
fails on exactly that. These tests pin the rows and what konsol renders from
them into dbt_project.yml's managed block.
"""
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_MODEL_FIXTURE = os.path.join(APP_DIR, "fixtures", "build_model.json")
DBT_CONFIG_PATH = os.path.join(APP_DIR, "dbt_config.py")

BUDGET_MODELS = ("gold_budget_entity_ownership", "gold_consolidated_budget_tb", "gold_fully_consolidated_budget",
                 # konsol#348 S6 KA7 (pair A): the forecast rounds' relations
                 "gold_forecast_current_round", "gold_forecast_current", "gold_forecast_current_at_hierarchy_node",
                 "gold_latest_estimate", "gold_latest_estimate_current",
                 # konsol#348 S6 KC2 (pair C): the consolidated budget fact and accuracy
                 "gold_consolidated_budget_fact", "gold_forecast_accuracy", "gold_forecast_accuracy_group")

# What konsol writes between the managed markers for the scenarios group once
# both rows exist: alphabetical within the domain, 6-space indent, one tags line.
EXPECTED_SCENARIOS_LINES = [
    "      # --- Domain: scenarios ---",
    "      gold_budget_entity_ownership:",
    "        +tags: ['gold', 'domain:scenarios']",
    # konsol#348 S6 KC2: the consolidated budget fact (K.EPM consolidated_budget)
    "      gold_consolidated_budget_fact:",
    "        +tags: ['gold', 'domain:scenarios']",
    "      gold_consolidated_budget_tb:",
    "        +tags: ['gold', 'domain:scenarios']",
    # konsol#348 S6 KC2: forecast accuracy (FC-4-2)
    "      gold_forecast_accuracy:",
    "        +tags: ['gold', 'domain:scenarios']",
    "      gold_forecast_accuracy_group:",
    "        +tags: ['gold', 'domain:scenarios']",
    # konsol#348 S6 KA7: the declared current round and its readers
    "      gold_forecast_current:",
    "        +tags: ['gold', 'domain:scenarios']",
    "      gold_forecast_current_at_hierarchy_node:",
    "        +tags: ['gold', 'domain:scenarios']",
    "      gold_forecast_current_round:",
    "        +tags: ['gold', 'domain:scenarios']",
    "      gold_fully_consolidated_budget:",
    "        +tags: ['gold', 'domain:scenarios']",
    "      gold_latest_estimate:",
    "        +tags: ['gold', 'domain:scenarios']",
    "      gold_latest_estimate_current:",
    "        +tags: ['gold', 'domain:scenarios']",
    "      gold_scenario_trial_balance:",
    "        +tags: ['gold', 'domain:scenarios']",
    "      gold_variance_analysis:",
    "        +tags: ['gold', 'domain:scenarios']",
    "      gold_variance_quarterly:",
    "        +tags: ['gold', 'domain:scenarios']",
    "      gold_variance_ytd:",
    "        +tags: ['gold', 'domain:scenarios']",
]


def _rows():
    with open(BUILD_MODEL_FIXTURE) as f:
        return json.load(f)


def _render_model_domains():
    if "frappe" not in sys.modules:
        sys.modules["frappe"] = types.ModuleType("frappe")
    spec = importlib.util.spec_from_file_location("dbt_config_k9", DBT_CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.render_model_domains


def _domain_group(rendered, domain):
    lines = rendered.split("\n")
    start = lines.index(f"      # --- Domain: {domain} ---")
    end = start + 1
    while end < len(lines) and lines[end].startswith("      ") \
            and not lines[end].startswith("      # --- Domain:") \
            and not lines[end].startswith("      # --- END"):
        end += 1
    return lines[start:end]


def test_consolidated_budget_models_are_build_models_in_the_scenarios_domain():
    by_name = {row["name"]: row for row in _rows()}
    for model in BUDGET_MODELS:
        assert model in by_name, f"{model} is not a Build Model row"
        row = by_name[model]
        assert row["doctype"] == "Build Model", row
        assert row["model_name"] == model, row
        assert row["build_domain"] == "scenarios", row


def test_consolidated_budget_rows_carry_the_same_fields_as_their_siblings():
    by_name = {row["name"]: row for row in _rows()}
    sibling = by_name["gold_scenario_trial_balance"]
    for model in BUDGET_MODELS:
        assert set(by_name[model]) == set(sibling), (model, sorted(by_name[model]))


def test_build_model_fixture_stays_sorted_by_name():
    names = [row["name"] for row in _rows()]
    assert names == sorted(names)
    assert len(names) == len(set(names)), "duplicate Build Model name"


def test_scenarios_group_renders_both_budget_models_into_dbt_project_yml():
    mapping = {row["model_name"]: row["build_domain"] for row in _rows()}
    rendered = _render_model_domains()(mapping)
    assert _domain_group(rendered, "scenarios") == EXPECTED_SCENARIOS_LINES
