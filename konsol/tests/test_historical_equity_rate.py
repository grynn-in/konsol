"""Historical Equity Rate controller guards (grynn-in/konsolidat#92).

Static-assertion style (validate() needs a live frappe + Consolidation Group
records to execute, so we assert the enforcement is wired in source — same
convention as test_exec_www.py).
"""
import ast
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(
    APP_DIR, "consolidation", "doctype", "historical_equity_rate",
    "historical_equity_rate.py",
)


def _src():
    with open(PY) as f:
        return f.read()


def test_file_exists():
    assert os.path.isfile(PY)


def test_validate_calls_both_guards():
    tree = ast.parse(_src())
    fns = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "validate" in fns
    assert "_validate_positive_rate" in fns
    assert "_validate_references" in fns  # #92 finding #3


def test_positive_rate_guard_present():
    src = _src()
    assert "historical_rate" in src
    assert "must be a positive number" in src.lower()


def test_referential_integrity_on_group_and_entity():
    # #92 finding #3: free-text keys enforced against the Consolidation Group registry
    src = _src()
    assert 'frappe.db.exists(' in src
    assert '"Consolidation Group"' in src
    assert "consolidation_group" in src
    assert "data_area_id" in src
    # every key must throw on an unknown value
    assert src.count("frappe.throw(") >= 4  # positive-rate + group + entity + account


# ---- the account key is a Link (#92 finding #3, second increment) ----------
#
# The retained-earnings account declares its own translation method, so
# customers enter historical rates in earnest now, and a key that misses its
# target silently drops the balance back to the closing rate. Unlike the other
# two keys, this one HAS a clean Link target: Main Account is named
# `field:main_account`, so its name IS the bare account code. The controller
# runs here against a stub frappe.

DOCTYPE_DIR = os.path.dirname(PY)
JSON_PATH = os.path.join(DOCTYPE_DIR, "historical_equity_rate.json")

#: The registries the stub knows about.
GROUPS, ENTITIES, ACCOUNTS = ("ZZGRP",), ("ZZ01",), ("ZZ3100",)


def _json():
    with open(JSON_PATH) as f:
        return json.load(f)


def _fields():
    return {f["fieldname"]: f for f in _json()["fields"]}


class Refused(Exception):
    pass


def _frappe(groups=(), entities=(), accounts=()):
    """A stub frappe whose db.exists answers from the three registries."""
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})

    def throw(msg, exc=None, *a, **k):
        raise Refused(msg)

    def exists(doctype, filters=None):
        if doctype == "Main Account":
            name = filters if isinstance(filters, str) else (filters or {}).get("name")
            return name in accounts
        if doctype == "Consolidation Group":
            f = filters or {}
            if "consolidation_group" in f:
                return f["consolidation_group"] in groups
            if "data_area_id" in f:
                return f["data_area_id"] in entities
        return False

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.db = types.SimpleNamespace(exists=exists)
    return frappe


def _controller(**registries):
    """The real controller, loaded by path against stubs: frappe and the konsol
    modules it imports at module level need a live site otherwise."""
    frappe = _frappe(**registries)

    class Document:
        def __init__(self, **fields):
            self.__dict__.update(fields)

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return None

        def get(self, name, default=None):
            return self.__dict__.get(name, default)

    mods = {n: types.ModuleType(n) for n in (
        "frappe.model", "frappe.model.document", "konsol", "konsol.clickhouse",
        "konsol.period_status", "konsol.epm", "konsol.epm.budget_grain")}
    mods["frappe"] = frappe
    mods["frappe.model.document"].Document = Document
    mods["konsol.clickhouse"].sync_doctype_after_commit = lambda *a, **k: None
    mods["konsol.period_status"].assert_open_between = lambda *a, **k: None
    mods["konsol.period_status"].first_period_affected = lambda *a, **k: None
    mods["konsol.epm.budget_grain"].digest_name = lambda prefix, keys: prefix
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("her_under_test", PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return module


def _validate(**fields):
    """(refused, message) for one validate() of a rate with ``fields``."""
    module = _controller(groups=GROUPS, entities=ENTITIES, accounts=ACCOUNTS)
    base = dict(doctype="Historical Equity Rate", consolidation_group=GROUPS[0],
                data_area_id=ENTITIES[0], main_account=ACCOUNTS[0],
                rate_date="2026-01-31", historical_rate=0.92)
    doc = module.HistoricalEquityRate(**dict(base, **fields))
    try:
        doc.validate()
    except Refused as e:
        return True, str(e)
    return False, None


def test_the_account_key_is_a_link_to_main_account():
    """Main Account is named `field:main_account`: its name IS the bare account
    code, so the stored value is unchanged and the warehouse join still
    matches byte for byte. Only the type moved."""
    f = _fields()["main_account"]
    assert f["fieldtype"] == "Link", f"main_account is still {f['fieldtype']}"
    assert f["options"] == "Main Account", f"main_account points at {f.get('options')!r}"
    assert f["reqd"] == 1, "the account is still required"


def test_the_other_two_keys_are_left_alone():
    """Consolidation Group is named `CG-{group}-{entity}` while the warehouse
    joins on the bare code, so that one cannot become a Link; the entity
    already is one."""
    f = _fields()
    assert f["consolidation_group"]["fieldtype"] == "Data"
    assert (f["data_area_id"]["fieldtype"], f["data_area_id"]["options"]) == ("Link", "Entity")


def test_an_unknown_account_is_refused_and_named():
    refused, msg = _validate(main_account="ZZ9999")
    assert refused, "a rate for an account that does not exist was accepted"
    assert "ZZ9999" in msg, f"the message does not name the account: {msg}"
    assert "closing rate" in msg, f"the message does not say what goes wrong: {msg}"


def test_a_rate_for_an_existing_account_passes():
    assert _validate() == (False, None)


def test_the_group_and_entity_refusals_still_fire():
    refused, msg = _validate(consolidation_group="ZZNOPE")
    assert refused and "ZZNOPE" in msg, msg
    refused, msg = _validate(data_area_id="ZZ99")
    assert refused and "ZZ99" in msg, msg


def test_the_docstring_says_why_this_key_is_a_link():
    """The next reader must not 'fix' the inconsistency by linking all three."""
    fn = next(n for n in ast.walk(ast.parse(_src()))
              if isinstance(n, ast.FunctionDef) and n.name == "_validate_references")
    doc = ast.get_docstring(fn) or ""
    assert "field:main_account" in doc, "the docstring does not say why this one IS a Link"
    assert "Main Account" in doc


def test_still_syncs_only_submitted_to_clickhouse():
    # finding #1 (systemic) lives in clickhouse.sync_doctype; here just confirm
    # the doctype still routes through it on submit/cancel/delete, after the
    # commit (konsol#124), and deletes with after_delete (#120).
    src = _src()
    assert "sync_doctype_after_commit" in src
    for hook in ("on_submit", "on_cancel", "after_delete"):
        assert f"def {hook}" in src
    assert "def on_trash" not in src
