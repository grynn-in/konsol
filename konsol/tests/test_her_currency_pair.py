"""Historical Equity Rate says which currency to which, and stores the To.

Deepak Pai, 5 Nov 2026, on the demo site: the Historical Equity Rate form
"doesnt show rate what to what". The rate is the entity's functional currency
-> a presentation currency, read as 1 From = rate To: the warehouse multiplies
the entity's local amount by it (konsolidat dbt gold_consolidated_trial_balance,
`translation_rate`: "local x translation_rate"), the same direction as the
group's closing and average rates.

konsol#374 (option b; Deepak Pai, 7 Nov 2026): the To currency is STORED and
required, and it is part of the rate's key. A sub-group that presents in EUR
and its parent that presents in USD each need their own historical rate for
the same entity and account; the warehouse matches a rate on (owning group,
entity, account, to_currency) against each consolidating group's reporting
currency. From stays virtual: it is the entity's functional currency and a
second copy could only drift. Rejected: (a) one presentation currency per
tree, validated; (c) a warning on the form only.
"""
import ast
import hashlib
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HER_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "historical_equity_rate")
HER_JSON = os.path.join(HER_DIR, "historical_equity_rate.json")
HER_PY = os.path.join(HER_DIR, "historical_equity_rate.py")
HER_JS = os.path.join(HER_DIR, "historical_equity_rate.js")
GRAIN_PY = os.path.join(APP_DIR, "epm", "budget_grain.py")


def _fields():
    with open(HER_JSON) as f:
        return {d["fieldname"]: d for d in json.load(f)["fields"]}


# --- the JSON ----------------------------------------------------------------

def test_from_currency_is_a_virtual_read_only_iso_currency_field():
    df = _fields()["from_currency"]
    assert df["fieldtype"] == "Link" and df["options"] == "ISO Currency"
    assert df.get("is_virtual") == 1 and df.get("read_only") == 1
    assert df["label"] == "From Currency"


def test_to_currency_is_a_stored_required_iso_currency_link():
    """konsol#374: stored, required, part of the key; shown in the list so two
    rates for two presentation currencies read apart."""
    df = _fields()["to_currency"]
    assert df["fieldtype"] == "Link" and df["options"] == "ISO Currency"
    assert not df.get("is_virtual"), "to_currency is stored now (konsol#374)"
    assert not df.get("read_only"), "the user enters it"
    assert df.get("reqd") == 1
    assert df.get("in_list_view") == 1
    assert df["label"] == "To Currency"
    assert "presentation" in df["description"].lower() or "reporting currency" in df["description"].lower()


def test_the_rate_label_states_the_direction():
    df = _fields()["historical_rate"]
    assert "1 From = rate To" in df["label"], df["label"]
    assert "multiplies" in df["description"]


def test_the_controller_computes_from_as_a_property_and_not_to():
    tree = ast.parse(open(HER_PY).read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HistoricalEquityRate")
    props = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)
             and any(ast.unparse(d) == "property" for d in n.decorator_list)}
    assert "from_currency" in props, props
    assert "to_currency" not in props, "a property would shadow the stored value"


def test_the_field_map_carries_to_currency():
    """The warehouse key has the currency (konsolidat init-db.sql ORDER BY ends
    in to_currency); a sync that leaves it out writes '' and matches no group."""
    tree = ast.parse(open(HER_PY).read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HistoricalEquityRate")
    fm = next(n for n in cls.body if isinstance(n, ast.Assign)
              and getattr(n.targets[0], "id", None) == "CH_FIELD_MAP")
    assert ast.literal_eval(fm.value) == {
        "consolidation_group": "consolidation_group",
        "data_area_id": "data_area_id",
        "main_account": "main_account",
        "rate_date": "rate_date",
        "historical_rate": "historical_rate",
        "to_currency": "to_currency",
    }


# --- the controller under a stub frappe ----------------------------------------

class _Refused(Exception):
    pass


def _controller(values=None, exists=None, digest=None, can_read=True):
    """The real controller class under a stub frappe.

    ``values``: {(doctype, frozen filters or name, frozen fieldname): answer}
    for frappe.db.get_value. ``exists``: a set of (doctype, frozen filters or
    name) that frappe.db.exists answers True for. ``digest``: the real
    digest_name when True, else a stub. ``can_read``: what frappe.has_permission
    answers; with throw=True a False raises _Refused, as Frappe would."""
    calls = []
    frappe = types.ModuleType("frappe")

    def has_permission(doctype, ptype="read", doc=None, throw=False, **k):
        calls.append(("has_permission", doctype, ptype, throw))
        if not can_read and throw:
            raise _Refused(f"No permission for {doctype}")
        return can_read

    frappe.has_permission = has_permission
    frappe.PermissionError = _Refused

    def _freeze(x):
        return json.dumps(x, sort_keys=True) if isinstance(x, (dict, list)) else x

    def get_value(doctype, filters, fieldname=None, as_dict=False, **k):
        key = (doctype, _freeze(filters), _freeze(fieldname))
        calls.append(("get_value", doctype, filters, fieldname))
        answer = (values or {}).get(key)
        if as_dict and isinstance(answer, dict):
            return types.SimpleNamespace(**answer)
        return answer

    def db_exists(doctype, filters=None, **k):
        calls.append(("exists", doctype, filters))
        return (doctype, _freeze(filters)) in (exists or set())

    def throw(msg, exc=None, **k):
        raise _Refused(msg)

    frappe.db = types.SimpleNamespace(get_value=get_value, exists=db_exists)
    frappe.throw = throw
    frappe.whitelist = lambda *a, **k: (lambda f: f)
    frappe.ValidationError = _Refused
    frappe.MandatoryError = _Refused
    frappe._ = lambda s: s
    document = types.ModuleType("frappe.model.document")

    class Document:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def get(self, k, default=None):
            return self.__dict__.get(k, default)

    document.Document = Document
    model = types.ModuleType("frappe.model")
    if digest:
        # budget_grain imports frappe at module level: load it under the stub
        saved_frappe = sys.modules.get("frappe")
        sys.modules["frappe"] = frappe
        try:
            spec = importlib.util.spec_from_file_location("budget_grain_for_her_test", GRAIN_PY)
            grain = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(grain)
        finally:
            if saved_frappe is None:
                sys.modules.pop("frappe", None)
            else:
                sys.modules["frappe"] = saved_frappe
        digest_name = grain.digest_name
    else:
        digest_name = lambda *a: "HER-ZZ"  # noqa: E731
    period_status = types.SimpleNamespace(
        assert_open_between=lambda *a, **k: calls.append(("assert_open_between", a, k)),
        first_period_affected=lambda d: ("first", d))
    stubs = {
        "frappe": frappe, "frappe.model": model, "frappe.model.document": document,
        "konsol": types.ModuleType("konsol"),
        "konsol.clickhouse": types.SimpleNamespace(sync_doctype_after_commit=lambda *a, **k: None),
        "konsol.period_status": period_status,
        "konsol.epm": types.ModuleType("konsol.epm"),
        "konsol.epm.budget_grain": types.SimpleNamespace(digest_name=digest_name),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("her_controller_under_test", HER_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod, calls


def _frz(x):
    return json.dumps(x, sort_keys=True)


ROOT_FILTER = _frz({"consolidation_group": "ZZSUB", "data_area_id": ["is", "not set"]})
NODE_FIELDS = _frz(["name", "consolidation_group", "parent_consolidation_group", "reporting_currency"])

#: A two-level tree: ZZSUB (EUR) under ZZGRP (USD). The root row of a group
#: is the Consolidation Group row with no entity; parent_consolidation_group
#: is the Frappe name of the parent node.
TREE = {
    ("Consolidation Group", ROOT_FILTER, NODE_FIELDS): {
        "name": "CG-ZZSUB-", "consolidation_group": "ZZSUB",
        "parent_consolidation_group": "CG-ZZGRP-", "reporting_currency": "EUR"},
    ("Consolidation Group", "CG-ZZGRP-", NODE_FIELDS): {
        "name": "CG-ZZGRP-", "consolidation_group": "ZZGRP",
        "parent_consolidation_group": None, "reporting_currency": "USD"},
    ("Entity", "ZZNL", "functional_currency"): "GBP",
}
EXISTS = {
    ("Consolidation Group", _frz({"consolidation_group": "ZZSUB"})),
    ("Consolidation Group", _frz({"data_area_id": "ZZNL"})),
    ("Main Account", "ZZ3000"),
}


def _rate(mod, **kw):
    base = dict(consolidation_group="ZZSUB", data_area_id="ZZNL", main_account="ZZ3000",
                rate_date="2024-01-01", historical_rate=1.2, to_currency="USD",
                docstatus=0, name=None)
    base.update(kw)
    return mod.HistoricalEquityRate(**base)


def test_from_is_the_entitys_functional_currency():
    mod, calls = _controller(TREE)
    doc = _rate(mod)
    assert doc.from_currency == "GBP"
    assert ("get_value", "Entity", "ZZNL", "functional_currency") in calls


def test_to_currency_is_what_frappe_stored_not_a_lookup():
    mod, calls = _controller(TREE)
    doc = _rate(mod, to_currency="EUR")
    assert doc.to_currency == "EUR"
    assert not [c for c in calls if c[0] == "get_value"], "nothing computed on read"


def test_presentation_currencies_walk_from_the_owning_group_to_the_top():
    """The currencies a rate recorded under ZZSUB may convert into: ZZSUB's
    own (EUR) and every ancestor's (USD), nearest first."""
    mod, _ = _controller(TREE)
    assert mod.presentation_currencies("ZZSUB") == [("ZZSUB", "EUR"), ("ZZGRP", "USD")]


def test_the_whitelisted_walk_requires_read_on_consolidation_group():
    """PR #436 review: a whitelisted read with no permission check lets any
    logged-in user walk the group tree. Refused before the first read."""
    mod, calls = _controller(TREE, can_read=False)
    try:
        mod.presentation_currencies("ZZSUB")
    except _Refused:
        pass
    else:
        raise AssertionError("a caller without read on Consolidation Group must be refused")
    assert ("has_permission", "Consolidation Group", "read", True) in calls
    assert not [c for c in calls if c[0] == "get_value"], "refused before the walk"


def test_the_controllers_own_check_does_not_ask_the_sessions_permission():
    """validate() walks the tree through the unguarded helper: a document's
    permission is its own save's business, and a patch or a system import
    must not fail on the session's Consolidation Group rights."""
    mod, calls = _controller(TREE, exists=EXISTS, can_read=False)
    _rate(mod, to_currency="EUR").validate()
    assert not [c for c in calls if c[0] == "has_permission"], calls


def test_a_blank_to_currency_is_refused():
    mod, _ = _controller(TREE, EXISTS)
    doc = _rate(mod, to_currency=None)
    try:
        doc.validate()
    except _Refused as e:
        assert "To Currency" in str(e), e
    else:
        raise AssertionError("a blank To Currency must be refused")


def test_a_currency_no_group_in_the_tree_presents_in_is_refused_naming_the_groups():
    """A rate into CHF under ZZSUB: neither ZZSUB (EUR) nor ZZGRP (USD)
    would ever read it, so it is refused, and the message says which groups
    present in which currencies so the user can pick one."""
    mod, _ = _controller(TREE, EXISTS)
    doc = _rate(mod, to_currency="CHF")
    try:
        doc.validate()
    except _Refused as e:
        msg = str(e)
        assert "CHF" in msg and "ZZSUB" in msg and "EUR" in msg and "ZZGRP" in msg and "USD" in msg, msg
    else:
        raise AssertionError("a currency no consolidating group presents in must be refused")


def test_the_owning_groups_currency_and_an_ancestors_are_both_accepted():
    mod, _ = _controller(TREE, EXISTS)
    _rate(mod, to_currency="EUR").validate()
    _rate(mod, to_currency="USD").validate()


def test_a_group_without_a_root_row_is_refused_before_the_currency_is_judged():
    """No root row, no currency to compare with: say so rather than refuse
    every currency as 'not in the tree'."""
    mod, _ = _controller({}, EXISTS)
    doc = _rate(mod, to_currency="USD")
    try:
        doc.validate()
    except _Refused as e:
        assert "ZZSUB" in str(e), e
    else:
        raise AssertionError("a group with no root row must be refused")


def test_two_rates_for_two_presentation_currencies_get_distinct_names():
    """autoname digests (group, entity, account, date, to_currency): the EUR
    and the USD rate for one tranche are two documents, not a collision."""
    mod, _ = _controller(TREE, digest=True)
    eur = _rate(mod, to_currency="EUR")
    usd = _rate(mod, to_currency="USD")
    eur.autoname()
    usd.autoname()
    assert eur.name != usd.name, (eur.name, usd.name)
    # digest_name slugs the readable head (the date's dashes become
    # underscores) and appends the hash of the exact tuple.
    assert eur.name.startswith("HER-ZZSUB-ZZNL-ZZ3000-2024_01_01-EUR-"), eur.name
    expected = hashlib.sha1("\x1f".join(
        ["ZZSUB", "ZZNL", "ZZ3000", "2024-01-01", "EUR"]).encode()).hexdigest()[:8]
    assert eur.name.endswith("-" + expected), eur.name


def test_cancel_looks_for_the_next_rate_in_the_same_currency():
    """A rate applies until the next tranche of the SAME series: group,
    entity, account and to_currency. The EUR series must not end the USD one."""
    values = dict(TREE)
    mod, calls = _controller(values, EXISTS)
    doc = _rate(mod, to_currency="USD", name="HER-1", docstatus=1)
    doc.before_cancel()
    lookups = [c for c in calls if c[0] == "get_value" and c[1] == "Historical Equity Rate"]
    assert lookups, calls
    assert lookups[0][2]["to_currency"] == "USD", lookups[0][2]


# --- the form ----------------------------------------------------------------

def test_the_sentence_sits_under_the_rate_field():
    """Deepak Pai, 5 Nov 2026: put "1 INR = 0.018800 USD at 01-12-2011, for
    account 3000 of IN_NALCO" next to or below the rate. It is the rate field's
    own help line, not a banner at the top of the form."""
    js = open(HER_JS).read()
    assert "from_currency" in js and "to_currency" in js
    assert 'set_df_property("historical_rate", "description"' in js
    assert "set_intro" not in js


def test_a_same_currency_rate_is_said_to_be_unused():
    """#373 review: when From equals To the warehouse translates at 1.0 and never
    reads this rate (gold_consolidated_trial_balance: the same-currency case
    comes before the historical one), so the intro must not claim
    "1 USD = 0.92 USD"."""
    js = open(HER_JS).read()
    assert "d.from_currency === d.to_currency" in js
    branch = js[js.index("d.from_currency === d.to_currency"):]
    assert "not used" in branch.split("return")[0]


def test_the_form_offers_the_owning_groups_currency_and_leaves_it_editable():
    """A visible default, never a silent one: when To Currency is blank and the
    group is set, the form fills in the owning group's reporting currency, and
    the user may change it to an ancestor's (konsol#374)."""
    js = open(HER_JS).read()
    assert "consolidation_group(frm)" in js or "consolidation_group:" in js, "fill on group change"
    assert "presentation_currencies" in js
    assert 'frm.set_value("to_currency"' in js
    assert "!frm.doc.to_currency" in js, "never overwrite a value the user entered"


def test_the_form_ignores_an_answer_for_a_group_the_user_has_since_changed():
    """PR #436 review: the callback must check that the group it asked about
    is still the group on the form, or a slow answer for the old group fills
    the new group's To Currency; and the description (the groups above it)
    is reset on every change, or it outlives the group it describes."""
    js = open(HER_JS).read()
    trigger = js[js.index("consolidation_group(frm)"):]
    assert "const group = frm.doc.consolidation_group;" in trigger
    assert "args: { consolidation_group: group }" in trigger, "ask about the captured group"
    callback = trigger[trigger.index("callback(r)"):]
    guard = callback[:callback.index('frm.set_value("to_currency"')]
    assert "frm.doc.consolidation_group !== group" in guard, "compare before set_value"
    reset = trigger.index('frm.set_df_property("to_currency", "description", "")')
    assert reset < trigger.index("frappe.call("), "reset the description before asking"
