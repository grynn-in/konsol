"""konsol#230: nothing konsol ships may be un-retirable.

`konsol/fixtures/` is force-reimported on EVERY `bench migrate`, so a row there
overwrites whatever a site holds under the same name. Right for product
structure, wrong for a semantic model: a site could unpublish a Dataset,
deactivate a Scenario or retire a Spread Profile, and the next migrate put it
back, Published.

Decision (Deepak Pai, 18 September 2026, konsol#230): **option A — the semantic
model is site-owned.** Measure, Dataset and Scenario are seeded
**create-if-missing** from `konsol/defaults/`, the mechanism
`workflows.install_workflows()` already uses for the reason its own docstring
gives: *"so a site may customise them."* Dimension and Spread Profile ship
nothing at all (Dimension decided separately, 17 September 2026).

The two rules it expresses, and which these tests hold:

  1. Nothing that ships is un-retirable.
  2. Nothing that ships creates a table in the customer's warehouse.

These enumerate the directories rather than naming files someone thought of —
the idiom `test_fixtures_reference_only.py` already uses, for the same reason:
a new file must not slip in unasserted.
"""
import ast
import glob
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(APP_DIR, "fixtures")
DEFAULTS = os.path.join(APP_DIR, "defaults")
DEFAULTS_PY = os.path.join(APP_DIR, "defaults.py")

#: Force-reimported every migrate, so only product structure may live here.
IMMUTABLE_DOCTYPES = {"Build Scope", "Build Model", "Pipeline"}
#: Seeded create-if-missing: a site may retire any of these and keep them retired.
SEEDED_DOCTYPES = {"Measure", "Dataset", "Scenario"}
#: Ships nothing at all.
SHIPS_NOTHING = {"Dimension", "Spread Profile", "Fiscal Period"}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _rows(directory):
    out = {}
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        with open(path, encoding="utf-8") as f:
            out[os.path.basename(path)] = json.load(f)
    return out


def _doctypes(directory):
    seen = set()
    for rows in _rows(directory).values():
        for row in rows:
            seen.add(row["doctype"])
    return seen


# --- rule 1: nothing that ships is un-retirable ----------------------------

def test_the_semantic_model_left_fixtures():
    """The whole point: these were force-reimported, so a site could not turn
    them off."""
    shipped = _doctypes(FIXTURES)
    for doctype in SEEDED_DOCTYPES:
        assert doctype not in shipped, (
            f"{doctype} is still force-reimported on every migrate; "
            "a site cannot retire it")


def test_fixtures_hold_product_structure_only():
    assert _doctypes(FIXTURES) == IMMUTABLE_DOCTYPES, (
        "fixtures/ must hold exactly the product structure: "
        f"{sorted(IMMUTABLE_DOCTYPES)}")


def test_nothing_ships_that_should_ship_nothing():
    """Dimension (decided 17 Sep 2026), Spread Profile, and the Fiscal Period
    template retired by konsol#189 PR4."""
    shipped = _doctypes(FIXTURES) | _doctypes(DEFAULTS)
    for doctype in SHIPS_NOTHING:
        assert doctype not in shipped, f"{doctype} still ships rows"
    for stale in ("dimension.json", "fiscal_period.json", "spread_profile.json"):
        assert not os.path.exists(os.path.join(FIXTURES, stale)), \
            f"fixtures/{stale} should be deleted"
        assert not os.path.exists(os.path.join(DEFAULTS, stale)), \
            f"defaults/{stale} should not exist either"


def test_hooks_fixtures_list_matches_the_directory():
    """The list only matters for export, but a stale entry exports rows that no
    longer ship and reads as a contradiction."""
    tree = ast.parse(_read(os.path.join(APP_DIR, "hooks.py")))
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == "fixtures" for t in n.targets))
    listed = {e if isinstance(e, str) else e.get("dt")
              for e in ast.literal_eval(node.value)}
    assert listed == IMMUTABLE_DOCTYPES, f"hooks.fixtures still lists {sorted(listed)}"


def test_no_row_name_is_in_both_directories():
    """A name in both would be seeded and then overwritten — the bug, restored."""
    fixture_names = {(r["doctype"], r["name"])
                     for rows in _rows(FIXTURES).values() for r in rows}
    default_names = {(r["doctype"], r["name"])
                     for rows in _rows(DEFAULTS).values() for r in rows}
    # Both must be non-empty, or an empty intersection proves nothing: this
    # test would pass simply because konsol/defaults/ did not exist yet.
    assert fixture_names, "fixtures/ is empty"
    assert default_names, "defaults/ is empty"
    assert not (fixture_names & default_names), fixture_names & default_names


# --- the seeder ------------------------------------------------------------

def test_defaults_directory_has_the_semantic_model():
    assert os.path.isdir(DEFAULTS), "konsol/defaults/ is missing"
    assert _doctypes(DEFAULTS) == SEEDED_DOCTYPES, (
        f"defaults/ must hold exactly {sorted(SEEDED_DOCTYPES)}")


def test_install_defaults_is_create_if_missing():
    """Parsed, not trusted: the guard must be there, and it must guard the
    insert. Without it this is fixtures again, by another name."""
    src = _read(DEFAULTS_PY)
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "install_defaults"), None)
    assert fn is not None, "no install_defaults()"
    body = ast.dump(fn)
    assert "exists" in body, "nothing checks whether the row is already there"
    assert "insert" in body, "nothing inserts"
    # the existence check must come before the insert, textually within the fn
    seg = src[src.index("def install_defaults"):]
    assert seg.index("exists") < seg.index("insert"), \
        "the insert is not guarded by the existence check"


def test_the_seeder_never_overwrites():
    """No update/save path: the difference between seeding and fixtures."""
    seg = _read(DEFAULTS_PY)
    seg = seg[seg.index("def install_defaults"):]
    for forbidden in (".save(", "db_update", "set_value", "overwrite"):
        assert forbidden not in seg, f"install_defaults uses {forbidden}"


def test_the_seeder_is_wired_into_install_and_migrate():
    """A fresh install never runs after_migrate, so both are needed — the same
    reason create_roles and install_workflows are in both."""
    hooks = _read(os.path.join(APP_DIR, "hooks.py"))
    install = _read(os.path.join(APP_DIR, "install.py"))
    assert "install_defaults" in hooks or "install_defaults" in install, \
        "install_defaults is never called"
    assert "install_defaults" in hooks, "after_install does not seed the defaults"
    seg = install[install.index("def after_migrate"):]
    assert "defaults" in seg, "after_migrate does not seed the defaults"


# --- rule 2: nothing that ships creates a warehouse table -------------------

def test_nothing_shipped_generates_a_source_table():
    """`generates_source = 1` materialises a table in the customer's ClickHouse
    whether they want it or not. The three driver samples that did this left
    with the allocation removal (konsol#264); nothing may reintroduce it."""
    for directory in (FIXTURES, DEFAULTS):
        for filename, rows in _rows(directory).items():
            for row in rows:
                assert not row.get("generates_source"), (
                    f"{filename}: {row.get('name')} generates a warehouse table")


# --- the silent fallback the decision named --------------------------------

def test_trial_balance_measures_refuse_rather_than_invent():
    """Condition 2 of the decision. `_trial_balance_measure_names()` had three
    returns of `_DEFAULT_BASE_MEASURE_NAMES`: a site that deliberately retires
    the trial-balance Dataset got four measures nobody declared, silently
    reinstated into dbt_project.yml. Now that Datasets ARE retirable, that
    fallback is reachable on purpose rather than by accident."""
    src = _read(os.path.join(APP_DIR, "dbt_config.py"))
    seg = src[src.index("def _trial_balance_measure_names"):]
    seg = seg[:seg.index("\ndef ")]
    assert "_DEFAULT_BASE_MEASURE_NAMES" not in seg, \
        "the silent fallback is still there"
    assert "frappe.throw" in seg or "raise" in seg, \
        "nothing refuses; it must name what is missing instead of inventing measures"
