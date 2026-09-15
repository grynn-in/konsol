"""Fair Value Allocation Profile (konsol#208): reusable weights that spread a
deal's Fair Value Adjustment Total over accounts when Get Balances from Trial
Balance builds the Acquired Balance Sheet.

The JSON shape is read from disk; the controller is loaded by file path
against a stub frappe (the pattern of test_business_combination.py) whose
chart answers ``frappe.db.get_value("Main Account", …)``.
"""
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_DIR = os.path.join(APP_DIR, "consolidation", "doctype")
PARENT = "fair_value_allocation_profile"
CHILD = "fair_value_allocation_profile_line"


class Refused(Exception):
    pass


def _json(folder):
    with open(os.path.join(DOCTYPE_DIR, folder, folder + ".json")) as f:
        return json.load(f)


def _fields(meta):
    return {f["fieldname"]: f for f in meta["fields"]}


# -- JSON shape ------------------------------------------------------------------

def test_both_folders_are_packages_with_json_and_controller():
    for folder in (PARENT, CHILD):
        for name in ("__init__.py", folder + ".json", folder + ".py"):
            assert os.path.exists(os.path.join(DOCTYPE_DIR, folder, name)), f"{folder}/{name} missing"


def test_parent_is_a_plain_consolidation_doctype_named_by_profile_name():
    meta = _json(PARENT)
    assert meta["name"] == "Fair Value Allocation Profile"
    assert meta["doctype"] == "DocType"
    assert meta["module"] == "Consolidation"
    assert not meta.get("istable")
    assert not meta.get("is_submittable"), "a profile is reference data, not an approval"
    assert meta["autoname"] == "field:profile_name"
    assert meta.get("track_changes") == 1


def test_parent_fields():
    fields = _fields(_json(PARENT))
    name = fields["profile_name"]
    assert name["fieldtype"] == "Data" and name.get("reqd") == 1 and name.get("unique") == 1
    assert fields["description"]["fieldtype"] == "Small Text"
    lines = fields["lines"]
    assert lines["fieldtype"] == "Table"
    assert lines["options"] == "Fair Value Allocation Profile Line"


def test_child_is_a_table_of_account_weight_note():
    meta = _json(CHILD)
    assert meta["name"] == "Fair Value Allocation Profile Line"
    assert meta["module"] == "Consolidation"
    assert meta.get("istable") == 1
    fields = _fields(meta)
    account = fields["main_account"]
    assert account["fieldtype"] == "Link" and account["options"] == "Main Account" and account.get("reqd") == 1
    weight = fields["weight"]
    assert weight["fieldtype"] == "Percent" and weight.get("reqd") == 1
    assert fields["note"]["fieldtype"] == "Data"


def _rights(meta):
    """role -> (read, write, create, delete) of a doctype's permission rows."""
    return {
        p["role"]: tuple(int(p.get(right) or 0) for right in ("read", "write", "create", "delete"))
        for p in meta.get("permissions") or []
    }


def test_parent_permissions_equal_consolidation_group():
    """A profile is part of the group's deal policy: EPM Admin maintains it and
    the EPM Analyst drafting a Business Combination must be able to read it."""
    group = _json("consolidation_group")
    profile = _rights(_json(PARENT))
    assert profile == _rights(group)
    assert profile["EPM Analyst"][0] == 1, "the analyst drafting a deal links the profile"
    assert profile["EPM Admin"] == (1, 1, 1, 1)


def test_not_synced_to_the_warehouse():
    for folder in (PARENT, CHILD):
        with open(os.path.join(DOCTYPE_DIR, folder, folder + ".py")) as f:
            source = f.read()
        assert "CH_TABLE" not in source and "sync_doctype" not in source, folder


# -- validate() ------------------------------------------------------------------

#: name -> (status, is_group)
CHART = {
    "ZZ1100": ("Published", 0),
    "ZZ1200": ("Published", 0),
    "ZZ1300": ("Published", 0),
    "ZZ1000": ("Published", 1),   # a group account
    "ZZ1400": ("Draft", 0),       # not published
}


class _Doc:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def get(self, key, default=None):
        return getattr(self, key, default)


class _Row(dict):
    __getattr__ = dict.get


def _load():
    frappe = types.ModuleType("frappe")

    def throw(msg, *a, **k):
        raise Refused(msg)

    def get_value(doctype, name, fieldname=None, *a, **k):
        assert doctype == "Main Account", doctype
        row = CHART.get(name)
        if row is None:
            return None
        values = {"status": row[0], "is_group": row[1]}
        if isinstance(fieldname, (list, tuple)):
            return tuple(values[f] for f in fieldname)
        return values[fieldname]

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.db = types.SimpleNamespace(get_value=get_value)
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = _Doc
    stubs = {"frappe": frappe, "frappe.model": model, "frappe.model.document": document}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        path = os.path.join(DOCTYPE_DIR, PARENT, PARENT + ".py")
        spec = importlib.util.spec_from_file_location("fvap_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _profile(lines):
    mod = _load()
    doc = mod.FairValueAllocationProfile(
        name="ZZ-PPA", profile_name="ZZ-PPA",
        lines=[_Row(main_account=a, weight=w) for a, w in lines],
    )
    return doc


def _refused(lines):
    try:
        _profile(lines).validate()
    except Refused as e:
        return str(e)
    raise AssertionError(f"{lines} was accepted")


def test_a_valid_profile_passes():
    _profile([("ZZ1100", 33.34), ("ZZ1200", 33.33), ("ZZ1300", 33.33)]).validate()
    _profile([("ZZ1100", 100)]).validate()


def test_a_profile_without_lines_is_refused():
    message = _refused([])
    assert "at least one line" in message


def test_weights_must_add_up_to_100():
    message = _refused([("ZZ1100", 60), ("ZZ1200", 39)])
    assert "Weights add up to 99; they must add up to 100." in message
    message = _refused([("ZZ1100", 60), ("ZZ1200", 40.5)])
    assert "Weights add up to 100.5; they must add up to 100." in message
    # within a ten-thousandth of 100
    _profile([("ZZ1100", 60), ("ZZ1200", 40.00005)]).validate()


def test_every_weight_must_be_positive_and_the_sentence_names_the_account():
    message = _refused([("ZZ1100", 100), ("ZZ1200", 0)])
    assert "ZZ1200" in message and "greater than 0" in message
    message = _refused([("ZZ1100", 110), ("ZZ1200", -10)])
    assert "ZZ1200" in message and "greater than 0" in message


def test_an_account_may_appear_only_once():
    message = _refused([("ZZ1100", 50), ("ZZ1100", 50)])
    assert "ZZ1100" in message and "more than once" in message


def test_every_account_must_be_a_published_non_group_main_account():
    for account in ("ZZ1000", "ZZ1400", "ZZ9999"):
        message = _refused([("ZZ1100", 50), (account, 50)])
        assert account in message and "Published" in message and "non-group" in message, account
