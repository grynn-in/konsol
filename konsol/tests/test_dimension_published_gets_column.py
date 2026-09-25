"""A Dimension that ends up Published gets its column, however it got there (konsol#295).

Decision of 25 September 2026, Deepak Pai, on konsol#295: "A dimension that
ends up Published gets its column (and its dbt vars) automatically, however it
got there." Rejected: keeping the save metadata-only and making the config
bundle tell the admin to run Apply Schema, because that leaves a second step
every onboarding has to remember.

Found by the konsol#255 live A/B: ``config_service.upsert_dimension(spec,
publish=False)`` with ``status: Published`` saved the row and never called
``publish()``, so no ``dim_*`` column was added and ``var('dimensions')`` was
not regenerated. ``Dimension.status`` is an editable Select, so the Desk form,
REST and data import reach the same state by a plain save.

These tests pin the outcome on every path, not on ``upsert_dimension`` alone:

  * any save that moves a Dimension INTO Published applies the schema;
  * any save that moves it OUT of Published applies the schema (the reverse
    move: its vars entry and budget fields have to go);
  * a save that changes a field the schema step reads, while Published,
    applies the schema;
  * a save that changes nothing the schema step reads requests no rebuild;
  * ``publish()`` / ``unpublish()`` apply it exactly once — not once in the
    save and again in the method;
  * a user who is not an EPM Admin cannot reach the schema step by editing
    ``status`` instead of pressing Publish.

"Applies the schema" is observed as a call to
``konsol.schema_lifecycle.apply_and_rebuild`` — the one function that runs the
DDL, regenerates the vars and requests the governed full-refresh build.

The controller is loaded by file path with stubbed frappe/konsol modules (the
pattern test_dimension_name_validation.py established). The stub ``Document``
does what Frappe's ``Document.save`` does in the order Frappe does it
(frappe/model/document.py, ``_save``/``insert``): load the stored row as the
doc-before-save (none on insert), run ``validate`` and ``before_save``, write
the row, then run ``on_update``. A stub that skipped ``on_update`` could not
see a controller hook at all, and one that skipped the doc-before-save could
not see a transition.
"""
import copy
import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_HERE, "..")
_SRC = os.path.join(_APP, "epm", "doctype", "dimension", "dimension.py")


class _ValidationError(Exception):
    """Stand-in for frappe.ValidationError."""


class _PermissionError(Exception):
    """Stand-in for frappe.PermissionError."""


class _MandatoryError(Exception):
    """Stand-in for frappe.MandatoryError."""


class _DoesNotExistError(Exception):
    """Stand-in for frappe.DoesNotExistError."""


def _throw(msg, exc=None, **kwargs):
    raise (exc or _ValidationError)(msg)


#: Every call to apply_and_rebuild, as (dimension name, action).
_REBUILDS = []
#: Whether the current user holds EPM Admin (check_epm_admin passes).
_ADMIN = {"on": True}
#: The committed Dimension rows, by name: the stub database.
_STORE = {}

#: The Dimension fields a row carries (dimension.json).
_FIELDS = (
    "dimension_name", "source_column", "label", "cube_type", "in_budget",
    "in_trial_balance", "survives_close", "allocation_role",
    "permission_doctype", "status",
)


def _check_epm_admin():
    if not _ADMIN["on"]:
        raise _PermissionError(
            "You need the 'EPM Admin' role to publish or unpublish.")


class _Flags(dict):
    """frappe._dict: attribute access, missing keys read as None."""

    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value


class _Row:
    """What Frappe's get_doc_before_save returns: the row as stored."""

    def __init__(self, values):
        self.__dict__.update(values)

    def get(self, key, default=None):
        return self.__dict__.get(key, default)


class _Doc:
    """frappe.model.document.Document, reduced to the save lifecycle."""

    doctype = "Dimension"

    def __init__(self):
        self.name = None
        self.flags = _Flags()
        for field in _FIELDS:
            setattr(self, field, None)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def get_doc_before_save(self):
        return getattr(self, "_doc_before_save", None)

    def _run(self, method):
        fn = getattr(self, method, None)
        if fn:
            fn()

    def save(self):
        # Frappe: a new doc is inserted and has no doc-before-save; an existing
        # one loads its stored row first. autoname is field:dimension_name, so
        # the name is set once, on insert, and never follows a later edit.
        if self.name is None:
            before = None
            name = self.dimension_name
        else:
            name = self.name
            before = _Row(copy.deepcopy(_STORE[name]))
        self._doc_before_save = before
        self._run("validate")
        self._run("before_save")
        self.name = name
        _STORE[name] = {f: getattr(self, f, None) for f in _FIELDS}
        self._run("on_update")
        return self

    insert = save

    def reload(self):
        for field, value in _STORE[self.name].items():
            setattr(self, field, value)


def _install_stubs():
    """Install the frappe/konsol stand-ins, replacing any a prior file left.

    Unconditional: the host runner restores sys.modules after each file, and
    reusing another file's stub frappe would bind this file to its fakes.
    """
    frappe = types.ModuleType("frappe")
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.throw = _throw
    frappe.ValidationError = _ValidationError
    frappe.PermissionError = _PermissionError
    frappe.MandatoryError = _MandatoryError
    frappe.DoesNotExistError = _DoesNotExistError
    frappe._dict = _Flags
    frappe.db = types.SimpleNamespace(
        exists=lambda doctype, name: name in _STORE,
        commit=lambda: None,
    )
    frappe.new_doc = lambda doctype: _m.Dimension()
    frappe.get_doc = _get_doc
    sys.modules["frappe"] = frappe

    konsol = types.ModuleType("konsol")
    # __path__ so konsol.config_service, konsol.connector_credentials and the
    # pure konsol.tb_dimension_model resolve to the REAL modules.
    konsol.__path__ = [_APP]
    sys.modules["konsol"] = konsol

    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = _Doc
    sys.modules["frappe.model"] = model
    sys.modules["frappe.model.document"] = document

    lifecycle = types.ModuleType("konsol.schema_lifecycle")
    lifecycle.check_epm_admin = _check_epm_admin
    lifecycle.apply_and_rebuild = (
        lambda doc, action: _REBUILDS.append((doc.name, action)))
    sys.modules["konsol.schema_lifecycle"] = lifecycle
    sys.modules.pop("konsol.config_service", None)


def _get_doc(doctype, name):
    if name not in _STORE:
        raise _DoesNotExistError(name)
    doc = _m.Dimension()
    doc.name = name
    doc.reload()
    return doc


_install_stubs()
_spec = importlib.util.spec_from_file_location("dimension_under_test_295", _SRC)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)

import konsol.config_service as config_service  # noqa: E402  (after the stubs)


# --- fixtures, by hand: the host runner has no pytest ---------------------


NAME = "dim_region"

#: The fields apply_schema_for_publish reads off a Published Dimension, and a
#: legal new value for each. Where each is read:
#:   dimension_name, source_column, label, cube_type, in_budget,
#:     allocation_role -> dbt_config._build_dimensions_vars (var('dimensions'))
#:   dimension_name, cube_type -> schema_apply._apply_clickhouse_columns
#:   dimension_name, in_trial_balance -> schema_apply._sync_tb_dimension_columns
#:   dimension_name, label, in_budget -> schema_apply._sync_budget_custom_fields
SCHEMA_EDITS = {
    "dimension_name": "dim_area",
    "source_column": "RegionCode",
    "label": "Sales Region",
    "cube_type": "number",
    "in_budget": 1,
    "in_trial_balance": 0,
    "allocation_role": "cost_center",
}


def _reset(admin=True):
    _STORE.clear()
    del _REBUILDS[:]
    _ADMIN["on"] = admin


def _stored(status, **fields):
    """A committed Dimension row in `status`, declared for the trial balance."""
    row = {f: None for f in _FIELDS}
    row.update(
        dimension_name=NAME, source_column="Region", label="Region",
        cube_type="string", in_budget=0, in_trial_balance=1, survives_close=0,
        status=status,
    )
    row.update(fields)
    _STORE[row["dimension_name"]] = row
    del _REBUILDS[:]
    return _get_doc("Dimension", row["dimension_name"])


def _spec_for(status):
    return {
        "dimension_name": NAME, "source_column": "Region", "label": "Region",
        "status": status,
    }


# --- the gap: Published without publish() --------------------------------


def test_upsert_creating_a_published_dimension_applies_the_schema():
    """The bundle path from the issue: publish=False, status Published."""
    _reset()
    config_service.upsert_dimension(_spec_for("Published"), publish=False)
    assert _STORE[NAME]["status"] == "Published"
    assert _REBUILDS == [(NAME, "Publish")], _REBUILDS


def test_upsert_moving_an_existing_draft_to_published_applies_the_schema():
    _reset()
    _stored("Draft")
    config_service.upsert_dimension(_spec_for("Published"), publish=False)
    assert _STORE[NAME]["status"] == "Published"
    assert _REBUILDS == [(NAME, "Publish")], _REBUILDS


def test_a_plain_save_moving_status_to_published_applies_the_schema():
    """Desk form, REST PUT and data-import update all end in Document.save."""
    for start in ("Draft", "Inactive"):
        _reset()
        doc = _stored(start)
        doc.status = "Published"
        doc.save()
        assert _REBUILDS == [(NAME, "Publish")], (start, _REBUILDS)


def test_inserting_a_dimension_as_published_applies_the_schema():
    """REST POST and data-import insert: a new doc that is Published at birth."""
    _reset()
    doc = _m.Dimension()
    for field, value in _spec_for("Published").items():
        setattr(doc, field, value)
    doc.cube_type = "string"
    doc.insert()
    assert _REBUILDS == [(NAME, "Publish")], _REBUILDS


def test_a_plain_save_moving_status_out_of_published_applies_the_schema():
    """The reverse move: var('dimensions') and the budget fields must drop it."""
    for end in ("Inactive", "Draft"):
        _reset()
        doc = _stored("Published")
        doc.status = end
        doc.save()
        assert _REBUILDS == [(NAME, "Unpublish")], (end, _REBUILDS)


def test_upsert_moving_published_to_inactive_applies_the_schema():
    _reset()
    _stored("Published")
    config_service.upsert_dimension(_spec_for("Inactive"), publish=False)
    assert _REBUILDS == [(NAME, "Unpublish")], _REBUILDS


def test_each_schema_field_edited_while_published_applies_the_schema():
    missed = []
    for field, value in SCHEMA_EDITS.items():
        _reset()
        doc = _stored("Published")
        setattr(doc, field, value)
        doc.save()
        if len(_REBUILDS) != 1:
            missed.append((field, list(_REBUILDS)))
    assert not missed, f"schema-relevant edits that applied no schema: {missed}"


# --- exactly once ---------------------------------------------------------


def test_publish_applies_the_schema_exactly_once():
    _reset()
    doc = _stored("Draft")
    doc.publish()
    assert _STORE[NAME]["status"] == "Published"
    assert _REBUILDS == [(NAME, "Publish")], _REBUILDS


def test_unpublish_applies_the_schema_exactly_once():
    _reset()
    doc = _stored("Published")
    doc.unpublish()
    assert _STORE[NAME]["status"] == "Inactive"
    assert _REBUILDS == [(NAME, "Unpublish")], _REBUILDS


def test_publish_of_an_already_published_dimension_still_applies_once():
    """Re-publishing is the documented repair (schema_lifecycle: "re-publishing
    recovers" a failed DDL), so a Publish with no transition still applies."""
    _reset()
    doc = _stored("Published")
    doc.publish()
    assert _REBUILDS == [(NAME, "Publish")], _REBUILDS


def test_upsert_with_publish_true_applies_the_schema_exactly_once():
    """A bundle that says Published AND passes publish=True: one apply, not
    one for the save and another for publish()."""
    for existing in (None, "Draft", "Published"):
        _reset()
        if existing:
            _stored(existing)
        config_service.upsert_dimension(_spec_for("Published"), publish=True)
        assert _STORE[NAME]["status"] == "Published"
        assert _REBUILDS == [(NAME, "Publish")], (existing, _REBUILDS)


# --- no rebuild when nothing the schema reads changed ---------------------


def test_a_save_that_changes_nothing_the_schema_reads_requests_no_rebuild():
    _reset()
    doc = _stored("Published")
    doc.permission_doctype = "Entity"   # not read by apply_schema_for_publish
    doc.save()
    doc.save()                          # and a save that changes nothing at all
    assert _REBUILDS == [], _REBUILDS


def test_a_check_arriving_as_text_zero_is_not_a_change():
    """REST and CSV carry a Check as the text "0"; it is the stored 0."""
    _reset()
    doc = _stored("Published")
    doc.in_budget = "0"
    doc.save()
    assert _REBUILDS == [], _REBUILDS


def test_editing_a_dimension_that_is_not_published_requests_no_rebuild():
    for status in ("Draft", "Inactive"):
        _reset()
        doc = _stored(status)
        for field, value in SCHEMA_EDITS.items():
            setattr(doc, field, value)
        doc.save()
        assert _REBUILDS == [], (status, _REBUILDS)


def test_moving_between_draft_and_inactive_requests_no_rebuild():
    for start, end in (("Draft", "Inactive"), ("Inactive", "Draft")):
        _reset()
        doc = _stored(start)
        doc.status = end
        doc.save()
        assert _REBUILDS == [], (start, end, _REBUILDS)


# --- the permission guard -------------------------------------------------


def test_a_non_admin_cannot_reach_the_schema_by_editing_status():
    """Publish is admin-only (check_epm_admin); editing status must be too."""
    _reset(admin=False)
    doc = _stored("Draft")
    doc.status = "Published"
    try:
        doc.save()
    except _PermissionError:
        pass
    else:
        raise AssertionError(
            "a non-admin moved a Dimension to Published with a plain save")
    assert _REBUILDS == [], _REBUILDS


def test_a_non_admin_cannot_reach_the_schema_by_editing_a_published_dimension():
    _reset(admin=False)
    doc = _stored("Published")
    doc.in_trial_balance = 0
    try:
        doc.save()
    except _PermissionError:
        pass
    else:
        raise AssertionError(
            "a non-admin changed a schema field on a Published Dimension")
    assert _REBUILDS == [], _REBUILDS


def test_a_non_admin_may_still_save_metadata_on_a_draft():
    """The guard is on the schema step, not on every save."""
    _reset(admin=False)
    doc = _stored("Draft")
    doc.label = "Sales Region"
    doc.save()
    assert _STORE[NAME]["label"] == "Sales Region"
    assert _REBUILDS == [], _REBUILDS
