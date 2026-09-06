"""Period Status — Open / Closed / Locked per (fiscal year, fiscal period).

Structural tests (no site needed), following the repo's convention for doctype
guards. The behavioural counterpart is test_period_status_bench.py, which needs
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


# ---- the controller's guards --------------------------------------------

def _controller_src():
    with open(_doctype_file("period_status", "py")) as f:
        return f.read()


def test_controller_parses():
    ast.parse(_controller_src())


def test_locked_periods_are_guarded_against_reopen():
    src = _controller_src()
    assert "_guard_reopen" in src
    assert "System Manager" in src
    assert "PermissionError" in src


def test_closure_is_stamped_and_cleared():
    src = _controller_src()
    assert "closed_by" in src
    assert "closed_on" in src


def test_period_number_is_validated():
    src = _controller_src()
    assert "_validate_period_exists" in src
    assert "Fiscal Period" in src


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


def test_absent_record_means_open():
    """No pre-population of the fourteen-by-N grid, and no backfill on upgrade."""
    with open(os.path.join(APP_DIR, "period_status.py")) as f:
        src = f.read()
    assert "return status or OPEN" in src
