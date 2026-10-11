"""A Measure that ends up Published gets its dbt vars, however it got there (konsol#299).

Decision of 11 October 2026, Deepak Pai, option #299-1 on konsol#299: a
Measure gets the same ``on_update`` transition hook as Dimension (konsol#295,
PR #296). Moving into or out of Published, or a schema-relevant edit while
Published (expression, label, cube_type), applies the schema exactly once, by
any route: bundle, Desk, REST, import. Rejected: #299-2, publish() only,
documented.

Before this, ``config_service.upsert_measure`` with ``status: Published`` and
``publish=False`` saved the row and never regenerated ``base_measures``; nor
did a Desk status edit, REST, a data import, or an edit of a field
``dbt_config._build_measures_vars`` reads while Published. Measures have no
DDL, so the symptom is stale dbt vars, not a missing column.

"Applies the schema" is observed as a call to
``konsol.schema_lifecycle.apply_and_rebuild``.

The stub ``Document`` follows Frappe's save in Frappe's order, as
test_dimension_published_gets_column.py does: load the stored row as the
doc-before-save (none on insert), run ``before_validate``, ``validate`` and
``before_save``, reset the autoname field to the name
(``_sync_autoname_field``), write the row with Frappe's Check cast
(``1 if cint(value) else 0``) for every Check in measure.json, then run
``on_update``. Measure has no Check field today; the cast is read from the
doctype so a Check added later is cast, not compared raw.
"""
import ast
import copy
import importlib.util
import json
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_HERE, "..")
_DOCTYPE_DIR = os.path.join(_APP, "epm", "doctype", "measure")
_SRC = os.path.join(_DOCTYPE_DIR, "measure.py")


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


#: Every call to apply_and_rebuild, as (measure name, action).
_REBUILDS = []
#: The stored row at each apply_and_rebuild call.
_APPLIED_ROWS = []
#: Whether the current user holds EPM Admin (check_epm_admin passes).
_ADMIN = {"on": True}
#: The committed Measure rows, by name: the stub database.
_STORE = {}

with open(os.path.join(_DOCTYPE_DIR, "measure.json")) as _f:
    _META = json.load(_f)
_NO_VALUE = {"Section Break", "Column Break", "Tab Break", "HTML", "Button"}
#: The Measure fields a row carries (measure.json).
_FIELDS = tuple(f["fieldname"] for f in _META["fields"] if f["fieldtype"] not in _NO_VALUE)
#: measure.json's Check fields: Frappe writes them as 0 or 1.
_CHECK_FIELDS = tuple(f["fieldname"] for f in _META["fields"] if f["fieldtype"] == "Check")
#: The autoname field: Frappe puts it back to the name on every save.
_AUTONAME_FIELD = _META["autoname"].split(":", 1)[1]


def _cint(value):
    """frappe.utils.cint: int(), then int(float()), else 0."""
    try:
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return 0


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

    doctype = "Measure"

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
        # A new doc is inserted and has no doc-before-save; an existing one
        # loads its stored row first. The name is set once, on insert.
        if self.name is None:
            before = None
            name = getattr(self, _AUTONAME_FIELD)
        else:
            name = self.name
            before = _Row(copy.deepcopy(_STORE[name]))
        self._doc_before_save = before
        self._run("before_validate")
        self._run("validate")
        self._run("before_save")
        self.name = name
        # _validate -> _sync_autoname_field: an edited measure_name is put
        # back to the name before the write.
        setattr(self, _AUTONAME_FIELD, name)
        row = {f: getattr(self, f, None) for f in _FIELDS}
        for f in _CHECK_FIELDS:
            # get_valid_dict: what db_insert / db_update actually write.
            row[f] = 1 if _cint(row[f]) else 0
        _STORE[name] = row
        self._run("on_update")
        return self

    insert = save

    def reload(self):
        for field, value in _STORE[self.name].items():
            setattr(self, field, value)


def _apply_and_rebuild(doc, action):
    _REBUILDS.append((doc.name, action))
    # What regenerate_vars would read: the row as written.
    _APPLIED_ROWS.append(dict(_STORE[doc.name]))
    return f"ZZ-BAPR-{len(_REBUILDS):05d}"


def _get_doc(doctype, name):
    if name not in _STORE:
        raise _DoesNotExistError(name)
    doc = _m.Measure()
    doc.name = name
    doc.reload()
    return doc


def _install_stubs():
    """Install the frappe/konsol stand-ins, replacing any a prior file left."""
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
    frappe.new_doc = lambda doctype: _m.Measure()
    frappe.get_doc = _get_doc
    sys.modules["frappe"] = frappe

    konsol = types.ModuleType("konsol")
    # __path__ so konsol.config_service and konsol.connector_credentials
    # resolve to the REAL modules.
    konsol.__path__ = [_APP]
    sys.modules["konsol"] = konsol

    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = _Doc
    sys.modules["frappe.model"] = model
    sys.modules["frappe.model.document"] = document

    lifecycle = types.ModuleType("konsol.schema_lifecycle")
    lifecycle.check_epm_admin = _check_epm_admin
    lifecycle.apply_and_rebuild = _apply_and_rebuild
    sys.modules["konsol.schema_lifecycle"] = lifecycle
    sys.modules.pop("konsol.config_service", None)


_install_stubs()
_spec = importlib.util.spec_from_file_location("measure_under_test_299", _SRC)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)

import konsol.config_service as config_service  # noqa: E402  (after the stubs)


NAME = "zz_period_amount"

#: The fields dbt_config._build_measures_vars reads off a Published Measure
#: (measure_name, expression, label, cube_type), less measure_name, which a
#: save cannot change; and a legal new value for each.
SCHEMA_EDITS = {
    "expression": "sum(debit_amount)",
    "label": "Period Debit",
    "cube_type": "avg",
}


def _reset(admin=True):
    _STORE.clear()
    del _REBUILDS[:]
    del _APPLIED_ROWS[:]
    _ADMIN["on"] = admin


def _stored(status, **fields):
    """A committed Measure row in `status`."""
    row = {f: None for f in _FIELDS}
    row.update(
        measure_name=NAME, expression="sum(amount)", label="Period Amount",
        cube_type="sum", status=status,
    )
    row.update(fields)
    _STORE[row["measure_name"]] = row
    del _REBUILDS[:]
    return _get_doc("Measure", row["measure_name"])


def _spec_for(status):
    return {
        "measure_name": NAME, "expression": "sum(amount)",
        "label": "Period Amount", "status": status,
    }


# --- what the schema reads ------------------------------------------------


def _measures_vars_fields():
    """The fields= list dbt_config._build_measures_vars passes to get_all."""
    with open(os.path.join(_APP, "dbt_config.py")) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_build_measures_vars")
    for node in ast.walk(fn):
        if isinstance(node, ast.keyword) and node.arg == "fields":
            return [elt.value for elt in node.value.elts]
    raise AssertionError("_build_measures_vars has no fields= list")


def test_schema_fields_are_exactly_what_the_measures_vars_read():
    read = set(_measures_vars_fields())
    assert read == {"measure_name", "expression", "label", "cube_type"}, read
    assert set(_m.SCHEMA_FIELDS) == read - {_AUTONAME_FIELD}, _m.SCHEMA_FIELDS
    assert set(SCHEMA_EDITS) == set(_m.SCHEMA_FIELDS)


def test_the_stub_reads_the_doctype():
    """A guard: the stub's fields and autoname come from measure.json."""
    assert {"measure_name", "expression", "label", "cube_type", "status"} <= set(_FIELDS)
    assert _AUTONAME_FIELD == "measure_name"


# --- the gap: Published without publish() --------------------------------


def test_upsert_creating_a_published_measure_applies_the_schema():
    """The bundle path from the issue: publish=False, status Published."""
    _reset()
    config_service.upsert_measure(_spec_for("Published"), publish=False)
    assert _STORE[NAME]["status"] == "Published"
    assert _REBUILDS == [(NAME, "Publish")], _REBUILDS


def test_upsert_moving_an_existing_draft_to_published_applies_the_schema():
    _reset()
    _stored("Draft")
    config_service.upsert_measure(_spec_for("Published"), publish=False)
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


def test_inserting_a_measure_as_published_applies_the_schema():
    """REST POST and data-import insert: a new doc that is Published at birth."""
    _reset()
    doc = _m.Measure()
    for field, value in _spec_for("Published").items():
        setattr(doc, field, value)
    doc.cube_type = "sum"
    doc.insert()
    assert _REBUILDS == [(NAME, "Publish")], _REBUILDS
    assert _APPLIED_ROWS[0]["status"] == "Published", _APPLIED_ROWS


def test_a_plain_save_moving_status_out_of_published_applies_the_schema():
    """The reverse move: base_measures must drop it."""
    for end in ("Inactive", "Draft"):
        _reset()
        doc = _stored("Published")
        doc.status = end
        doc.save()
        assert _REBUILDS == [(NAME, "Unpublish")], (end, _REBUILDS)


def test_upsert_moving_published_to_inactive_applies_the_schema():
    _reset()
    _stored("Published")
    config_service.upsert_measure(_spec_for("Inactive"), publish=False)
    assert _REBUILDS == [(NAME, "Unpublish")], _REBUILDS


def test_each_schema_field_edited_while_published_applies_the_schema():
    missed = []
    for field, value in SCHEMA_EDITS.items():
        _reset()
        doc = _stored("Published")
        setattr(doc, field, value)
        doc.save()
        if _REBUILDS != [(NAME, "Publish")] or _APPLIED_ROWS[0][field] != value:
            missed.append((field, list(_REBUILDS)))
    assert not missed, f"schema-relevant edits that applied no schema: {missed}"


def test_upsert_editing_the_expression_of_a_published_measure_applies_the_schema():
    _reset()
    _stored("Published")
    spec = _spec_for("Published")
    spec["expression"] = "sum(debit_amount)"
    config_service.upsert_measure(spec, publish=False)
    assert _REBUILDS == [(NAME, "Publish")], _REBUILDS
    assert _APPLIED_ROWS[0]["expression"] == "sum(debit_amount)"


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


def test_publish_of_an_already_published_measure_still_applies_once():
    """Re-publishing is the documented repair, so a Publish with no
    transition still applies."""
    _reset()
    doc = _stored("Published")
    doc.publish()
    assert _REBUILDS == [(NAME, "Publish")], _REBUILDS


def test_unpublish_of_an_inactive_measure_still_applies_once():
    _reset()
    doc = _stored("Inactive")
    doc.unpublish()
    assert _REBUILDS == [(NAME, "Unpublish")], _REBUILDS


def test_upsert_with_publish_true_applies_the_schema_exactly_once():
    """A bundle that says Published AND passes publish=True: one apply."""
    for existing in (None, "Draft", "Published"):
        for status in ("Published", "Draft"):
            _reset()
            if existing:
                _stored(existing)
            config_service.upsert_measure(_spec_for(status), publish=True)
            assert _STORE[NAME]["status"] == "Published"
            assert _REBUILDS == [(NAME, "Publish")], (existing, status, _REBUILDS)


def test_a_refused_publish_does_not_force_the_next_save():
    """publish() sets a flag that forces the apply; a save refused before
    on_update must not leave it to force the next, unrelated save."""
    _reset()
    doc = _stored("Published")

    def refuse():
        raise _ValidationError("refused")
    doc.validate = refuse
    try:
        doc.publish()
    except _ValidationError:
        pass
    del doc.validate
    doc.save()
    assert _REBUILDS == [], _REBUILDS


# --- no rebuild when nothing the schema reads changed ---------------------


def test_a_save_that_changes_nothing_requests_no_rebuild():
    _reset()
    doc = _stored("Published")
    doc.save()
    doc.save()
    assert _REBUILDS == [], _REBUILDS


def test_editing_a_measure_that_is_not_published_requests_no_rebuild():
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


def test_inserting_a_draft_measure_requests_no_rebuild():
    _reset()
    config_service.upsert_measure(_spec_for("Draft"), publish=False)
    assert _STORE[NAME]["status"] == "Draft"
    assert _REBUILDS == [], _REBUILDS


def test_editing_measure_name_is_reverted_and_applies_nothing():
    """autoname is field:measure_name: Frappe puts the name back."""
    _reset()
    doc = _stored("Published")
    doc.measure_name = "zz_other_amount"
    doc.save()
    assert _STORE[NAME]["measure_name"] == NAME, _STORE[NAME]
    assert _REBUILDS == [], _REBUILDS


# --- the permission guard -------------------------------------------------


def _refused(fn):
    try:
        fn()
    except _PermissionError:
        return True
    return False


def test_a_non_admin_cannot_reach_the_schema_by_editing_status():
    _reset(admin=False)
    doc = _stored("Draft")
    doc.status = "Published"
    assert _refused(doc.save), "a non-admin moved a Measure to Published with a plain save"
    assert _REBUILDS == [], _REBUILDS


def test_a_non_admin_cannot_reach_the_schema_by_editing_a_published_measure():
    _reset(admin=False)
    doc = _stored("Published")
    doc.expression = "sum(debit_amount)"
    assert _refused(doc.save), "a non-admin changed a schema field on a Published Measure"
    assert _REBUILDS == [], _REBUILDS


def test_a_non_admin_cannot_press_publish():
    _reset(admin=False)
    doc = _stored("Draft")
    assert _refused(doc.publish)
    assert _STORE[NAME]["status"] == "Draft"
    assert _REBUILDS == [], _REBUILDS


def test_a_non_admin_may_still_save_metadata_on_a_draft():
    """The guard is on the schema step, not on every save."""
    _reset(admin=False)
    doc = _stored("Draft")
    doc.label = "Period Debit"
    doc.save()
    assert _STORE[NAME]["label"] == "Period Debit"
    assert _REBUILDS == [], _REBUILDS
