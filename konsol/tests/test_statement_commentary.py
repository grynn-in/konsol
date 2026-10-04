"""Statement Commentary — one versioned comment per (consolidation group,
fiscal year, fiscal period, heading) (konsol#305 M43; stories 8.3, 10.1;
#305-W4-5 5b, W4-6 6a; W4-E14).

The JSON is read as data. The controller is loaded by path against a stub
frappe and a stub ``konsol.fiscal_calendar``, with the REAL
``commentary_model`` (M42) attached under a stub ``konsol.close`` package.
The lazy ``from konsol.close import close_event`` inside ``on_update`` is
stubbed only for the tests that exercise it, following
test_close_tb_exception.py's ``_with_close`` pattern. sys.modules is restored
after every load.
"""
import ast
import contextlib
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DT_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "statement_commentary")
JSON_PATH = os.path.join(DT_DIR, "statement_commentary.json")
PY_PATH = os.path.join(DT_DIR, "statement_commentary.py")
INIT_PATH = os.path.join(DT_DIR, "__init__.py")
COMMENTARY_MODEL_PATH = os.path.join(APP_DIR, "close", "commentary_model.py")


def _json():
    with open(JSON_PATH) as f:
        return json.load(f)


def _fields():
    return {f["fieldname"]: f for f in _json()["fields"]}


# ---- JSON shape -------------------------------------------------------------

def test_files_exist():
    for path in (INIT_PATH, JSON_PATH, PY_PATH):
        assert os.path.exists(path), f"missing {os.path.relpath(path, APP_DIR)}"


def test_doctype_settings():
    d = _json()
    assert d["doctype"] == "DocType"
    assert d["name"] == "Statement Commentary"
    assert d["module"] == "Consolidation"
    assert d.get("is_submittable") in (0, None)
    assert d.get("track_changes") == 1
    assert d.get("autoname") == "format:SC-{consolidation_group}-{fiscal_year}-{fiscal_period}-{heading}"


def test_fields():
    f = _fields()
    expected = {
        "consolidation_group": ("Data", None),
        "fiscal_year": ("Int", None),
        "fiscal_period": ("Int", None),
        "heading": ("Link", "Main Account"),
        "heading_name": ("Data", None),
        "text": ("Text", None),
    }
    for name, (fieldtype, options) in expected.items():
        assert name in f, f"missing field {name}"
        assert f[name]["fieldtype"] == fieldtype, (name, f[name]["fieldtype"])
        if options:
            assert f[name].get("options") == options, (name, f[name].get("options"))
    for name in ("consolidation_group", "fiscal_year", "fiscal_period", "heading"):
        assert f[name].get("reqd") == 1, f"{name} must be required"
        assert f[name].get("set_only_once") == 1, f"{name} is the key: set only once"
    assert not f["text"].get("set_only_once"), "the text is the whole point of a save"
    assert f["heading_name"].get("read_only") == 1
    assert f["heading_name"].get("fetch_from") == "heading.account_name"
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
    assert p.get("System Manager") == {"read", "write", "create"}
    assert p.get("EPM Admin") == {"read", "write", "create"}
    assert p.get("EPM Analyst") == {"read", "write", "create"}, "the Group Accountant also writes commentary"
    assert p.get("EPM User") == {"read"}, "the Viewer reads only"
    assert set(p) == {"System Manager", "EPM Admin", "EPM Analyst", "EPM User"}, (
        f"unexpected roles: {sorted(set(p))}")
    for role, flags in p.items():
        assert "delete" not in flags, f"{role} must not delete commentary: it is history"


def test_controller_parses():
    with open(PY_PATH) as f:
        ast.parse(f.read())


def test_controller_never_names_the_close_event_doctype():
    with open(PY_PATH) as f:
        source = f.read()
    assert '"Close Event"' not in source and "'Close Event'" not in source


# ---- controller against a stub frappe --------------------------------------

class Refused(Exception):
    pass


def _load_commentary_model():
    spec = importlib.util.spec_from_file_location("commentary_model_real_m43", COMMENTARY_MODEL_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Site:
    """In-memory answers for the controller's reads."""

    def __init__(self, periods=(), main_accounts=None, root_groups=()):
        self.periods = list(periods)
        self.main_accounts = dict(main_accounts or {})
        self.root_groups = set(root_groups)
        self.get_value_calls = []
        self.exists_calls = []

    def get_value(self, doctype, name, fields, as_dict=False):
        self.get_value_calls.append((doctype, name, tuple(fields)))
        assert doctype == "Main Account"
        assert as_dict is True
        row = self.main_accounts.get(name)
        return None if row is None else dict(row)

    def exists(self, doctype, filters):
        self.exists_calls.append((doctype, dict(filters)))
        assert doctype == "Consolidation Group"
        assert filters.get("data_area_id") == ["is", "not set"]
        return filters.get("consolidation_group") in self.root_groups


class _Document:
    """A draft: new unless ``_before`` (the saved version) is given."""

    def __init__(self, **fields):
        before = fields.pop("_before", None)
        self.__dict__.update(fields)
        self.__dict__["_before"] = before

    def is_new(self):
        return self.__dict__.get("_before") is None

    def has_value_changed(self, fieldname):
        before = self.__dict__.get("_before")
        if before is None:
            return True
        return before.get(fieldname) != getattr(self, fieldname, None)


def _load(site, user="close.lead@example.com"):
    commentary_model = _load_commentary_model()

    fiscal_calendar = types.ModuleType("konsol.fiscal_calendar")
    fiscal_calendar.fiscal_period_rows = lambda: list(site.periods)

    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    close = types.ModuleType("konsol.close")
    close.__path__ = []
    close.commentary_model = commentary_model
    konsol.close = close
    konsol.fiscal_calendar = fiscal_calendar

    def throw(msg, exc=None, *a, **k):
        raise (exc or Refused)(msg)

    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.throw = throw
    frappe.db = site
    frappe.session = types.SimpleNamespace(user=user)

    doc_mod = types.ModuleType("frappe.model.document")
    doc_mod.Document = _Document

    mods = {
        "frappe": frappe,
        "frappe.model": types.ModuleType("frappe.model"),
        "frappe.model.document": doc_mod,
        "konsol": konsol,
        "konsol.close": close,
        "konsol.close.commentary_model": commentary_model,
        "konsol.fiscal_calendar": fiscal_calendar,
    }
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("statement_commentary_under_test", PY_PATH)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return m, frappe


@contextlib.contextmanager
def _with_close_event(log, raises=None):
    """Stub the lazy ``from konsol.close import close_event`` inside
    ``on_update`` only for the duration of the block; restored after."""
    close_event = types.ModuleType("konsol.close.close_event")

    def record(kind, *a, **k):
        if raises is not None:
            raise raises
        log.append((kind, a, k))
        return "CE-000000001"

    close_event.record = record
    saved = sys.modules.get("konsol.close.close_event")
    sys.modules["konsol.close.close_event"] = close_event
    try:
        yield
    finally:
        if saved is None:
            sys.modules.pop("konsol.close.close_event", None)
        else:
            sys.modules["konsol.close.close_event"] = saved


OPEN = (2025, 7, "Open")
CLOSED = (2025, 7, "Closed")
LOCKED = (2025, 7, "Locked")

HEADING_4 = {"is_group": 1, "status": "Published", "account_name": "COST OF SALES"}
LEAF_4100 = {"is_group": 0, "status": "Published", "account_name": "Freight in"}


def _periods(*rows):
    return [{"fiscal_year": fy, "fiscal_period": fp, "status": status} for fy, fp, status in rows]


def _doc(**kw):
    base = dict(
        name="SC-ZZGRP-2025-7-4", consolidation_group="ZZGRP", fiscal_year=2025,
        fiscal_period=7, heading="4", heading_name="COST OF SALES",
        text="Volume down 4% on the prior period.", _before=None,
    )
    base.update(kw)
    return _Document(**base)


def _refused(fn, expect, exc=Refused):
    try:
        fn()
    except exc as e:
        assert expect in str(e), f"wrong refusal: {e}"
        return
    raise AssertionError(f"not refused (expected {expect!r})")


def test_insert_in_an_open_period_passes_validate():
    site = _Site(periods=_periods(OPEN), main_accounts={"4": HEADING_4}, root_groups={"ZZGRP"})
    m, _ = _load(site)
    _doc_m = getattr(m, "StatementCommentary")
    doc = _doc_m(**{k: v for k, v in vars(_doc()).items() if not k.startswith("_")}, _before=None)
    doc.validate()  # does not throw
    assert site.get_value_calls == [("Main Account", "4", ("is_group", "status", "account_name"))]
    assert site.exists_calls == [("Consolidation Group", {"consolidation_group": "ZZGRP",
                                                            "data_area_id": ["is", "not set"]})]


def test_insert_writes_one_commentary_saved_event_with_the_m41_detail_and_no_entity():
    site = _Site(periods=_periods(OPEN), main_accounts={"4": HEADING_4}, root_groups={"ZZGRP"})
    m, _ = _load(site)
    doc = m.StatementCommentary(**{k: v for k, v in vars(_doc()).items() if not k.startswith("_")}, _before=None)
    log = []
    with _with_close_event(log):
        doc.validate()
        doc.on_update()
    assert [e[0] for e in log] == ["commentary_saved"], log
    _kind, args, kw = log[0]
    assert args == (2025, 7, "Statement Commentary", "SC-ZZGRP-2025-7-4")
    assert kw.get("entity") is None
    assert kw["detail"] == {
        "consolidation_group": "ZZGRP",
        "heading": "4",
        "heading_name": "COST OF SALES",
        "text": "Volume down 4% on the prior period.",
    }


def test_saving_with_the_same_text_writes_no_second_event():
    site = _Site(periods=_periods(OPEN), main_accounts={"4": HEADING_4}, root_groups={"ZZGRP"})
    m, _ = _load(site)
    before = dict(consolidation_group="ZZGRP", fiscal_year=2025, fiscal_period=7, heading="4",
                  heading_name="COST OF SALES", text="Volume down 4% on the prior period.")
    doc = m.StatementCommentary(**before, name="SC-ZZGRP-2025-7-4", _before=before)
    log = []
    with _with_close_event(log):
        doc.validate()
        doc.on_update()
    assert log == [], log


def test_changing_the_text_writes_one_more_event():
    site = _Site(periods=_periods(OPEN), main_accounts={"4": HEADING_4}, root_groups={"ZZGRP"})
    m, _ = _load(site)
    before = dict(consolidation_group="ZZGRP", fiscal_year=2025, fiscal_period=7, heading="4",
                  heading_name="COST OF SALES", text="Volume down 4% on the prior period.")
    doc = m.StatementCommentary(**dict(before, text="Volume down 6%, see the IC note."),
                                 name="SC-ZZGRP-2025-7-4", _before=before)
    log = []
    with _with_close_event(log):
        doc.validate()
        doc.on_update()
    assert [e[0] for e in log] == ["commentary_saved"], log
    assert log[0][2]["detail"]["text"] == "Volume down 6%, see the IC note."


def test_closed_period_is_refused_and_writes_no_event():
    site = _Site(periods=_periods(CLOSED), main_accounts={"4": HEADING_4}, root_groups={"ZZGRP"})
    m, _ = _load(site)
    doc = m.StatementCommentary(**{k: v for k, v in vars(_doc()).items() if not k.startswith("_")}, _before=None)
    log = []
    with _with_close_event(log):
        _refused(lambda: (doc.validate(), doc.on_update()), "Closed")
    assert log == [], log


def test_locked_period_is_refused_the_same_way():
    site = _Site(periods=_periods(LOCKED), main_accounts={"4": HEADING_4}, root_groups={"ZZGRP"})
    m, _ = _load(site)
    doc = m.StatementCommentary(**{k: v for k, v in vars(_doc()).items() if not k.startswith("_")}, _before=None)
    log = []
    with _with_close_event(log):
        _refused(lambda: (doc.validate(), doc.on_update()), "Locked")
    assert log == [], log


def test_a_leaf_heading_is_refused_and_writes_no_event():
    site = _Site(periods=_periods(OPEN), main_accounts={"4100": LEAF_4100}, root_groups={"ZZGRP"})
    m, _ = _load(site)
    doc = m.StatementCommentary(**dict(
        {k: v for k, v in vars(_doc()).items() if not k.startswith("_")}, heading="4100"), _before=None)
    log = []
    with _with_close_event(log):
        _refused(lambda: (doc.validate(), doc.on_update()), "4100")
    assert log == [], log


def test_an_undeclared_heading_is_refused():
    site = _Site(periods=_periods(OPEN), main_accounts={}, root_groups={"ZZGRP"})
    m, _ = _load(site)
    doc = m.StatementCommentary(**{k: v for k, v in vars(_doc()).items() if not k.startswith("_")}, _before=None)
    log = []
    with _with_close_event(log):
        _refused(lambda: (doc.validate(), doc.on_update()), "4")
    assert log == [], log


def test_a_non_root_group_is_refused_and_writes_no_event():
    site = _Site(periods=_periods(OPEN), main_accounts={"4": HEADING_4}, root_groups=set())
    m, _ = _load(site)
    doc = m.StatementCommentary(**{k: v for k, v in vars(_doc()).items() if not k.startswith("_")}, _before=None)
    log = []
    with _with_close_event(log):
        _refused(lambda: (doc.validate(), doc.on_update()), "is not a consolidation group")
    assert log == [], log


def test_an_undeclared_period_is_refused():
    site = _Site(periods=_periods(), main_accounts={"4": HEADING_4}, root_groups={"ZZGRP"})
    m, _ = _load(site)
    doc = m.StatementCommentary(**{k: v for k, v in vars(_doc()).items() if not k.startswith("_")}, _before=None)
    log = []
    with _with_close_event(log):
        _refused(lambda: (doc.validate(), doc.on_update()), "FY2025 P07")
    assert log == [], log


def test_a_failing_event_propagates_and_is_not_swallowed():
    site = _Site(periods=_periods(OPEN), main_accounts={"4": HEADING_4}, root_groups={"ZZGRP"})
    m, _ = _load(site)
    doc = m.StatementCommentary(**{k: v for k, v in vars(_doc()).items() if not k.startswith("_")}, _before=None)
    log = []
    with _with_close_event(log, raises=RuntimeError("event refused")):
        doc.validate()
        try:
            doc.on_update()
            assert False, "the writer's error was swallowed"
        except RuntimeError as e:
            assert str(e) == "event refused"


def test_on_trash_is_refused_for_every_role():
    site = _Site()
    m, frappe = _load(site)
    doc = m.StatementCommentary(**{k: v for k, v in vars(_doc()).items() if not k.startswith("_")}, _before=None)
    _refused(lambda: doc.on_trash(), "Commentary is history", exc=frappe.PermissionError)
