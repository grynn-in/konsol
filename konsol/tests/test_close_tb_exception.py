"""TB Exception (konsol#303 point 3, konsol#305 A08).

A submittable record of "no trial balance for <entity> in Pn, because
<reason>", declared by the Close Lead (EPM Admin). The completeness gate at
sign-off trusts a submitted one in place of a trial balance, so the controller
refuses a blank reason, a group entity, a closed period, an entity-period that
already has a submitted trial balance, and a second exception. ``declared_by``
is only ever the user who submitted it: a value sent on a draft is cleared.

The JSON is read as data; the controller is loaded by path against a stub
frappe and a stub ``konsol.period_status``; sys.modules is restored.
"""
import ast
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DT_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "tb_exception")
JSON_PATH = os.path.join(DT_DIR, "tb_exception.json")
PY_PATH = os.path.join(DT_DIR, "tb_exception.py")
INIT_PATH = os.path.join(DT_DIR, "__init__.py")
FISCAL_CALENDAR = os.path.join(APP_DIR, "fiscal_calendar.py")


def _json():
    with open(JSON_PATH) as f:
        return json.load(f)


def _fields():
    return {f["fieldname"]: f for f in _json()["fields"]}


# ---- JSON shape --------------------------------------------------------------

def test_files_exist():
    for path in (INIT_PATH, JSON_PATH, PY_PATH):
        assert os.path.exists(path), f"missing {os.path.relpath(path, APP_DIR)}"


def test_doctype_settings_mirror_trial_balance_submission():
    d = _json()
    assert d["doctype"] == "DocType"
    assert d["name"] == "TB Exception"
    assert d["module"] == "Consolidation"
    assert d.get("is_submittable") == 1
    assert d.get("track_changes") == 1
    assert d.get("autoname") == "TBX-.#####"
    assert d.get("title_field") == "data_area_id"


def test_fields():
    f = _fields()
    expected = {
        "data_area_id": ("Link", "Entity"),
        "fiscal_year": ("Int", None),
        "fiscal_period": ("Int", None),
        "reason": ("Small Text", None),
        "declared_by": ("Link", "User"),
        "amended_from": ("Link", "TB Exception"),
    }
    for name, (fieldtype, options) in expected.items():
        assert name in f, f"missing field {name}"
        assert f[name]["fieldtype"] == fieldtype, (name, f[name]["fieldtype"])
        if options:
            assert f[name].get("options") == options, (name, f[name].get("options"))
    for name in ("data_area_id", "fiscal_year", "fiscal_period", "reason"):
        assert f[name].get("reqd") == 1, f"{name} must be required"
    assert f["declared_by"].get("read_only") == 1, "declared_by is set by the controller"
    assert not f["declared_by"].get("reqd"), "declared_by is empty on a draft"
    assert f["amended_from"].get("read_only") == 1
    assert f["amended_from"].get("no_copy") == 1
    assert "options" not in f["fiscal_period"], (
        "the period is the number, not a Link")
    field_order = _json()["field_order"]
    for name in expected:
        assert name in field_order, f"{name} not in field_order"
    assert set(field_order) == set(f), sorted(set(field_order) ^ set(f))


FLAGS = ("read", "write", "create", "delete", "submit", "cancel", "amend")


def _perms():
    out = {}
    for row in _json()["permissions"]:
        assert row.get("permlevel", 0) == 0
        assert row["role"] not in out, f"duplicate permission row for {row['role']}"
        out[row["role"]] = {k for k in FLAGS if row.get(k)}
    return out


def test_permissions():
    p = _perms()
    assert p.get("System Manager") == set(FLAGS)
    assert p.get("EPM Admin") == {"read", "write", "create", "submit", "cancel", "amend"}
    assert p.get("EPM Analyst") == {"read"}, "the Group Accountant reads, never declares"
    assert p.get("EPM User") == {"read"}, "Viewers see the audit trail (R6)"
    assert set(p) == {"System Manager", "EPM Admin", "EPM Analyst", "EPM User"}, (
        f"unexpected roles: {sorted(set(p))}")


def test_controller_parses():
    with open(PY_PATH) as f:
        ast.parse(f.read())


# ---- fiscal_calendar registration ----------------------------------------------

def test_registered_as_period_data():
    with open(FISCAL_CALENDAR) as f:
        tree = ast.parse(f.read())
    period_data = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_PERIOD_DATA" for t in node.targets):
            period_data = ast.literal_eval(node.value)
    assert period_data is not None, "_PERIOD_DATA not found"
    assert period_data.get("TB Exception") is True, (
        "TB Exception must be period data (submittable)")


# ---- controller against a stub frappe ----------------------------------------------

class Thrown(Exception):
    pass


class PeriodNotDeclared(Thrown):
    pass


class _Site:
    """In-memory answers for the few reads the controller makes."""

    def __init__(self, groups=(), submitted_tbs=(), exceptions=(), closed=(), undeclared=()):
        self.groups = set(groups)
        self.submitted_tbs = set(submitted_tbs)      # (entity, fy, fp)
        self.exceptions = dict(exceptions)           # name -> (entity, fy, fp) submitted
        self.closed = set(closed)                    # (fy, fp)
        self.undeclared = set(undeclared)            # (fy, fp)
        self.calls = []

    def get_value(self, doctype, filters, fieldname=None, *a, **k):
        self.calls.append((doctype, filters, fieldname, k))
        if doctype == "Entity":
            name = filters if isinstance(filters, str) else filters.get("name")
            if fieldname == "is_group":
                return 1 if name in self.groups else 0
            return None
        key = (filters.get("data_area_id"), int(filters.get("fiscal_year")),
               int(filters.get("fiscal_period")))
        assert filters.get("docstatus") == 1, (doctype, filters)
        if doctype == "Trial Balance Submission":
            return "TBS-X" if key in self.submitted_tbs else None
        if doctype == "TB Exception":
            skip = filters.get("name")
            for name, val in self.exceptions.items():
                if val != key:
                    continue
                if isinstance(skip, (list, tuple)) and skip[0] == "!=" and skip[1] == name:
                    continue
                return name
            return None
        raise AssertionError(f"unexpected read of {doctype}")


def _load(site, user="close.lead@example.com"):
    period_status = types.ModuleType("konsol.period_status")
    period_status.PeriodNotDeclared = PeriodNotDeclared

    def assert_declared(fy, fp):
        if (int(fy), int(fp)) in site.undeclared:
            raise PeriodNotDeclared(f"FY{fy} has no period {fp}.")

    def assert_open(fy, fp, action="run"):
        assert_declared(fy, fp)
        if (int(fy), int(fp)) in site.closed:
            raise Thrown(f"Cannot {action}: fiscal period {fp} of FY{fy} is closed.")

    period_status.assert_declared = assert_declared
    period_status.assert_open = assert_open

    def throw(msg, exc=None, *a, **k):
        raise (exc or Thrown)(msg)

    names = ("frappe", "frappe.model", "frappe.model.document", "konsol", "konsol.period_status")
    mods = {n: types.ModuleType(n) for n in names[:4]}
    mods["konsol.period_status"] = period_status
    frappe = mods["frappe"]
    frappe._ = lambda s: s
    frappe.throw = throw
    frappe.ValidationError = Thrown
    frappe.db = site
    frappe.session = types.SimpleNamespace(user=user)
    mods["konsol"].period_status = period_status
    mods["konsol"].__path__ = []
    mods["frappe.model.document"].Document = type("Document", (), {
        "__init__": lambda self, **kw: self.__dict__.update(kw)})
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("tb_exception_under_test", PY_PATH)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return m, frappe


def _doc(m, **kw):
    base = dict(name="TBX-00001", docstatus=0, data_area_id="ZZOP", fiscal_year=2025,
                fiscal_period=3, reason="Dormant entity, no activity", declared_by=None)
    base.update(kw)
    return m.TBException(**base)


def _refused(fn, expect, exc=Thrown):
    try:
        fn()
    except exc as e:
        assert expect in str(e), f"wrong refusal: {e}"
        return
    raise AssertionError(f"not refused (expected {expect!r})")


def test_whitespace_reason_is_refused():
    m, _ = _load(_Site())
    for reason in ("   ", "\n\t", "", None):
        _refused(lambda: _doc(m, reason=reason).validate(), "reason")


def test_group_entity_is_refused():
    m, _ = _load(_Site(groups={"ZZGRP"}))
    _refused(lambda: _doc(m, data_area_id="ZZGRP").validate(), "group")


def test_closed_period_is_refused():
    m, _ = _load(_Site(closed={(2025, 3)}))
    _refused(lambda: _doc(m).validate(), "closed")


def test_undeclared_period_is_refused():
    m, _ = _load(_Site(undeclared={(2099, 1)}))
    _refused(lambda: _doc(m, fiscal_year=2099, fiscal_period=1).validate(),
             "FY2099", exc=PeriodNotDeclared)


def test_existing_submitted_tb_is_refused():
    m, _ = _load(_Site(submitted_tbs={("ZZOP", 2025, 3)}))
    _refused(lambda: _doc(m).validate(),
             "A trial balance is already submitted for ZZOP")


def test_duplicate_exception_is_refused():
    m, _ = _load(_Site(exceptions={"TBX-00007": ("ZZOP", 2025, 3)}))
    _refused(lambda: _doc(m).validate(), "TBX-00007")


def test_own_submitted_record_is_not_a_duplicate():
    """validate runs again on submit; the document must not collide with itself."""
    m, _ = _load(_Site(exceptions={"TBX-00001": ("ZZOP", 2025, 3)}))
    _doc(m, docstatus=1).validate()


def test_duplicate_check_is_a_locking_read():
    site = _Site()
    m, _ = _load(site)
    _doc(m).validate()
    reads = [c for c in site.calls if c[0] == "TB Exception"]
    assert reads and all(c[3].get("for_update") for c in reads), reads


def test_cancel_in_closed_period_is_refused():
    m, _ = _load(_Site(closed={(2025, 3)}))
    _refused(lambda: _doc(m, docstatus=1).before_cancel(),
             "cancel a trial balance exception")


def test_cancel_in_open_period_passes():
    m, _ = _load(_Site())
    _doc(m, docstatus=1).before_cancel()


def test_valid_exception_passes_and_declared_by_is_the_session_user():
    m, _ = _load(_Site(), user="close.lead@example.com")
    doc = _doc(m)
    doc.validate()
    doc.before_submit()
    assert doc.declared_by == "close.lead@example.com"


def test_forged_declared_by_is_cleared_on_draft_and_replaced_on_submit():
    m, _ = _load(_Site(), user="close.lead@example.com")
    doc = _doc(m, declared_by="someone.else@example.com")
    doc.validate()
    assert doc.declared_by is None, "a draft names no declarer"
    doc.docstatus = 1
    doc.declared_by = "someone.else@example.com"
    doc.validate()
    doc.before_submit()
    assert doc.declared_by == "close.lead@example.com"
