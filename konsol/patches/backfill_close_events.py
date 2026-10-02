"""Backfill the close audit trail from the old records (konsol#305 T06b,
#298 story 10.1, #305-W2-1).

Reads Versions, Comments, sign-off fields and journal fields, and inserts the
Close Events they imply through ``close_event.record_backfill`` (source
"backfill"). The rules live in the pure ``close_event_backfill_model``
(T06a); this patch only reads and writes.

- ``plan()`` reads only and returns ``(events, unplaced)``.
- ``execute()`` inserts the planned events and prints the counts by kind,
  what it could not place, and ``NOT_RECOVERABLE`` (E10-P4).
- ``dry_run()`` returns ``{"by_kind", "unplaced"}`` and writes nothing.

Rules this patch keeps:
- It only inserts. It never changes or deletes any record, and a second run
  inserts nothing (the model drops events already in the log, and everything
  from the first live event on, where the live writer is the record).
- Placement is ``close_event.period_of`` (#305-W2-5). A document it refuses
  (a ``frappe.ValidationError``: no declared period) is counted under
  ``"no declared period (#305-W2-5)"`` and the rest still insert. Any other
  error is a bug and stops the patch.
- Nothing commits here: the patch runner commits after ``execute`` returns,
  so a failure inserts nothing.
- patches.txt has no sections, so this runs pre_model_sync: ``execute``
  reloads Close Event before anything else. It is the last line of
  patches.txt, after ``lift_ownership_to_ownership_period``, whose system
  submits it recovers (E10-P10).
"""
import frappe

from konsol.close import close_event, close_event_backfill_model, close_policy_model

_TB = "Trial Balance Submission"
_TBE = "TB Exception"
_FY = "EPM Fiscal Year"
_RUN = "Assertion Run"
_JOURNAL = "Consolidation Journal"

#: Document fields the model or ``period_of`` / ``entity_of`` may read; each
#: is read only where the doctype has it.
_DOC_FIELDS = (
    "data_area_id", "uploaded_on_behalf", "amended_from", "reason",
    "fiscal_year", "fiscal_period", "effective_date", "rate_date",
    "acquired_entity", "disposed_entity", "closing_note",
)

#: Placed through the controller (``_acquisition_period`` /
#: ``_disposal_period``), so ``period_of`` needs the full document.
_CONTROLLER_PLACED = ("Business Combination", "Business Disposal")

_RUN_FIELDS = ["name", "fiscal_year", "fiscal_period", "signed_off_by", "signed_off_at",
               "signoff_status", "status", "override_reason", "acknowledgement", "affected_by"]
_COMMENT_PREFIXES = ("Self-approved by ", "Rejected: ")


class _Placements:
    """``placements`` for the model, computed on demand: only a document the
    model asks about is placed. A key the patch did not read is absent, as
    the model's contract requires."""

    def __init__(self, rows):
        self._rows = rows
        self._cache = {}

    def __contains__(self, key):
        return key in self._rows

    def __getitem__(self, key):
        if key not in self._cache:
            self._cache[key] = _place(key, self._rows[key])
        return self._cache[key]


def _place(key, row):
    doctype, name = key
    doc = frappe.get_doc(doctype, name) if doctype in _CONTROLLER_PLACED else row
    try:
        return close_event.period_of(doc)
    except frappe.ValidationError:
        # Counted by the model under "no declared period (#305-W2-5)".
        return None


def _read_docs(keys):
    """``{(doctype, name): frappe._dict}`` for the documents that still
    exist; a deleted one is absent (the model counts it)."""
    names = {}
    for doctype, name in keys:
        names.setdefault(doctype, set()).add(name)
    rows = {}
    for doctype, wanted in names.items():
        meta = frappe.get_meta(doctype)
        fields = ["name", "owner"] + [f for f in _DOC_FIELDS if meta.has_field(f)]
        for row in frappe.get_all(doctype, filters={"name": ["in", sorted(wanted)]}, fields=fields):
            rows[(doctype, row.name)] = frappe._dict(row, doctype=doctype)
    return rows


def _doc_input(key, row, approval_doctypes):
    """The plain dict the model takes for one document."""
    doc = {f: row.get(f) for f in ("owner",) + _DOC_FIELDS if f in row}
    if key[0] in approval_doctypes:
        doc["entity"] = close_event.entity_of(row)
    return doc


def _read():
    approval_doctypes = tuple(close_policy_model.APPROVAL_DOCTYPES)
    version_doctypes = list(approval_doctypes) + [_TB, _TBE, _FY, _RUN]
    versions = frappe.get_all(
        "Version", filters={"ref_doctype": ["in", version_doctypes]},
        fields=["name", "ref_doctype", "docname", "owner", "creation", "data"],
        order_by="creation asc")
    run_versions = {}
    doc_versions = []
    for v in versions:
        if v.ref_doctype == _RUN:
            run_versions.setdefault(v.docname, []).append(v)
        else:
            doc_versions.append(v)

    comments = []
    for prefix in _COMMENT_PREFIXES:
        comments += frappe.get_all(
            "Comment",
            filters={"comment_type": "Comment",
                     "reference_doctype": ["in", list(approval_doctypes)],
                     "content": ["like", prefix + "%"]},
            fields=["reference_doctype", "reference_name", "owner", "creation", "content"],
            order_by="creation asc")

    keys = {(v.ref_doctype, v.docname) for v in doc_versions}
    keys |= {(c.reference_doctype, c.reference_name) for c in comments}
    rows = _read_docs(keys)
    docs = {key: _doc_input(key, row, approval_doctypes) for key, row in rows.items()}

    periods = {
        p.name: (int(p.fiscal_year), int(p.fiscal_period), p.period_code)
        for p in frappe.db.sql(
            "SELECT p.name, y.fiscal_year, p.fiscal_period, p.period_code "
            "FROM `tabEPM Fiscal Year Period` p "
            "JOIN `tabEPM Fiscal Year` y ON y.name = p.parent "
            "WHERE p.parenttype = 'EPM Fiscal Year' AND p.parentfield = 'periods'",
            as_dict=True)
    }

    runs = []
    for run in frappe.get_all(_RUN, filters={"signed_off_by": ["is", "set"]}, fields=_RUN_FIELDS):
        runs.append(dict(run, versions=run_versions.get(run.name, [])))

    journals = frappe.get_all(
        _JOURNAL, filters={"docstatus": 1},
        fields=["name", "owner", "approved_by", "approved_at", "fiscal_year", "fiscal_period"])

    existing_rows = frappe.get_all(
        "Close Event", fields=["kind", "reference_doctype", "reference_name", "at", "source"])
    existing = [(e.kind, e.reference_doctype, e.reference_name, e.at) for e in existing_rows]
    live = [e.at for e in existing_rows if e.source == "live"]
    cutoff = min(live) if live else None

    state_fields = {
        w.document_type: w.workflow_state_field or None
        for w in frappe.get_all(
            "Workflow",
            filters={"is_active": 1, "document_type": ["in", list(approval_doctypes)]},
            fields=["document_type", "workflow_state_field"])
    }

    return {
        "versions": doc_versions, "docs": docs, "periods": periods,
        "placements": _Placements(rows), "comments": comments, "runs": runs,
        "journals": journals, "existing": existing, "cutoff": cutoff,
        "approval_doctypes": approval_doctypes, "state_fields": state_fields,
    }


def plan():
    """``(events, unplaced)``: the events to insert, and ``{reason: count}``
    of what could not be placed. Reads only."""
    return close_event_backfill_model.backfill(**_read())


def _counts(events, unplaced):
    by_kind = {}
    for e in events:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
    return {"by_kind": dict(sorted(by_kind.items())), "unplaced": dict(sorted(unplaced.items()))}


def dry_run():
    """What ``execute`` would insert, as counts; writes nothing."""
    return _counts(*plan())


def execute():
    frappe.reload_doc("consolidation", "doctype", "close_event")
    events, unplaced = plan()
    for event in events:
        close_event.record_backfill(event)
    summary = _counts(events, unplaced)
    print("backfill_close_events: %d event(s) inserted" % len(events))
    for kind, n in summary["by_kind"].items():
        print("  %s: %d" % (kind, n))
    if summary["unplaced"]:
        print("backfill_close_events: not placed, so not in the trail:")
        for reason, n in summary["unplaced"].items():
            print("  %s: %d" % (reason, n))
    print("backfill_close_events: not recoverable from the old records:")
    for sentence in close_event_backfill_model.NOT_RECOVERABLE:
        print("  - " + sentence)
    return summary
