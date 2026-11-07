"""The fill_historical_rate_to_currency patch (konsol#374), host-run.

`Historical Equity Rate.to_currency` was a virtual field computed from the
root node of the rate's consolidation group; it is stored and required now,
and part of the document name. Every existing rate gets exactly the value the
virtual field computed (the root's reporting currency), and the new digest
name, so nothing about existing data changes except that it is written down.

`plan` is pure and tested without a site; `execute` runs under a stub frappe
that records every call. A group whose root has no reporting currency is
reported and stops the migrate: guessing a currency would invent a rate.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "fill_historical_rate_to_currency.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
GRAIN_PY = os.path.join(APP_DIR, "epm", "budget_grain.py")
MODULE = "konsol.patches.fill_historical_rate_to_currency"


def _digest_name():
    """The real digest_name. budget_grain imports frappe at module level and
    uses it only in other functions, so a bare stub module satisfies the import."""
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = types.ModuleType("frappe")
    try:
        spec = importlib.util.spec_from_file_location("budget_grain_for_patch_test", GRAIN_PY)
        grain = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(grain)
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    return grain.digest_name


class _Dict(dict):
    """frappe._dict: a dict with attribute read, which is what get_all returns.
    PR #436 review: the patch reads rows as dicts only; the stub hands back
    the same shape so a `.attr`-only row could not pass here."""
    __getattr__ = dict.get


class _Site:
    """An in-memory site: the rate rows, the group roots' currencies."""

    def __init__(self, rates=(), roots=None, tables=("Historical Equity Rate", "Consolidation Group")):
        #: rows as frappe.get_all returns them
        self.rates = [dict(r) for r in rates]
        #: {group code: reporting_currency or None}
        self.roots = dict(roots or {})
        self.tables = set(tables)
        self.calls = []

    def module(self):
        site = self
        frappe = types.ModuleType("frappe")

        def table_exists(doctype):
            site.calls.append(("table_exists", doctype))
            return doctype in site.tables

        def reload_doc(module, kind, name):
            site.calls.append(("reload_doc", module, kind, name))

        def get_all(doctype, filters=None, fields=None, pluck=None, **k):
            site.calls.append(("get_all", doctype, filters, fields))
            if doctype == "Historical Equity Rate":
                return [_Dict(r) for r in site.rates]
            if doctype == "Consolidation Group":
                return [_Dict(consolidation_group=g, reporting_currency=c)
                        for g, c in site.roots.items()]
            raise AssertionError(doctype)

        def set_value(doctype, name, field, value, update_modified=True):
            site.calls.append(("set_value", doctype, name, field, value, update_modified))
            for r in site.rates:
                if r["name"] == name:
                    r[field] = value

        def exists(doctype, name):
            site.calls.append(("exists", doctype, name))
            return any(r["name"] == name for r in site.rates)

        def rename_doc(doctype, old, new, force=False, show_alert=True, **k):
            site.calls.append(("rename_doc", doctype, old, new, force))
            for r in site.rates:
                if r["name"] == old:
                    r["name"] = new

        def throw(msg, exc=None, **k):
            raise _Stopped(msg)

        frappe.db = types.SimpleNamespace(
            table_exists=table_exists, set_value=set_value, exists=exists)
        frappe.get_all = get_all
        frappe.reload_doc = reload_doc
        frappe.rename_doc = rename_doc
        frappe.throw = throw
        frappe.ValidationError = _Stopped
        return frappe


class _Stopped(Exception):
    pass


def _load(site=None):
    """Import the patch under ``site``'s stub frappe. budget_grain is stubbed
    with the REAL digest_name read from its file, so the names the patch
    computes are the controller's, without importing the konsol package
    (whose __init__ needs frappe)."""
    site = site or _Site()
    stubs = {
        "frappe": site.module(),
        "konsol": types.ModuleType("konsol"),
        "konsol.epm": types.ModuleType("konsol.epm"),
        "konsol.epm.budget_grain": types.SimpleNamespace(digest_name=_digest_name()),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("_fill_her_to_currency_under_test", PATCH_PY)
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        return patch
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _row(name, group="ZZGRP", entity="ZZNL", account="ZZ3000", date="2024-01-01", to_currency=None):
    return {"name": name, "consolidation_group": group, "data_area_id": entity,
            "main_account": account, "rate_date": date, "to_currency": to_currency}


def _done(group="ZZGRP", entity="ZZNL", account="ZZ3000", date="2024-01-01", to_currency="USD"):
    """A row the patch has nothing to do for: filled, and named by the 5-tuple."""
    name = _digest_name()("HER", [group, entity, account, date, to_currency])
    return _row(name, group, entity, account, date, to_currency)


# --- plan: pure --------------------------------------------------------------

def test_a_blank_to_currency_gets_the_roots_currency_and_the_new_name():
    patch = _load()
    digest = _digest_name()
    updates, problems = patch.plan([_row("HER-old")], {"ZZGRP": "USD"})
    assert problems == {}
    assert updates == [("HER-old", "USD",
                        digest("HER", ["ZZGRP", "ZZNL", "ZZ3000", "2024-01-01", "USD"]))]


def test_a_filled_and_correctly_named_row_is_left_alone_so_a_second_run_writes_nothing():
    patch = _load()
    updates, problems = patch.plan([_done()], {"ZZGRP": "USD"})
    assert (updates, problems) == ([], {})


def test_a_filled_row_with_the_old_name_is_still_renamed():
    """PR #436 review: a blank To Currency was the only "not migrated" marker,
    so a row filled by hand (or by a run that stopped before its rename) kept
    the 4-tuple digest name for ever. The name shape is a marker too; the
    stored currency, not the root's, is what the new name digests."""
    patch = _load()
    digest = _digest_name()
    old_name = digest("HER", ["ZZGRP", "ZZNL", "ZZ3000", "2024-01-01"])
    updates, problems = patch.plan([_row(old_name, to_currency="EUR")], {"ZZGRP": "USD"})
    assert problems == {}
    assert updates == [(old_name, None,
                        digest("HER", ["ZZGRP", "ZZNL", "ZZ3000", "2024-01-01", "EUR"]))]


def test_a_filled_row_needs_no_root_currency():
    """The root's currency is only for filling: a filled row under a group
    whose root has none is renamed, not reported."""
    patch = _load()
    old_name = _digest_name()("HER", ["ZZNONE", "ZZNL", "ZZ3000", "2024-01-01"])
    updates, problems = patch.plan([_row(old_name, group="ZZNONE", to_currency="EUR")], {})
    assert problems == {} and [u[0] for u in updates] == [old_name]


def test_plan_reads_rows_as_dicts_only():
    """PR #436 review: frappe.get_all returns _dict (a dict), so the
    attribute-access fallback supported a row shape no caller produces."""
    src = open(PATCH_PY).read()
    assert "getattr(" not in src and "isinstance(row" not in src, "dict access only"
    patch = _load()
    updates, _ = patch.plan([dict(_row("HER-plain"))], {"ZZGRP": "USD"})
    assert updates and updates[0][0] == "HER-plain"


def test_a_group_without_a_root_currency_is_reported_not_guessed():
    patch = _load()
    updates, problems = patch.plan(
        [_row("HER-a"), _row("HER-b", group="ZZOTHER"), _row("HER-c", group="ZZNONE")],
        {"ZZGRP": "USD", "ZZOTHER": None})
    assert [u[0] for u in updates] == ["HER-a"]
    assert problems == {"ZZOTHER": ["HER-b"], "ZZNONE": ["HER-c"]}


def test_the_rate_date_is_digested_as_the_controller_digests_it():
    """autoname digests str(rate_date); frappe.get_all hands back a date
    object, which must become the same ISO string or the name differs from
    what a resave would give."""
    import datetime
    patch = _load()
    digest = _digest_name()
    updates, _ = patch.plan([_row("HER-d", date=datetime.date(2024, 1, 1))], {"ZZGRP": "USD"})
    assert updates[0][2] == digest("HER", ["ZZGRP", "ZZNL", "ZZ3000", "2024-01-01", "USD"])


# --- execute: under a stub site ------------------------------------------------

def test_execute_reloads_the_doctype_then_sets_and_renames_each_blank_row():
    done = _done(entity="ZZDE")
    site = _Site(rates=[_row("HER-old"), done], roots={"ZZGRP": "USD"})
    patch = _load(site)
    patch.execute()
    kinds = [c[0] for c in site.calls]
    assert kinds.index("reload_doc") < kinds.index("get_all"), "the column must exist first"
    assert ("reload_doc", "consolidation", "doctype", "historical_equity_rate") in site.calls
    sets = [c for c in site.calls if c[0] == "set_value"]
    assert sets == [("set_value", "Historical Equity Rate", "HER-old", "to_currency", "USD", False)]
    renames = [c for c in site.calls if c[0] == "rename_doc"]
    assert len(renames) == 1 and renames[0][2] == "HER-old" and renames[0][4] is True
    assert renames[0][3].startswith("HER-ZZGRP-ZZNL-ZZ3000-2024_01_01-USD-"), renames[0][3]
    # the already-filled, correctly named row was not touched
    assert not [c for c in site.calls if c[0] in ("set_value", "rename_doc") and c[2] == done["name"]]


def test_execute_renames_a_filled_row_with_the_old_name_without_rewriting_its_currency():
    digest = _digest_name()
    old_name = digest("HER", ["ZZGRP", "ZZNL", "ZZ3000", "2024-01-01"])
    site = _Site(rates=[_row(old_name, to_currency="EUR")], roots={"ZZGRP": "USD"})
    patch = _load(site)
    patch.execute()
    assert not [c for c in site.calls if c[0] == "set_value"], "the stored currency stands"
    renames = [c for c in site.calls if c[0] == "rename_doc"]
    assert renames == [("rename_doc", "Historical Equity Rate", old_name,
                        digest("HER", ["ZZGRP", "ZZNL", "ZZ3000", "2024-01-01", "EUR"]), True)]
    site.calls.clear()
    patch.execute()
    assert not [c for c in site.calls if c[0] in ("set_value", "rename_doc")], "idempotent"


def test_execute_is_idempotent():
    site = _Site(rates=[_row("HER-old")], roots={"ZZGRP": "USD"})
    patch = _load(site)
    patch.execute()
    site.calls.clear()
    patch.execute()
    assert not [c for c in site.calls if c[0] in ("set_value", "rename_doc")], site.calls


def test_execute_stops_naming_the_group_and_rates_it_cannot_fill():
    site = _Site(rates=[_row("HER-x", group="ZZNONE")], roots={"ZZGRP": "USD"})
    patch = _load(site)
    try:
        patch.execute()
    except _Stopped as e:
        assert "ZZNONE" in str(e) and "HER-x" in str(e), e
    else:
        raise AssertionError("a rate whose group has no currency must stop the migrate")
    assert not [c for c in site.calls if c[0] in ("set_value", "rename_doc")]


def test_execute_refuses_to_rename_onto_an_existing_document():
    """Two blank rows that digest to one name would be the same tranche twice;
    overwriting one with the other would lose a rate. Stop and name both."""
    digest = _digest_name()
    taken = digest("HER", ["ZZGRP", "ZZNL", "ZZ3000", "2024-01-01", "USD"])
    site = _Site(rates=[_row("HER-old"), _row(taken, to_currency="USD")], roots={"ZZGRP": "USD"})
    patch = _load(site)
    try:
        patch.execute()
    except _Stopped as e:
        assert "HER-old" in str(e) and taken in str(e), e
    else:
        raise AssertionError("renaming onto an existing document must stop the migrate")


def test_execute_refuses_two_planned_rows_that_digest_to_one_name_naming_both():
    """PR #436 review: the pre-check asked frappe.db.exists(new_name) against
    pre-existing rows only, so two blank rows of one tranche both passed, the
    first rename took the name and the second rename_doc failed mid-loop with
    Frappe's generic message. Refused before any write, naming both rows."""
    site = _Site(rates=[_row("HER-first"), _row("HER-second"), _row("HER-fine", entity="ZZDE")],
                 roots={"ZZGRP": "USD"})
    patch = _load(site)
    try:
        patch.execute()
    except _Stopped as e:
        assert "HER-first" in str(e) and "HER-second" in str(e), e
        assert "HER-fine" not in str(e), e
    else:
        raise AssertionError("two rows renaming to one name must stop the migrate")
    assert not [c for c in site.calls if c[0] in ("set_value", "rename_doc")], site.calls


def test_duplicate_targets_is_pure_and_names_every_old_row():
    patch = _load()
    assert patch.duplicate_targets([("a", "USD", "X"), ("b", None, "X"), ("c", "USD", "Y")]) == {"X": ["a", "b"]}
    assert patch.duplicate_targets([("a", "USD", "X"), ("c", "USD", "Y")]) == {}


def test_execute_is_a_no_op_without_the_table():
    site = _Site(tables=())
    patch = _load(site)
    patch.execute()
    assert [c[0] for c in site.calls] == ["table_exists"]


def test_the_patch_is_registered_once():
    with open(PATCHES_TXT) as f:
        lines = [line.strip() for line in f]
    assert lines.count(MODULE) == 1, lines
