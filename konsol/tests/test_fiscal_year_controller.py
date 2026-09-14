"""EPM Fiscal Year validate() runs the structure rules (konsol#189).

The controller builds the year and row dicts from the doc and hands them to
konsol.fiscal_structure_model; every error comes back in one throw. Loaded
against a stub frappe, as in test_submit_period_gate.py."""
import contextlib
import importlib.util
import os
import sys
import types
from datetime import date, datetime, timedelta

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTROLLER = os.path.join(APP_DIR, "epm", "doctype", "epm_fiscal_year", "epm_fiscal_year.py")
PURE = os.path.join(APP_DIR, "fiscal_structure_model.py")
STATUS = os.path.join(APP_DIR, "fiscal_status_model.py")
PATTERNS = os.path.join(APP_DIR, "fiscal_patterns_model.py")


class Thrown(Exception):
    """frappe.throw was called."""


class PermissionRefused(Exception):
    """frappe.throw was called with frappe.PermissionError."""


def _getdate(v=None):
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def _get_datetime(v=None):
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


@contextlib.contextmanager
def _load():
    """Yield the controller module with frappe stubbed; the stubs stay
    installed until the block ends, so calls made inside it see them too."""
    class _dict(dict):
        """frappe._dict: a missing key reads as None."""
        def __getattr__(self, name):
            return self.get(name)

        def __setattr__(self, name, value):
            self[name] = value

    class Document:
        def __init__(self, **kwargs):
            self.flags = _dict()
            self.__dict__.update(kwargs)

        def __getattr__(self, name):   # an unset field reads as None, as in Frappe
            if name.startswith("__"):
                raise AttributeError(name)
            return None

        def get(self, name, default=None):
            return self.__dict__.get(name, default)

        def get_doc_before_save(self):
            """The saved version, set by a test as `_before_save`; None when new."""
            return self.__dict__.get("_before_save")

    def throw(msg, exc=None, *args, **kwargs):
        raise (exc or Thrown)(msg)

    mods = {name: types.ModuleType(name) for name in (
        "frappe", "frappe.model", "frappe.model.document", "frappe.utils", "konsol",
        "konsol.fiscal_calendar")}
    # periods_in_use() queries the database; a test sets the periods in use
    # with _in_use(). Default: nothing in use.
    calendar = mods["konsol.fiscal_calendar"]
    calendar.used = set()
    calendar.periods_in_use = lambda fiscal_year: set(calendar.used)
    mods["konsol"].fiscal_calendar = calendar
    frappe = mods["frappe"]
    frappe._ = lambda s: s
    frappe._dict = _dict
    frappe.throw = throw
    frappe.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) and not k else (lambda fn: fn))
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = PermissionRefused
    frappe.utils = mods["frappe.utils"]
    mods["frappe.model.document"].Document = Document
    mods["frappe.utils"].getdate = _getdate
    mods["frappe.utils"].get_datetime = _get_datetime
    mods["frappe.utils"].cint = lambda v: int(v or 0)
    mods["konsol"].__path__ = []

    pure_names = {"konsol.fiscal_structure_model": PURE, "konsol.fiscal_status_model": STATUS,
                  "konsol.fiscal_patterns_model": PATTERNS}
    saved = {name: sys.modules.get(name) for name in (*mods, *pure_names)}
    sys.modules.update(mods)
    try:
        for name, path in pure_names.items():
            spec = importlib.util.spec_from_file_location(name, path)
            pure = importlib.util.module_from_spec(spec)
            sys.modules[name] = pure
            spec.loader.exec_module(pure)
            setattr(mods["konsol"], name.rsplit(".", 1)[1], pure)

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


def _row(period, code, ptype, start, end, name=None):
    """A period row. `name` is the child row's own docname, stable across an
    edit even when `period_code` changes; it defaults to `code` so a fresh
    call still gives every row a distinct, deterministic one."""
    return types.SimpleNamespace(fiscal_period=period, period_code=code, period_type=ptype,
                                 start_date=start, end_date=end, status="Open",
                                 name=name if name is not None else code)


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


def _saved(module, year_status="Open", row_status="Open", rows=None):
    """A saved version of the 2025 year, every row at `row_status`."""
    rows = rows if rows is not None else _monthly_2025()
    for r in rows:
        r.status = row_status
    doc = _year(module, rows)
    doc.status = year_status
    return doc


def _edit(module, saved):
    """An edit of `saved`: same values, fresh row objects."""
    rows = [types.SimpleNamespace(**vars(r)) for r in saved.periods]
    doc = _year(module, rows)
    doc.status = saved.status
    doc._before_save = saved
    return doc


def _declared(module, year, rows):
    """The action flag declaring exactly these status changes: the year's
    (when `year` is given) and each row's current values, keyed as the
    status guard matches rows (module._row_key)."""
    out = {"rows": {module._row_key(r): module._status_values(r) for r in rows}}
    if year is not None:
        out["year"] = module._status_values(year)
    return out


def _refused(doc):
    """The PermissionError message, or None when none was raised."""
    try:
        doc.validate()
    except PermissionRefused as e:
        return str(e)
    return None


def test_rest_cannot_set_status():
    with _load() as module:
        # Year status edited directly on a saved year.
        doc = _edit(module, _saved(module))
        doc.status = "Closed"
        for r in doc.periods:
            r.status = "Closed"
        msg = _refused(doc)
        assert msg is not None, "a direct edit of the year's status was accepted"
        assert "Status" in msg and "Close Year" in msg, msg

        # A row's status edited directly (stricter than the year, so only the
        # action rule refuses it).
        doc = _edit(module, _saved(module))
        doc.periods[3].status = "Closed"
        msg = _refused(doc)
        assert msg is not None, "a direct edit of a row's status was accepted"
        assert "P03" in msg, msg

        # A row's closed_by edited directly.
        doc = _edit(module, _saved(module))
        doc.periods[2].closed_by = "someone@example.com"
        msg = _refused(doc)
        assert msg is not None, "a direct edit of a row's closed_by was accepted"
        assert "P02" in msg, msg

        # A new year posted already Closed.
        rows = _monthly_2025()
        for r in rows:
            r.status = "Closed"
        doc = _year(module, rows)
        doc.status = "Closed"
        assert _refused(doc) is not None, "a new year was created Closed"

        # The same changes, made by the Close action, go through.
        doc = _edit(module, _saved(module))
        doc.status = "Closed"
        doc.closed_by = "admin@example.com"
        doc.closed_on = "2026-01-05 10:00:00"
        for r in doc.periods:
            r.status = "Closed"
        doc.flags.konsol_status_action = _declared(module, doc, doc.periods)
        assert _validate(doc) is None

        # And by the migration patch.
        doc = _edit(module, _saved(module))
        doc.periods[3].status = "Closed"
        doc.flags.konsol_fiscal_migration = True
        assert _validate(doc) is None

        # Saving an unchanged Closed year is fine.
        doc = _edit(module, _saved(module, "Closed", "Closed"))
        assert _validate(doc) is None


def test_row_looser_than_year_refused():
    with _load() as module:
        doc = _edit(module, _saved(module, "Closed", "Closed"))
        doc.periods[4].status = "Open"
        doc.flags.konsol_status_action = _declared(module, None, [doc.periods[4]])
        msg = _validate(doc)
        assert msg is not None, "an Open row in a Closed year was accepted"
        assert "P04" in msg and "looser" in msg, msg


def test_add_row_to_closed_year_refused():
    with _load() as module:
        saved = _saved(module, "Closed", "Closed", rows=_monthly_2025()[:-1])  # no CLS yet
        doc = _edit(module, saved)
        cls = _monthly_2025()[-1]
        cls.status = "Closed"
        doc.periods.append(cls)
        doc.flags.konsol_status_action = _declared(module, None, [cls])
        msg = _validate(doc)
        assert msg is not None, "a new row was added to a Closed year"
        assert "CLS" in msg and "cannot be added" in msg, msg


# --- Periods documents use are frozen (konsol#189) --------------------------

def _in_use(periods):
    """Make the stubbed fiscal_calendar.periods_in_use() return `periods`;
    call inside a _load() block."""
    sys.modules["konsol.fiscal_calendar"].used = set(periods)


def _move_p03_end(doc):
    """Re-date P03 and P04 around a new boundary; the year stays valid."""
    doc.periods[3].end_date = "2025-03-30"
    doc.periods[4].start_date = "2025-03-31"
    return doc


def _delete(doc):
    """The message of the throw from on_trash, or None when it passes."""
    try:
        doc.on_trash()
    except Thrown as e:
        return str(e)
    return None


def test_used_period_cannot_be_redated():
    with _load() as module:
        _in_use({3})
        doc = _move_p03_end(_edit(module, _saved(module)))
        msg = _validate(doc)
        assert msg is not None, "a used period was re-dated"
        assert "P03 can't be re-dated" in msg, msg


def test_unused_period_edits_freely():
    with _load() as module:
        _in_use({5})
        doc = _move_p03_end(_edit(module, _saved(module)))
        assert _validate(doc) is None


def test_used_year_cannot_be_deleted():
    with _load() as module:
        _in_use(range(1, 13))
        msg = _delete(_saved(module))
        assert msg is not None, "a year documents use was deleted"
        assert "FY2025 can't be deleted: documents use 12 of its periods." in msg, msg


def test_unused_year_can_be_deleted():
    with _load() as module:
        _in_use(set())
        assert _delete(_saved(module)) is None


def test_migration_flag_skips_in_use_rule():
    with _load() as module:
        _in_use({3})
        doc = _move_p03_end(_edit(module, _saved(module)))
        doc.flags.konsol_fiscal_migration = True
        assert _validate(doc) is None


# --- The status guard keys a row by its name, not its code (review #191, 3) -

def test_renamed_row_keeps_status():
    """Recoding a Locked row's period_code must not reset it to Open: the
    guard matches it to its saved status by the row's own name, which a
    rename doesn't change."""
    with _load() as module:
        saved = _saved(module)
        for r in saved.periods:
            if r.period_code == "P05":
                r.status = "Locked"   # unused, but Locked
        doc = _edit(module, saved)
        renamed = next(r for r in doc.periods if r.name == "P05")
        renamed.period_code = "P05b"  # same row, new code
        renamed.status = "Open"       # the client's edit resets it
        msg = _refused(doc)
        assert msg is not None, "renaming a Locked row's code reset its status to Open"
        assert "P05b" in msg, msg


# --- A blank status is refused, not read as Open (review #191, 4) ----------

def _thrown(doc):
    """The message of whatever validate() throws (Thrown or PermissionRefused
    — a blank status can trip either, depending on whether row_problems or
    the status guard sees it first), or None when it passes."""
    try:
        doc.validate()
    except (Thrown, PermissionRefused) as e:
        return str(e)
    return None


def test_blank_status_not_saved():
    with _load() as module:
        # A blank row status.
        doc = _year(module, _monthly_2025())
        doc.periods[4].status = ""
        msg = _thrown(doc)
        assert msg is not None, "a blank row status was accepted"
        assert "P04" in msg, msg

        # A blank year status.
        doc = _year(module, _monthly_2025())
        doc.status = ""
        msg = _thrown(doc)
        assert msg is not None, "a blank year status was accepted"
        assert "status" in msg.lower(), msg
