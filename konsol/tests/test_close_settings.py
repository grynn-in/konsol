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
