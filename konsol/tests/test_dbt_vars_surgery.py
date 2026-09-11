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


# --- the region must stay valid YAML, and unsafe names must not reach it ---

def test_empty_managed_block_is_valid_yaml_in_context():
    """yaml.dump({}) is the flow scalar "{}" — indented under `vars:` that is a
    mapping value where a key belongs, and the whole dbt_project.yml stops
    parsing (ScannerError). A site with nothing Published hit it on its first
    regenerate. Empty renders as markers and nothing else."""
    rendered = _m.render_managed_vars({})
    assert "{}" not in rendered
    out = _m.splice_managed_block(DOC, rendered)
    parsed = yaml.safe_load(out)
    assert parsed["vars"]["erp_sources"] == ["d365_fo"]
    assert parsed["vars"]["cluster_enabled"] is False
    assert "dimensions" not in parsed["vars"]


def test_domain_names_that_would_corrupt_the_yaml_are_refused():
    """model_name and build_domain are raw-interpolated into YAML; both are
    ordinary Frappe Data fields. A name carrying ': ', '#' or a quote used to
    rewrite dbt_project.yml into something unparseable — or worse, parseable
    and wrong — on the next Build Model save."""
    for bad in ("gold_x: injected", "gold_x #c", "gold_x'", "gold x", "", "  "):
        try:
            _m.render_model_domains({bad: "consolidation"})
        except _m.UnsafeDomainMapping:
            continue
        raise AssertionError(f"model name {bad!r} was not refused")

    for bad in ("consolidation'], 'domain:evil", "con: sol", "#c", ""):
        try:
            _m.render_model_domains({"gold_x": bad})
        except _m.UnsafeDomainMapping:
            continue
        raise AssertionError(f"domain {bad!r} was not refused")


def test_safe_domain_names_still_render_and_parse():
    out = _m.render_model_domains({"gold_trial_balance": "consolidation",
                                   "gold_cash_flow_indirect": "reporting-v2"})
    doc = _m.splice_managed_block(
        DOMAINS_DOC, out,
        begin=_m.MANAGED_DOMAINS_BEGIN, end=_m.MANAGED_DOMAINS_END)
    parsed = yaml.safe_load(doc)
    gold = parsed["models"]["open_epm"]["gold"]
    assert gold["gold_trial_balance"]["+tags"] == ["gold", "domain:consolidation"]
    assert gold["gold_cash_flow_indirect"]["+tags"] == ["gold", "domain:reporting-v2"]
