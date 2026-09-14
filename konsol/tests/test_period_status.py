"""Period Status — Open / Closed / Locked per (fiscal year, fiscal period).

Structural tests (no site needed), following the repo's convention for doctype
guards. The behavioural counterpart is test_fiscal_year_bench.py, which needs
a live site.

The design point these tests exist to protect: status is NOT a field on
``Fiscal Period``. That DocType is a template — ``format:FP-{fiscal_period}``
gives exactly fourteen records (OPN, P1..P12, CLS) that every fiscal year
reuses — so a status there would make closing September 2024 also close
September 2025.
"""
import ast
import glob
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _doctype_file(doctype_dir, ext):
    matches = glob.glob(os.path.join(
        APP_DIR, "*", "doctype", doctype_dir, f"{doctype_dir}.{ext}"))
    return matches[0] if matches else None


def _load_json(doctype_dir):
    with open(_doctype_file(doctype_dir, "json")) as f:
        return json.load(f)


def _fields(doctype_dir):
    return {f["fieldname"]: f for f in _load_json(doctype_dir).get("fields", [])}


# ---- the grain -----------------------------------------------------------

def test_period_status_doctype_exists():
    assert _doctype_file("period_status", "json") is not None


def test_keyed_by_year_and_period():
    f = _fields("period_status")
    assert "fiscal_year" in f
    assert "fiscal_period" in f
    d = _load_json("period_status")
    assert d["autoname"] == "format:PS-{fiscal_year}-{fiscal_period}", (
        "one record per (year, period); the name must carry both"
    )


def test_fiscal_period_is_the_number_not_a_link():
    """The whole app passes the period *number*: launch_options returns it,
    start_run takes it, the console sends it. Fiscal Period records are named
    FP-12, so a Link would store "FP-12" and quietly diverge from every other
    caller."""
    f = _fields("period_status")
    assert f["fiscal_period"]["fieldtype"] == "Int"
    assert "options" not in f["fiscal_period"]


def test_status_is_the_three_states():
    f = _fields("period_status")
    assert f["status"]["fieldtype"] == "Select"
    assert f["status"]["options"].split("\n") == ["Open", "Closed", "Locked"]
    assert f["status"]["default"] == "Open"


def test_records_who_closed_it():
    f = _fields("period_status")
    assert f["closed_by"]["read_only"] == 1
    assert f["closed_on"]["read_only"] == 1


def test_fiscal_period_doctype_did_not_grow_a_status():
    """The bug this design avoids: Fiscal Period is a template shared by every
    year, so a status on it would close the same month in all of them."""
    f = _fields("fiscal_period")
    assert "status" not in f, "status must live on Period Status, not the template"
    assert "fiscal_year" not in f, "Fiscal Period is year-agnostic by design"


# ---- retired: read-only history (konsol#189) ------------------------------
#
# Period status now lives on EPM Fiscal Year's period rows; its close / lock /
# reopen rules (and the group-rate gate) are tested in test_fiscal_year_actions.
# Period Status stays only as history until a later PR deletes it.

RETIRED = "Period Status is retired; set period status on the EPM Fiscal Year"
WRITE_FLAGS = ("write", "create", "submit", "cancel", "amend", "delete")


def _controller_src():
    with open(_doctype_file("period_status", "py")) as f:
        return f.read()


def test_controller_parses():
    ast.parse(_controller_src())


def _controller(in_patch):
    """period_status.py loaded by path against a stub frappe."""
    import importlib.util
    import sys
    import types

    class Thrown(Exception):
        pass

    def throw(msg, exc=None, *a, **k):
        raise (exc or Thrown)(msg)

    mods = {n: types.ModuleType(n) for n in ("frappe", "frappe.model", "frappe.model.document")}
    frappe = mods["frappe"]
    frappe._ = lambda s: s
    frappe.throw = throw
    frappe.ValidationError = Thrown
    frappe.flags = types.SimpleNamespace(in_patch=in_patch)
    mods["frappe.model.document"].Document = type("Document", (), {
        "__init__": lambda self, **kw: self.__dict__.update(kw)})
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location(
            "period_status_controller_under_test", _doctype_file("period_status", "py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return m, Thrown


def test_period_status_is_retired_read_only():
    rows = _load_json("period_status")["permissions"]
    assert rows, "the history stays readable"
    for p in rows:
        assert p.get("read") == 1, f"{p['role']} must still read the history"
        granted = [f for f in WRITE_FLAGS if p.get(f)]
        want = ["delete"] if p["role"] == "System Manager" else []
        assert granted == want, f"{p['role']} on retired Period Status: {granted}, want {want}"
    assert any(p["role"] == "System Manager" and p.get("delete") for p in rows), \
        "System Manager keeps delete to clear the history"

    m, Thrown = _controller(in_patch=False)
    doc = m.PeriodStatus(name="PS-2024-3", fiscal_year="2024", fiscal_period=3, status="Closed")
    try:
        doc.validate()
    except Thrown as e:
        assert str(e) == RETIRED
    else:
        raise AssertionError("saving a Period Status must be refused")
    before_insert = getattr(doc, "before_insert", None)
    if before_insert is not None:
        try:
            before_insert()
        except Thrown as e:
            assert str(e) == RETIRED
        else:
            raise AssertionError("inserting a Period Status must be refused")

    m, _ = _controller(in_patch=True)
    doc = m.PeriodStatus(name="PS-2024-3", fiscal_year="2024", fiscal_period=3, status="Closed")
    doc.validate()  # a migration patch may still write the history
    for hook in ("before_insert",):
        if hasattr(doc, hook):
            getattr(doc, hook)()
    assert "assert_rates_complete" not in _controller_src(), "the rate gate is EPM Fiscal Year's"


# ---- the run guard -------------------------------------------------------

def test_start_process_refuses_a_closed_period():
    """Enforced server-side, so the API cannot be used to post into a period
    someone has signed off."""
    with open(os.path.join(APP_DIR, "control_api.py")) as f:
        src = f.read()
    assert "assert_open" in src
    start = src.index("def start_process")
    nxt = src.index("def ", start + 10)
    assert "assert_open" in src[start:nxt], "the guard must be inside start_process"


def test_snapshot_reports_the_period_block():
    with open(os.path.join(APP_DIR, "control_api.py")) as f:
        src = f.read()
    assert "_period_block" in src
    assert "def get_snapshot(fiscal_year=None, fiscal_period=None)" in src


def test_set_period_status_is_whitelisted_and_permission_checked():
    with open(os.path.join(APP_DIR, "control_api.py")) as f:
        src = f.read()
    start = src.index("def set_period_status")
    nxt = src.index("def ", start + 10)
    body = src[start:nxt]
    assert "check_epm_admin()" in body
    assert "@frappe.whitelist()" in src[:start].rsplit("\n\n", 1)[-1] + src[start - 60:start]


def test_undeclared_period_is_refused():
    """konsol#189: only declared periods exist; nothing defaults to Open. The
    behaviour is exercised in test_period_status_api.py."""
    with open(os.path.join(APP_DIR, "period_status.py")) as f:
        src = f.read()
    assert "return status or OPEN" not in src
    assert "class PeriodNotDeclared" in src
