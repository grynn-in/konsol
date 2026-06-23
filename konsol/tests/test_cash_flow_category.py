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
    assert doc["autoname"] == "hash"
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
    path = os.path.join(APP_DIR, "fixtures", "cash_flow_category.json")
    with open(path) as f:
        rows = json.load(f)
    assert len(rows) == 12
    assert all(r["doctype"] == "Cash Flow Category" and r.get("name") for r in rows)
    assert all(r["status"] == "Published" for r in rows)
    # exactly one cash account flagged
    assert sum(int(r["is_cash"]) for r in rows) == 1
