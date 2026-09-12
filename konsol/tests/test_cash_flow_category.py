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
    """Publish/unpublish/after_delete come from the shared base (mirrors
    Dimension Mapping); the controller keeps only its own unique-account guard.

    Each controller used to carry its own copies of the sync call, and those
    copies disagreed with reconcile_all — which re-filled this table with Draft
    and Inactive rows on every migrate, with no consumer filtering status.
    """
    path = os.path.join(
        APP_DIR, "epm", "doctype", "cash_flow_category", "cash_flow_category.py"
    )
    with open(path) as f:
        src = f.read()
    assert "from konsol.governed_reference import GovernedReferenceDocument" in src
    assert "class CashFlowCategory(GovernedReferenceDocument)" in src
    assert "_validate_unique_account" in src
    for copied in ("def publish(", "def unpublish(", "def after_delete(",
                   "sync_doctype_filtered("):
        assert copied not in src, f"{copied} should come from the base class"
    # F3: Published rows write through to the warehouse; no CSV seed is written
    assert 'CH_TABLE = "epm_staging.cash_flow_categories"' in src
    assert 'CH_SYNC_FILTERS = {"status": "Published"}' in src
    assert "regenerate_cash_flow_categories_seed" not in src


def test_seed_regenerator_is_gone_from_dbt_config():
    """F3: Frappe no longer writes CSV seeds into the dbt repo."""
    path = os.path.join(APP_DIR, "dbt_config.py")
    with open(path) as f:
        src = f.read()
    assert "regenerate_cash_flow_categories_seed" not in src
    assert "cash_flow_categories.csv" not in src


