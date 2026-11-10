"""The three period reports call ``konsol.period_reports.setup`` from their
onload with the filters each remembers (Deepak Pai, 10 Nov 2026), and every
Desk page loads the four period-strip scripts in dependency order.
"""
import ast
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(APP_DIR, "consolidation", "report")

#: Report script -> the filters it remembers, in order.
KEYS = {
    "consolidation_worksheet": ["fiscal_year", "fiscal_period", "consolidation_group",
                                "entities", "dimension"],
    "consolidated_statement": ["fiscal_year", "fiscal_period", "consolidation_group"],
    "statement_drill": ["fiscal_year", "fiscal_period", "consolidation_group", "heading"],
}

_SETUP = re.compile(
    r"""onload\s*:\s*\(report\)\s*=>\s*konsol\.period_reports\.setup\(\s*report\s*,\s*"""
    r"""\{\s*keys\s*:\s*\[([^\]]*)\]\s*\}\s*\)""")

SCRIPTS = ["period_strip_model.js", "report_filter_memory.js", "period_strip.js",
           "period_reports.js"]


def _source(name):
    return open(os.path.join(REPORT_DIR, name, f"{name}.js")).read()


def test_each_period_report_calls_setup_in_onload_once():
    for name, keys in KEYS.items():
        source = _source(name)
        found = _SETUP.findall(source)
        assert len(found) == 1, f"{name}: expected one onload calling konsol.period_reports.setup"
        listed = [k.strip().strip("\"'") for k in found[0].split(",") if k.strip()]
        assert listed == keys, f"{name}: keys {listed}"
        assert source.count("onload") == 1, f"{name}: a second onload would replace the first"


def test_every_listed_key_is_a_filter_of_that_report():
    for name, keys in KEYS.items():
        source = _source(name)
        for key in keys:
            assert re.search(rf"""fieldname:\s*["']{key}["']""", source), f"{name}: no filter {key}"


def _app_include_js():
    tree = ast.parse(open(os.path.join(APP_DIR, "hooks.py")).read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "app_include_js" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("hooks.py has no app_include_js")


def test_desk_loads_the_four_scripts_in_dependency_order():
    included = _app_include_js()
    paths = [f"/assets/konsol/js/{s}" for s in SCRIPTS]
    for p, s in zip(paths, SCRIPTS):
        assert p in included, p
        assert os.path.exists(os.path.join(APP_DIR, "public", "js", s)), s
    assert [included.index(p) for p in paths] == sorted(included.index(p) for p in paths)
