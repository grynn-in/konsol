"""Budget Annual Input carries a required layer and a required currency (Q13-a).

A budget figure without a layer cannot be placed in a budget layer, and one
without a currency cannot be read against the functional currency. Both are
Link fields, so Frappe checks the value against the target doctype.
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAI_JSON = os.path.join(
    APP_DIR, "epm", "doctype", "budget_annual_input", "budget_annual_input.json"
)


def _field(fieldname):
    with open(BAI_JSON) as f:
        meta = json.load(f)
    matches = [f for f in meta["fields"] if f.get("fieldname") == fieldname]
    assert len(matches) == 1, f"expected exactly one {fieldname} field"
    return matches[0]


def test_layer_is_a_link_to_budget_layer():
    field = _field("layer")
    assert field["fieldtype"] == "Link"
    assert field["options"] == "Budget Layer"
    assert field["label"] == "Layer"


def test_layer_is_required():
    assert _field("layer")["reqd"] == 1


def test_currency_is_a_link_to_iso_currency():
    field = _field("currency")
    assert field["fieldtype"] == "Link"
    assert field["options"] == "ISO Currency"
    assert field["label"] == "Currency"


def test_currency_is_required():
    assert _field("currency")["reqd"] == 1


def test_layer_and_currency_follow_main_account():
    with open(BAI_JSON) as f:
        names = [f["fieldname"] for f in json.load(f)["fields"]]
    i = names.index("main_account")
    assert names[i + 1 : i + 3] == ["layer", "currency"]
