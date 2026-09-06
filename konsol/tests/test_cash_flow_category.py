"""TDD tests for Cash Flow Category DocType (konsolidat#63)."""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _doctype_json():
    path = os.path.join(
        APP_DIR, "epm", "doctype", "cash_flow_category", "cash_flow_category.json"
    )
    with open(path) as f:
        return json.load(f)


def test_cash_flow_category_json_valid():
    doc = _doctype_json()
    assert doc["name"] == "Cash Flow Category"
    assert doc["module"] == "EPM"
    # Deliberately not "hash". konsol#68 moved this to a deterministic name so
    # fixture re-import is idempotent: the fixture names rows CFC-{main_account}
    # and validate() enforces one mapping per account, so a hash-named row made
    # re-import try to INSERT a duplicate and abort bench migrate.
    assert doc["autoname"] == "format:CFC-{main_account}"
    fields = {f["fieldname"]: f for f in doc["fields"]}
    for fn in ("main_account", "cf_category", "cf_line_item", "is_cash", "sign", "status"):
        assert fn in fields, f"missing field {fn}"
    assert fields["cf_category"]["options"] == "Operating\nInvesting\nFinancing"
    assert fields["status"]["options"] == "Draft\nPublished\nInactive"


def test_cash_flow_category_controller_lifecycle():
    """Publish/unpublish/after_delete + unique-account guard exist (mirrors Dimension Mapping)."""
    path = os.path.join(
        APP_DIR, "epm", "doctype", "cash_flow_category", "cash_flow_category.py"
    )
    with open(path) as f:
        src = f.read()
    for hook in ("def publish(", "def unpublish(", "def after_delete(",
                 "_validate_unique_account", "regenerate_cash_flow_categories_seed"):
        assert hook in src, f"missing {hook}"


def test_regenerator_defined_in_dbt_config():
    path = os.path.join(APP_DIR, "dbt_config.py")
    with open(path) as f:
        src = f.read()
    assert "def regenerate_cash_flow_categories_seed(" in src
    assert "_CASH_FLOW_CATEGORY_COLUMNS" in src


def test_fixture_seeds_demo_default():
    """The shipped mapping is a real chart of accounts, not a 12-row demo.

    Asserts the invariants rather than a row count: a count breaks every time
    an account is added, which tells you nothing, while these are the
    properties the fixture has to hold for import to work at all.
    """
    path = os.path.join(APP_DIR, "fixtures", "cash_flow_category.json")
    with open(path) as f:
        rows = json.load(f)

    assert rows, "fixture must not be empty"
    assert all(r["doctype"] == "Cash Flow Category" and r.get("name") for r in rows)
    assert all(r["status"] == "Published" for r in rows)

    # The konsol#68 invariant: fixture names must match the autoname format, or
    # re-import inserts duplicates instead of updating in place.
    assert all(r["name"] == f"CFC-{r['main_account']}" for r in rows)

    # validate() enforces one mapping per account; the fixture must not ship a
    # violation of its own guard.
    accounts = [r["main_account"] for r in rows]
    assert len(set(accounts)) == len(accounts), "duplicate main_account in fixture"

    # Values must be inside the Select options the doctype declares.
    assert {r["cf_category"] for r in rows} <= {"Operating", "Investing", "Financing"}
    assert {str(r["sign"]) for r in rows} <= {"1", "-1"}

    # Cash flow needs at least one account flagged as cash to reconcile against.
    assert sum(int(r["is_cash"]) for r in rows) >= 1
