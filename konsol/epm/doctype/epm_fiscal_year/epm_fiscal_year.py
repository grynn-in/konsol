import frappe
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, getdate

from konsol import fiscal_calendar
from konsol import fiscal_patterns_model as fpm
from konsol import fiscal_status_model as fstm
from konsol import fiscal_structure_model as fsm

#: Labels of the fields only the Close/Lock/Reopen actions (or the migration
#: patch) set, in the order _status_values() returns them.
_STATUS_LABELS = ("Status", "Closed By", "Closed On")

_ACTIONS_NOTE = "is set by Close Year, Lock Year and Reopen Year, not by editing."

#: Roles that may run Generate Periods (EPM Admin is the Close Lead).
_GENERATE_ROLES = frozenset({"EPM Admin", "System Manager"})


def _int(value):
    """An Int field as an int; blank stays None and a non-number stays as
    given, so the checks name it rather than reading it as 0."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _date(value):
    """A Date field as a date; blank stays None (getdate(None) is today)."""
    return getdate(value) if value else None


def _status(value):
    """A Select status, exactly as saved: no default filled in for blank.
    Frappe keeps a "" a REST save sends rather than filling in the field's
    JSON default (that default only fills a missing value on insert), so a
    blank must reach fiscal_status_model as blank: row_problems and
    transition_problem then refuse it by name instead of it being silently
    read as Open."""
    return value


def _status_values(doc):
    """(status, closed_by, closed_on) normalised for comparison; `doc` None
    stands for a not-yet-saved record: Open, never closed."""
    if doc is None:
        return (fstm.OPEN, None, None)
    closed_on = getattr(doc, "closed_on", None)
    return (
        _status(getattr(doc, "status", None)),
        getattr(doc, "closed_by", None) or None,
        get_datetime(closed_on) if closed_on else None,
    )


def _row_key(row):
    """How the status guard matches a row to its saved version, and how an
    action names the rows it moves. Frappe keeps a child row's `name`
    across edits (including a period_code rename), so a recoded row is
    still matched to its saved status rather than read as new. A brand-new
    row (appended, not yet saved) has no name: it is compared against Open,
    which fiscal_status_model.row_problems already requires of it."""
    return row.name


def _row_is_new(row):
    """True for a row that hasn't been saved yet. Frappe marks a freshly
    appended child row with `__islocal` (base_document.py:_init_child) and,
    before that flag reaches the row, the desk gives it a temporary name
    like "new-epm-fiscal-year-period-1"; a row appended in Python
    (Document.append, as the migration patch and scripts do) carries
    neither and has no name at all."""
    # A "new-..." name alone is not a signal: Frappe decides by __islocal
    # and writes any other named row with UPDATE ... WHERE name=..., so a
    # planted "new-p05" row saved under another year could be taken over
    # (PR #191 re-review 3). The desk clears its temporary names before save.
    return bool(getattr(row, "__islocal", None)) or not row.name


def _year_dict(doc):
    """The year fields as fiscal_structure_model reads them."""
    return {
        "year": _int(doc.fiscal_year),
        "start_date": _date(doc.start_date),
        "end_date": _date(doc.end_date),
    }


def _row_dicts(doc):
    """The period rows as fiscal_structure_model reads them."""
    return [
        {
            "period": _int(r.fiscal_period),
            "code": r.period_code,
            "type": r.period_type,
            "start_date": _date(r.start_date),
            "end_date": _date(r.end_date),
            "status": _status(r.status),
        }
        for r in (doc.periods or [])
    ]


def _stamp(target, new, now):
    """Set the year's or a row's status to `new`: closed_by/closed_on name the
    user and time, or are cleared when `new` is Open."""
    target.status = new
    if new == fstm.OPEN:
        target.closed_by = None
        target.closed_on = None
    else:
        target.closed_by = frappe.session.user
        target.closed_on = now


def _rate_failure(fiscal_year, fiscal_period):
    """The group-rate gate's refusal for one period, or None if it passes.
    frappe.throw logs its message before raising; the caught refusal's message
    is taken back off the log, since the caller throws one message naming
    every failure instead."""
    from konsol import group_rates
    log = getattr(getattr(frappe, "local", None), "message_log", None)
    logged = len(log) if log is not None else 0
    try:
        group_rates.assert_rates_complete(fiscal_year, fiscal_period)
    except frappe.ValidationError as e:
        if log is not None:
            del log[logged:]
        return str(e)
    return None


class EPMFiscalYear(Document):
    #: konsol#189: every period row, with its effective status, is published
    #: here (fiscal_calendar.fiscal_period_rows). The rows are computed — a
    #: join to the parent year — so reconcile_all calls resync_staging, not a
    #: field map. Columns in DDL order (clickhouse._REFERENCE_TABLE_DDL).
    CH_STAGING_TABLE = "epm_staging.fiscal_periods"
    CH_STAGING_COLUMNS = ("fiscal_year", "fiscal_period", "period_code", "period_label",
                          "period_type", "start_date", "end_date", "quarter", "status")

    @classmethod
    def resync_staging(cls, force=False):
        """TRUNCATE+INSERT every declared period. reconcile_all calls this with
        force=True. Returns the rows written (None when the write failed)."""
        from konsol.clickhouse import sync_table

        columns = list(cls.CH_STAGING_COLUMNS)
        rows = [[r[c] for c in columns] for r in fiscal_calendar.fiscal_period_rows()]
        return sync_table(cls.CH_STAGING_TABLE, columns, rows, force=force)

    def on_update(self):
        # Fires on insert, edit, Generate Periods and every status action.
        self._resync()

    def after_delete(self):
        """after_delete, NOT on_trash: the sync re-reads every year, and
        on_trash runs before the row is gone (konsol#120). on_trash only
        refuses."""
        self._resync()

    def _resync(self):
        """Queue the sync for after the commit, once per transaction
        (konsol#124): ClickHouse has no transaction, so an inline sync would
        publish a save that later rolls back. Same pattern as Entity."""
        from konsol.clickhouse import after_commit_once

        after_commit_once(("fiscal_periods", self.CH_STAGING_TABLE), type(self).resync_staging)

    def validate(self):
        year = _year_dict(self)
        rows = _row_dicts(self)

        errors = (
            fsm.period_problems(rows)
            + fsm.year_problems(year, rows)
            + fsm.regular_period_problems(year, rows)
            + fsm.placement_problems(year, rows)
        )

        before = self.get_doc_before_save()
        self._check_status_fields_unchanged(before)

        previous_codes = (
            None if before is None else {r.period_code for r in (before.periods or [])}
        )
        errors += fstm.row_problems(_status(self.status), rows, previous_codes)

        # Periods documents already use are frozen. A new year has no saved
        # version to compare with, and the migration patch creates years from
        # data that already uses them. The decision reads committed rows
        # under the year-row lock (_used_periods).
        #
        # A declared status action never changes structure: _lock_and_reload
        # reloaded this doc from the database just before the action ran, and
        # the action only moves status fields (checked above by
        # _check_status_fields_unchanged, which already throws on any other
        # status difference and so can already refuse a status action on a
        # doctored flag before this point). So used_period_problems can never
        # fire for a real status action; skip the freeze's own locking reads
        # of the period-data tables for one. Those reads, taken in every
        # Close/Lock/Reopen action, lock in the opposite order from a
        # document save/submit/cancel in the same year (its own row first,
        # then the year row via period_status.period_row) and can deadlock
        # with one (PR #191 re-review finding 3). Ordinary saves (desk edits,
        # Generate Periods on a saved year) keep the freeze and its locks.
        status_action = isinstance(self.flags.konsol_status_action, dict)
        if before is not None and not self.flags.konsol_fiscal_migration and not status_action:
            errors += fsm.used_period_problems(
                _year_dict(before), year, _row_dicts(before), rows,
                self._used_periods())
        if errors:
            frappe.throw("\n".join(errors))

        # Assert the "never changes structure" premise above cheaply, rather
        # than only trusting it: only reached once every ordinary structural
        # and status check has passed, so this is a last-resort check for a
        # status action smuggling a structural change past them (e.g. a date
        # move that _check_status_fields_unchanged doesn't compare).
        if status_action and before is not None:
            self._assert_status_action_structure_unchanged(before)

    def on_trash(self):
        """Refuse deleting a year whose periods documents use. on_trash runs
        before the delete (after_delete would be too late to refuse)."""
        used = self._used_periods()
        if used:
            frappe.throw(
                f"FY{self.fiscal_year} can't be deleted: "
                f"documents use {len(used)} of its periods."
            )

    @frappe.whitelist(methods=["POST"])
    def generate_periods(self):
        """Replace the period table with the rows the year's pattern gives,
        then save (validate runs as usual). Only EPM Admin or System Manager.

        On a new (unsaved) doc there is nothing saved yet to protect, so the
        lock/reload and the Open/unused-year checks (which read the saved
        year) are skipped; the sent fields build the rows and the year is
        inserted, full validate included. This is the only way to create a
        year: it can't be saved without Regular periods.

        On a saved year the rest still applies, and only on an Open year whose
        saved rows are all Open too (a Locked or Closed row must be reopened
        first, since the rows it replaces have no saved name to protect
        them), and none of whose periods documents use; the action still
        works on the saved year, under lock, so unsaved edits the client
        sent are dropped.

        Returns the year's name either way.

        "Unsaved" is is_new() OR no name: Frappe v15's is_new() returns the
        __islocal flag, which only the desk sets, so a year built in Python
        (frappe.get_doc({...}), as scripts and the bench test do) has no
        name and a falsy is_new()."""
        if self.is_new() or not self.name:
            if not _GENERATE_ROLES.intersection(frappe.get_roles()):
                frappe.throw("Only an EPM Admin can generate periods.", frappe.PermissionError)
            self._replace_periods()
            self.insert()
            return self.name

        self._lock_and_reload()
        if not _GENERATE_ROLES.intersection(frappe.get_roles()):
            frappe.throw("Only an EPM Admin can generate periods.", frappe.PermissionError)

        status = _status(self.status)
        if status != fstm.OPEN:
            frappe.throw(f"FY{self.fiscal_year} is {status}; periods can't be generated.")

        # _replace_periods drops every saved row and appends fresh ones with
        # no saved name, so the status guard (_check_status_fields_unchanged)
        # can't match them to their saved version and would silently read
        # them as Open (PR #191 re-review 1). Refuse here, against the
        # just-reloaded saved rows, before any row is replaced.
        non_open = [r for r in (self.periods or []) if _status(r.status) != fstm.OPEN]
        if non_open:
            named = ", ".join(f"{r.period_code} is {_status(r.status)}" for r in non_open)
            pronoun = "it" if len(non_open) == 1 else "them"
            frappe.throw(
                f"FY{self.fiscal_year}: {named}; reopen {pronoun} before generating periods."
            )

        # _lock_and_reload already took the year row FOR UPDATE; a locking
        # read here (not a second lock) sees a document committed after this
        # transaction's REPEATABLE READ snapshot, so Generate can't replace a
        # row a just-committed document uses (PR #191 review 6).
        used = fiscal_calendar.periods_in_use(self.fiscal_year, lock=True)
        if used:
            frappe.throw(
                f"FY{self.fiscal_year}: documents use {len(used)} of its periods; "
                "generate only on an unused year."
            )

        self._replace_periods()
        self.save()
        return self.name

    def _replace_periods(self):
        """Set the period table to the rows the year's pattern gives, for
        generate_periods's new-doc and saved-doc paths alike."""
        try:
            rows = fpm.generate_periods(
                self.period_pattern, _date(self.start_date), _date(self.end_date),
                bool(cint(self.include_opening_period)),
                bool(cint(self.include_closing_period)))
        except ValueError as e:
            frappe.throw(str(e))

        self.set("periods", [])
        for r in rows:
            self.append("periods", {
                "fiscal_period": r["period"],
                "period_code": r["code"],
                "period_label": r["label"],
                "period_type": r["type"],
                "start_date": r["start_date"],
                "end_date": r["end_date"],
                "quarter": r["quarter"],
                "status": fstm.OPEN,
            })

    @frappe.whitelist(methods=["POST"])
    def close_period(self, fiscal_period, note=None):
        """Close one period row; leaving Open checks its group rates first."""
        return self._set_period_status(fiscal_period, fstm.CLOSED, "closed", note)

    @frappe.whitelist(methods=["POST"])
    def lock_period(self, fiscal_period, note=None):
        """Lock one period row; leaving Open checks its group rates first."""
        return self._set_period_status(fiscal_period, fstm.LOCKED, "locked", note)

    @frappe.whitelist(methods=["POST"])
    def reopen_period(self, fiscal_period, reason):
        """Reopen one period row of an Open year, for a stated reason."""
        return self._set_period_status(fiscal_period, fstm.OPEN, "reopened", reason)

    def _set_period_status(self, fiscal_period, new, verb, text):
        """Move the row numbered `fiscal_period` to `new`, as
        fiscal_status_model.transition_problem allows for the user's roles,
        then save as a status action (validate still runs). A row leaving Open
        must pass the group-rate gate before anything changes. Reopening
        needs a reason and an Open year. `text` (a note, or the reason) goes
        onto the year's closing note with the period code and date."""
        self._lock_and_reload()
        wanted = _int(fiscal_period)
        row = next((r for r in (self.periods or []) if _int(r.fiscal_period) == wanted), None)
        if row is None:
            frappe.throw(f"FY{self.fiscal_year} has no fiscal period {fiscal_period}.")

        current = _status(row.status)
        if current == new:
            frappe.throw(f"Period {row.period_code} is already {new}.")

        problem = fstm.transition_problem(current, new, frappe.get_roles())
        if problem:
            frappe.throw(f"Period {row.period_code}: {problem}", frappe.PermissionError)

        text = (text or "").strip()
        if new == fstm.OPEN:
            if not text:
                frappe.throw(f"Give a reason for reopening period {row.period_code}.")
            year_status = _status(self.status)
            if year_status != fstm.OPEN:
                frappe.throw(
                    f"FY{self.fiscal_year} is {year_status}; Reopen the year first, "
                    f"then period {row.period_code}.")
        elif current == fstm.OPEN:
            from konsol import group_rates
            group_rates.assert_rates_complete(self.fiscal_year, row.fiscal_period)

        now = frappe.utils.now_datetime()
        _stamp(row, new, now)
        self._append_note(row.period_code, verb, text, now)

        self._save_as_status_action([row])
        return {"fiscal_period": row.fiscal_period, "period_code": row.period_code, "status": new}

    @frappe.whitelist(methods=["POST"])
    def close_year(self, note=None):
        """Close the year and every Open row; all or nothing on group rates."""
        return self._set_year_status(fstm.CLOSED, "closed", note)

    @frappe.whitelist(methods=["POST"])
    def lock_year(self, note=None):
        """Lock the year and every Open or Closed row; all or nothing on
        group rates (only the rows leaving Open are checked)."""
        return self._set_year_status(fstm.LOCKED, "locked", note)

    @frappe.whitelist(methods=["POST"])
    def reopen_year(self, reason):
        """Reopen the year, for a stated reason; its rows stay as they are
        (each period is reopened on its own)."""
        return self._set_year_status(fstm.OPEN, "reopened", reason)

    def _set_year_status(self, new, verb, text):
        """Move the year to `new`, as fiscal_status_model.transition_problem
        allows for the user's roles. Closing or locking also moves every row
        looser than `new` to it; every row leaving Open is rate-checked first
        and, if any fail, one message names them all and nothing changes.
        Reopening needs a reason and leaves the rows alone. `text` goes onto
        the closing note with the year and date."""
        self._lock_and_reload()
        label = f"FY{self.fiscal_year}"
        current = _status(self.status)
        if current == new:
            frappe.throw(f"{label} is already {new}.")

        problem = fstm.transition_problem(current, new, frappe.get_roles())
        if problem:
            frappe.throw(f"{label}: {problem}", frappe.PermissionError)

        text = (text or "").strip()
        moving = []
        if new == fstm.OPEN:
            if not text:
                frappe.throw(f"Give a reason for reopening {label}.")
        else:
            moving = [r for r in (self.periods or [])
                      if fstm.effective_status(new, _status(r.status)) != _status(r.status)]
            failures = []
            for r in moving:
                if _status(r.status) == fstm.OPEN:
                    failure = _rate_failure(self.fiscal_year, r.fiscal_period)
                    if failure:
                        failures.append(f"{r.period_code}: {failure}")
            if failures:
                frappe.throw(
                    f"{label} can't be {verb}: {len(failures)} of its periods fail the "
                    "group-rate check; nothing was changed.\n" + "\n".join(failures))

        now = frappe.utils.now_datetime()
        for r in moving:
            _stamp(r, new, now)
        _stamp(self, new, now)
        self._append_note(label, verb, text, now)

        self._save_as_status_action(moving, year=True)
        return {"fiscal_year": self.fiscal_year, "status": new,
                "periods_moved": [r.period_code for r in moving]}

    def _lock_and_reload(self):
        """Lock the year row, then reload the year from the database. A
        whitelisted doc method runs on the document the client sent
        (frappe.handler.run_doc_method), so nothing it carries may reach a
        decision or the save: every check below reads the saved year. The
        reload is a plain read, so a year changed since this transaction's
        snapshot fails save()'s check_if_latest rather than writing stale
        rows back.

        Locking the year row does not make a later plain read of another
        table see rows committed after this transaction's REPEATABLE READ
        snapshot opened — that snapshot can predate this lock (on the SPA
        path, period_status.set_status reads the year with a plain get_doc
        before calling the action that takes this lock). The group-rate gate
        takes its own locking read of Group Exchange Rate for that reason
        (group_rates._approved_keys, PR #191 re-review finding 4)."""
        self._lock_year()
        self.reload()
        self.flags.konsol_status_action = None

    def _lock_year(self):
        """Take the year row FOR UPDATE. Every document gate holds a SHARE
        lock on it through its transaction (period_status.period_row), so
        this waits for in-flight documents in the year to commit."""
        frappe.db.sql("SELECT name FROM `tabEPM Fiscal Year` WHERE name=%s FOR UPDATE",
                      (self.name,))

    def _used_periods(self):
        """The periods documents use, for the used-period freeze (validate,
        on_trash): the year row locked first, then a locking read, so a
        document committed after this transaction's REPEATABLE READ snapshot
        is still seen (PR #191 review 6)."""
        self._lock_year()
        return fiscal_calendar.periods_in_use(self.fiscal_year, lock=True)

    def _assert_status_action_structure_unchanged(self, before):
        """A status action declares only status-field changes, and
        _check_status_fields_unchanged above already refused any other status
        difference. This asserts the rest of the doc — the rows' identity and
        dates — is untouched too, so skipping the used-period freeze (which a
        structural edit would otherwise have to pass) is safe. This should
        already be impossible: _lock_and_reload reloaded the doc from the
        database right before the action ran, and every action method only
        stamps status fields on the reloaded rows. AssertionError, not
        frappe.throw: a mismatch here is a bug in this controller, not
        something a caller can fix by resubmitting.

        Compares the year's own dates too, and each row's period_type (plus
        period_label and quarter, cheap alongside it): a status action never
        touches any of these, but the row tuple alone didn't cover them
        (PR #191 re-review 3)."""
        def year_structure(doc):
            return (_date(doc.start_date), _date(doc.end_date))

        def structure(doc):
            return [
                (r.name, _int(r.fiscal_period), r.period_code, r.period_type,
                 r.period_label, r.quarter, _date(r.start_date), _date(r.end_date))
                for r in (doc.periods or [])
            ]

        assert year_structure(self) == year_structure(before), (
            f"FY{self.fiscal_year}: a status action changed the year's dates."
        )
        assert structure(self) == structure(before), (
            f"FY{self.fiscal_year}: a status action changed period structure."
        )

    def _save_as_status_action(self, rows, year=False):
        """Save, declaring the status changes this action makes: the exact
        (status, closed_by, closed_on) of each moved row, keyed as the status
        guard matches rows, and of the year when it moves. The guard refuses
        any other status difference.

        The flag must not outlive this save: cleared in `finally` so a later
        plain save() on the same in-memory object (a script or console
        holding onto it after the action) re-applies the used-period freeze
        and the foreign/displaced-row checks instead of still validating as
        a declared status action (PR #191 re-review 3)."""
        declared = {"rows": {_row_key(r): _status_values(r) for r in rows}}
        if year:
            declared["year"] = _status_values(self)
        self.flags.konsol_status_action = declared
        try:
            self.save()
        finally:
            self.flags.konsol_status_action = None

    def _append_note(self, subject, verb, text, now):
        """Add "<subject> <verb> on <date> by <user>: <text>" to the closing
        note; nothing when `text` is blank."""
        if text:
            line = f"{subject} {verb} on {getdate(now)} by {frappe.session.user}: {text}"
            self.closing_note = f"{self.closing_note}\n{line}" if self.closing_note else line

    def _check_status_fields_unchanged(self, before):
        """Refuse, as a PermissionError, any change to the status fields of
        the year or a row, except the migration patch's and exactly the
        changes a status action declared (flags.konsol_status_action, set by
        _save_as_status_action; a bare True declares nothing). A row is
        matched to its saved version by _row_key; a row with no saved version
        (and a new year) is compared with Open, never closed.

        Matching by name alone lets a value-for-value comparison be dodged:
        deleting a Locked row (optionally re-adding a same-numbered row as a
        fresh, unnamed Open row), or a crafted save that swaps two rows'
        names while keeping each name's own status values, both leave every
        per-name comparison above looking unchanged (PR #191 re-review
        finding 2). Outside a declared status action (which never touches a
        row's name or fiscal_period, only its status fields, checked above)
        or the migration patch, every saved row that isn't Open must still be
        named, at its own fiscal_period, by the new doc; and no other row may
        claim that fiscal_period either.

        A row can also carry a *foreign* name: the child name of a period
        row saved under a different year. Such a name is never in this
        year's saved_rows either, so without a check of its own it would be
        compared against Open like any other unmatched row (PR #191
        re-review 2, finding 2) — and Frappe would then update that other
        year's row by name, moving it into this one. Refuse any row whose
        name is set, isn't a not-yet-saved row's (_row_is_new), and isn't
        one of this year's own saved rows, before anything below trusts a
        row's name to look up its saved status. This runs even for the
        migration patch and a declared status action: neither ever sends a
        foreign name, so it costs them nothing."""
        saved_rows = {_row_key(r): r for r in ((before.periods or []) if before else [])}
        # On insert there is nothing to take over (Frappe names every child
        # row afresh), so only a year with a saved version is checked.
        foreign = [] if self.get_doc_before_save() is None else [r for r in (self.periods or [])
                   if r.name and not _row_is_new(r) and r.name not in saved_rows]
        if foreign:
            frappe.throw(
                "\n".join(
                    f"Row {r.period_code} belongs to another fiscal year; "
                    "add a new row instead."
                    for r in foreign),
                frappe.PermissionError)

        if self.flags.konsol_fiscal_migration:
            return
        status_action = self.flags.konsol_status_action
        declared = status_action if isinstance(status_action, dict) else {}
        declared_rows = declared.get("rows") or {}

        problems = []
        values = _status_values(self)
        if values != declared.get("year"):
            for label, new, old in zip(_STATUS_LABELS, values, _status_values(before)):
                if new != old:
                    problems.append(f"{label} {_ACTIONS_NOTE}")

        for r in self.periods or []:
            values = _status_values(r)
            if values == declared_rows.get(_row_key(r)):
                continue
            old_values = _status_values(saved_rows.get(_row_key(r)))
            for label, new, old in zip(_STATUS_LABELS, values, old_values):
                if new != old:
                    problems.append(f"Period {r.period_code}: {label} {_ACTIONS_NOTE}")

        if not isinstance(status_action, dict):
            new_by_name = {r.name: r for r in (self.periods or []) if r.name}
            displaced = {}
            for key, sr in saved_rows.items():
                if _status(sr.status) == fstm.OPEN:
                    continue
                nr = new_by_name.get(key)
                if nr is None or _int(nr.fiscal_period) != _int(sr.fiscal_period):
                    displaced[key] = sr
            for r in self.periods or []:
                if r.name in saved_rows:
                    continue  # matched to its own saved row above
                for key, sr in saved_rows.items():
                    if (_status(sr.status) != fstm.OPEN
                            and _int(r.fiscal_period) == _int(sr.fiscal_period)):
                        displaced.setdefault(key, sr)
            for sr in displaced.values():
                problems.append(
                    f"Period {sr.period_code} is {_status(sr.status)}; it can't be "
                    "removed or replaced — reopen it first."
                )

        if problems:
            frappe.throw("\n".join(problems), frappe.PermissionError)
