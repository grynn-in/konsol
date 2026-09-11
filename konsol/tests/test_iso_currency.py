"""The ISO 4217 list konsol owns (konsolidat#146).

It was seeds/currencies.csv — the last reference list the dbt project owned
outright. Not a collision like the other ten seeds (nothing else wrote it), but
a list the warehouse validates FX codes against belongs in the app.
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_DIR = os.path.join(APP_DIR, "epm", "doctype", "iso_currency")


def _meta():
    with open(os.path.join(DOCTYPE_DIR, "iso_currency.json")) as f:
        return json.load(f)


def _fixture():
    with open(os.path.join(APP_DIR, "fixtures", "iso_currency.json")) as f:
        return json.load(f)


def test_keyed_on_the_code_not_the_name():
    """Frappe's own Currency doctype is autonamed `field:currency_name`, so its
    primary key IS the display name — giving ANG its real name renames the
    record to "Netherlands Antillean Guilder". That is why konsol keeps its own
    list rather than enriching Frappe's."""
    meta = _meta()
    assert meta["autoname"] == "field:currency_code"
    code = next(f for f in meta["fields"] if f["fieldname"] == "currency_code")
    assert code.get("reqd") == 1 and code.get("unique") == 1


def test_writes_through_to_the_relation_the_warehouse_validates_against():
    with open(os.path.join(DOCTYPE_DIR, "iso_currency.py")) as f:
        src = f.read()
    assert 'CH_TABLE = "epm_gold.currencies"' in src
    for column in ("currency_code", "currency_name", "symbol", "minor_unit"):
        assert f'"{column}"' in src, column


def test_fixture_carries_the_whole_iso_list_the_seed_had():
    rows = _fixture()
    assert len(rows) == 66, "the deleted seed had 66 codes"
    codes = {r["currency_code"] for r in rows}
    # the six Frappe does not ship at all — the reason enriching Currency would
    # have lost data even if the naming worked
    for code in ("ANG", "AZN", "GEL", "TJS", "TMT", "XDR"):
        assert code in codes, code
    assert all(len(c) == 3 and c.isupper() for c in codes), sorted(codes)[:5]


def test_minor_unit_is_the_iso_exponent_not_frappes_fraction_units():
    """Frappe ships fraction_units = 100 for every currency including the
    zero-decimal ones; ISO 4217 states the exponent. JPY is 0, KWD is 3."""
    by_code = {r["currency_code"]: r for r in _fixture()}
    assert by_code["JPY"]["minor_unit"] == 0
    assert by_code["KWD"]["minor_unit"] == 3
    assert by_code["USD"]["minor_unit"] == 2


def test_shipped_as_a_fixture_and_registered():
    """Reference data, not transactional: unlike ownership, a site is not
    expected to edit it, so the force-reimport fixtures do on every migrate is
    the behaviour we want."""
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        hooks = f.read()
    fixtures = hooks.split("fixtures = [")[1].split("]")[0]
    entries = [line.strip() for line in fixtures.splitlines()
               if line.strip() and not line.strip().startswith("#")]
    assert '"ISO Currency",' in entries
