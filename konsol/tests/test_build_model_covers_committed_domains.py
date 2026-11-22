"""konsol#588: every model konsolidat's committed dbt_project.yml tags with a
`domain:` has a Build Model with the same domain.

konsol regenerates the managed block of dbt_project.yml from the Build Models.
A model tagged in the committed yml but missing here loses its tag on every
site, is selected by no scoped build (`tag:domain:consolidation`) and goes
stale (`gold_ic_side_slices`, read by `gold_fully_consolidated_tb`).

The konsolidat checkout is found through KONSOLIDAT_DBT_PROJECT (a dbt_project
directory, or the dbt_project.yml itself), else ~/Documents/grynn/konsolidat/
repo/dbt_project. The test skips when neither exists; CI that has the repo
must set the variable.
"""
import json
import os
import re
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_MODEL_FIXTURE = os.path.join(APP_DIR, "fixtures", "build_model.json")
DEFAULT_DBT_PROJECT = os.path.expanduser("~/Documents/grynn/konsolidat/repo/dbt_project")

#: Models the committed yml tags but that have no Build Model, by decision.
#: Each needs a reason; one that gains a Build Model fails the test.
EXEMPT = {
    # Stale tag: no models/gold/gold_disposal_adjustments.sql exists in
    # konsolidat. It is a konsolidat clean-up, not a konsol Build Model.
    "gold_disposal_adjustments": "tag with no model in konsolidat",
}


def _yml_path():
    root = os.environ.get("KONSOLIDAT_DBT_PROJECT") or DEFAULT_DBT_PROJECT
    path = root if root.endswith(".yml") else os.path.join(root, "dbt_project.yml")
    if not os.path.isfile(path):
        raise unittest.SkipTest(f"konsolidat dbt_project.yml not found at {path}")
    return path


def _committed_domains(path):
    """{model: domain} from the `+tags` lines of the managed block."""
    out, model = {}, None
    for line in open(path, encoding="utf-8"):
        m = re.match(r"^\s{6}(\w+):\s*$", line)
        if m:
            model = m.group(1)
            continue
        m = re.search(r"\+tags:.*'domain:(\w+)'", line)
        if m and model:
            out[model] = m.group(1)
            model = None
    return out


def _build_models():
    rows = json.load(open(BUILD_MODEL_FIXTURE, encoding="utf-8"))
    return {r["model_name"]: r["build_domain"] for r in rows}


def test_every_committed_domain_tag_has_a_build_model():
    committed = _committed_domains(_yml_path())
    assert len(committed) > 30, f"parsed only {len(committed)} tagged models; the yml layout changed"
    have = _build_models()
    problems = []
    for model, domain in sorted(committed.items()):
        if model in EXEMPT:
            continue
        if model not in have:
            problems.append(f"{model}: tagged domain:{domain} in the yml, no Build Model")
        elif have[model] != domain:
            problems.append(f"{model}: yml says domain:{domain}, Build Model says {have[model]}")
    assert not problems, "Build Model drift (konsol#588):\n  " + "\n  ".join(problems)


def test_exemptions_have_no_build_model():
    """An exempt model that gains a Build Model no longer needs the exemption.
    (An exemption absent from the yml is fine: konsolidat main may have fixed
    the stale tag already while an older checkout still carries it.)"""
    stale = [m for m in EXEMPT if m in _build_models()]
    assert not stale, f"exemptions no longer needed, remove: {stale}"
