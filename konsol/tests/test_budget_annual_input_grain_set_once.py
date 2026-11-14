"""An approved Cycle's Annual Input cannot be moved out of it: the grain
fields are set-once, so changing one is a delete plus a create (Q21 side
fixes, konsol#348). The fields are the ones _validate_unique_grain filters on
(the dim_* fields are the site's own, made by the Dimension doctype)."""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(APP_DIR, "epm", "doctype", "budget_annual_input")

GRAIN = ["scenario_id", "fiscal_year", "data_area_id", "layer", "main_account"]


def _fields():
    with open(os.path.join(BASE, "budget_annual_input.json")) as f:
        return {fld["fieldname"]: fld for fld in json.load(f)["fields"]}


def test_every_grain_field_is_set_only_once():
    fields = _fields()
    assert [g for g in GRAIN if not fields[g].get("set_only_once")] == []


def test_the_pinned_grain_is_the_one_the_unique_check_filters_on():
    with open(os.path.join(BASE, "budget_annual_input.py")) as f:
        source = f.read()
    start = source.index("def _validate_unique_grain")
    body = source[start:source.index("grain.update", start)]
    used = {name for name in _fields() if f'"{name}"' in body}
    assert used == set(GRAIN), used
