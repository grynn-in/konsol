"""EPM Fiscal Year validate() runs the structure rules (konsol#189).

The controller builds the year and row dicts from the doc and hands them to
konsol.fiscal_structure_model; every error comes back in one throw. Loaded
against a stub frappe, as in test_submit_period_gate.py."""
import contextlib
import copy
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
CALENDAR = os.path.join(APP_DIR, "fiscal_calendar.py")


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

        def save(self, *args, **kwargs):
            """As Frappe's save: runs validate(), then writes the document
            back as its own saved version (deep-copied, so a later edit to
            this doc doesn't retroactively change what "before" was)."""
            self.validate()
            fields = {k: copy.deepcopy(v) for k, v in self.__dict__.items()
                      if k not in ("flags", "_before_save")}
            self._before_save = type(self)(**fields)
            return self

    def throw(msg, exc=None, *args, **kwargs):
        raise (exc or Thrown)(msg)

    mods = {name: types.ModuleType(name) for name in (
        "frappe", "frappe.model", "frappe.model.document", "frappe.utils", "konsol",
        "konsol.fiscal_calendar")}
    # periods_in_use() queries the database; a test sets the periods in use
    # with _in_use(). Default: nothing in use.
    calendar = mods["konsol.fiscal_calendar"]
    calendar.used = set()
    calendar.periods_in_use = lambda fiscal_year, lock=False: set(calendar.used)
    mods["konsol"].fiscal_calendar = calendar
    frappe = mods["frappe"]
    #: Every db.sql call, as ("sql", normalised query, values).
    frappe.events = []

    def sql(query, values=None, *args, **kwargs):
        frappe.events.append(("sql", " ".join(query.split()), values))
        return []

    frappe.db = types.SimpleNamespace(sql=sql)
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
    edit even when `period_code` changes. It defaults to None — as a row
    appended in Python and not yet saved has no name (mirrors Frappe:
    Document.append leaves `name` unset until insert); `_saved()` assigns a
    real one once a test treats the row as already in the database."""
    return types.SimpleNamespace(fiscal_period=period, period_code=code, period_type=ptype,
                                 start_date=start, end_date=end, status="Open",
                                 period_label=f"Period {code}", quarter=None, name=name)


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
    """A saved version of the 2025 year, every row at `row_status`. A row
    with no name yet (fresh out of _monthly_2025()) is given one — its own
    period_code, distinct and deterministic — as Frappe would on insert."""
    rows = rows if rows is not None else _monthly_2025()
    for r in rows:
        r.status = row_status
        if r.name is None:
            r.name = r.period_code
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


def test_freeze_reads_lock():
    """The used-period freeze decides on committed rows (review #191, 6).
    A document's gate holds a SHARE lock on the year row through its
    transaction (period_status.period_row), so validate and on_trash first
    take the year row FOR UPDATE, waiting out in-flight documents; then they
    read the periods in use with a locking read, since under REPEATABLE READ
    a plain SELECT returns the transaction's snapshot and would miss a
    document committed after it. The real periods_in_use runs here, against
    the recording db stub."""
    with _load() as module:
        frappe = sys.modules["frappe"]
        spec = importlib.util.spec_from_file_location("fiscal_calendar_real_under_test", CALENDAR)
        real = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(real)
        sys.modules["konsol.fiscal_calendar"].periods_in_use = real.periods_in_use

        for what, run in (("validate", lambda: _validate(_edit(module, _saved(module)))),
                          ("on_trash", lambda: _delete(_saved(module)))):
            frappe.events.clear()
            assert run() is None, what
            queries = [e[1] for e in frappe.events]
            lock = next((i for i, q in enumerate(queries)
                         if "`tabEPM Fiscal Year`" in q and "FOR UPDATE" in q), None)
            assert lock is not None, f"{what}: no FOR UPDATE on the year row: {queries}"
            assert frappe.events[lock][2] in ("2025", ("2025",)), frappe.events[lock]
            reads = [i for i, q in enumerate(queries) if "SELECT DISTINCT fiscal_period" in q]
            assert reads, f"{what}: periods_in_use was not queried: {queries}"
            assert all(lock < i for i in reads), f"{what}: a freeze read ran before the lock: {queries}"
            for i in reads:
                for part in queries[i].split("UNION"):
                    assert "LOCK IN SHARE MODE" in part, f"{what}: a plain freeze read: {part}"


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


# --- A saved non-Open row can't be dropped or have its identity swapped
# --- outside a declared status action (review #191 re-review, finding 2) ---

def test_non_open_row_cannot_be_removed_or_swapped():
    with _load() as module:
        def _lock_p05(saved):
            for r in saved.periods:
                if r.period_code == "P05":
                    r.status = "Locked"
                    r.closed_by = "sm@example.com"
                    r.closed_on = "2025-06-05 10:00:00"

        # (a) delete the Locked P05 row and add it back as a brand-new row
        # (no saved name), same period/code/dates, status Open. Every field
        # of the replacement matches P05's own saved values except its
        # missing name, so only the identity check catches this.
        saved = _saved(module)
        _lock_p05(saved)
        doc = _edit(module, saved)
        i = next(i for i, r in enumerate(doc.periods) if r.name == "P05")
        old = doc.periods.pop(i)
        new = types.SimpleNamespace(**vars(old))
        new.name = None
        new.status, new.closed_by, new.closed_on = "Open", None, None
        doc.periods.insert(i, new)
        msg = _refused(doc)
        assert msg is not None, "deleting a Locked row and re-adding it as Open was accepted"
        assert "P05" in msg and "Locked" in msg, msg

        # (b) swap names: P04's data (fiscal_period 4) is sent under P05's
        # name with P05's saved Locked stamps, and P05's data (fiscal_period
        # 5) under P04's name as Open. Each name's status fields match its
        # own saved values exactly, so only checking fiscal_period together
        # with name catches the swap.
        saved = _saved(module)
        _lock_p05(saved)
        doc = _edit(module, saved)
        p04 = next(r for r in doc.periods if r.name == "P04")
        p05 = next(r for r in doc.periods if r.name == "P05")
        p04.name, p05.name = "P05", "P04"
        p04.status, p04.closed_by, p04.closed_on = "Locked", "sm@example.com", "2025-06-05 10:00:00"
        p05.status, p05.closed_by, p05.closed_on = "Open", None, None
        msg = _refused(doc)
        assert msg is not None, "swapping a Locked row's name with an Open row's was accepted"
        assert "P05" in msg and "Locked" in msg, msg

        # (c) deleting an Open, unused row is still allowed. The usual
        # structure rules still apply (the last Regular period must still
        # reach the year end), but the identity guard itself must not block
        # removing a row that was never non-Open.
        saved = _saved(module)
        doc = _edit(module, saved)
        i = next(i for i, r in enumerate(doc.periods) if r.name == "P12")
        doc.periods.pop(i)
        p11 = next(r for r in doc.periods if r.name == "P11")
        p11.end_date = "2025-12-31"
        assert _validate(doc) is None


# --- A row can't carry another year's saved child name (PR #191 re-review 2,
# --- finding 2): unmatched to *this* year's saved rows, it would otherwise
# --- be silently compared against Open and then written by name, moving the
# --- other year's row (and its status) into this one. --------------------

def test_foreign_row_name_refused():
    with _load() as module:
        saved = _saved(module)
        doc = _edit(module, saved)
        # A row carrying the child name of a period row saved under some
        # other fiscal year (this year's own saved names are the period
        # codes OPN..CLS; a name none of them is, by construction, foreign).
        p05 = next(r for r in doc.periods if r.period_code == "P05")
        p05.name = "FY2025-P05-actual-row-name"
        msg = _refused(doc)
        assert msg is not None, "a row with another year's saved child name was accepted"
        assert "P05" in msg and "another fiscal year" in msg, msg

        # A desk-new row (carries __islocal, Frappe's own not-yet-saved
        # signal) isn't foreign, even though it isn't among this year's
        # saved rows either — it hasn't been saved to compare against.
        doc = _edit(module, saved)
        p05 = next(r for r in doc.periods if r.period_code == "P05")
        p05.name = "new-epm-fiscal-year-period-1"
        p05.__dict__["__islocal"] = 1
        assert _thrown(doc) is None, "a desk-new row (__islocal) was refused as foreign"


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


# --- A status action skips the freeze's locking reads (PR #191 re-review,
# --- finding 3): its own locks (year FOR UPDATE, then a document's) run in
# --- the opposite order from a document save/submit/cancel's, and can
# --- deadlock with one. --------------------------------------------------

def test_status_action_skips_freeze_read():
    """validate()'s used-period freeze takes the year row FOR UPDATE, then a
    locking read of the period-data tables (periods_in_use). A declared
    status action can't fail that freeze (it never touches structure), so it
    must not take those locks at all — that's what removes the deadlock. An
    ordinary save still takes them."""
    with _load() as module:
        frappe = sys.modules["frappe"]
        calendar = sys.modules["konsol.fiscal_calendar"]
        calls = []

        def counting(fiscal_year, lock=False):
            calls.append((fiscal_year, lock))
            return set()

        calendar.periods_in_use = counting

        def has_year_lock():
            return any(e[0] == "sql" and "`tabEPM Fiscal Year`" in e[1] and "FOR UPDATE" in e[1]
                       for e in frappe.events)

        # Ordinary save: the freeze runs.
        frappe.events.clear()
        calls.clear()
        doc = _edit(module, _saved(module))
        assert _validate(doc) is None
        assert calls, "an ordinary save did not call periods_in_use"
        assert has_year_lock(), "an ordinary save did not take the year row FOR UPDATE"

        # Status-action save: the freeze — and both its locks — are skipped.
        frappe.events.clear()
        calls.clear()
        doc = _edit(module, _saved(module))
        doc.status = "Closed"
        doc.closed_by = "admin@example.com"
        doc.closed_on = "2026-01-05 10:00:00"
        for r in doc.periods:
            r.status = "Closed"
        doc.flags.konsol_status_action = _declared(module, doc, doc.periods)
        assert _validate(doc) is None
        assert not calls, f"a status action called periods_in_use: {calls}"
        assert not has_year_lock(), \
            "a status action took the year row FOR UPDATE from validate's freeze path"


def test_status_action_refuses_structural_change():
    """Under the status-action flag, validate still refuses a structural
    edit smuggled in alongside the declared status move — here, a changed
    start_date that _check_status_fields_unchanged wouldn't catch on its own
    (it only compares status fields). That assertion is what makes it safe
    to skip the used-period freeze for a status action."""
    with _load() as module:
        doc = _edit(module, _saved(module))
        doc.status = "Closed"
        doc.closed_by = "admin@example.com"
        doc.closed_on = "2026-01-05 10:00:00"
        for r in doc.periods:
            r.status = "Closed"
        doc.flags.konsol_status_action = _declared(module, doc, doc.periods)
        _move_p03_end(doc)  # P03's end_date and P04's start_date both move
        try:
            doc.validate()
        except AssertionError:
            pass
        else:
            raise AssertionError(
                "a status action's structural change (start_date) was accepted")


def test_status_action_refuses_year_date_change():
    """_assert_status_action_structure_unchanged must also compare the
    year's own start_date/end_date, not just each row (PR #191 re-review,
    nit 3): a status action never moves the year's dates, but the row-only
    tuple didn't cover them.

    Exercises the assert directly rather than through a full validate():
    fsm.year_problems and regular_period_problems always require the last
    Regular period to reach the year end, so a bare end_date move with no
    matching row edit is already refused, for an unrelated reason, before
    validate() would ever reach this assert."""
    with _load() as module:
        saved = _saved(module)
        doc = _edit(module, saved)
        doc.end_date = "2025-12-30"
        try:
            doc._assert_status_action_structure_unchanged(saved)
        except AssertionError:
            pass
        else:
            raise AssertionError(
                "a status action's year date change (end_date) was accepted")


def test_status_action_flag_cleared():
    """_save_as_status_action's flag must not outlive the save it declares:
    a later plain save() on the same object must re-apply the used-period
    freeze rather than still validate as a declared status action
    (PR #191 re-review, nit 3)."""
    with _load() as module:
        calendar = sys.modules["konsol.fiscal_calendar"]
        calls = []

        def counting(fiscal_year, lock=False):
            calls.append((fiscal_year, lock))
            return set()

        calendar.periods_in_use = counting

        doc = _edit(module, _saved(module))
        row = next(r for r in doc.periods if r.period_code == "P05")
        row.status = "Closed"
        row.closed_by = "admin@example.com"
        row.closed_on = "2026-01-05 10:00:00"
        doc._save_as_status_action([row])

        assert doc.flags.konsol_status_action is None, \
            "_save_as_status_action left its flag set after the save"

        # A following plain save (here, a direct validate() — as save()
        # would run) re-applies the freeze: the flag no longer reads as a
        # declared status action.
        calls.clear()
        assert _validate(doc) is None, "a following plain save was refused"
        assert calls, "a following plain save did not re-run the used-period freeze"


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


# --- Round-3 re-review: "new-" is not Frappe's unsaved signal (review #191) ---

def test_new_prefix_without_islocal_is_foreign():
    """Frappe decides a child row is unsaved by `__islocal` alone; a row
    that merely has a name starting "new-" is written with UPDATE ... WHERE
    name=..., so it can carry another year's saved row (an EPM Admin can
    plant one named "new-p05" while it's Open). Without `__islocal` such a
    row is foreign like any other name this year never saved."""
    with _load() as module:
        saved = _saved(module)
        doc = _edit(module, saved)
        p05 = next(r for r in doc.periods if r.period_code == "P05")
        p05.name = "new-p05"          # no __islocal
        msg = _refused(doc)
        assert msg is not None and "another fiscal year" in msg, \
            f"a 'new-' named row without __islocal was accepted: {msg}"


def test_new_year_with_named_rows_not_foreign():
    """On insert there is nothing to take over: Frappe names every child row
    afresh. A new year whose rows arrive with names (e.g. a JSON copy of last
    year) must not be refused as "belongs to another fiscal year"."""
    with _load() as module:
        rows = _monthly_2025()
        for r in rows:
            r.name = "copied-" + r.period_code
        doc = _year(module, rows)          # no saved version: a new year
        msg = _thrown(doc)
        assert msg is None or "another fiscal year" not in msg, msg
