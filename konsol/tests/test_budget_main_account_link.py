"""Budget Annual Input.main_account is a Link to Main Account, not free text.

A Data field lets any string into the budget row, so a typo becomes a budget
for an account that does not exist. Linking it to Main Account makes Frappe
validate the value against the chart of accounts.
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAI_JSON = os.path.join(
    APP_DIR, "epm", "doctype", "budget_annual_input", "budget_annual_input.json"
)


def _main_account_field():
    with open(BAI_JSON) as f:
        meta = json.load(f)
    matches = [f for f in meta["fields"] if f.get("fieldname") == "main_account"]
    assert len(matches) == 1, "expected exactly one main_account field"
    return matches[0]


def test_main_account_is_a_link_field():
    assert _main_account_field()["fieldtype"] == "Link"


def test_main_account_links_to_the_main_account_doctype():
    assert _main_account_field()["options"] == "Main Account"
