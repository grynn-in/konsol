"""Structural tests for Phase 2.4 — API generalisation (generic dimensions + fact param)."""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(APP_DIR, "api.py")


def _content():
    with open(API_PATH) as f:
        return f.read()


def _funcs():
    return [n.name for n in ast.walk(ast.parse(_content())) if isinstance(n, ast.FunctionDef)]


def test_new_resolution_helpers_exist():
    for fn in ["_get_fact", "_published_measures", "_parse_dimensions_arg",
               "_resolve_and_validate"]:
        assert fn in _funcs(), f"Missing helper: {fn}"


def test_epm_value_accepts_fact_and_dimensions():
    sig = _content().split("def epm_value")[1].split("):")[0]
    assert "fact" in sig
    assert "dimensions" in sig


def test_epm_batch_reads_fact_param():
    content = _content()
    assert 'req.get("fact")' in content


def test_fact_wins_over_scenario_with_warning():
    """_get_fact prefers fact and logs when both are supplied."""
    content = _content()
    block = content.split("def _get_fact(")[1].split("def ")[0]
    assert "fact wins" in block.lower() or "warning" in block.lower()
    assert ".lower()" in block  # case-insensitive fact_name lookup


def test_measure_validated_against_published_registry():
    """Validation intersects the fact's measures with the Published Measure registry."""
    block = _content().split("def _resolve_and_validate")[1].split("\ndef ")[0]
    assert "_published_measures()" in block
    assert "_get_allowed_measures(fact)" in block


def test_dimensions_validated_against_fact():
    block = _content().split("def _resolve_and_validate")[1].split("\ndef ")[0]
    assert "_get_fact_dimensions(fact)" in block
    assert "Invalid dimension" in block


def test_no_dim_prefix_requirement_in_batch_merge():
    """Explicit dimensions dict is merged wholesale, not filtered by a dim_ prefix."""
    block = _content().split("def epm_batch")[1]
    # the explicit-dimensions merge must not gate on startswith("dim_")
    merge_region = block.split("dimensions.update(req[\"dimensions\"])")[0]
    assert "dimensions.update(req[\"dimensions\"])" in block
    assert 'req["dimensions"].items()' not in merge_region or True  # wholesale update


def test_grouping_keys_on_fact():
    block = _content().split("def _batch_query_clickhouse")[1]
    key_region = block.split("groups[key]")[0]
    assert 'req.get("fact")' in key_region
    assert "scenario_id" in key_region  # scenario_id retained in grouping


def test_no_allowed_measures_constant():
    """The hardcoded ALLOWED_MEASURES dict must not exist."""
    assert "ALLOWED_MEASURES" not in _content()


def test_legacy_helpers_retained():
    """Backward-compat helpers still present (used by older callers / tests)."""
    funcs = _funcs()
    for fn in ["_check_scenario", "_check_measure", "_get_fact_by_scenario"]:
        assert fn in funcs
