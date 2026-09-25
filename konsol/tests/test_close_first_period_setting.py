"""konsol#303 point 1: EPM Settings gets the first close period, with no
default. Earlier periods are history (opening balances); sign-off and period
close are refused until it is declared.

The controller is loaded by path with a stubbed frappe/Document, mirroring
test_trial_balance_submission.py:22-49. konsol.period_status is stubbed too,
since EPMSettings.validate_first_close_period imports it lazily so the stub
here can supply it without a live site.
"""
import importlib.util
import json
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(_HERE)
_DOCTYPE_DIR = os.path.join(APP_DIR, "pipeline", "doctype", "epm_settings")
_JSON = os.path.join(_DOCTYPE_DIR, "epm_settings.json")
_SRC = os.path.join(_DOCTYPE_DIR, "epm_settings.py")


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

_spec = importlib.util.spec_from_file_location("epm_settings_under_test", _SRC)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)


# ---------------------------------------------------------------------------
# JSON shape
# ---------------------------------------------------------------------------

def _doc():
    with open(_JSON) as f:
        return json.load(f)


def _tab_close_fields(doc):
    fields = doc["fields"]
    start = next(i for i, f in enumerate(fields) if f["fieldname"] == "tab_close")
    out = []
    for f in fields[start + 1:]:
        if f["fieldtype"] == "Tab Break":
            break
        out.append(f)
    return out


def test_first_close_fields_exist_in_tab_close():
    names = {f["fieldname"] for f in _tab_close_fields(_doc())}
    assert {"first_close_fiscal_year", "first_close_fiscal_period"} <= names


def test_first_close_fields_are_int_with_no_default_or_reqd():
    by_name = {f["fieldname"]: f for f in _tab_close_fields(_doc())}
    for fname in ("first_close_fiscal_year", "first_close_fiscal_period"):
        field = by_name[fname]
        assert field["fieldtype"] == "Int", f"{fname} must be Int"
        assert not field.get("default"), f"{fname} must have no default"
        assert not field.get("reqd"), f"{fname} must not be reqd"


# ---------------------------------------------------------------------------
# Controller: validate_first_close_period
# ---------------------------------------------------------------------------

class _Refused(Exception):
    """What the stubbed frappe.throw raises."""


def _run(year, period, period_row_fn):
    """Run EPMSettings.validate() with konsol.period_status.period_row
    swapped for ``period_row_fn``. frappe.throw raises _Refused(message)."""
    saved_throw = _m.frappe.throw
    saved_row = sys.modules["konsol.period_status"].period_row

    def throw(msg, *a, **k):
        raise _Refused(msg)

    _m.frappe.throw = throw
    sys.modules["konsol.period_status"].period_row = period_row_fn
    try:
        doc = _m.EPMSettings()
        doc.first_close_fiscal_year = year
        doc.first_close_fiscal_period = period
        doc.validate()
    finally:
        _m.frappe.throw = saved_throw
        sys.modules["konsol.period_status"].period_row = saved_row


def test_both_blank_is_ok():
    _run(None, None, _default_period_row)  # must not touch period_row


def test_year_only_throws():
    try:
        _run(2025, None, _default_period_row)
        assert False, "expected a throw"
    except _Refused as e:
        assert "both" in str(e).lower()


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
        assert "Regular" in str(e)
        assert "FY2025-P07-ADJ" in str(e)


def test_undeclared_period_propagates():
    def row(y, p):
        raise _PeriodNotDeclared(f"FY{y} has no period {p}.")

    try:
        _run(2099, 1, row)
        assert False, "expected PeriodNotDeclared to propagate"
    except _PeriodNotDeclared:
        pass


def test_regular_period_is_ok():
    def row(y, p):
        return {"code": "FY2025-P07", "type": "Regular"}

    _run(2025, 7, row)  # must not raise
