"""The link_historical_equity_rate_account migration patch (konsolidat#92), host-run.

`Historical Equity Rate.main_account` has become a Link to Main Account. The
stored value does not change — Main Account is named `field:main_account`, so
its name is the bare account code the warehouse joins on — but a value already
in the table that does not match a Main Account would now be rejected on the
next save, and until then it goes on silently missing the join and dropping the
account to the closing rate.

So existing values are mapped first: a value that matches a code once trimmed
and compared case-insensitively is rewritten to the spelling Main Account
stores, a blank is left alone, and anything else stops the migrate with every
offending value and the rates that carry it named.

`plan` is pure and tested without a site; `execute` runs under a stub frappe
that records every call over an in-memory rate table.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "link_historical_equity_rate_account.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
MODULE = "konsol.patches.link_historical_equity_rate_account"


class _Site:
    """An in-memory site: a rate table, the Main Account codes, and which
    tables exist at all."""

    def __init__(self, rates=(), accounts=("ZZ1000", "ZZ2000"), tables=None):
        #: [(rate name, main_account)]
        self.rates = [tuple(r) for r in rates]
        self.accounts = list(accounts)
        self.tables = set(("Historical Equity Rate", "Main Account")
                          if tables is None else tables)
        self.calls = []

    def module(self):
        site = self
        frappe = types.ModuleType("frappe")

        def table_exists(doctype):
            site.calls.append(("table_exists", doctype))
            return doctype in site.tables

        def sql(statement, *a, **k):
            site.calls.append(("sql", statement))
            return list(site.rates)

        def set_value(doctype, name, field, value, update_modified=True):
            site.calls.append(("set_value", doctype, name, field, value))
            site.rates = [(n, value if n == name else v) for n, v in site.rates]

        def commit():
            site.calls.append(("commit",))

        def get_all(doctype, pluck=None, **k):
            site.calls.append(("get_all", doctype, pluck))
            return list(site.accounts)

        frappe.db = types.SimpleNamespace(
            table_exists=table_exists, sql=sql, set_value=set_value, commit=commit)
        frappe.get_all = get_all
        return frappe


def _load(site=None):
    """Import the patch under ``site``'s stub frappe and hand back the module."""
    site = site or _Site()
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = site.module()
    try:
        spec = importlib.util.spec_from_file_location(
            "_link_her_account_under_test", PATCH_PY)
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        return patch
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


def _plan(rows, codes):
    return _load().plan(rows, codes)


def _writes(site):
    return [c for c in site.calls if c[0] == "set_value"]


def test_patch_module_exists():
    assert os.path.exists(PATCH_PY), PATCH_PY


# --- plan: pure, no site ---------------------------------------------------

def test_exact_code_is_left_alone():
    updates, unmapped = _plan([("HER-1", "ZZ1000")], ["ZZ1000", "ZZ2000"])
    assert updates == {}, updates
    assert unmapped == {}, unmapped


def test_untrimmed_lowercase_value_is_rewritten_to_the_stored_spelling():
    updates, unmapped = _plan([("HER-1", " zz1000 ")], ["ZZ1000"])
    assert updates == {"HER-1": "ZZ1000"}, updates
    assert unmapped == {}, unmapped


def test_rewritten_to_how_main_account_spells_it_not_to_upper_case():
    # The Link stores Main Account's own name, so the target is the stored
    # spelling — upper-casing would invent a code the table does not have.
    updates, unmapped = _plan([("HER-1", "zz1000")], ["Zz1000"])
    assert updates == {"HER-1": "Zz1000"}, updates
    assert unmapped == {}, unmapped


def test_unknown_code_is_unmapped_with_every_rate_that_carries_it():
    updates, unmapped = _plan(
        [("HER-1", "ZZ9999"), ("HER-2", "ZZ9999"), ("HER-3", "ZZ1000")],
        ["ZZ1000"])
    assert updates == {}, updates
    assert unmapped == {"ZZ9999": ["HER-1", "HER-2"]}, unmapped


def test_blank_and_null_values_are_skipped():
    updates, unmapped = _plan(
        [("HER-1", ""), ("HER-2", None), ("HER-3", "   ")], ["ZZ1000"])
    assert updates == {}, updates
    assert unmapped == {}, unmapped


# --- execute: stub frappe --------------------------------------------------

def test_no_rate_table_means_no_work():
    site = _Site(rates=[("HER-1", "zz1000")], tables=["Main Account"])
    _load(site).execute()
    assert _writes(site) == [], site.calls
    assert not [c for c in site.calls if c[0] == "sql"], site.calls


def test_clean_site_rewrites_and_commits():
    site = _Site(rates=[("HER-1", " zz1000 "), ("HER-2", "ZZ2000")])
    _load(site).execute()
    assert _writes(site) == [
        ("set_value", "Historical Equity Rate", "HER-1", "main_account", "ZZ1000")
    ], site.calls
    assert ("commit",) in site.calls, site.calls
    assert dict(site.rates) == {"HER-1": "ZZ1000", "HER-2": "ZZ2000"}, site.rates


def test_unmapped_values_stop_the_migrate_and_are_named():
    site = _Site(rates=[("HER-1", "ZZ9999"), ("HER-2", "zz1000")])
    patch = _load(site)
    try:
        patch.execute()
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("an unmapped account must stop the migrate")
    assert "ZZ9999" in message, message
    assert "HER-1" in message, message
    assert "Main Account" in message, message
    # Nothing is rewritten, so the migrate can be re-run after the fix.
    assert _writes(site) == [], site.calls
    assert dict(site.rates)["HER-2"] == "zz1000", site.rates


def test_second_run_changes_nothing():
    site = _Site(rates=[("HER-1", " zz1000 ")])
    _load(site).execute()
    site.calls.clear()
    _load(site).execute()
    assert _writes(site) == [], site.calls


def test_listed_once_after_the_rate_rekey_patch():
    """Registered exactly once, and after the patch that rewrites the same keys.

    `rekey_historical_equity_rate_to_group_corp` rewrites the rate table's keys,
    so this mapping must see the rows in their final shape. What must NOT be
    asserted is that this module is the last line of patches.txt: every future
    patch is appended there, and this one is guarded and idempotent, so what
    runs after it is none of this test's business.
    """
    with open(PATCHES_TXT) as f:
        lines = [l.strip() for l in f if l.strip()]
    assert lines.count(MODULE) == 1, lines.count(MODULE)
    mine = lines.index(MODULE)
    earlier = "konsol.patches.rekey_historical_equity_rate_to_group_corp"
    assert lines.index(earlier) < mine, (lines.index(earlier), mine)
