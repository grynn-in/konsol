"""EPM Fiscal Year validate() runs the structure rules (konsol#189).

The controller builds the year and row dicts from the doc and hands them to
konsol.fiscal_structure_model; every error comes back in one throw. Loaded
against a stub frappe, as in test_submit_period_gate.py."""
import contextlib
import importlib.util
import os
import sys
import types
from datetime import date, timedelta

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTROLLER = os.path.join(APP_DIR, "epm", "doctype", "epm_fiscal_year", "epm_fiscal_year.py")
PURE = os.path.join(APP_DIR, "fiscal_structure_model.py")


class Thrown(Exception):
    """frappe.throw was called."""


def _getdate(v=None):
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


@contextlib.contextmanager
def _load():
    """Yield the controller module with frappe stubbed; the stubs stay
    installed until the block ends, so calls made inside it see them too."""
    class Document:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def __getattr__(self, name):   # an unset field reads as None, as in Frappe
            if name.startswith("__"):
                raise AttributeError(name)
            return None

        def get(self, name, default=None):
            return self.__dict__.get(name, default)

    def throw(msg, *args, **kwargs):
        raise Thrown(msg)

    mods = {name: types.ModuleType(name) for name in (
        "frappe", "frappe.model", "frappe.model.document", "frappe.utils", "konsol")}
    frappe = mods["frappe"]
    frappe._ = lambda s: s
    frappe.throw = throw
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.utils = mods["frappe.utils"]
    mods["frappe.model.document"].Document = Document
    mods["frappe.utils"].getdate = _getdate
    mods["frappe.utils"].cint = lambda v: int(v or 0)
    mods["konsol"].__path__ = []

    saved = {name: sys.modules.get(name) for name in (*mods, "konsol.fiscal_structure_model")}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("konsol.fiscal_structure_model", PURE)
        pure = importlib.util.module_from_spec(spec)
        sys.modules["konsol.fiscal_structure_model"] = pure
        spec.loader.exec_module(pure)
        mods["konsol"].fiscal_structure_model = pure

        spec = importlib.util.spec_from_file_location("epm_fiscal_year_under_test", CONTROLLER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        for name, old in saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


def _row(period, code, ptype, start, end):
    return types.SimpleNamespace(fiscal_period=period, period_code=code, period_type=ptype,
                                 start_date=start, end_date=end, status="Open")


def _monthly_2025():
    """OPN + P01..P12 + CLS, dates as the strings a form posts."""
    rows = [_row(0, "OPN", "Opening", "2025-01-01", "2025-01-01")]
    for m in range(1, 13):
        start = date(2025, m, 1)
        end = (date(2025, m + 1, 1) if m < 12 else date(2026, 1, 1)) - timedelta(days=1)
        rows.append(_row(m, f"P{m:02d}", "Regular", start.isoformat(), end.isoformat()))
    rows.append(_row(13, "CLS", "Closing", "2025-12-31", "2025-12-31"))
    return rows


def _year(module, rows):
    return module.EPMFiscalYear(doctype="EPM Fiscal Year", name="2025", fiscal_year=2025,
                                start_date="2025-01-01", end_date="2025-12-31",
                                status="Open", periods=rows)


def _validate(doc):
    """The message of the one throw, or None when validate passes."""
    try:
        doc.validate()
    except Thrown as e:
        return str(e)
    return None


def test_valid_monthly_year_saves():
    with _load() as module:
        assert _validate(_year(module, _monthly_2025())) is None


def test_overlapping_regulars_refused_naming_both():
    with _load() as module:
        rows = _monthly_2025()
        rows[3].start_date = "2025-02-20"   # P03 starts before P02 ends
        msg = _validate(_year(module, rows))
        assert msg is not None, "overlapping Regular periods were accepted"
        assert "overlaps" in msg, msg
        assert "P02" in msg and "P03" in msg, msg


def test_all_errors_reported_at_once():
    with _load() as module:
        rows = _monthly_2025()
        rows[5].period_code = "P04"         # P05 reuses P04's code
        rows[-1].start_date = "2025-12-30"  # Closing no longer one day on the year end
        msg = _validate(_year(module, rows))
        assert msg is not None, "a year with two problems was accepted"
        assert "Code 'P04' is used by rows 5 and 6" in msg, msg
        assert "Closing period 'CLS' must be one day" in msg, msg
        assert len(msg.splitlines()) >= 2, msg
