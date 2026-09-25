"""konsol#305 A22 (story 9.2, #303-3a): the sign-off gates are wired into
`sign_off_close`, and it is POST-only (Problems 10).

`sign_off_close` refuses a run with no period, then calls
`konsol.close.signoff_gate.assert_can_sign(year, period)` before any state
changes. The gate itself (order, completeness, configuration gaps) is tested in
test_close_signoff_gate.py; this file tests only that the sign-off calls it,
with the run's own period, and stops when it raises.

`assertion_run.py` is loaded against a stub frappe (the `_load` of
test_assertion_warn_amber.py:58-133, copied, not imported). The gate is
imported lazily inside `sign_off_close`, so the stub gate is installed in
`sys.modules` only for the duration of each call.
"""
import ast
import contextlib
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AR_PY = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run", "assertion_run.py")
AR_JSON = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run", "assertion_run.json")


class GateBlocked(Exception):
    """What the stub gate raises: stands for "Sign-off blocked"."""


def _load(status="Green", signoff_status="Not Signed Off", fiscal_year=2099, fiscal_period=1,
          gate_raises=False, affected_by=None):
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, **k):
        raise (exc or frappe.ValidationError)(msg)

    saved_doc = types.SimpleNamespace(
        name="AR-1", status=status, warned=0, signoff_status=signoff_status,
        signoff_saved=False, acknowledgement=None, warnings_at_signoff=None,
        override_reason=None, signed_off_by=None, signed_off_at=None,
        fiscal_year=fiscal_year, fiscal_period=fiscal_period, affected_by=affected_by,
    )
    saved_doc.save = lambda **k: setattr(saved_doc, "signoff_saved", True)

    frappe.throw = throw
    frappe._ = lambda s: types.SimpleNamespace(format=lambda *a: s.format(*a))
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.get_roles = lambda: ["EPM Admin"]
    frappe.has_permission = lambda *a, **k: True
    frappe.session = types.SimpleNamespace(user="lead@example.com")
    frappe.utils = types.SimpleNamespace(get_bench_path=lambda: "/bench",
                                         now_datetime=lambda: "NOW")
    frappe.db = types.SimpleNamespace(
        get_value=lambda *a, **k: "AR-1", commit=lambda: None, exists=lambda *a, **k: True)
    frappe.get_doc = lambda dt, name=None: saved_doc
    frappe.get_all = lambda dt, **k: []
    frappe.publish_realtime = lambda *a, **k: None

    class Document:
        def __init__(self, **f):
            self.__dict__.update(f)

    doc_mod = types.ModuleType("frappe.model.document")
    doc_mod.Document = Document

    period_status = types.ModuleType("konsol.period_status")
    period_status.PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})
    period_status.assert_declared = lambda *a: None

    as_spec = importlib.util.spec_from_file_location(
        "konsol.assertion_status", os.path.join(APP_DIR, "assertion_status.py"))
    assertion_status = importlib.util.module_from_spec(as_spec)
    as_spec.loader.exec_module(assertion_status)

    mods = {"frappe": frappe, "frappe.model": types.ModuleType("frappe.model"),
            "frappe.model.document": doc_mod, "konsol": types.ModuleType("konsol"),
            "konsol.period_status": period_status,
            "konsol.assertion_status": assertion_status}
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("assertion_run_under_test", AR_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old

    calls = []

    def assert_can_sign(fy, fp):
        calls.append((fy, fp))
        if gate_raises:
            raise GateBlocked("Sign-off blocked: Declare the first close period")

    gate = types.ModuleType("konsol.close.signoff_gate")
    gate.assert_can_sign = assert_can_sign
    return module, frappe, saved_doc, gate, calls


@contextlib.contextmanager
def _gate_installed(gate):
    """The stub gate in sys.modules for one call; the real modules after."""
    close = types.ModuleType("konsol.close")
    close.signoff_gate = gate
    names = ("konsol.close", "konsol.close.signoff_gate")
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({"konsol.close": close, "konsol.close.signoff_gate": gate})
    try:
        yield
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _sign(module, gate, **kwargs):
    with _gate_installed(gate):
        return module.sign_off_close("AR-1", **kwargs)


def test_the_gate_is_called_with_the_runs_period():
    module, _, doc, gate, calls = _load(fiscal_year=2099, fiscal_period=7)
    _sign(module, gate)
    assert calls == [(2099, 7)], f"the gate was not called with the run's period: {calls}"
    assert doc.signoff_status == "Signed Off"
    assert doc.signoff_saved is True


def test_a_blocked_gate_stops_the_signoff_before_any_change():
    module, _, doc, gate, calls = _load(gate_raises=True)
    try:
        _sign(module, gate)
        raise AssertionError("a run was signed while the gate refused it")
    except GateBlocked as e:
        assert "Sign-off blocked" in str(e)
    assert calls == [(2099, 1)]
    assert doc.signoff_saved is False, "the run was saved although the gate refused it"
    assert doc.signoff_status == "Not Signed Off"
    assert doc.signed_off_by is None and doc.signed_off_at is None


def test_the_gate_runs_before_the_amber_and_red_branches():
    """A blocked period is refused whatever the run's status: the gate is not
    something an acknowledgement or an override reason gets past."""
    for status, kwargs in (("Amber", {"acknowledgement": "reviewed"}),
                           ("Red", {"override_reason": "known"}),
                           ("Error", {"override_reason": "known"})):
        module, _, doc, gate, calls = _load(status=status, gate_raises=True)
        try:
            _sign(module, gate, **kwargs)
            raise AssertionError(f"a {status} run was signed while the gate refused it")
        except GateBlocked:
            pass
        assert calls == [(2099, 1)], f"{status}: gate not called: {calls}"
        assert doc.signoff_saved is False, f"{status}: saved although the gate refused it"


def test_a_year_only_run_is_refused():
    for empty in (0, None, ""):
        module, frappe, doc, gate, calls = _load(fiscal_period=empty)
        try:
            _sign(module, gate)
            raise AssertionError(f"a run with fiscal_period={empty!r} was signed")
        except frappe.ValidationError as e:
            assert "A sign-off is for a period" in str(e), str(e)
        assert calls == [], "the gate was called for a run with no period"
        assert doc.signoff_saved is False


def test_an_already_signed_run_is_refused_before_the_gate():
    for state in ("Signed Off", "Acknowledged", "Overridden"):
        module, frappe, doc, gate, calls = _load(signoff_status=state)
        try:
            _sign(module, gate)
            raise AssertionError(f"an {state} run was signed again")
        except frappe.ValidationError as e:
            assert "already" in str(e), str(e)
        assert calls == [], f"{state}: the gate was called for an already-signed run"
        assert doc.signoff_saved is False


def test_a_queued_or_running_run_is_refused_before_the_gate():
    for status in ("Queued", "Running"):
        module, frappe, doc, gate, calls = _load(status=status)
        try:
            _sign(module, gate)
            raise AssertionError(f"a {status} run was signed")
        except frappe.ValidationError as e:
            assert "still" in str(e), str(e)
        assert calls == []
        assert doc.signoff_saved is False


def _whitelist_methods(name):
    with open(AR_PY) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    for dec in fn.decorator_list:
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "whitelist"):
            for kw in dec.keywords:
                if kw.arg == "methods":
                    return ast.literal_eval(kw.value)
    return None


def test_sign_off_close_is_post_only():
    """It writes the signature and commits, so a GET must never reach it
    (Problems 10)."""
    methods = _whitelist_methods("sign_off_close")
    assert methods == ["POST"], f'sign_off_close is not @frappe.whitelist(methods=["POST"]): {methods}'


# --- A27: "Re-sign Needed" (story 9.5, #303 point 4; Problems 9) ------------

RE_SIGN = "Re-sign Needed"
AFFECTED = "FY2099 P01 reopened by lead@example.com on 2026-09-25"


def _ar_fields():
    with open(AR_JSON) as f:
        meta = json.load(f)
    return meta["fields"]


def test_re_sign_needed_is_a_signoff_option():
    fields = {f["fieldname"]: f for f in _ar_fields()}
    options = fields["signoff_status"]["options"].split("\n")
    assert RE_SIGN in options, f"signoff_status has no {RE_SIGN!r} option: {options}"
    # The existing states stay, in their order, ahead of the new one.
    assert options[:4] == ["Not Signed Off", "Signed Off", "Acknowledged", "Overridden"], options


def test_affected_by_is_a_read_only_field_on_the_signoff_tab():
    fields = _ar_fields()
    names = [f["fieldname"] for f in fields]
    assert "affected_by" in names, "Assertion Run has no affected_by field"
    field = fields[names.index("affected_by")]
    assert field["fieldtype"] == "Small Text", field
    assert field.get("read_only") == 1, "affected_by must be read_only: it is set by the system"
    start, end = names.index("tab_signoff"), names.index("tab_timing")
    assert start < names.index("affected_by") < end, "affected_by is not on the Sign-off tab"


def test_re_sign_needed_does_not_count_as_signed():
    module, *_ = _load()
    assert RE_SIGN not in module.SIGNED_STATES, module.SIGNED_STATES
    assert set(module.SIGNED_STATES) == {"Signed Off", "Acknowledged", "Overridden"}


def test_sign_off_close_refuses_a_re_sign_needed_run_and_saves_nothing():
    for status in ("Green", "Amber", "Red"):
        module, frappe, doc, gate, calls = _load(status=status, signoff_status=RE_SIGN,
                                                 affected_by=AFFECTED)
        try:
            _sign(module, gate, acknowledgement="ok" if status == "Amber" else None,
                  override_reason="ok" if status == "Red" else None)
            raise AssertionError(f"a {status} run in {RE_SIGN} was signed again")
        except frappe.ValidationError as e:
            msg = str(e)
        assert msg == ("An earlier period was reopened after this run: %s. Run the checks "
                       "again, then sign off the new run." % AFFECTED), msg
        assert doc.signoff_saved is False, f"{status}: the run was saved"
        assert doc.signoff_status == RE_SIGN
        assert doc.signed_off_by is None and doc.signed_off_at is None
        assert calls == [], f"{status}: the gate was called for a run that must be re-run"


def test_a_not_signed_run_is_still_signed():
    """Failure path: the refusal is for Re-sign Needed only."""
    module, _, doc, gate, calls = _load(signoff_status="Not Signed Off")
    _sign(module, gate)
    assert doc.signoff_status == "Signed Off" and doc.signoff_saved is True


def _run_row(signoff_status):
    row = types.SimpleNamespace(name="AR-1", status="Green", signoff_status=signoff_status,
                                failed=0, errored=0)
    row.get = lambda k: getattr(row, k)
    return row


def test_assert_close_signed_off_refuses_a_re_sign_needed_run():
    module, frappe, *_ = _load()
    frappe.get_all = lambda dt, **k: [] if "pluck" in k else [_run_row(RE_SIGN)]
    try:
        module.assert_close_signed_off(2099, 1)
        raise AssertionError("a Re-sign Needed run passed assert_close_signed_off")
    except frappe.ValidationError as e:
        assert "is not signed off" in str(e), str(e)
    # Failure path: a signed run still passes.
    frappe.get_all = lambda dt, **k: [] if "pluck" in k else [_run_row("Signed Off")]
    assert module.assert_close_signed_off(2099, 1) == "AR-1"
