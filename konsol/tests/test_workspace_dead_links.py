"""The workspace must not outlive the doctypes it links to (konsol#264 fallout).

Opening /app/konsolidat on the live site threw "DocType Allocation Run not
found" and still rendered an "Allocation Runs" tile. Measured in the live
database: `Allocation Run` had 0 rows in tabDocType, while tabWorkspace
Shortcut idx 5 and tabWorkspace Link idx 32/33/34 still pointed at Allocation
Run, Allocation Rule and Allocation Driver.

`_create_workspace` already filters every shortcut and card entry through
`_dt()`, so a REBUILD produces a clean workspace, and dashboard.py no longer
mentions allocation at all. The gap is the trigger:
`_workspace_needs_refresh()` tested only for ADDITIONS and layout changes --
Konsol Exec present, Konsol Control absent, pre-redesign card labels, the
Model & Metadata card, Main Account added (konsol#182), EPM Fiscal Year added
(konsol#189), number cards/charts, an Overview header. Nothing asked whether a
doctype it links to had DISAPPEARED, so a removal never triggered a refresh
and the dead links survived every migrate.

Note the shape these tests exist to break: each past addition got its own
bespoke one-shot clause. Removal gets one general condition instead, so the
next removal needs no new line here.

On stubbing: frappe.db.exists is a REAL function over a real set, never a
MagicMock. A MagicMock answers every attribute truthily, so `_dt()` would
return True for every name, the "doctype is missing" branch could never fire,
and these tests would pass while asserting nothing -- exactly the defect
konsol 81b468c fixed in the sync tests.
"""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD_PY = os.path.join(APP_DIR, "dashboard.py")
RETIRE_PY = os.path.join(APP_DIR, "patches", "retire_allocation.py")


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Workspace:
    """Only what _workspace_needs_refresh reads."""

    def __init__(self, shortcuts, links):
        self.shortcuts = shortcuts
        self.links = links
        self.number_cards = []
        self.charts = []
        self.content = "[]"


def _current_layout(extra_links=(), extra_shortcuts=()):
    """A workspace that every PRE-EXISTING refresh condition considers current.

    If any of them fired, a test below would pass for the wrong reason.
    """
    shortcuts = [_Row(label="Konsol Exec", link_to=None, type="URL"),
                 _Row(label="Datasets", link_to="Dataset", type="DocType")]
    links = [_Row(label="Model & Metadata", link_to=None, type="Card Break"),
             _Row(label="Datasets", link_to="Dataset", type="Link"),
             _Row(label="Group Chart of Accounts", link_to="Main Account", type="Link"),
             _Row(label="Fiscal Year", link_to="EPM Fiscal Year", type="Link")]
    return _Workspace(shortcuts + list(extra_shortcuts), links + list(extra_links))


LIVE_DOCTYPES = {"Dataset", "Main Account", "EPM Fiscal Year", "Workspace"}


def _dashboard_with(ws, doctypes=LIVE_DOCTYPES):
    """Import dashboard.py against a stub frappe whose exists() is real."""
    fake = types.ModuleType("frappe")

    def exists(doctype, name=None):
        if doctype == "Workspace":
            return True
        return name in doctypes if name is not None else doctype in doctypes

    fake.db = types.SimpleNamespace(exists=exists)
    fake.get_doc = lambda *a, **k: ws
    fake.logger = lambda *a, **k: types.SimpleNamespace(info=lambda *a, **k: None)
    fake.delete_doc = lambda *a, **k: None
    fake.new_doc = lambda *a, **k: None
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = fake
    try:
        spec = importlib.util.spec_from_file_location("_dash_under_test", DASHBOARD_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


# --- the stub itself must be able to say "no" -------------------------------

def test_the_stub_reports_a_missing_doctype_as_missing():
    """Guards the guard: if exists() were a MagicMock every test here is vacuous."""
    mod = _dashboard_with(_current_layout())
    assert mod._dt("Dataset") is True
    assert mod._dt("Allocation Run") is False


# --- a current workspace is left alone --------------------------------------

def test_a_current_workspace_does_not_need_a_refresh():
    """Without this, 'needs refresh' could be True for every input and the
    tests below would prove nothing."""
    mod = _dashboard_with(_current_layout())
    assert mod._workspace_needs_refresh() is False


# --- the defect ---------------------------------------------------------------

def test_a_link_to_a_deleted_doctype_forces_a_refresh():
    ws = _current_layout(extra_links=[
        _Row(label="Allocations", link_to=None, type="Card Break"),
        _Row(label="Allocation Runs", link_to="Allocation Run", type="Link"),
    ])
    mod = _dashboard_with(ws)
    assert mod._workspace_needs_refresh() is True, \
        "a workspace link pointing at a deleted DocType must trigger a rebuild"


def test_a_shortcut_to_a_deleted_doctype_forces_a_refresh():
    ws = _current_layout(extra_shortcuts=[
        _Row(label="Allocation Runs", link_to="Allocation Run", type="DocType"),
    ])
    mod = _dashboard_with(ws)
    assert mod._workspace_needs_refresh() is True, \
        "a workspace shortcut pointing at a deleted DocType must trigger a rebuild"


def test_a_url_shortcut_is_not_treated_as_a_missing_doctype():
    """Konsol Exec is type=URL with no link_to; it must not look deleted."""
    ws = _current_layout(extra_shortcuts=[
        _Row(label="Somewhere", link_to=None, type="URL", url="https://example.invalid"),
    ])
    mod = _dashboard_with(ws)
    assert mod._workspace_needs_refresh() is False


def test_a_card_break_is_not_treated_as_a_missing_doctype():
    """Card Breaks carry link_to=None; they are headings, not links."""
    ws = _current_layout(extra_links=[
        _Row(label="Consolidation", link_to=None, type="Card Break"),
    ])
    mod = _dashboard_with(ws)
    assert mod._workspace_needs_refresh() is False


# --- the cutover for sites that already migrated -----------------------------

def _src(path):
    with open(path) as fh:
        return fh.read()


def _func(src, name):
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.get_source_segment(src, n)
    return None


def test_the_retire_patch_rebuilds_the_workspace():
    """A site that migrated before this fix keeps its dead links until the
    patch reruns, and patches do not rerun. So the patch itself must rebuild,
    the same way it already drops the tables delete_doc leaves behind."""
    body = _func(_src(RETIRE_PY), "execute")
    assert body, "retire_allocation.execute not found"
    assert "setup_workspace" in body, \
        "retire_allocation does not rebuild the workspace, so an already-" \
        "migrated site keeps its Allocation shortcut and links"
    assert "force=True" in body, \
        "setup_workspace() without force=True is a no-op when the workspace " \
        "already exists"


def test_the_retire_patch_rebuilds_after_the_doctypes_are_gone():
    """Order matters: _create_workspace filters on _dt(), so a rebuild that
    ran before the deletions would put the allocation entries straight back."""
    body = _func(_src(RETIRE_PY), "execute")
    assert "setup_workspace" in body, "retire_allocation does not rebuild the workspace"
    deletion = body.index('"DocType", "Allocation Run"')
    assert body.index("setup_workspace") > deletion, \
        "the workspace is rebuilt before the allocation DocTypes are deleted, " \
        "so the rebuild would re-add the very links it is meant to remove"
