"""Which doctypes carry fiscal-period data, and which periods of a year are in
use (konsol#189). A used period is frozen by the fiscal-structure rules, so
every doctype with fiscal_year + fiscal_period must be classified — a new one
that is left out would let a period be removed from under its documents.

The module is loaded by path against a stub frappe; sys.modules is restored."""
import glob
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(APP_DIR, "fiscal_calendar.py")


class _DB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def sql(self, query, values=None, *args, **kwargs):
        self.calls.append((query, values))
        return self.rows


def _load(db=None):
    saved = sys.modules.get("frappe")
    frappe = types.ModuleType("frappe")
    frappe.db = db or _DB([])
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location("fiscal_calendar_under_test", MODULE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


def _doctype_jsons():
    """name -> DocType JSON for every doctype under konsol/."""
    out = {}
    for path in glob.glob(os.path.join(APP_DIR, "**", "doctype", "*", "*.json"), recursive=True):
        try:
            with open(path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("doctype") == "DocType" and d.get("name"):
            out[d["name"]] = d
    return out


def _has_period_fields(d):
    fields = {f.get("fieldname"): f for f in d.get("fields", [])}
    return ("fiscal_year" in fields and "fiscal_period" in fields
            and fields["fiscal_period"].get("fieldtype") == "Int")


def test_every_period_doctype_is_classified():
    M = _load()
    doctypes = _doctype_jsons()
    using = set(M.DOCTYPES_USING_PERIODS)
    not_data = set(M.NOT_PERIOD_DATA)

    assert len(using) == len(M.DOCTYPES_USING_PERIODS), "duplicate in DOCTYPES_USING_PERIODS"
    assert not (using & not_data), f"classified both ways: {sorted(using & not_data)}"
    for name in using | not_data:
        assert name in doctypes, f"{name!r} is classified but has no doctype JSON"
    for name, reason in M.NOT_PERIOD_DATA.items():
        assert isinstance(reason, str) and reason.strip(), f"{name!r} needs a reason"

    with_periods = {n for n, d in doctypes.items() if _has_period_fields(d)}
    unclassified = with_periods - using - not_data
    assert not unclassified, (
        f"doctypes with fiscal_year + fiscal_period not classified: {sorted(unclassified)} — "
        "add each to DOCTYPES_USING_PERIODS or NOT_PERIOD_DATA in konsol/fiscal_calendar.py")
    # a period-data doctype must really have the fields the query reads
    for name in using:
        assert _has_period_fields(doctypes[name]), f"{name!r} lacks fiscal_year/fiscal_period"


def test_assertion_runs_freeze_periods():
    """Assertion Run records sign-off evidence against a period, so a period
    with assertion runs must be frozen like one with postings — Pipeline Run
    stays a build log (NOT_PERIOD_DATA)."""
    M = _load()
    assert "Assertion Run" in M.DOCTYPES_USING_PERIODS
    assert "Assertion Run" not in M.NOT_PERIOD_DATA
    assert "Pipeline Run" in M.NOT_PERIOD_DATA

    db = _DB([])
    M2 = _load(db)
    saved = sys.modules.get("frappe")
    frappe = types.ModuleType("frappe")
    frappe.db = db
    sys.modules["frappe"] = frappe
    try:
        M2.periods_in_use(2026)
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    assert len(db.calls) == 1, "must be one UNION query"
    query, _ = db.calls[0]
    assert "`tabAssertion Run`" in query


def test_periods_in_use_query():
    db = _DB([(3,), (0,), (12,), (3,), (None,)])
    M = _load(db)
    doctypes = _doctype_jsons()

    # patch frappe back in for the call: the module imports it lazily
    saved = sys.modules.get("frappe")
    frappe = types.ModuleType("frappe")
    frappe.db = db
    sys.modules["frappe"] = frappe
    try:
        result = M.periods_in_use(2026)
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved

    assert result == {0, 3, 12}
    assert len(db.calls) == 1, "must be one UNION query"
    query, values = db.calls[0]
    assert "2026" not in query, "the year must be a parameter, not formatted in"
    flat = list(values.values()) if isinstance(values, dict) else list(values or ())
    assert 2026 in flat

    parts = query.split("UNION")
    assert len(parts) == len(M.DOCTYPES_USING_PERIODS)
    for name in M.DOCTYPES_USING_PERIODS:
        [part] = [p for p in parts if f"`tab{name}`" in p]
        assert "fiscal_period" in part and "fiscal_year" in part
        if doctypes[name].get("is_submittable"):
            assert "docstatus < 2" in part, f"{name}: cancelled documents must not count"
        else:
            assert "docstatus" not in part, f"{name}: not submittable, every row counts"
