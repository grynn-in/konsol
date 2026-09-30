"""konsol#255 — Dimension declares its trial-balance role.

Decision of 22 September 2026 (Deepak Pai), option #255-3: whether a dimension
survives the year-end close is DECLARED per dimension, not assumed. Two Check
fields carry that declaration, and both default OFF so nothing is assumed for
a customer who has not said.

Pure JSON-reading tests — no frappe import, so they run on the host.
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIMENSION_JSON = os.path.join(APP_DIR, "epm", "doctype", "dimension", "dimension.json")


def _load_dimension():
    with open(DIMENSION_JSON) as f:
        return json.load(f)


def _field(fieldname):
    meta = _load_dimension()
    for field in meta["fields"]:
        if field["fieldname"] == fieldname:
            return field
    return None


def _fieldnames():
    return [f["fieldname"] for f in _load_dimension()["fields"]]


# --- in_trial_balance: a file may carry a dim_<name> column for this dimension ---

def test_in_trial_balance_field_exists():
    assert "in_trial_balance" in _fieldnames(), (
        "Dimension must declare in_trial_balance — a trial balance file may only "
        "carry a dim_<name> column for a dimension that declared it"
    )


def test_in_trial_balance_is_a_check():
    field = _field("in_trial_balance")
    assert field is not None, "in_trial_balance missing from Dimension"
    assert field["fieldtype"] == "Check", f"in_trial_balance is {field['fieldtype']}, not Check"


def test_in_trial_balance_label():
    field = _field("in_trial_balance")
    assert field is not None, "in_trial_balance missing from Dimension"
    assert field["label"] == "Include in Trial Balance"


def test_in_trial_balance_defaults_off():
    """No dimension reaches the trial balance unless a customer declares it.

    The key must be PRESENT and "0". `field.get("default", "0")` passed when
    the key was deleted outright — the one mutation this test exists to catch
    — because an absent key and an explicit "0" then look identical. An absent
    key leaves the policy to whatever Frappe does with a Check that declares
    nothing; declared OFF is not the same thing as undeclared.
    """
    field = _field("in_trial_balance")
    assert field is not None, "in_trial_balance missing from Dimension"
    assert "default" in field, (
        "in_trial_balance must carry an explicit default — deleting the key "
        "leaves the policy undeclared"
    )
    assert str(field["default"]) == "0", (
        "in_trial_balance must default OFF — an ON default would be a silent policy"
    )


# --- survives_close: the year-end close keeps this value on retained earnings ---

def test_survives_close_field_exists():
    assert "survives_close" in _fieldnames(), (
        "Dimension must declare survives_close — whether the year-end close keeps "
        "this dimension's value on the retained-earnings row is declared, not assumed"
    )


def test_survives_close_is_a_check():
    field = _field("survives_close")
    assert field is not None, "survives_close missing from Dimension"
    assert field["fieldtype"] == "Check", f"survives_close is {field['fieldtype']}, not Check"


def test_survives_close_label():
    field = _field("survives_close")
    assert field is not None, "survives_close missing from Dimension"
    assert field["label"] == "Survives Year-End Close"


def test_survives_close_defaults_off():
    """P&L closes to a retained-earnings row outside the dimension unless declared.

    Key present and "0", for the reason given on
    test_in_trial_balance_defaults_off: a `.get` fallback cannot tell a
    deleted key from a declared OFF.
    """
    field = _field("survives_close")
    assert field is not None, "survives_close missing from Dimension"
    assert "default" in field, (
        "survives_close must carry an explicit default — deleting the key "
        "leaves the policy undeclared"
    )
    assert str(field["default"]) == "0", (
        "survives_close must default OFF — an ON default would be a silent policy"
    )


def test_survives_close_depends_on_in_trial_balance():
    """The form must not offer a declaration that cannot apply."""
    field = _field("survives_close")
    assert field is not None, "survives_close missing from Dimension"
    depends_on = field.get("depends_on")
    assert depends_on, "survives_close must carry a depends_on on in_trial_balance"
    assert "in_trial_balance" in depends_on, (
        f"survives_close depends_on is {depends_on!r}, which does not reference in_trial_balance"
    )


# --- placement: next to in_budget, the declaration that already exists ---

def test_tb_flags_sit_next_to_in_budget():
    names = _fieldnames()
    for fieldname in ("in_budget", "in_trial_balance", "survives_close"):
        assert fieldname in names, f"{fieldname} missing from Dimension"
    budget_at = names.index("in_budget")
    assert names.index("in_trial_balance") == budget_at + 1, (
        "in_trial_balance must sit immediately after in_budget"
    )
    assert names.index("survives_close") == budget_at + 2, (
        "survives_close must sit immediately after in_trial_balance"
    )


def test_dimension_json_has_no_field_order():
    """This doctype orders by the fields list alone — adding field_order would fork it."""
    assert "field_order" not in _load_dimension()
