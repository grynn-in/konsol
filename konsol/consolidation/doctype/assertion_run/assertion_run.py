"""Assertion Run — runs the dbt close assertion suite and records each step.

Mirrors the frappe/press build-state pattern: a parent doc with a child table
(`Assertion Step`) of per-check rows, a status Select rendered as a colored
indicator, and a streamed log via frappe.publish_realtime.
"""
import contextlib
import contextvars
import json
import os
import subprocess

import frappe
from frappe.model.document import Document

from konsol.assertion_status import (captures_rows, is_assertion, run_status,
                                     severity_of, step_status)
from konsol.period_status import PeriodNotDeclared, assert_declared


def _assert_year_declared(fiscal_year):
    """Refuse a fiscal year with no EPM Fiscal Year row.

    A year-only Assertion Run (no period) still counts toward
    ``fiscal_calendar.periods_in_use``, so it must not name a year nobody
    declared (konsol#189, PR#191 review finding 8). Checks existence only:
    it must never declare one (declaring is EPM Fiscal Year's job).
    """
    if not frappe.db.exists("EPM Fiscal Year", {"fiscal_year": fiscal_year}):
        frappe.throw(
            frappe._("FY{0} is not declared: create it in EPM Fiscal Year.").format(fiscal_year),
            PeriodNotDeclared,
        )


#: Result and sign-off fields a new run must never carry in (konsol#305 A02b).
#: With R3 the Analyst may create a run; measured live 25 Sep 2026, a run
#: inserted with status "Green" and signoff_status "Signed Off" saved as sent,
#: because read_only only guards the form. A test keeps this list equal to the
#: doctype's read_only fields, less title, status, signoff_status, triggered_by.
NEW_RUN_BLANK_FIELDS = (
    "total", "passed", "failed", "errored", "warned",
    "signed_off_by", "signed_off_at", "override_reason", "acknowledgement",
    "warnings_at_signoff", "affected_by", "started_at", "completed_at", "duration_seconds",
    "log",
)


#: Sign-off fields: only sign_off_close writes them (A48). Measured live 25 Sep
#: 2026 (A22): frappe.client.set_value by the Close Lead stored a forged
#: "Signed Off" / "Overridden", and every gate reads signoff_status.
#: affected_by (A27) goes with them: it records why a run became "Re-sign
#: Needed", so it changes only with signoff_status, through the same writer.
SIGNOFF_FIELDS = ("signoff_status", "signed_off_by", "signed_off_at", "override_reason",
                  "acknowledgement", "warnings_at_signoff", "affected_by")
#: Result fields: only the worker (run_close_assertions) writes them (A48).
RESULT_FIELDS = ("status", "total", "passed", "failed", "errored", "warned",
                 "started_at", "completed_at", "duration_seconds")

#: Scope and origin: set at insert, never changed after, by any writer (A50).
#: Found live by A48: set_value(fiscal_period=N) by the Close Lead MOVED a
#: signed run, and latest_close_run / signoff_gate, which find a period's runs
#: by fiscal_year/fiscal_period, then read that other period as signed.
SCOPE_FIELDS = ("fiscal_year", "fiscal_period", "pipeline_run", "triggered_by")
#: The fields of an Assertion Step row. The results table changes only inside
#: the worker (A50): a save with edited rows could rewrite which checks failed.
STEP_FIELDS = ("assertion", "dimension", "status", "rows_failed", "severity", "message",
               "failures_table", "sample_rows")

SIGNOFF_WRITER = "sign-off"
WORKER_WRITER = "worker"
#: What each writer may change. A writer changing the other's fields is refused.
_WRITER_FIELDS = {SIGNOFF_WRITER: SIGNOFF_FIELDS, WORKER_WRITER: RESULT_FIELDS}

# The writer is held in a context variable, not in doc.flags: it is set only by
# server code in this module, a request cannot carry it, and it names one run.
# reap_stale_close_runs and the log checkpoint write with frappe.db.set_value,
# which does not call validate, so they need no writer.
_writer = contextvars.ContextVar("assertion_run_writer", default=None)


@contextlib.contextmanager
def writing(writer, run_name):
    """Mark saves of ``run_name`` inside the block as made by ``writer``."""
    if writer not in _WRITER_FIELDS:
        raise ValueError("Unknown Assertion Run writer %r" % (writer,))
    token = _writer.set((writer, run_name))
    try:
        yield
    finally:
        _writer.reset(token)


def active_writer():
    """The (writer, run name) in force, or None."""
    return _writer.get()


def _norm(value):
    """A value as the database holds it, so a Desk save that sends a datetime
    as a string or a whole Float as an int is not a change."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def _norm_scope(value):
    """A scope value as the database holds it: Frappe stores an empty Int as 0
    and an empty Link as NULL, so 0, "" and None are the same empty scope."""
    value = _norm(value)
    return None if value == 0 else value


def _steps(rows):
    """The results table as comparable tuples, in row order. Rows are child
    Documents on a real save and dicts from a Desk payload; both have .get."""
    return [tuple(_norm(row.get(f)) for f in STEP_FIELDS) for row in (rows or [])]


class AssertionRun(Document):
    def before_insert(self):
        """A new run starts Queued, unsigned, with no results, triggered by
        the caller, whatever the insert request carried (A02b). Only the
        worker and sign_off_close fill these in, on later saves.

        Only trigger_close_run may create a run (A02c): it also enqueues the
        suite, so a run inserted any other way would sit Queued with no job and
        block every other run until the reaper errors it."""
        if not getattr(self.flags, "started_by_trigger", False):
            frappe.throw(frappe._("Start a close run with Run checks (trigger_close_run), not by creating the record."))
        for field in NEW_RUN_BLANK_FIELDS:
            self.set(field, None)
        self.status = "Queued"
        self.signoff_status = "Not Signed Off"
        self.set("results", [])
        self.triggered_by = frappe.session.user

    def validate(self):
        """The year, and the period when one is given, must be declared: an
        undeclared one is refused before the run starts (konsol#189). Unlike
        a doctype that writes into the period, an Assertion Run only reads
        data, so the rule is declared, not open.

        Gated to the declared scope, not every save (PR#191 review finding
        5): a year-only run is stored with fiscal_period=0 (Frappe stores an
        empty Int as 0). The worker and sign-off reload the run and save it
        again; if that re-ran the check against period 0, a year with no
        Opening period would refuse its own worker save and the run would
        stay Queued forever, blocking every other run behind the
        concurrency guard. So the check runs only on insert, or when
        fiscal_year/fiscal_period actually changed from the saved version —
        a later status/result save is not re-gated.

        The frozen fields are checked on every save of a stored run (A48,
        A50). A stored run's scope never changes (A50); the declared check
        still runs first on a scope change, so moving a run to an undeclared
        period is refused as undeclared, and to a declared one as a move.
        """
        self._refuse_unflagged_changes()
        if (self.is_new() or self.has_value_changed("fiscal_year")
                or self.has_value_changed("fiscal_period")):
            if self.fiscal_year and self.fiscal_period not in (None, ""):
                assert_declared(self.fiscal_year, self.fiscal_period)
            elif self.fiscal_year:
                _assert_year_declared(self.fiscal_year)
        self._refuse_scope_change()

    def _refuse_scope_change(self):
        """Refuse any change to a stored run's scope or origin, whoever saves
        it (A50). No writer moves a run: the worker and sign-off only reload
        and save it."""
        if self.is_new():
            return
        before = self.get_doc_before_save()
        if before is None:
            return
        moved = [f for f in SCOPE_FIELDS
                 if _norm_scope(before.get(f)) != _norm_scope(self.get(f))]
        if moved:
            frappe.throw(frappe._(
                "Assertion Run {0}: {1} is fixed when the run starts and cannot be changed. "
                "Run the checks for the other period instead. Nothing was saved.").format(
                    self.name, ", ".join(moved)))

    def _refuse_unflagged_changes(self):
        """Refuse a change to a sign-off or result field made by anyone but
        its writer (A48). frappe.client.set_value and save both come here."""
        if self.is_new():
            return
        before = self.get_doc_before_save()
        if before is None:
            # Frappe loads the saved version on every save of a stored row; it
            # is None only when the row no longer exists, so there is nothing
            # to forge. has_value_changed treats this case the same way.
            return
        changed = [f for f in SIGNOFF_FIELDS + RESULT_FIELDS
                   if _norm(before.get(f)) != _norm(self.get(f))]
        # The results table belongs to the worker, like the counts it feeds (A50).
        if _steps(before.get("results")) != _steps(self.get("results")):
            changed.append("results")
        if not changed:
            return
        active = _writer.get()
        allowed = ()
        if active and active[1] == self.name:
            allowed = _WRITER_FIELDS[active[0]]
        if active and active[1] == self.name and active[0] == WORKER_WRITER:
            allowed = allowed + ("results",)
        refused = [f for f in changed if f not in allowed]
        if refused:
            frappe.throw(frappe._(
                "Assertion Run {0}: {1} can only be changed by running the checks or by Sign off "
                "(sign_off_close). Nothing was saved.").format(self.name, ", ".join(refused)))


# --- dimension classification (filename/keyword -> bucket) ---------------
def _classify(name):
    n = name.lower()
    if "ownership" in n:
        return "Ownership"
    if any(k in n for k in ("cta", "rate", "currency", "fx")):
        return "FX"
    if any(k in n for k in ("ic_", "consolidat", "nci", "equity", "elimination",
                            "fctb", "balance", "bs_", "end_to_end")):
        return "Consolidation"
    if any(k in n for k in ("null", "schema", "chart", "unique", "valid")):
        return "Data Quality"
    return "Other"


def _manifest_nodes(project_path):
    """dbt's manifest nodes, or {} — used only to read each test's severity.

    Never raises: a missing or unreadable manifest degrades `severity_of` to
    reading the observed status, it must not fail a finished run.
    """
    try:
        with open(os.path.join(project_path, "target", "manifest.json")) as fh:
            return json.load(fh).get("nodes") or {}
    except Exception:  # noqa: BLE001
        return {}


def _dbt_bin():
    """Absolute path to the dbt binary in the bench venv.

    The web/worker processes don't have the venv's bin on PATH, so a bare
    `dbt` raises FileNotFoundError. Fall back to `dbt` only if the venv copy
    isn't present.
    """
    candidate = os.path.join(frappe.utils.get_bench_path(), "env", "bin", "dbt")
    return candidate if os.path.exists(candidate) else "dbt"


@frappe.whitelist(methods=["POST"])
def trigger_close_run(fiscal_year=None, fiscal_period=None):
    """Create an Assertion Run and enqueue the assertion suite.

    This writes — it inserts an Assertion Run, commits, and enqueues
    `run_close_assertions` on the long queue — so it is POST-only. Starting a
    run is not the sign-off: the Close Lead (`EPM Admin`), the Group
    Accountant (`EPM Analyst`) and System Manager may all trigger it (R3,
    konsol#297); only the Close Lead and System Manager may sign it off
    (`sign_off_close`, which enforces write on Assertion Run). The gate comes
    before the "already in progress" check, so a user without the role is
    refused whatever the run state, rather than learning from the error which
    runs are live.

    Refuses to start if another run is already Queued/Running — only one
    assertion suite may run at a time (concurrent `dbt test` would contend on
    the warehouse and produce confusing interleaved state).
    """
    frappe.only_for(("EPM Admin", "EPM Analyst", "System Manager"))
    active = frappe.db.get_value("Assertion Run", {"status": ["in", ("Queued", "Running")]}, "name")
    if active:
        frappe.throw(
            frappe._("A close run is already in progress: {0}. Wait for it to finish before starting another.").format(active),
            title=frappe._("Run already in progress"),
        )

    doc = frappe.get_doc(
        {
            "doctype": "Assertion Run",
            "status": "Queued",
            "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period,
            "triggered_by": frappe.session.user,
            "title": frappe.utils.now(),
        }
    )
    doc.flags.started_by_trigger = True
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.enqueue(
        "konsol.consolidation.doctype.assertion_run.assertion_run.run_close_assertions",
        queue="long",
        timeout=900,
        close_run=doc.name,
    )
    return doc.name


# Runs older than this with no terminal status are treated as dead. Kept above
# the 900s job timeout so a legitimately-long run is never reaped mid-flight.
STALE_MINUTES = 20


def reap_stale_close_runs():
    """Scheduled: mark long-stuck Assertion Runs as Error.

    The concurrency guard in trigger_close_run() blocks new runs while one is
    Queued/Running. If a worker dies mid-run, that record would stay Running
    forever and wedge the guard permanently. This sweep releases it.
    """
    cutoff = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-STALE_MINUTES)
    stale = frappe.get_all(
        "Assertion Run",
        filters={"status": ["in", ("Queued", "Running")], "modified": ["<", cutoff]},
        pluck="name",
    )
    for name in stale:
        prev = frappe.db.get_value("Assertion Run", name, "log") or ""
        frappe.db.set_value(
            "Assertion Run", name,
            {"status": "Error",
             "log": f"{prev}\n[reaper] marked Error: no progress for >{STALE_MINUTES}m"},
            update_modified=False,
        )
    if stale:
        frappe.db.commit()
        frappe.logger().info(f"Assertion Run reaper: marked {len(stale)} stale run(s) Error: {stale}")
    return stale


def _emit(name, **payload):
    payload["run"] = name
    frappe.publish_realtime("close_run_update", payload, doctype="Assertion Run", docname=name)


# --- Sign-off gate (PRD §6.10 §4) ----------------------------------------
# A close is "signed off" only via sign_off_close(): a Green run signs off
# cleanly; a Red/Error run is blocked unless an EPM Admin supplies a reason
# (audited as an Overridden sign-off). Queued/Running can't be signed off.
OVERRIDE_ROLES = {"System Manager", "EPM Admin"}
# Amber is terminal (konsol#265): a run with warnings and no failures has
# finished, and latest_close_run must be able to see it.
TERMINAL_STATUSES = ("Green", "Amber", "Red", "Error")
# "Acknowledged" is an Amber close signed with a written acknowledgement. It
# counts as signed off — warnings do not block a close — but it is a distinct
# state so a list of closes shows which were signed over outstanding warnings.
SIGNED_STATES = ("Signed Off", "Acknowledged", "Overridden")
# "Re-sign Needed" (A27; #303 point 4) is a run whose sign-off stopped counting
# because an earlier period was reopened after it. It is NOT a signed state, so
# latest_close_run users and assert_close_signed_off read it as unsigned, and
# sign_off_close refuses it: the checks must run again (Problems 9).
RE_SIGN_NEEDED = "Re-sign Needed"


def latest_close_run(fiscal_year, fiscal_period):
    """Most recent terminal Assertion Run for a period, or None.

    Ordered by completion time (not creation): a run is created Queued and only
    later becomes terminal, so a re-run that finishes later is authoritative.
    """
    rows = frappe.get_all(
        "Assertion Run",
        filters={"fiscal_year": fiscal_year, "fiscal_period": fiscal_period,
                 "status": ["in", TERMINAL_STATUSES]},
        fields=["name", "status", "signoff_status", "failed", "errored"],
        order_by="completed_at desc, creation desc",
        limit=1,
    )
    return rows[0] if rows else None


def _failed_assertion_names(close_run, limit=10):
    return frappe.get_all(
        "Assertion Step",
        filters={"parent": close_run, "status": ["in", ("Fail", "Error")]},
        pluck="assertion",
        limit=limit,
    )


#: How many warned assertion names are listed verbatim on a signature. Beyond
#: this the record says so rather than quietly stopping at the limit.
WARNING_NAME_LIMIT = 50


def _warned_assertion_names(close_run, limit=WARNING_NAME_LIMIT):
    """The assertions that warned — named in the acknowledgement prompt and
    recorded on the signature (konsol#265)."""
    return frappe.get_all(
        "Assertion Step",
        filters={"parent": close_run, "status": "Warn"},
        pluck="assertion",
        order_by="assertion asc",
        limit=limit,
    )


def _warning_summary(names, total):
    """The warned assertions as one auditable line.

    `total` is the run's own counter, not `len(names)`: the name list is capped,
    and a record that silently stopped at the cap would understate what was
    outstanding at the moment of signature.
    """
    if not total:
        return None
    text = ", ".join(names) or "(see the run's results)"
    if total > len(names):
        text += frappe._(" … and {0} more ({1} warnings in total)").format(
            total - len(names), total)
    return text


@frappe.whitelist(methods=["POST"])
def sign_off_close(close_run, override_reason=None, acknowledgement=None):
    """Sign off a Assertion Run — the reconciliation gate.

    POST-only: it writes the signature and commits (konsol#305 Problems 10).
    A sign-off is for a period: a year-only run is refused, and the period's
    gates (``konsol.close.signoff_gate.assert_can_sign``: configuration gaps,
    the order gate, completeness) must pass before anything changes (A22,
    story 9.2). They run ahead of the status branches, so neither an
    acknowledgement nor an override reason gets past a blocked period.

    Green  -> signed off (caller must have write on Assertion Run).
    Amber  -> warnings only (konsol#265): not blocked and no override role, but
              an `acknowledgement` is required -> "Acknowledged", recorded with
              the list of assertions that were warning at the time.
    Red/Error -> BLOCKED, unless the caller is an EPM Admin / System Manager AND
                 supplies a reason -> recorded as an audited "Overridden" sign-off.
    Queued/Running -> rejected (run not finished).
    """
    # Enforce write access BEFORE we switch to ignore_permissions for the save
    # (the sign-off fields are read_only, so the save itself must bypass perms).
    frappe.has_permission("Assertion Run", "write", doc=close_run, throw=True)

    # Row lock so two concurrent sign-offs can't both pass the idempotency check.
    frappe.db.get_value("Assertion Run", close_run, "name", for_update=True)
    doc = frappe.get_doc("Assertion Run", close_run)

    if doc.signoff_status in SIGNED_STATES:
        frappe.throw(
            frappe._("Assertion Run {0} is already {1}.").format(close_run, doc.signoff_status),
            title=frappe._("Already signed off"))

    if doc.signoff_status == RE_SIGN_NEEDED:
        frappe.throw(
            frappe._("An earlier period was reopened after this run: {0}. Run the checks again, "
                     "then sign off the new run.").format(doc.affected_by),
            title=frappe._("Sign-off blocked"))

    if doc.status in ("Queued", "Running"):
        frappe.throw(
            frappe._("Assertion Run {0} is still {1} — wait for it to finish before signing off.")
            .format(close_run, doc.status))

    if doc.fiscal_period in (None, "", 0):
        frappe.throw(
            frappe._("A sign-off is for a period; run {0} has none. Run the checks for a period and sign that run off.")
            .format(close_run), title=frappe._("Sign-off blocked"))
    # Imported here: signoff_gate reads assertion_run's TERMINAL_STATUSES.
    from konsol.close import signoff_gate
    signoff_gate.assert_can_sign(doc.fiscal_year, doc.fiscal_period)

    # Recorded on every path, not only the Amber one: a Red close overridden
    # with 12 warnings outstanding must say so too, or the stronger gate ends
    # up with the weaker record.
    warnings = _warning_summary(_warned_assertion_names(close_run) if doc.warned else [],
                                doc.warned or 0)

    ack = None
    if acknowledgement and doc.status != "Amber":
        # Refused rather than dropped: a whitelisted call that returns success
        # having stored nothing is exactly the silent fallback this issue is about.
        frappe.throw(
            frappe._("An acknowledgement applies only to an Amber close; run {0} is {1}.")
            .format(close_run, doc.status), title=frappe._("Nothing to acknowledge"))

    if doc.status == "Green":
        new_state = "Signed Off"
        reason = None
    elif doc.status == "Amber":
        # konsol#265 option C. Warnings do not block the close and do not need
        # the override role — that stays for Red, which must remain the
        # stronger gate. They do need a written acknowledgement, so the close
        # record carries what was outstanding AND why it was signed anyway.
        ack = (acknowledgement or "").strip()
        if not ack:
            frappe.throw(
                frappe._("This close has {0} warning(s): {1}. Acknowledge them to sign off.")
                .format(doc.warned, warnings or "(see the run's results)"),
                title=frappe._("Acknowledgement required"))
        new_state = "Acknowledged"
        reason = None
    else:
        # Red or Error — gated override: require an override role FIRST, then a reason.
        if not (OVERRIDE_ROLES & set(frappe.get_roles())):
            frappe.throw(
                frappe._("Only an EPM Admin may override a {0} close sign-off.").format(doc.status),
                exc=frappe.PermissionError, title=frappe._("Sign-off blocked"))
        reason = (override_reason or "").strip()
        if not reason:
            failing = ", ".join(_failed_assertion_names(close_run)) or "(see results)"
            frappe.throw(
                frappe._("Close is not reconciled (status {0}). Failing assertions: {1}. "
                         "Provide a reason to override.").format(doc.status, failing),
                title=frappe._("Override reason required"))
        new_state = "Overridden"

    doc.signoff_status = new_state
    doc.signed_off_by = frappe.session.user
    doc.signed_off_at = frappe.utils.now_datetime()
    doc.override_reason = reason
    doc.acknowledgement = ack
    doc.warnings_at_signoff = warnings
    with writing(SIGNOFF_WRITER, doc.name):
        doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"signoff_status": new_state, "signed_off_by": doc.signed_off_by}


def assert_close_signed_off(fiscal_year, fiscal_period):
    """Gate hook: raise unless the period's latest Assertion Run is signed off
    (Green) or audited-overridden.

    NOTE: this is the integration point for the budget/consolidation approval
    chain (PRD §6.5). §6.5 is not built yet, so there is no caller in this PR —
    wire `assert_close_signed_off(year, period)` into the approval transition
    when §6.5 lands. The sign-off *action* itself is already gated by
    sign_off_close() above.
    """
    run = latest_close_run(fiscal_year, fiscal_period)
    if not run:
        frappe.throw(
            frappe._("No completed Assertion Run for {0}-{1}. Run the close assertion suite before sign-off.")
            .format(fiscal_year, fiscal_period),
            title=frappe._("Close not asserted"))
    if run.signoff_status not in SIGNED_STATES:
        failing = ", ".join(_failed_assertion_names(run.name)) or "(see results)"
        frappe.throw(
            frappe._("Close {0}-{1} is not signed off (run {2}, status {3}). Failing: {4}.")
            .format(fiscal_year, fiscal_period, run.name, run.status, failing),
            title=frappe._("Close sign-off required"))
    return run.name


def run_close_assertions(close_run):
    """Background job: run `dbt test` for the singular assertions, stream the
    log, then parse run_results.json into Assertion Step rows."""
    doc = frappe.get_doc("Assertion Run", close_run)
    doc.status = "Running"
    doc.started_at = frappe.utils.now_datetime()
    doc.log = ""
    with writing(WORKER_WRITER, doc.name):
        doc.save(ignore_permissions=True)
    frappe.db.commit()

    project_path = frappe.get_single("EPM Settings").dbt_project_path or "/home/frappe/dbt_project"
    cmd = [
        _dbt_bin(), "test",
        "--select", "test_type:singular",
        "--store-failures",
        "--project-dir", project_path,
        "--profiles-dir", project_path,
    ]

    lines = []
    try:
        proc = subprocess.Popen(
            cmd, cwd=project_path, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            lines.append(line)
            _emit(close_run, line=line)
            if len(lines) % 20 == 0:  # checkpoint the log periodically
                frappe.db.set_value("Assertion Run", close_run, "log", "\n".join(lines)[-20000:],
                                    update_modified=False)
                frappe.db.commit()
        proc.wait(timeout=900)
    except subprocess.TimeoutExpired:
        proc.kill()
        lines.append("ERROR: assertion run timed out after 900s")
    except Exception as e:  # noqa: BLE001
        lines.append(f"ERROR: {e}")

    doc.reload()
    doc.log = "\n".join(lines)[-20000:]
    _parse_results(doc, project_path)

    doc.completed_at = frappe.utils.now_datetime()
    if doc.started_at and doc.completed_at:
        doc.duration_seconds = round((doc.completed_at - doc.started_at).total_seconds(), 1)
    with writing(WORKER_WRITER, doc.name):
        doc.save(ignore_permissions=True)
    frappe.db.commit()
    _emit(close_run, done=True, status=doc.status, passed=doc.passed,
          failed=doc.failed, errored=doc.errored, warned=doc.warned)


def _fetch_failure_sample(relation, limit=20):
    """Fetch up to `limit` offending rows from a --store-failures table.

    `relation` is dbt's relation_name, e.g. `epm_dbt_test__audit`.`assert_x`.
    Returns an aligned text table (ClickHouse PrettyCompact) for display in the
    Assertion Step, or a short note on failure — never raises.
    """
    rel = relation.replace("`", "").strip()
    if not rel:
        return ""
    try:
        from konsol.clickhouse import execute
        return execute(f"SELECT * FROM {rel} LIMIT {int(limit)} FORMAT PrettyCompactNoEscapes")[:8000]
    except Exception as e:  # noqa: BLE001
        return f"(could not fetch sample from {rel}: {e})"


def _parse_results(doc, project_path):
    """Read target/run_results.json into the results child table."""
    rr_path = os.path.join(project_path, "target", "run_results.json")
    doc.set("results", [])
    passed = failed = errored = warned = 0

    try:
        with open(rr_path) as fh:
            rr = json.load(fh)
    except Exception as e:  # noqa: BLE001
        doc.status = "Error"
        doc.append("results", {"assertion": "run_results.json", "status": "Error",
                               "dimension": "Other", "message": f"could not read results: {e}"})
        doc.total, doc.passed, doc.failed, doc.errored, doc.warned = 1, 0, 0, 1, 0
        return

    manifest_nodes = _manifest_nodes(project_path)

    for node in rr.get("results", []):
        uid = node.get("unique_id", "")
        # dbt's own hooks ride in the same results list; they are not
        # assertions and their 'success' status has no place in the map.
        if not is_assertion(uid):
            continue
        # unique_id looks like: test.open_epm.assert_xxx.<hash>
        name = uid.split(".")[2] if len(uid.split(".")) > 2 else uid
        status = step_status(node.get("status"))
        failures = node.get("failures") or 0
        if status == "Pass":
            passed += 1
        elif status == "Fail":
            failed += 1
        elif status == "Warn":
            warned += 1
        else:
            errored += 1

        # konsol#265: a warn writes a --store-failures table just as a fail
        # does, and showing those rows is the entire point of a warning.
        relation = (node.get("relation_name") or "") if captures_rows(status) else ""
        doc.append("results", {
            "assertion": name,
            "dimension": _classify(name),
            "status": status,
            "rows_failed": failures,
            "severity": severity_of(uid, manifest_nodes, status),
            "message": (node.get("message") or "")[:280],
            "failures_table": relation.replace("`", ""),
            "sample_rows": _fetch_failure_sample(relation) if relation else "",
        })

    doc.total = passed + failed + errored + warned
    doc.passed, doc.failed, doc.errored, doc.warned = passed, failed, errored, warned
    doc.status = run_status(passed, failed, errored, warned)
