"""Guarded DocType renames (patches.txt lines 10-23).

The bare form — ``frappe.rename_doc(..., ignore_if_exists=True)`` — guards the
target but not the source, so a rename that does not apply to this site's
vintage raises ``DoesNotExistError``. ``patch_handler`` stops at the first
failure, so one inapplicable rename blocks every patch after it. That is how a
site ends up with ``Run Step`` renamed and ``Build Approval`` never created.

These tests cover the guard's decision table with a fake db, and assert the
structural properties of patches.txt that the renames depend on.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")


# ---- the guard's decision table ----------------------------------------

class _FakeDB:
    def __init__(self, doctypes, tables=None):
        self._doctypes = set(doctypes)
        self._tables = set(doctypes if tables is None else tables)

    def exists(self, doctype, name):
        assert doctype == "DocType"
        return name in self._doctypes

    def table_exists(self, name):
        return name in self._tables


def _load_helper(db):
    """Import safe_rename against a stubbed frappe."""
    fake = types.ModuleType("frappe")
    fake.db = db
    fake.renamed = []

    def rename_doc(doctype, old, new, **kwargs):
        fake.renamed.append((old, new))

    fake.rename_doc = rename_doc
    fake.logger = lambda: types.SimpleNamespace(info=lambda *a, **k: None,
                                                warning=lambda *a, **k: None)
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = fake
    try:
        path = os.path.join(APP_DIR, "patches", "doctype_renames.py")
        spec = importlib.util.spec_from_file_location("_konsol_doctype_renames", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, fake
    finally:
        if saved is not None:
            sys.modules["frappe"] = saved
        else:
            del sys.modules["frappe"]


def test_renames_when_source_exists_and_target_does_not():
    mod, fake = _load_helper(_FakeDB(["Close Run"]))
    assert mod.safe_rename("Close Run", "Period Close") is True
    assert fake.renamed == [("Close Run", "Period Close")]


def test_skips_when_source_is_absent():
    """A site of a later vintage never had this DocType. Must not raise."""
    mod, fake = _load_helper(_FakeDB(["Assertion Run"]))
    assert mod.safe_rename("Step Definition", "Pipeline Step") is False
    assert fake.renamed == []


def test_skips_when_target_already_exists():
    """Already renamed — re-running migrate must be a no-op."""
    mod, fake = _load_helper(_FakeDB(["Close Run", "Period Close"]))
    assert mod.safe_rename("Close Run", "Period Close") is False
    assert fake.renamed == [], "merging two live doctypes would lose data"


def test_skips_when_the_source_table_is_missing():
    """A DocType row without its table is a broken site, not one needing a
    rename. Skipping keeps migrate alive instead of crashing it."""
    mod, fake = _load_helper(_FakeDB(["Pipeline Definition"], tables=[]))
    assert mod.safe_rename("Pipeline Definition", "Pipeline") is False
    assert fake.renamed == []


def test_two_hop_chain_survives_a_partial_site():
    """Close Run -> Period Close -> Assertion Run. A site holding the middle
    name must complete the second hop and skip the first."""
    mod, fake = _load_helper(_FakeDB(["Period Close"]))
    assert mod.safe_rename("Close Run", "Period Close") is False
    assert mod.safe_rename("Period Close", "Assertion Run") is True
    assert fake.renamed == [("Period Close", "Assertion Run")]


def test_name_freeing_chain_runs_in_order():
    """Pipeline Step -> Run Step frees the name for Step Definition."""
    db = _FakeDB(["Pipeline Step", "Step Definition"])
    mod, fake = _load_helper(db)

    assert mod.safe_rename("Pipeline Step", "Run Step") is True
    db._doctypes.discard("Pipeline Step")
    db._doctypes.add("Run Step")
    db._tables = set(db._doctypes)

    assert mod.safe_rename("Step Definition", "Pipeline Step") is True
    assert fake.renamed == [("Pipeline Step", "Run Step"),
                            ("Step Definition", "Pipeline Step")]


# ---- patches.txt structure ---------------------------------------------

def _patches():
    with open(PATCHES_TXT) as f:
        return [l.strip() for l in f if l.strip()]


def test_no_bare_rename_doc_remains():
    """The unguarded form is what breaks migrate. None may come back."""
    offenders = [p for p in _patches() if "frappe.rename_doc(" in p and "safe_rename" not in p]
    assert offenders == [], f"unguarded rename_doc in patches.txt: {offenders}"


def test_every_rename_goes_through_the_guard():
    renames = [p for p in _patches() if "safe_rename(" in p]
    assert len(renames) == 13, f"expected 13 guarded renames, found {len(renames)}"


def test_pipeline_step_is_freed_before_it_is_reused():
    """Ordering is load-bearing: the name must be vacated first."""
    ps = [i for i, p in enumerate(_patches()) if 'safe_rename("Pipeline Step", "Run Step")' in p]
    sd = [i for i, p in enumerate(_patches()) if 'safe_rename("Step Definition", "Pipeline Step")' in p]
    assert ps and sd
    assert ps[0] < sd[0], "Pipeline Step must be renamed away before it is reused"


def test_close_run_chain_is_ordered():
    lines = _patches()
    first = [i for i, p in enumerate(lines) if 'safe_rename("Close Run", "Period Close")' in p]
    second = [i for i, p in enumerate(lines) if 'safe_rename("Period Close", "Assertion Run")' in p]
    assert first and second
    assert first[0] < second[0], "Close Run -> Period Close -> Assertion Run"


def test_field_rename_patches_check_the_table_first():
    """has_column raises when the table is absent, so the table check has to
    come first or the guard is what crashes migrate."""
    for name, doctype in [
        ("rename_pipeline_definition_name_to_pipeline_name", "Pipeline"),
        ("rename_build_scope_domain_name_to_scope_name", "Build Scope"),
        ("rename_pipeline_run_pipeline_build_request_to_build_approval", "Pipeline Run"),
    ]:
        with open(os.path.join(APP_DIR, "patches", f"{name}.py")) as f:
            src = f.read()
        table_at = src.find("frappe.db.table_exists(")
        column_at = src.find("frappe.db.has_column(")
        assert table_at != -1, f"{name} does not check table_exists"
        assert column_at != -1, f"{name} does not check has_column"
        assert table_at < column_at, f"{name} calls has_column before table_exists"
