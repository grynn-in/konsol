"""Tests for the Close Assertion sign-off gate + dashboard (PRD §6.10 §4/§5).

Site-free AST/source + JSON-structure checks, matching the repo convention.
"""
import ast
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CR_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run")
CR_PY = os.path.join(CR_DIR, "assertion_run.py")
CR_JSON = os.path.join(CR_DIR, "assertion_run.json")
CR_JS = os.path.join(CR_DIR, "assertion_run.js")
RPT_DIR = os.path.join(APP_DIR, "consolidation", "report", "close_assertions")


def _funcs(path):
    with open(path) as fh:
        return {n.name for n in ast.walk(ast.parse(fh.read())) if isinstance(n, ast.FunctionDef)}


def _src(path):
    with open(path) as fh:
        return fh.read()


# --- gate: doctype fields --------------------------------------------------

def test_period_close_has_signoff_fields():
    meta = json.load(open(CR_JSON))
    fields = {f["fieldname"]: f for f in meta["fields"]}
    for fn in ("signoff_status", "signed_off_by", "signed_off_at", "override_reason"):
        assert fn in fields, f"missing {fn}"
    opts = fields["signoff_status"]["options"]
    assert "Not Signed Off" in opts and "Signed Off" in opts and "Overridden" in opts
    assert fields["signoff_status"]["read_only"] == 1  # only set via sign_off_close


# --- gate: logic -----------------------------------------------------------

def test_signoff_api_exists():
    fns = _funcs(CR_PY)
    assert {"sign_off_close", "assert_close_signed_off", "latest_close_run"} <= fns


def test_signoff_blocks_red_without_override():
    src = _src(CR_PY)
    # Green signs off; Red/Error requires a reason AND an override role
    assert 'doc.status == "Green"' in src
    assert "OVERRIDE_ROLES" in src and "frappe.get_roles()" in src
    assert "Overridden" in src
    # can't sign off an in-flight run
    assert '("Queued", "Running")' in src or "Queued" in src


def test_signoff_enforces_write_permission():
    """Green path must not be open to any user — write perm is checked before save."""
    src = _src(CR_PY)
    seg = src[src.index("def sign_off_close"):src.index("def assert_close_signed_off")]
    assert 'frappe.has_permission("Assertion Run", "write"' in seg
    assert "throw=True" in seg


def test_signoff_takes_row_lock():
    """Concurrent sign-offs must serialise (no double sign-off race)."""
    seg = _src(CR_PY)
    seg = seg[seg.index("def sign_off_close"):seg.index("def assert_close_signed_off")]
    assert "for_update=True" in seg


def test_signoff_checks_role_before_reason():
    """Override path checks the role first, then requires a reason."""
    seg = _src(CR_PY)
    seg = seg[seg.index("def sign_off_close"):seg.index("def assert_close_signed_off")]
    assert seg.index("OVERRIDE_ROLES & set(frappe.get_roles())") < seg.index("if not reason")


def test_latest_run_ordered_by_completion_not_creation():
    """A re-run that finishes later must win, so order by completed_at."""
    seg = _src(CR_PY)
    seg = seg[seg.index("def latest_close_run"):seg.index("def _failed_assertion_names")]
    assert "completed_at desc" in seg


def test_status_state_constants_centralised():
    src = _src(CR_PY)
    assert "TERMINAL_STATUSES" in src and "SIGNED_STATES" in src
    # report reuses them rather than re-hardcoding
    rsrc = _src(os.path.join(RPT_DIR, "close_assertions.py"))
    assert "TERMINAL_STATUSES" in rsrc and "SIGNED_STATES" in rsrc


def test_signoff_is_idempotent_guard():
    src = _src(CR_PY)
    assert "doc.signoff_status in SIGNED_STATES" in src


def test_assert_gate_hook_for_approval_chain():
    """assert_close_signed_off raises unless the period's latest run is signed off."""
    src = _src(CR_PY)
    seg = src[src.index("def assert_close_signed_off"):]
    assert "latest_close_run" in seg and "frappe.throw" in seg


# --- gate: client buttons --------------------------------------------------

def test_signoff_buttons_wired():
    src = _src(CR_JS)
    assert "sign_off_close" in src
    assert "Sign Off" in src and "Override" in src


# --- dashboard report ------------------------------------------------------

def test_close_assertions_report_registered():
    meta = json.load(open(os.path.join(RPT_DIR, "close_assertions.json")))
    assert meta["report_type"] == "Script Report"
    assert meta["ref_doctype"] == "Assertion Run"
    assert meta["report_name"] == "Close Assertions"


def test_close_assertions_report_executes_shape():
    fns = _funcs(os.path.join(RPT_DIR, "close_assertions.py"))
    assert "execute" in fns
    src = _src(os.path.join(RPT_DIR, "close_assertions.py"))
    # per-category board + summary + chart
    assert "_chart" in src and "_summary" in src and "Assertion Step" in src


# --- gate: undeclared fiscal year/period (konsol#189, PR#191 review finding 8)
# ---------------------------------------------------------------------------
# Assertion Runs with a period count toward fiscal_calendar.periods_in_use
# (they freeze the period), so an undeclared year or period must be refused
# in validate(), the same as IC Balance. A year-only run (no
# fiscal_period — the field is optional) must still refuse an undeclared
# year: it checks the year exists as an EPM Fiscal Year, it never invents one.
#
# assertion_run.py is loaded against a stub frappe + konsol.period_status, the
# same technique as test_group_exchange_rate.py's _rules_module/_controller.

def _load_assertion_run(declared_years=(), declared_periods=()):
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})

    def throw(msg, exc=None, *a, **k):
        raise (exc or frappe.ValidationError)(msg)

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.utils = types.SimpleNamespace(get_bench_path=lambda: "/bench")
    frappe.db = types.SimpleNamespace(
        exists=lambda dt, filters=None: (
            dt == "EPM Fiscal Year" and int(filters["fiscal_year"]) in declared_years
        ),
    )

    class Document:
        def __init__(self, **fields):
            is_new = fields.pop("_is_new", False)
            before_save = fields.pop("_before_save", None)
            self.__dict__.update(fields)
            self.__dict__["_is_new"] = is_new
            self.__dict__["_before_save"] = before_save

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return None

        def get(self, fieldname):
            return getattr(self, fieldname)

        def is_new(self):
            return self._is_new

        def get_doc_before_save(self):
            return self._before_save

        def has_value_changed(self, fieldname):
            """Mirrors frappe.model.document.Document.has_value_changed:
            no saved version means "changed" (insert path); otherwise
            compare against the saved value."""
            previous = self.get_doc_before_save()
            if not previous:
                return True
            return previous.get(fieldname) != self.get(fieldname)

    doc_mod = types.ModuleType("frappe.model.document")
    doc_mod.Document = Document

    period_status = types.ModuleType("konsol.period_status")
    period_status.PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})

    def assert_declared(fiscal_year, fiscal_period):
        if (int(fiscal_year), int(fiscal_period)) not in declared_periods:
            raise period_status.PeriodNotDeclared(
                f"FY{fiscal_year} has no period {fiscal_period}: not declared.")

    period_status.assert_declared = assert_declared

    mods = {
        "frappe": frappe,
        "frappe.model": types.ModuleType("frappe.model"),
        "frappe.model.document": doc_mod,
        "konsol": types.ModuleType("konsol"),
        "konsol.period_status": period_status,
    }
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("assertion_run_under_test", CR_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return module, period_status.PeriodNotDeclared


def test_undeclared_year_only_run_refused():
    """No fiscal_period given, FY2099 has no EPM Fiscal Year row: refused."""
    module, not_declared = _load_assertion_run(declared_years=set(), declared_periods=set())
    doc = module.AssertionRun(fiscal_year=2099, fiscal_period=None)
    try:
        doc.validate()
    except not_declared as e:
        assert "2099" in str(e)
    else:
        raise AssertionError("undeclared FY2099 (year-only run) was accepted")


def test_declared_year_only_run_ok():
    """No fiscal_period given, FY2099 IS declared: validate must not raise."""
    module, _ = _load_assertion_run(declared_years={2099}, declared_periods=set())
    doc = module.AssertionRun(fiscal_year=2099, fiscal_period=None)
    doc.validate()


def test_undeclared_period_refused_even_with_declared_year():
    module, not_declared = _load_assertion_run(declared_years={2099}, declared_periods=set())
    doc = module.AssertionRun(fiscal_year=2099, fiscal_period=12)
    try:
        doc.validate()
    except not_declared:
        pass
    else:
        raise AssertionError("undeclared period 12 of a declared FY2099 was accepted")


def test_declared_year_and_period_ok():
    module, _ = _load_assertion_run(declared_years={2099}, declared_periods={(2099, 12)})
    doc = module.AssertionRun(fiscal_year=2099, fiscal_period=12)
    doc.validate()


# --- gate: don't re-check an unchanged scope on every save (PR#191 review
# finding 5) --------------------------------------------------------------
# A year-only run is stored with fiscal_period=0 (Frappe stores an empty Int
# as 0). The worker and sign-off reload the saved run and save it again; if
# validate re-ran assert_declared(year, 0) on that save, a year with no
# Opening period (no declared (year, 0)) would refuse the run's own worker
# save and it would stay Queued forever. The gate must only fire on insert,
# or when fiscal_year/fiscal_period actually changed since the saved version.

def test_resave_year_only_run_not_regated():
    """A saved year-only run (fiscal_period stored as 0) in a year with no
    declared period 0 (no Opening period) must re-save fine: the scope
    didn't change, so the gate must not re-check it."""
    module, _ = _load_assertion_run(declared_years={2099}, declared_periods=set())
    doc = module.AssertionRun(
        fiscal_year=2099, fiscal_period=0,
        _is_new=False,
        _before_save={"fiscal_year": 2099, "fiscal_period": 0},
    )
    doc.validate()  # must not raise, even though (2099, 0) is undeclared


def test_changing_period_regates():
    """Changing fiscal_period on a saved run must re-check the new scope."""
    module, not_declared = _load_assertion_run(declared_years={2099}, declared_periods=set())
    doc = module.AssertionRun(
        fiscal_year=2099, fiscal_period=5,
        _is_new=False,
        _before_save={"fiscal_year": 2099, "fiscal_period": 3},
    )
    try:
        doc.validate()
    except not_declared:
        pass
    else:
        raise AssertionError("changing fiscal_period to an undeclared one was accepted")
