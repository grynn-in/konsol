"""Host tests for the surgical dbt_project.yml vars writer (F3).

The old writer round-tripped the whole file through yaml.dump — 28 comments
stripped to 0, three times in one day via bench migrate, plus deletion of keys
it never owned. These tests pin the new contract: only the marker-delimited
region changes; a file without markers is refused.
"""
import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "dbt_config.py")

try:
    import yaml  # noqa: F401
except ModuleNotFoundError:  # host runner without pyyaml — runner skips
    raise ImportError("needs yaml")

if "frappe" not in sys.modules:
    sys.modules["frappe"] = types.ModuleType("frappe")

_spec = importlib.util.spec_from_file_location("dbt_config_under_test", _SRC)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)


DOC = (
    "name: open_epm\n"
    "# a comment that must survive\n"
    "vars:\n"
    "  cluster_enabled: false\n"
    "  # erp_sources is HAND-owned — see #139\n"
    "  erp_sources:\n"
    "  - d365_fo\n"
    + _m.render_managed_vars({
        "dimensions": [{"name": "dim_old", "cube_type": "string"}],
    })
    + "\nmodels:\n  open_epm:\n    +materialized: table\n# trailing comment\n"
)


def test_splice_replaces_only_the_region():
    out = _m.splice_managed_block(
        DOC, _m.render_managed_vars({"dimensions": [{"name": "dim_new", "cube_type": "string"}]}))
    assert "dim_new" in out and "dim_old" not in out
    for survivor in ("# a comment that must survive",
                     "# erp_sources is HAND-owned — see #139",
                     "cluster_enabled: false", "# trailing comment"):
        assert survivor in out, survivor


def test_splice_is_byte_identical_outside_markers():
    out = _m.splice_managed_block(
        DOC, _m.render_managed_vars({"dimensions": [{"name": "x", "cube_type": "string"}]}))
    before = DOC.split(_m.MANAGED_BEGIN)[0]
    after = DOC.split(_m.MANAGED_END)[1]
    assert out.startswith(before) and out.endswith(after)


def test_missing_markers_refused():
    assert _m.splice_managed_block("vars:\n  erp_sources: [d365_fo]\n", "x") is None


def test_rendered_block_is_valid_yaml_in_context():
    out = _m.splice_managed_block(
        DOC, _m.render_managed_vars({
            "dimensions": [{"name": "dim_a", "cube_type": "string", "in_budget": True}],
            "base_measures": [{"name": "m1", "expression": "sum(x)", "cube_type": "sum"}],
            "fiscal_extra_periods": [{"period": 0, "label": "OPN"}],
        }))
    parsed = yaml.safe_load(out)
    assert parsed["vars"]["dimensions"][0]["name"] == "dim_a"
    assert parsed["vars"]["base_measures"][0]["cube_type"] == "sum"
    assert parsed["vars"]["erp_sources"] == ["d365_fo"]      # untouched
    assert parsed["vars"]["cluster_enabled"] is False        # not deleted


def test_erp_sources_is_not_a_managed_key():
    assert "erp_sources" not in _m.MANAGED_KEYS


DOMAINS_DOC = (
    "models:\n  open_epm:\n    gold:\n      +schema: gold\n"
    + _m.render_model_domains({"gold_old": "staging"})
    + "\n\nseeds:\n  open_epm: {}\n# after-domains comment\n"
)


def test_domains_splice_replaces_only_its_region():
    out = _m.splice_managed_block(
        DOMAINS_DOC,
        _m.render_model_domains({"gold_new": "consolidation"}),
        begin=_m.MANAGED_DOMAINS_BEGIN, end=_m.MANAGED_DOMAINS_END)
    assert "gold_new" in out and "gold_old" not in out
    assert "+schema: gold" in out and "# after-domains comment" in out


def test_domains_render_groups_and_tags():
    out = _m.render_model_domains(
        {"gold_b": "staging", "gold_a": "staging", "gold_c": "consolidation"})
    assert out.index("Domain: consolidation") < out.index("Domain: staging")
    assert out.index("gold_a:") < out.index("gold_b:")
    assert "+tags: ['gold', 'domain:staging']" in out


def test_domains_splice_refused_without_markers():
    assert _m.splice_managed_block(
        "models: {}", "x",
        begin=_m.MANAGED_DOMAINS_BEGIN, end=_m.MANAGED_DOMAINS_END) is None
