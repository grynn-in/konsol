"""The fingerprint of the numbers a close run checked, read from the
warehouse (konsol#338, #338-1, Deepak Pai 6 Oct 2026).

- ``read_rows``: ``gold_fully_consolidated_tb`` up to a period, aggregated to
  (group, period, entity, account, adjustment_type) — the grain the
  coordinator chose (#338 decision comment). Through ``ch_read.rows``, which
  raises on a failed, empty or malformed reply.
- ``stamp_new_run``: ``trigger_close_run`` stamps a new period run with the
  fingerprint and the latest completed build time before it is inserted. A
  failed read is written on the run (``fingerprint_error``), and
  ``sign_off_close`` refuses a run with no fingerprint: no signed run is left
  without one.
- ``check_after_build``: called once a governed build or an orchestrator run
  has completed and committed. For the latest signed run of every period it
  recomputes the fingerprint, and voids (``signoff_gate._mark_latest_signed``,
  one ``signoff_voided`` Close Event each) only the periods whose numbers
  changed; the event names the build and both fingerprints. A failed read
  voids nothing and says so on the Pipeline Run (and the Build Approval),
  and in the error log: never a silent pass. The next completed build checks
  the same signatures again.

Rejected (#338): #338-2 void on every rebuild; #338-3 flag without voiding.
"""
import frappe

from konsol.close import ch_read, fingerprint_model, signoff_gate

#: Aggregated to the decided grain. Up to and including (fy, fp), in
#: (fiscal_year, fiscal_period) order. The sum is aliased ``amt``, not
#: ``amount``: beside ``countIf(amount IS NULL)`` ClickHouse would substitute
#: the alias into the countIf (ILLEGAL_AGGREGATION, statement_api._TB_SQL).
NUMBERS_SQL = (
    "SELECT consolidation_group, fiscal_year, fiscal_period, data_area_id, main_account, "
    "adjustment_type, sum(amount) AS amt, countIf(amount IS NULL) AS null_rows "
    "FROM epm_gold.gold_fully_consolidated_tb "
    "WHERE fiscal_year < {fy:UInt16} OR (fiscal_year = {fy:UInt16} AND fiscal_period <= {fp:UInt16}) "
    "GROUP BY consolidation_group, fiscal_year, fiscal_period, data_area_id, main_account, "
    "adjustment_type"
)

#: How a failed check after a build starts, on the Pipeline Run, the Build
#: Approval and the error log.
SIGNATURE_CHECK_FAILED = "Signature check after this build failed"


def read_rows(fiscal_year, fiscal_period):
    """The warehouse numbers up to (fiscal_year, fiscal_period), as
    ``fingerprint_model`` rows. A key holding any NULL amount reads as
    ``amount=None``, which the model refuses."""
    rows = ch_read.rows(NUMBERS_SQL, {"fy": int(fiscal_year), "fp": int(fiscal_period)})
    return [{
        "group": r["consolidation_group"],
        "fiscal_year": int(r["fiscal_year"]),
        "fiscal_period": int(r["fiscal_period"]),
        "entity": r["data_area_id"],
        "account": r["main_account"],
        "adjustment_type": r["adjustment_type"],
        "amount": None if int(r["null_rows"] or 0) else r["amt"],
    } for r in rows]


def fingerprints(periods):
    """{(fy, fp): fingerprint} for each period, from one read up to the latest."""
    keys = sorted({(int(fy), int(fp)) for fy, fp in periods})
    if not keys:
        return {}
    return fingerprint_model.run_fingerprints(read_rows(*keys[-1]), keys)


def latest_build_at():
    """When the latest completed build finished (governed builds and
    orchestrator runs both finish a Pipeline Run), or None."""
    rows = frappe.get_all("Pipeline Run", filters={"status": "Completed"},
                          fields=["completed_at"], order_by="completed_at desc", limit=1)
    return rows[0].completed_at if rows else None


def stamp_new_run(doc):
    """Set ``numbers_fingerprint`` and ``fingerprint_as_of`` on a new period
    run, or ``fingerprint_error`` when the warehouse cannot be read. A
    year-only run is never signed, so it is not stamped."""
    if not doc.fiscal_year or doc.fiscal_period in (None, "", 0):
        return
    key = (int(doc.fiscal_year), int(doc.fiscal_period))
    try:
        doc.numbers_fingerprint = fingerprints([key])[key]
        doc.fingerprint_as_of = latest_build_at()
        doc.fingerprint_error = None
    except Exception as exc:  # noqa: BLE001 - recorded on the run, never passed over
        doc.numbers_fingerprint = None
        doc.fingerprint_as_of = None
        doc.fingerprint_error = (
            "The warehouse numbers could not be read when this run started, so the "
            "run has no fingerprint and cannot be signed; run the checks again: %s" % exc)


def _build_label(pipeline_run, build_approval):
    if build_approval:
        return "Build Approval %s (Pipeline Run %s)" % (build_approval, pipeline_run)
    if pipeline_run:
        return "Pipeline Run %s" % pipeline_run
    # tasks._run_dbt_build_background, queued by schema_apply with no run.
    return "a dbt build with no Pipeline Run"


def void_changed_signatures(pipeline_run, build_approval=None):
    """Void the latest signed run of every period whose numbers changed;
    return the voided run names. Raises when the warehouse cannot be read."""
    runs = signoff_gate.latest_signed_runs(("numbers_fingerprint",))
    if not runs:
        return []
    current = fingerprints(runs)
    label = _build_label(pipeline_run, build_approval)
    at = frappe.utils.now_datetime()
    marked = []
    for key in sorted(runs):
        old = runs[key].get("numbers_fingerprint") or None
        new = current[key]
        if old == new:
            continue
        affected_by = "The numbers changed in %s, completed %s" % (
            label, at.strftime("%Y-%m-%d %H:%M:%S"))
        detail = {"pipeline_run": pipeline_run, "build_approval": build_approval,
                  "old_fingerprint": old, "new_fingerprint": new}
        marked += signoff_gate._mark_latest_signed({key}, affected_by, detail=detail)
    return marked


def _append(doctype, name, field, message):
    before = frappe.db.get_value(doctype, name, field) or ""
    frappe.db.set_value(doctype, name, {field: (before + "\n" + message).strip("\n")},
                        update_modified=False)


def check_after_build(pipeline_run, build_approval=None):
    """Run ``void_changed_signatures`` after a completed build, in its own
    transaction (the build's status is already committed). Returns the voided
    run names, or None when the check failed: then nothing is voided, the
    failure is written on the Pipeline Run and the Build Approval and logged,
    and the signatures stand until the next completed build checks them."""
    try:
        marked = void_changed_signatures(pipeline_run, build_approval)
        frappe.db.commit()
        return marked
    except Exception as exc:  # noqa: BLE001 - written on the build, never silent
        frappe.db.rollback()
        message = (
            "%s: %s. No signature was voided; a signed period may stand on changed "
            "numbers until the next completed build checks it again." % (
                SIGNATURE_CHECK_FAILED, exc))
        if pipeline_run:
            _append("Pipeline Run", pipeline_run, "error_log", message)
        if build_approval:
            _append("Build Approval", build_approval, "error_message", message)
        frappe.log_error(title="%s (%s)" % (SIGNATURE_CHECK_FAILED,
                                            _build_label(pipeline_run, build_approval)),
                         message=message)
        frappe.db.commit()
        return None
