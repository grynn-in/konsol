"""R4 (konsol#297): a TB uploaded on an entity's behalf is recorded (konsol#305 A18, story 3.7).

Only EPM Admin uploads for an entity, and it must be visibly labelled. Trial
Balance Submission carries ``uploaded_on_behalf`` (Select "", "Yes", "No"):
set server-side in ``before_insert`` from the uploader's roles and assigned
entities, never taken from the request. Blank means "unknown": every TB made
before the field existed holds blank (Problems 16), and nothing back-fills it.

Pure half: ``konsol/close/tb_model.is_on_behalf`` is loaded by path. Controller
half: the controller is loaded with stub frappe/konsol modules that are swapped
into ``sys.modules`` only for the load and the call, then restored.
"""
import ast
import contextlib
import importlib.util
import json
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(_HERE)
_TB_MODEL = os.path.join(_APP, "close", "tb_model.py")
_DT = os.path.join(_APP, "consolidation", "doctype", "trial_balance_submission")
_CTRL = os.path.join(_DT, "trial_balance_submission.py")
_JSON = os.path.join(_DT, "trial_balance_submission.json")

_spec = importlib.util.spec_from_file_location("close_tb_model_on_behalf", _TB_MODEL)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


# --- the pure function ------------------------------------------------------

def test_entity_accountant_on_their_own_entity_is_not_on_behalf():
    assert M.is_on_behalf(["Entity Accountant"], "E1", {"E1", "E2"}) is False


def test_entity_accountant_on_another_entity_is_on_behalf():
    assert M.is_on_behalf(["Entity Accountant"], "E3", {"E1", "E2"}) is True


def test_epm_admin_is_on_behalf():
    assert M.is_on_behalf(["EPM Admin"], "E1", set()) is True
    # an assignment alone does not make an admin the entity's accountant
    assert M.is_on_behalf(["EPM Admin"], "E1", {"E1"}) is True


def test_admin_who_is_also_the_entitys_accountant_is_not_on_behalf():
    assert M.is_on_behalf(["EPM Admin", "Entity Accountant"], "E1", {"E1"}) is False


def test_admin_who_is_an_accountant_elsewhere_is_on_behalf():
    assert M.is_on_behalf(["EPM Admin", "Entity Accountant"], "E9", {"E1"}) is True


def test_empty_inputs_are_on_behalf():
    for roles, subtree in ((None, None), ([], set()), (["Entity Accountant"], None)):
        assert M.is_on_behalf(roles, "E1", subtree) is True


def test_tb_model_imports_no_frappe():
    with open(_TB_MODEL) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("frappe") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("frappe")


# --- the field ----------------------------------------------------------------

def _doctype():
    with open(_JSON) as f:
        return json.load(f)


def _field():
    fields = {f["fieldname"]: f for f in _doctype()["fields"]}
    assert "uploaded_on_behalf" in fields, "Trial Balance Submission has no uploaded_on_behalf field"
    return fields["uploaded_on_behalf"]


def test_field_is_a_read_only_select_with_blank_yes_no():
    f = _field()
    assert f["fieldtype"] == "Select"
    assert f["options"] == "\nYes\nNo"
    assert f.get("read_only") == 1
    assert not f.get("allow_on_submit")


def test_field_has_no_default_so_old_rows_stay_unknown():
    # Problems 16: a default would claim "not on behalf" (or "on behalf") for
    # uploads that were never tracked.
    assert "default" not in _field()


def test_field_says_why_and_is_in_field_order():
    f = _field()
    assert "R4" in f.get("description", "") and "konsol#297" in f["description"]
    assert "uploaded_on_behalf" in _doctype()["field_order"]


# --- the controller -----------------------------------------------------------

class _Doc:  # stand-in for frappe.model.document.Document
    doctype = "Trial Balance Submission"

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def get(self, key, default=None):
        return self.__dict__.get(key, default)

    def __getattr__(self, key):  # a Document has every field, None when unset
        if key.startswith("__"):
            raise AttributeError(key)
        return None

    def is_new(self):
        return bool(self.__dict__.get("__islocal"))


class _Stop(Exception):
    pass


class _World:
    """The request context the stubs read: who is uploading, and what is stored."""

    def __init__(self, roles=(), assigned=(), subtree=None, stored=None):
        self.roles = list(roles)
        self.assigned = set(assigned)
        self.subtree = set(self.assigned if subtree is None else subtree)
        self.stored = dict(stored or {})
        self.calls = []


def _stubs(world):
    def mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        return m

    def get_value(doctype, name, field, *a, **k):
        world.calls.append(("get_value", doctype, name, field))
        return world.stored.get((name, field))

    frappe = mod(
        "frappe",
        whitelist=lambda *a, **k: (lambda fn: fn),
        get_roles=lambda user=None: list(world.roles),
        session=types.SimpleNamespace(user="zz-uploader@example.com"),
        db=types.SimpleNamespace(get_value=get_value),
    )

    def assigned_entities(user=None):
        world.calls.append(("assigned_entities", user))
        return set(world.assigned)

    def subtree_codes(codes):
        world.calls.append(("subtree_codes", set(codes or ())))
        return set(world.subtree) if codes else set()

    return {
        "frappe": frappe,
        "frappe.model": mod("frappe.model"),
        "frappe.model.document": mod("frappe.model.document", Document=_Doc),
        "konsol": mod("konsol", __path__=[_APP]),
        "konsol.clickhouse": mod("konsol.clickhouse", execute=lambda *a, **k: "",
                                 ensure_raw_tables=lambda: None),
        "konsol.period_status": mod("konsol.period_status", assert_open=lambda *a, **k: None,
                                    assert_postable=lambda *a, **k: None),
        "konsol.schema_lifecycle": mod("konsol.schema_lifecycle", check_epm_admin=lambda: None),
        "konsol.entity_permissions": mod("konsol.entity_permissions",
                                         assigned_entities=assigned_entities,
                                         subtree_codes=subtree_codes),
    }


@contextlib.contextmanager
def _swapped(world):
    stubs = _stubs(world)
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _controller(world):
    with _swapped(world):
        spec = importlib.util.spec_from_file_location("tbs_on_behalf_under_test", _CTRL)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    return m


def _insert(world, **payload):
    """Run before_insert on a new TB built from the request payload."""
    m = _controller(world)
    doc = m.TrialBalanceSubmission(**{"__islocal": 1, "name": "new-tbs-1",
                                      "data_area_id": "ZZE1", **payload})
    with _swapped(world):
        doc.before_insert()
    return doc


def test_admin_upload_for_an_entity_is_yes():
    doc = _insert(_World(roles=["EPM Admin"]))
    assert doc.uploaded_on_behalf == "Yes"


def test_entity_accountant_in_scope_is_no():
    doc = _insert(_World(roles=["Entity Accountant"], assigned={"ZZE1"}))
    assert doc.uploaded_on_behalf == "No"


def test_entity_accountant_scope_is_the_subtree_of_the_assignment():
    doc = _insert(_World(roles=["Entity Accountant"], assigned={"ZZGRP"}, subtree={"ZZGRP", "ZZE1"}))
    assert doc.uploaded_on_behalf == "No"


def test_entity_accountant_out_of_scope_is_yes():
    doc = _insert(_World(roles=["Entity Accountant"], assigned={"ZZE2"}))
    assert doc.uploaded_on_behalf == "Yes"


def test_a_new_tb_always_gets_yes_or_no():
    for roles, assigned in ((["EPM Admin"], ()), (["Entity Accountant"], {"ZZE1"}),
                            (["System Manager"], ()), ([], ())):
        doc = _insert(_World(roles=roles, assigned=assigned))
        assert doc.uploaded_on_behalf in ("Yes", "No"), (roles, doc.uploaded_on_behalf)


def test_forged_payload_is_overwritten():
    # an admin claims "No"; an in-scope accountant claims "Yes"; stray fields are ignored
    doc = _insert(_World(roles=["EPM Admin"]), uploaded_on_behalf="No", on_behalf=0,
                  uploaded_for="ZZE1")
    assert doc.uploaded_on_behalf == "Yes"
    doc = _insert(_World(roles=["Entity Accountant"], assigned={"ZZE1"}),
                  uploaded_on_behalf="Yes")
    assert doc.uploaded_on_behalf == "No"
    doc = _insert(_World(roles=["Entity Accountant"], assigned={"ZZE1"}),
                  uploaded_on_behalf="")
    assert doc.uploaded_on_behalf == "No"


def _validate_until_the_period_check(world, doc):
    """A save as Frappe runs it (before_validate, then validate) up to
    validate's first external call, which is stopped."""
    m = _controller(world)
    doc.__class__ = m.TrialBalanceSubmission

    def stop(*a, **k):
        raise _Stop()

    m.assert_postable = stop
    with _swapped(world):
        try:
            doc.before_validate()
            doc.validate()
        except _Stop:
            pass
    return doc


def test_a_later_validate_does_not_change_the_flag_on_a_new_tb():
    world = _World(roles=["EPM Admin"])
    doc = _insert(world)
    _validate_until_the_period_check(world, doc)
    assert doc.uploaded_on_behalf == "Yes"


def test_a_forged_edit_of_a_saved_tb_is_put_back():
    world = _World(roles=["EPM Admin"], stored={("TBS-1", "uploaded_on_behalf"): "Yes"})
    doc = _Doc(name="TBS-1", data_area_id="ZZE1", fiscal_year=2026, fiscal_period=1, batch_id="b", uploaded_on_behalf="No")
    _validate_until_the_period_check(world, doc)
    assert doc.uploaded_on_behalf == "Yes"


def test_an_old_row_read_back_blank_stays_blank():
    # Problems 16: a TB from before the field existed is stored blank (NULL).
    for forged in ("No", "Yes", ""):
        world = _World(roles=["Entity Accountant"], assigned={"ZZE1"}, stored={})
        doc = _Doc(name="TBS-OLD", data_area_id="ZZE1", fiscal_year=2026, fiscal_period=1, batch_id="b", uploaded_on_behalf=forged)
        _validate_until_the_period_check(world, doc)
        assert not doc.uploaded_on_behalf, forged
    # and an old row that never had the attribute gains no value either
    world = _World(roles=["EPM Admin"])
    doc = _Doc(name="TBS-OLD", data_area_id="ZZE1", fiscal_year=2026, fiscal_period=1, batch_id="b")
    _validate_until_the_period_check(world, doc)
    assert not doc.get("uploaded_on_behalf")
