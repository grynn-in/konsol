"""My-work model, pure: konsol/close/mywork_model.py (konsol#305 A14; stories 1.2, 0.4).

One configuration gap is ONE item listing the affected entities (#291: never
one item per entity per month). Loaded by path; imports nothing from frappe.
"""
import ast
import importlib.util
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "close", "mywork_model.py")
_spec = importlib.util.spec_from_file_location("close_mywork_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _facts(**over):
    facts = {
        "first_close": (2025, 7),
        "chart_published": True,
        "frequency_missing": [],
        "ownership_missing": [],
        "accountants_without_entities": [],
    }
    facts.update(over)
    return facts


def _by_id(items):
    return {i["id"]: i for i in items}


def test_nothing_missing_gives_no_items():
    assert M.setup_gap_items(_facts()) == []


def test_three_entities_without_ownership_is_one_item():
    items = M.setup_gap_items(_facts(ownership_missing=["FR01", "DE01", "US01"]))
    assert [i["id"] for i in items] == ["gap:ownership"]
    item = items[0]
    assert item["entities"] == ["DE01", "FR01", "US01"]
    assert "3 entities" in item["title"]
    assert item["action"] == {"desk": "/app/ownership-period"}


def test_one_entity_title_is_singular():
    item = M.setup_gap_items(_facts(ownership_missing=["FR01"]))[0]
    assert "1 entity" in item["title"]
    assert "entities" not in item["title"]


def test_duplicate_entities_listed_once():
    item = M.setup_gap_items(_facts(frequency_missing=["FR01", "FR01", "DE01"]))[0]
    assert item["entities"] == ["DE01", "FR01"]


def test_empty_lists_give_no_item():
    ids = [i["id"] for i in M.setup_gap_items(_facts(frequency_missing=[], ownership_missing=[],
                                                      accountants_without_entities=[]))]
    assert ids == []


def test_every_gap_present_ids_stable_and_ordered():
    facts = _facts(first_close=None, chart_published=False, frequency_missing=["FR01"],
                   ownership_missing=["DE01"], accountants_without_entities=["zz-a@example.com"])
    items = M.setup_gap_items(facts)
    assert [i["id"] for i in items] == [
        "gap:first_close", "gap:chart", "gap:frequency", "gap:ownership", "gap:accountants"]
    # Same input, same output: ids never depend on order or content.
    assert M.setup_gap_items(dict(facts)) == items


def test_desk_actions_point_at_configuration():
    items = _by_id(M.setup_gap_items(_facts(
        first_close=None, chart_published=False, frequency_missing=["FR01"],
        ownership_missing=["DE01"], accountants_without_entities=["zz-a@example.com"])))
    assert items["gap:first_close"]["action"] == {"desk": "/app/close-settings"}
    assert items["gap:chart"]["action"] == {"desk": "/app/main-account"}
    assert items["gap:frequency"]["action"] == {"desk": "/app/entity"}
    assert items["gap:ownership"]["action"] == {"desk": "/app/ownership-period"}
    assert items["gap:accountants"]["action"] == {"desk": "/app/user"}
    assert items["gap:accountants"]["users"] == ["zz-a@example.com"]
    assert items["gap:accountants"]["entities"] == []


def test_every_gap_is_blocking_with_an_owner_role():
    items = M.setup_gap_items(_facts(
        first_close=None, chart_published=False, frequency_missing=["FR01"],
        ownership_missing=["DE01"], accountants_without_entities=["zz-a@example.com"]))
    for item in items:
        assert item["kind"] == "blocking"
        assert item["owner"] in ("EPM Admin", "System Manager")
        assert item["title"] and item["detail"]


def test_first_close_missing_is_an_item_even_when_everything_else_is_fine():
    items = M.setup_gap_items(_facts(first_close=None))
    assert [i["id"] for i in items] == ["gap:first_close"]
    assert "Close Settings" in items[0]["detail"]
    assert items[0]["owner"] == "EPM Admin"


def test_first_close_zero_key_is_undeclared():
    # Close Settings Int fields read back as 0 when unset (COORDINATOR NOTE A06).
    for key in ((0, 0), (2025, 0), (0, 7)):
        assert [i["id"] for i in M.setup_gap_items(_facts(first_close=key))] == ["gap:first_close"]


def test_missing_fact_key_raises_not_guessed():
    facts = _facts()
    del facts["ownership_missing"]
    with pytest.raises(ValueError, match="ownership_missing"):
        M.setup_gap_items(facts)


def test_chart_published_must_be_bool():
    with pytest.raises(ValueError, match="chart_published"):
        M.setup_gap_items(_facts(chart_published=None))


def test_module_imports_no_frappe():
    with open(_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] in ("frappe", "konsol") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("frappe", "konsol")
