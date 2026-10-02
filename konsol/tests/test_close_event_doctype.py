"""konsol#305 T01b — the `Close Event` doctype (#298 story 10.1, #305-W2-1).

An append-only audit log:
- no role can create, write, delete, submit, cancel, amend, import or export
  it; the read roles are System Manager, EPM Admin, EPM Analyst and EPM User;
- the controller refuses an insert made outside the writer context, any save
  of a saved row, and every delete, Administrator included (E10-P2,
  decision #305-W2-6, Deepak Pai 2 Oct 2026);
- no Link / Dynamic Link field and no ``data_area_id`` (E10-P8): a rename
  must never edit an immutable row, and the trail is not entity-scoped.

The controller is loaded by path against a stub frappe (the pattern of
test_close_self_approval.py:30-100, copied, not imported); the pure model is
loaded by path for the ``kind`` options.
"""
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DT_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "close_event")
JSON_PATH = os.path.join(DT_DIR, "close_event.json")
CONTROLLER_PY = os.path.join(DT_DIR, "close_event.py")
MODEL_PY = os.path.join(APP_DIR, "close", "close_event_model.py")

FIELDS = [
    ("fiscal_year", "Int"),
    ("fiscal_period", "Int"),
    ("kind", "Select"),
    ("entity", "Data"),
    ("reference_doctype", "Data"),
    ("reference_name", "Data"),
    ("actor", "Data"),
    ("at", "Datetime"),
    ("reason", "Small Text"),
    ("detail", "Code"),
    ("source", "Select"),
]
REQD = {"fiscal_year", "kind", "actor", "at", "source"}
READ_ROLES = {"System Manager", "EPM Admin", "EPM Analyst", "EPM User"}
FORBIDDEN = ("create", "write", "delete", "submit", "cancel", "amend", "import", "export")


def _meta():
    with open(JSON_PATH) as f:
        return json.load(f)


def _model():
    spec = importlib.util.spec_from_file_location("close_event_model_for_t01b", MODEL_PY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --- JSON -----------------------------------------------------------------

def test_fields_names_types_and_order():
    meta = _meta()
    got = [(f["fieldname"], f["fieldtype"]) for f in meta["fields"]]
    assert got == FIELDS, got
    assert meta["field_order"] == [n for n, _ in FIELDS]


def test_required_fields():
    fields = {f["fieldname"]: f for f in _meta()["fields"]}
    assert {n for n, f in fields.items() if f.get("reqd")} == REQD
    # Frappe treats an Int 0 as missing on a reqd field; 0 = the whole year
    assert not fields["fiscal_period"].get("reqd")


def test_select_options():
    fields = {f["fieldname"]: f for f in _meta()["fields"]}
    assert fields["source"]["options"] == "live\nbackfill"
    assert tuple(fields["kind"]["options"].split("\n")) == _model().KINDS
    assert fields["detail"].get("options") == "JSON"


def test_descriptions():
    fields = {f["fieldname"]: f for f in _meta()["fields"]}
    assert fields["fiscal_period"]["description"] == (
        "0 = the whole year (Close Year, Lock Year, Reopen Year). Otherwise the period the action was on.")
    assert fields["source"]["description"] == (
        "live: written when the action happened. backfill: recovered from existing records "
        "by the backfill patch (konsol#305 T06b).")


def _link_problems(meta):
    bad = []
    for f in meta["fields"]:
        if f["fieldtype"] in ("Link", "Dynamic Link"):
            bad.append("%s is a %s" % (f["fieldname"], f["fieldtype"]))
        if f["fieldname"] == "data_area_id":
            bad.append("data_area_id would make the trail entity-scoped")
    return bad


def test_no_link_and_no_data_area_id():
    meta = _meta()
    assert _link_problems(meta) == []
    # failure path: adding either one is caught
    forged = dict(meta, fields=meta["fields"] + [
        {"fieldname": "actor_user", "fieldtype": "Link", "options": "User"},
        {"fieldname": "data_area_id", "fieldtype": "Data"},
    ])
    assert len(_link_problems(forged)) == 2


def test_properties():
    meta = _meta()
    assert meta["name"] == "Close Event"
    assert meta["module"] == "Consolidation"
    assert meta["autoname"] == "CE-.#########"
    assert meta.get("in_create") == 1
    assert not meta.get("is_submittable")
    assert not meta.get("track_changes")
    assert not meta.get("allow_rename")
    assert meta["sort_field"] == "at"
    assert meta["sort_order"] == "DESC"


def _forge_problems(meta):
    bad = []
    for p in meta.get("permissions", []):
        for flag in FORBIDDEN:
            if p.get(flag):
                bad.append("%s has %s" % (p.get("role"), flag))
    return bad


def test_no_role_can_create_write_or_delete():
    meta = _meta()
    assert _forge_problems(meta) == []
    # failure path: any one forbidden grant is caught
    for flag in FORBIDDEN:
        forged = dict(meta, permissions=[dict(meta["permissions"][0], **{flag: 1})])
        assert _forge_problems(forged) == ["%s has %s" % (meta["permissions"][0]["role"], flag)]


def test_read_roles_exactly():
    perms = _meta()["permissions"]
    assert {p["role"] for p in perms if p.get("read")} == READ_ROLES
    assert {p["role"] for p in perms} == READ_ROLES
    for p in perms:
        assert p.get("report"), p["role"]


def test_init_file_exists():
    assert os.path.exists(os.path.join(DT_DIR, "__init__.py"))


# --- controller -------------------------------------------------------------

class _Flags(dict):
    def __getattr__(self, n):
        return self.get(n)

    def __setattr__(self, n, v):
        self[n] = v


def _load(user="Administrator"):
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, **k):
        raise (exc or frappe.ValidationError)(msg)

    frappe.throw = throw
    frappe.flags = _Flags()
    frappe.session = types.SimpleNamespace(user=user)

    class Document:
        def __init__(self, new=True):
            self._new = new

        def is_new(self):
            return self._new

    model_pkg = types.ModuleType("frappe.model")
    model_pkg.__path__ = []
    document_mod = types.ModuleType("frappe.model.document")
    document_mod.Document = Document
    model_pkg.document = document_mod
    frappe.model = model_pkg

    mods = {"frappe": frappe, "frappe.model": model_pkg, "frappe.model.document": document_mod}
    saved = {n: sys.modules.get(n) for n in mods}
    for n in list(sys.modules):
        if n == "konsol" or n.startswith("konsol."):
            saved.setdefault(n, sys.modules[n])
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("close_event_controller_under_test", CONTROLLER_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is not None:
                sys.modules[n] = old
            else:
                sys.modules.pop(n, None)
    return module, frappe, Document


def _raises(fn, exc):
    try:
        fn()
    except exc as e:
        return str(e)
    raise AssertionError("expected %s" % exc.__name__)


def test_insert_outside_the_writer_is_refused():
    mod, frappe, _ = _load()
    doc = mod.CloseEvent()
    msg = _raises(doc.before_insert, frappe.PermissionError)
    assert "konsol.close.close_event" in msg and "by hand" in msg


def test_insert_inside_the_writer_passes():
    mod, frappe, _ = _load()
    assert mod.active() is False
    with mod.writing():
        assert mod.active() is True
        mod.CloseEvent().before_insert()
    assert mod.active() is False


def test_writer_resets_when_the_block_raises():
    mod, frappe, _ = _load()

    class Boom(Exception):
        pass

    try:
        with mod.writing():
            raise Boom()
    except Boom:
        pass
    assert mod.active() is False
    _raises(mod.CloseEvent().before_insert, frappe.PermissionError)


def test_a_saved_row_cannot_change():
    mod, frappe, _ = _load()
    doc = mod.CloseEvent(new=False)
    msg = _raises(doc.validate, frappe.PermissionError)
    assert "append-only" in msg
    # even inside the writer: the writer inserts, it never edits
    with mod.writing():
        _raises(doc.validate, frappe.PermissionError)
    mod.CloseEvent(new=True).validate()


def test_delete_is_refused_for_everyone():
    for user in ("Administrator", "sm@example.com", "lead@example.com"):
        mod, frappe, _ = _load(user=user)
        for new in (False, True):
            msg = _raises(mod.CloseEvent(new=new).on_trash, frappe.PermissionError)
            assert "cannot be deleted" in msg
        with mod.writing():
            _raises(mod.CloseEvent(new=False).on_trash, frappe.PermissionError)


def test_after_delete_is_refused_for_everyone():
    # ignore_on_trash skips on_trash, but Frappe still runs after_delete once the
    # row is gone (konsol#305 T01c, W2-6, E10-P2 / W2-P2). The refusal here only
    # works through the request's rollback restoring the row.
    for user in ("Administrator", "sm@example.com", "lead@example.com"):
        mod, frappe, _ = _load(user=user)
        for new in (False, True):
            msg = _raises(mod.CloseEvent(new=new).after_delete, frappe.PermissionError)
            assert "cannot be deleted" in msg
        with mod.writing():
            _raises(mod.CloseEvent(new=False).after_delete, frappe.PermissionError)


def test_on_trash_and_after_delete_share_the_same_sentence():
    mod, frappe, _ = _load()
    on_trash_msg = _raises(mod.CloseEvent(new=False).on_trash, frappe.PermissionError)
    after_delete_msg = _raises(mod.CloseEvent(new=False).after_delete, frappe.PermissionError)
    assert on_trash_msg == after_delete_msg


def test_controller_imports_nothing_from_konsol_close():
    with open(CONTROLLER_PY) as f:
        src = f.read()
    for line in src.splitlines():
        s = line.strip()
        if s.startswith(("import ", "from ")):
            assert "konsol" not in s, s
