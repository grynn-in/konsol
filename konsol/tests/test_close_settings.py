"""konsol#305 A36a (decision P13): Close Settings, a single doctype the
Close Lead (EPM Admin) owns, holding the first close period (konsol#303).

EPM Settings is System Manager only, and Frappe's base write check reads
permlevel-0 rows only (A36, proved live), so the close policy moves to its
own doctype: System Manager and EPM Admin read+write, EPM Analyst and
EPM User read only.

The controller is loaded by path with a stubbed frappe/Document, mirroring
test_close_first_period_setting.py. konsol.period_status is stubbed too,
since CloseSettings.validate_first_close_period imports it lazily.
"""
import importlib.util
import json
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(_HERE)
_DOCTYPE_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "close_settings")
_JSON = os.path.join(_DOCTYPE_DIR, "close_settings.json")
_SRC = os.path.join(_DOCTYPE_DIR, "close_settings.py")
_INIT = os.path.join(_DOCTYPE_DIR, "__init__.py")


def _stub(name, **attrs):
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod


class _Doc:  # stand-in for frappe.model.document.Document
    pass


class _PeriodNotDeclared(Exception):
    """Stand-in for konsol.period_status.PeriodNotDeclared."""


def _default_period_row(year, period):
    raise AssertionError("period_status.period_row must not be called here")


_stub("frappe", throw=lambda *a, **k: (_ for _ in ()).throw(RuntimeError(
    "frappe.throw stub not wired for this call")), _=lambda s: s)
_stub("frappe.model")
_stub("frappe.model.document", Document=_Doc)
_stub("konsol", __path__=[APP_DIR])
_stub("konsol.period_status", period_row=_default_period_row,
      PeriodNotDeclared=_PeriodNotDeclared)

_spec = importlib.util.spec_from_file_location("close_settings_under_test", _SRC)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)


class _NothingStoredDb:
    """A64: the stored first close the lock check compares against. By
    default nothing is stored (a first declaration), so the A36a validate
    cases below never reach the lock check; A64's tests install their own."""

    @staticmethod
    def get_single_value(doctype, field):
        assert doctype == "Close Settings", doctype
        return 0


if not hasattr(_m.frappe, "db"):
    _m.frappe.db = _NothingStoredDb()


# ---------------------------------------------------------------------------
# JSON shape
# ---------------------------------------------------------------------------

def _doc():
    with open(_JSON) as f:
        return json.load(f)


def test_package_init_exists():
    assert os.path.isfile(_INIT), "close_settings/__init__.py is missing"


def test_is_a_single_doctype_in_consolidation():
    doc = _doc()
    assert doc["name"] == "Close Settings"
    assert doc["doctype"] == "DocType"
    assert doc["module"] == "Consolidation"
    assert doc.get("issingle") == 1, "Close Settings must be a single doctype"
    assert not doc.get("istable")
    assert not doc.get("is_submittable")


def test_first_close_fields_are_int_with_no_default_or_reqd():
    by_name = {f["fieldname"]: f for f in _doc()["fields"]}
    for fname in ("first_close_fiscal_year", "first_close_fiscal_period"):
        assert fname in by_name, f"{fname} is missing"
        field = by_name[fname]
        assert field["fieldtype"] == "Int", f"{fname} must be Int"
        assert "default" not in field, f"{fname} must have no default"
        assert not field.get("reqd"), f"{fname} must not be reqd"
        assert not field.get("permlevel"), f"{fname} must be permlevel 0"
    assert "No default" in by_name["first_close_fiscal_year"].get("description", "")


def test_field_order_lists_every_field():
    doc = _doc()
    assert doc["field_order"] == [f["fieldname"] for f in doc["fields"]]


_RW = {"read": 1, "write": 1}
_R = {"read": 1}
_EXPECTED = {
    "System Manager": _RW,
    "EPM Admin": _RW,
    "EPM Analyst": _R,
    "EPM User": _R,
}
_GRANTS = ("read", "write", "create", "delete", "submit", "cancel", "amend")


def _perm_rows():
    return _doc()["permissions"]


def test_permission_rows_are_exactly_the_decided_roles():
    rows = _perm_rows()
    roles = [r["role"] for r in rows]
    assert sorted(roles) == sorted(_EXPECTED), f"roles are {roles}"
    for r in rows:
        assert not r.get("permlevel"), f"{r['role']} row must be permlevel 0"
        granted = {k: 1 for k in _GRANTS if r.get(k)}
        assert granted == _EXPECTED[r["role"]], (
            f"{r['role']} grants {granted}, expected {_EXPECTED[r['role']]}")


def test_analyst_and_user_cannot_write():
    for r in _perm_rows():
        if r["role"] in ("EPM Analyst", "EPM User"):
            assert not r.get("write"), f"{r['role']} must not write Close Settings"


def test_no_role_outside_the_decision_can_write():
    writers = sorted(r["role"] for r in _perm_rows() if r.get("write"))
    assert writers == ["EPM Admin", "System Manager"], writers


# ---------------------------------------------------------------------------
# Controller: validate_first_close_period (A06's five cases, plus period only)
# ---------------------------------------------------------------------------

class _Refused(Exception):
    """What the stubbed frappe.throw raises."""


def _run(year, period, period_row_fn):
    saved_throw = _m.frappe.throw
    saved_row = sys.modules["konsol.period_status"].period_row

    def throw(msg, *a, **k):
        raise _Refused(msg)

    _m.frappe.throw = throw
    sys.modules["konsol.period_status"].period_row = period_row_fn
    try:
        doc = _m.CloseSettings()
        doc.first_close_fiscal_year = year
        doc.first_close_fiscal_period = period
        doc.validate()
    finally:
        _m.frappe.throw = saved_throw
        sys.modules["konsol.period_status"].period_row = saved_row


def test_both_blank_is_ok():
    _run(None, None, _default_period_row)  # must not touch period_row


def test_both_zero_is_ok():
    # A06 note: unset Int fields read back as 0 on live.
    _run(0, 0, _default_period_row)


def test_year_only_throws():
    try:
        _run(2025, None, _default_period_row)
        assert False, "expected a throw"
    except _Refused as e:
        assert str(e) == "Give both the first close year and period, or neither."


def test_period_only_throws():
    try:
        _run(None, 7, _default_period_row)
        assert False, "expected a throw"
    except _Refused as e:
        assert "both" in str(e).lower()


def test_adjustment_period_throws():
    def row(y, p):
        return {"code": "FY2025-P07-ADJ", "type": "Adjustment"}

    try:
        _run(2025, 7, row)
        assert False, "expected a throw"
    except _Refused as e:
        assert str(e) == ("The first close period must be a Regular period; "
                          "FY2025-P07-ADJ is Adjustment.")


def test_undeclared_period_propagates():
    def row(y, p):
        raise _PeriodNotDeclared(f"FY{y} has no period {p}.")

    try:
        _run(2099, 1, row)
        assert False, "expected PeriodNotDeclared to propagate"
    except _PeriodNotDeclared:
        pass


def test_regular_period_is_ok():
    seen = []

    def row(y, p):
        seen.append((y, p))
        return {"code": "FY2025-P07", "type": "Regular"}

    _run(2025, 7, row)  # must not raise
    assert seen == [(2025, 7)]


# ---------------------------------------------------------------------------
# konsol#305 A64 (#305-R5a, Deepak 26 Sep): the first close period is locked
# once used. A move of the first close period (year or period) is refused when
# any Regular period from min(old, new) onward has a signed latest close run
# or is Closed or Locked. A first declaration (0 -> a value) and a save with no
# change stay allowed.
# ---------------------------------------------------------------------------

_SIGNED_STATES = ("Signed Off", "Acknowledged", "Overridden")


def _period_rows(statuses, year=2025):
    """Twelve Regular rows for ``year`` plus an Adjustment row at P13.
    ``statuses`` maps a period number to its effective status (default Open)."""
    rows = []
    for p in range(1, 13):
        rows.append({"fiscal_year": year, "fiscal_period": p,
                     "period_code": "FY%d-P%02d" % (year, p),
                     "period_type": "Regular", "status": statuses.get(p, "Open")})
    rows.append({"fiscal_year": year, "fiscal_period": 13,
                 "period_code": "FY%d-P13-ADJ" % year,
                 "period_type": "Adjustment", "status": statuses.get(13, "Open")})
    return rows


def _regular_row(y, p):
    return {"code": "FY%d-P%02d" % (y, p), "type": "Regular"}


def _move(old, new, rows, runs=None):
    """Save Close Settings from ``old`` (stored) to ``new`` (on the doc), with
    ``rows`` as fiscal_period_rows() and ``runs`` as the latest terminal run per
    period key. Stubs are installed only for the call and restored after."""
    runs = runs or {}
    stored = {"first_close_fiscal_year": old[0], "first_close_fiscal_period": old[1]}

    fc = types.ModuleType("konsol.fiscal_calendar")
    fc.fiscal_period_rows = lambda: [dict(r) for r in rows]
    close_pkg = types.ModuleType("konsol.close")
    close_pkg.__path__ = []
    gate = types.ModuleType("konsol.close.signoff_gate")
    gate._latest_runs = lambda: {k: dict(v) for k, v in runs.items()}
    close_pkg.signoff_gate = gate
    ar = types.ModuleType("konsol.consolidation.doctype.assertion_run.assertion_run")
    ar.SIGNED_STATES = _SIGNED_STATES
    stubs = {
        "konsol.fiscal_calendar": fc,
        "konsol.close": close_pkg,
        "konsol.close.signoff_gate": gate,
        "konsol.consolidation": types.ModuleType("konsol.consolidation"),
        "konsol.consolidation.doctype": types.ModuleType("konsol.consolidation.doctype"),
        "konsol.consolidation.doctype.assertion_run":
            types.ModuleType("konsol.consolidation.doctype.assertion_run"),
        "konsol.consolidation.doctype.assertion_run.assertion_run": ar,
    }
    for name in ("konsol.consolidation", "konsol.consolidation.doctype",
                 "konsol.consolidation.doctype.assertion_run"):
        stubs[name].__path__ = []
    saved = {name: sys.modules.get(name) for name in stubs}
    saved_konsol_fc = getattr(sys.modules["konsol"], "fiscal_calendar", None)
    saved_db = getattr(_m.frappe, "db", None)

    class _Db:
        @staticmethod
        def get_single_value(doctype, field):
            assert doctype == "Close Settings", doctype
            return stored[field]

    sys.modules.update(stubs)
    sys.modules["konsol"].fiscal_calendar = fc
    _m.frappe.db = _Db()
    try:
        _run(new[0], new[1], _regular_row)
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        if saved_konsol_fc is None:
            if hasattr(sys.modules["konsol"], "fiscal_calendar"):
                delattr(sys.modules["konsol"], "fiscal_calendar")
        else:
            sys.modules["konsol"].fiscal_calendar = saved_konsol_fc
        if saved_db is None:
            if hasattr(_m.frappe, "db"):
                delattr(_m.frappe, "db")
        else:
            _m.frappe.db = saved_db


def _refused(old, new, rows, runs=None):
    try:
        _move(old, new, rows, runs)
    except _Refused as e:
        return str(e)
    raise AssertionError("expected the move %r -> %r to be refused" % (old, new))


_P07_CLOSED = "FY2025 P07 is already closed under the current first close (FY2025 P07); " \
              "the first close period can no longer move."


def test_move_forward_past_a_closed_period_is_refused():
    rows = _period_rows({7: "Closed"})
    assert _refused((2025, 7), (2025, 9), rows) == _P07_CLOSED


def test_move_back_before_a_closed_period_is_refused():
    rows = _period_rows({7: "Closed"})
    assert _refused((2025, 7), (2025, 5), rows) == _P07_CLOSED


def test_move_of_the_year_is_refused():
    rows = _period_rows({7: "Closed"}) + [
        dict(r, fiscal_year=2026, period_code=r["period_code"].replace("2025", "2026"),
             status="Open") for r in _period_rows({})]
    assert _refused((2025, 7), (2026, 7), rows) == _P07_CLOSED


def test_locked_period_blocks_the_move():
    rows = _period_rows({8: "Locked"})
    msg = _refused((2025, 7), (2025, 9), rows)
    assert msg == ("FY2025 P08 is already locked under the current first close (FY2025 P07); "
                   "the first close period can no longer move.")


def test_signed_latest_run_blocks_the_move():
    rows = _period_rows({})
    for state in _SIGNED_STATES:
        runs = {(2025, 8): {"name": "AR-1", "signoff_status": state}}
        msg = _refused((2025, 7), (2025, 5), rows, runs)
        assert msg == ("FY2025 P08 is already signed under the current first close "
                       "(FY2025 P07); the first close period can no longer move."), msg


def test_first_blocking_period_is_named():
    rows = _period_rows({9: "Closed"})
    runs = {(2025, 8): {"name": "AR-1", "signoff_status": "Signed Off"}}
    msg = _refused((2025, 7), (2025, 10), rows, runs)
    assert msg.startswith("FY2025 P08 is already signed"), msg


def test_nothing_signed_or_closed_from_min_on_allows_the_move():
    # P03 is Closed, and P04's run is unsigned or Re-sign Needed: all are
    # before min(old, new) = P05, or not signed, so the move is allowed.
    rows = _period_rows({3: "Closed"})
    runs = {(2025, 3): {"name": "AR-0", "signoff_status": "Signed Off"},
            (2025, 6): {"name": "AR-1", "signoff_status": "Re-sign Needed"},
            (2025, 8): {"name": "AR-2", "signoff_status": ""}}
    _move((2025, 7), (2025, 9), rows, runs)
    _move((2025, 7), (2025, 5), rows, runs)


def test_non_regular_periods_never_block():
    rows = _period_rows({13: "Closed"})
    runs = {(2025, 13): {"name": "AR-ADJ", "signoff_status": "Signed Off"}}
    _move((2025, 7), (2025, 9), rows, runs)


def test_first_declaration_is_allowed_even_with_closed_periods():
    rows = _period_rows({7: "Closed", 8: "Closed"})
    _move((0, 0), (2025, 7), rows)
    _move((None, None), (2025, 9), rows)


def test_save_without_change_is_allowed():
    rows = _period_rows({7: "Closed", 8: "Locked"})
    runs = {(2025, 9): {"name": "AR-1", "signoff_status": "Signed Off"}}
    _move((2025, 7), (2025, 7), rows, runs)


def test_clearing_a_used_first_close_is_refused():
    # Clearing is a move too: from the old value onward nothing may be used.
    rows = _period_rows({7: "Closed"})
    assert _refused((2025, 7), (0, 0), rows) == _P07_CLOSED


def test_clearing_an_unused_first_close_is_allowed():
    _move((2025, 7), (0, 0), _period_rows({3: "Closed"}))
