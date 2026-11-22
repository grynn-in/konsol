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


def test_validate_calls_all_three_guards():
    tree = ast.parse(_src())
    fns = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "validate" in fns
    assert "_validate_positive_rate" in fns
    assert "_validate_references" in fns  # #92 finding #3
    assert "_validate_to_currency" in fns  # konsol#374


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
GROUPS, ACCOUNTS = ("ZZGRP",), ("ZZ3100",)

#: Consolidation Group rows that carry a data_area_id, mapped to their is_group
#: flag (konsol#240). ZZ01 is a leaf entity; ZZPAR is a trading parent — a node
#: marked is_group = 1 that is nevertheless a real company with its own share
#: capital, its own trial balance and subsidiaries beneath it. On the live site
#: 27 of the 28 group nodes look like ZZPAR.
#:
#: The map deliberately cannot represent a pure rollup — a row that exists
#: carrying a blank data_area_id — and does not need to: no lookup keyed on
#: data_area_id can return such a row, so the controller can never see one.
#: What a user CAN type in the rollup's place is its *group* code, which the
#: entity backfill made a real Entity row; that input is GROUPS[0] here.
ENTITIES = {"ZZ01": 0, "ZZPAR": 1}
ENTITY = "ZZ01"


def _json():
    with open(JSON_PATH) as f:
        return json.load(f)


def _fields():
    return {f["fieldname"]: f for f in _json()["fields"]}


class Refused(Exception):
    pass


def _frappe(groups=(), entities=None, accounts=()):
    """A stub frappe whose db.exists answers from the three registries, and
    whose db.get_value knows each group's root node (USD, no parent) for the
    To Currency check (konsol#374)."""
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.MandatoryError = frappe.ValidationError
    frappe.whitelist = lambda *a, **k: (lambda f: f)

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
                rows = entities or {}
                if f["data_area_id"] not in rows:
                    return False
                # honour an is_group filter the way the database would: the
                # row exists, but the filter may still exclude it.
                if "is_group" in f:
                    return rows[f["data_area_id"]] == f["is_group"]
                return True
        return False

    def get_value(doctype, filters=None, fieldname=None, as_dict=False, **k):
        if doctype == "Consolidation Group" and isinstance(filters, dict):
            group = filters.get("consolidation_group")
            if group in groups:
                return types.SimpleNamespace(
                    name=f"CG-{group}-", consolidation_group=group,
                    parent_consolidation_group=None, reporting_currency="USD")
        return None

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.db = types.SimpleNamespace(exists=exists, get_value=get_value)
    return frappe


def _controller(digest=None, record=None, **registries):
    """The real controller, loaded by path against stubs: frappe and the konsol
    modules it imports at module level need a live site otherwise. ``digest``
    replaces the stub digest_name (prefix only); ``record`` collects the
    before_cancel reads when given."""
    frappe = _frappe(**registries)
    if record is not None:
        frappe.db.get_value = lambda doctype, filters=None, fieldname=None, **k: (
            record.setdefault("get_value", []).append((doctype, filters, fieldname, k)) or None)

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
    mods["konsol.clickhouse"].after_commit_once = lambda *a, **k: None
    mods["konsol.period_status"].assert_open_between = (
        lambda *a, **k: (record or {}).setdefault("gates", []).append((a, k)))
    mods["konsol.period_status"].first_period_affected = lambda *a, **k: None
    mods["konsol.epm.budget_grain"].digest_name = digest or (lambda prefix, keys: prefix)
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
                data_area_id=ENTITY, main_account=ACCOUNTS[0],
                rate_date="2026-01-31", historical_rate=0.92, to_currency="USD", rate_set="ACTUAL")
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


# ---- a trading parent is still an entity (konsol#240) ---------------------
#
# The entity check read `is_group = 0` as shorthand for "is a real company".
# That shorthand only holds when every parent is a pure holding shell, and on
# the live site it is false 27 times out of 28. Membership is the real test:
# a Consolidation Group row carrying this data_area_id at all.


def test_a_trading_parent_is_still_an_entity():
    """A group node that carries a data_area_id IS an entity row.

    `_is_group_node()` is `is_group or not data_area_id`: carrying an entity
    code is what makes a row an entity row, whatever is_group says."""
    refused, msg = _validate(data_area_id="ZZPAR")
    assert not refused, f"a trading parent was refused a historical rate: {msg}"


def test_an_entity_in_no_consolidation_group_row_is_still_refused():
    """Membership is still enforced — only leafness stopped being the test."""
    refused, msg = _validate(data_area_id="ZZ99")
    assert refused, "a rate for an entity in no consolidation group was accepted"
    assert "ZZ99" in msg, f"the message does not name the entity: {msg}"
    assert "closing rate" in msg, f"the message does not say what goes wrong: {msg}"
    assert "Unknown entity" not in msg, (
        f"the message still names the wrong cause — the entity is known: {msg}")


def test_a_group_code_is_refused_without_being_called_a_non_node():
    """The other reachable bad input: a code that names a group, not an entity.

    `konsol/patches/backfill_entities_from_consolidation_group.py` creates one
    Entity per group node, named after the *group* code
    (`code = data_area_id if not is_group else consolidation_group`). So a
    group code satisfies the reqd Link to Entity and reaches this check, while
    no Consolidation Group row carries it as a `data_area_id`. Telling that
    user the code "must be a node of the consolidation tree" is false — it is
    one. It is not an entity *on* one, and the message has to say so.
    """
    refused, msg = _validate(data_area_id=GROUPS[0])
    assert refused, "a group code was accepted as the entity of a historical rate"
    assert GROUPS[0] in msg, f"the message does not name the code: {msg}"
    assert "closing rate" in msg, f"the message does not say what goes wrong: {msg}"
    assert "must be a node" not in msg, (
        f"the message tells a group code it must be a node — it is one: {msg}")
    assert "entity" in msg.lower(), (
        f"the message does not say an entity on a node is what is wanted: {msg}")


def test_a_blank_entity_is_not_this_check_s_business():
    """A pure rollup carries no entity code — and this check never sees that.

    There is deliberately no test named for a rollup node: one is unreachable
    by construction here. It has no `data_area_id` to be found by, so no query
    reaches it, and the Link offers the user only its group code (the case
    above). The blank itself short-circuits — `if self.data_area_id and ...` —
    so membership does not refuse it; the reqd Link does, as a mandatory field.
    Asserted so that division of labour stays deliberate: this check owns
    *wrong* codes, `reqd` owns *missing* ones.
    """
    assert _validate(data_area_id="") == (False, None), (
        "a blank entity was refused by the membership check; reqd owns that case")


def test_the_entity_check_does_not_test_leafness():
    """The `is_group: 0` clause must be gone from the controller, not merely
    satisfied: it is what refused the 27 trading parents."""
    assert '"is_group": 0' not in _src(), (
        "the entity check still filters on is_group; membership is the test")


def test_the_docstring_does_not_still_call_the_entity_key_free_text():
    """`data_area_id` became a reqd Link to Entity; the docstring must not keep
    arguing from "not a Link", which was the old justification for checking it
    here. The justification that survives is the true one: existing as an
    Entity is not the same as being in the consolidation tree."""
    fn = next(n for n in ast.walk(ast.parse(_src()))
              if isinstance(n, ast.FunctionDef) and n.name == "_validate_references")
    doc = ast.get_docstring(fn) or ""
    assert "are Data fields, not Links" not in doc, (
        "the docstring still calls data_area_id a Data field; it is a Link to Entity")
    assert "Entity" in doc and "consolidation tree" in doc, (
        "the docstring does not give the real reason the Link is not enough")
    # the reasoning that is still true is kept, not thrown out with it
    assert "CG-{group}-{entity}" in doc, "the consolidation_group half lost its reason"


def test_the_docstring_says_why_leafness_is_the_wrong_test():
    fn = next(n for n in ast.walk(ast.parse(_src()))
              if isinstance(n, ast.FunctionDef) and n.name == "_validate_references")
    doc = ast.get_docstring(fn) or ""
    assert "_is_group_node" in doc, "the docstring does not cite the rule it follows"
    # and the two settled checks either side keep their reasoning (#92)
    assert "field:main_account" in doc
    assert "konsolidat#130" in doc


def test_still_syncs_only_submitted_to_clickhouse():
    # finding #1 (systemic) lives in clickhouse.sync_doctype; here just confirm
    # the doctype still routes through it on submit/cancel/delete, after the
    # commit (konsol#124), and deletes with after_delete (#120).
    src = _src()
    assert "sync_doctype_after_commit" in src
    for hook in ("on_submit", "on_cancel", "after_delete"):
        assert f"def {hook}" in src
    assert "def on_trash" not in src


# ---- the rate set (konsol#348, S4-Q1 option c, 15 Nov 2026) ------------------
#
# A tranche's historical rate belongs to a Rate Set like a group rate does: the
# actuals set for the actuals translation, a budget's own set for a budget. The
# set is in the key (the digest name, the warehouse sort key) and in the
# cancel's next-tranche search, so a budget tranche never shadows an actuals one.

def test_a_historical_rate_names_its_set():
    f = _fields()["rate_set"]
    assert (f["fieldtype"], f["options"], f["reqd"], f["in_list_view"]) == ("Link", "Rate Set", 1, 1)
    assert f.get("description") and "Actuals" in f["description"], "say which set the actuals read"
    names = [x["fieldname"] for x in _json()["fields"]]
    assert names.index("rate_set") == names.index("to_currency") + 1, "beside the currency it keys with"


def test_a_blank_set_is_refused_not_defaulted():
    refused, msg = _validate(rate_set=None)
    assert refused and "Rate Set" in msg, msg
    assert _validate(rate_set="BUDGET_2027") == (False, None)


def test_two_tranches_with_one_key_in_different_sets_get_distinct_names():
    module = _controller(digest=lambda prefix, keys: prefix + "|" + "|".join(str(k) for k in keys))
    base = dict(consolidation_group="ZZGRP", data_area_id="ZZNL", main_account="ZZ3000",
                rate_date="2024-01-01", to_currency="EUR")
    actual = module.HistoricalEquityRate(**base, rate_set="ACTUAL")
    budget = module.HistoricalEquityRate(**base, rate_set="BUDGET_2027")
    actual.autoname()
    budget.autoname()
    assert actual.name != budget.name
    assert actual.name.endswith("|EUR|ACTUAL") and budget.name.endswith("|EUR|BUDGET_2027"), (actual.name, budget.name)


def test_cancel_finds_the_next_tranche_within_its_own_set():
    record = {}
    module = _controller(record=record)
    doc = module.HistoricalEquityRate(
        name="HER-X", consolidation_group="ZZGRP", data_area_id="ZZNL", main_account="ZZ3000",
        rate_date="2024-01-01", to_currency="EUR", rate_set="BUDGET_2027", docstatus=1)
    doc.before_cancel()
    [(doctype, filters, fieldname, _k)] = record["get_value"]
    assert doctype == "Historical Equity Rate" and fieldname == "rate_date"
    assert filters["rate_set"] == "BUDGET_2027" and filters["to_currency"] == "EUR", filters
    assert filters["docstatus"] == 1 and filters["rate_date"] == [">", "2024-01-01"]
    assert record["gates"], "the period gate still runs"


def test_the_set_is_written_through_last():
    module = _controller()
    field_map = module.HistoricalEquityRate.CH_FIELD_MAP
    assert list(field_map)[-2:] == ["to_currency", "rate_set"], list(field_map)
    assert field_map["rate_set"] == "rate_set"


# -- T24 (konsol#592): a new row defaults to the actuals set, visibly ---------------

def _before_insert(**fields):
    module = _controller(groups=GROUPS, entities=ENTITIES, accounts=ACCOUNTS)
    gr = types.ModuleType("konsol.group_rates")
    gr.actuals_rate_set = lambda: "ACTUAL"
    saved = sys.modules.get("konsol.group_rates")
    sys.modules["konsol.group_rates"] = gr
    try:
        doc = module.HistoricalEquityRate(doctype="Historical Equity Rate", **fields)
        doc.before_insert()
    finally:
        if saved is None:
            sys.modules.pop("konsol.group_rates", None)
        else:
            sys.modules["konsol.group_rates"] = saved
    return doc


def test_a_new_row_with_no_set_gets_the_actuals_set():
    assert _before_insert(rate_set=None).rate_set == "ACTUAL"


def test_a_set_the_user_chose_is_kept():
    assert _before_insert(rate_set="CONSTANT").rate_set == "CONSTANT"


def test_validate_still_refuses_a_blank_set():
    refused, msg = _validate(rate_set=None)
    assert refused and "Rate Set" in msg
