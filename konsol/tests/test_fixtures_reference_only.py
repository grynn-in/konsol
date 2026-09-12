"""konsol ships reference data only; the Contoso/Alpine demo is gone (12 Sep 2026).

Everything in konsol/fixtures/ is force-reimported on every migrate, so a row
there overwrites whatever a site has under the same name. That is right for a
currency list and wrong for anything a site owns. These tests ENUMERATE the
directory rather than naming the files someone thought of.
"""
import ast
import glob
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(APP_DIR, "fixtures")

REFERENCE = {"Fiscal Period", "Dimension", "Measure", "Dataset", "Scenario",
             "ISO Currency", "Spread Profile", "Build Scope", "Build Model", "Pipeline"}
# fields that tie a row to a particular company's structure
ENTITY_FIELDS = {"data_area_id", "erp_data_area", "entity", "consolidation_group",
                 "parent_consolidation_group", "cycle", "sheet"}


def _shipped():
    out = {}
    for path in sorted(glob.glob(os.path.join(FIXTURES, "*.json"))):
        with open(path) as f:
            out[os.path.basename(path)] = json.load(f)
    return out


def _hooks_fixtures():
    """Parsed, not split on "]": a future {"dt": ..., "filters": [[...]]} entry
    would cut a text split short without any error."""
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        tree = ast.parse(f.read())
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == "fixtures" for t in n.targets))
    return [e if isinstance(e, str) else e.get("dt") for e in ast.literal_eval(node.value)]


def _meta(doctype):
    snake = doctype.lower().replace(" ", "_")
    paths = glob.glob(os.path.join(APP_DIR, "*", "doctype", snake, snake + ".json"))
    assert paths, f"no DocType JSON for {doctype}"
    with open(paths[0]) as f:
        return json.load(f)


def test_only_reference_doctypes_ship():
    shipped = {r.get("doctype") for rows in _shipped().values() for r in rows}
    assert shipped <= REFERENCE, f"not reference data: {sorted(shipped - REFERENCE)}"


def test_the_hook_and_the_directory_agree():
    """import reads the directory; export reads the hook. A name on one and not
    the other is a surprise waiting: Connector sat on the hook with no file,
    one `bench export-fixtures` away from committing site credentials."""
    listed = set(_hooks_fixtures())
    in_dir = {r.get("doctype") for rows in _shipped().values() for r in rows}
    assert listed == in_dir, f"hook only: {sorted(listed - in_dir)}; dir only: {sorted(in_dir - listed)}"


def test_no_shipped_row_belongs_to_a_company():
    offenders = [(name, r.get("name"), k) for name, rows in _shipped().items()
                 for r in rows for k in ENTITY_FIELDS if r.get(k)]
    assert not offenders, offenders[:10]


def test_every_link_in_a_shipped_row_resolves():
    """On an empty site the only records that exist are the ones shipped here,
    so a Link must point at a shipped row or be empty. Fixture import sets
    ignore_links, so a dangling one would load and then be unsaveable. Child
    rows are walked too, since that is where most shipped links live (Dataset's
    measures and dimensions)."""
    by_doctype = {}
    for rows in _shipped().values():
        for r in rows:
            by_doctype.setdefault(r["doctype"], set()).add(r["name"])
    dangling, checked = [], {"top": 0, "child": 0}

    def walk(doctype, row, where, depth):
        fields = _meta(doctype)["fields"]
        for f in fields:
            value = row.get(f["fieldname"])
            if f["fieldtype"] == "Link" and value:
                checked["child" if depth else "top"] += 1
                if value not in by_doctype.get(f["options"], set()):
                    dangling.append(f"{where}.{f['fieldname']} -> {f['options']} {value!r}")
            elif f["fieldtype"] in ("Table", "Table MultiSelect"):
                for i, child in enumerate(value or []):
                    walk(f["options"], child, f"{where}.{f['fieldname']}[{i}]", depth + 1)

    for rows in _shipped().values():
        for r in rows:
            walk(r["doctype"], r, f"{r['doctype']} {r['name']}", 0)
    assert not dangling, dangling[:10]
    assert checked["top"] and checked["child"], f"checked nothing real: {checked}"


def test_no_demo_seeding_survives():
    assert not os.path.exists(os.path.join(APP_DIR, "demo_data"))
    assert not os.path.exists(os.path.join(APP_DIR, "demo_seed_pipeline.py"))
    with open(os.path.join(APP_DIR, "install.py")) as f:
        after = f.read().split("def after_migrate")[1].split("\ndef ")[0]
    seeding = [l.strip() for l in after.splitlines()
               if l.strip().startswith("_bootstrap_") and not l.strip().startswith("#")]
    assert not seeding, seeding


def test_shipped_scenarios_are_generic():
    """ACTUAL / BUDGET / FORECAST are kinds of scenario; BUDGET_2024 and
    FORECAST_2024F7 were the demo's calendar."""
    names = [r["name"] for r in _shipped()["scenario.json"]]
    dated = [n for n in names if re.search(r"\d{4}", n)]
    assert not dated, dated
    assert {"ACTUAL", "BUDGET", "FORECAST"} <= set(names)
