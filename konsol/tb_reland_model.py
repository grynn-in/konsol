"""Compare the submitted trial balances with what epm_raw holds (konsol#283).

Pure: no frappe, no ClickHouse. ``trial_balance_submission.reland_submitted``
reads both sides and acts on the plan this returns.

A submitted Trial Balance Submission lands its rows and its claim once, in
``on_submit``. When the documents and the warehouse are separated (a site
restore, a lost ClickHouse volume, a move between hosts), the two sides
disagree in two ways:

* a submitted document whose batch has no claim, or whose landed row count
  is not the document's ``row_count``: its numbers are missing or wrong, and
  re-landing it from its file restores them;
* a claim with no submitted document behind it: bronze still counts it, and
  removing the claim (what a cancel does) takes it out.
"""

NO_CLAIM = "no claim in the warehouse"


def plan(documents, claims, landed, names=None):
    """What to do about each batch.

    ``documents``: the submitted documents, dicts with ``name``, ``batch_id``
    and ``row_count``. ``claims``: the control-table rows, dicts with
    ``batch_id``, ``submission_name``, ``fiscal_year``, ``fiscal_period``
    (one batch may appear more than once: a re-claim adds a row until the
    parts merge). ``landed``: ``{batch_id: rows landed}``. ``names``: when
    given, only these documents are judged; orphan claims are still found from
    the whole control table, since an orphan has no document to name.

    Returns ``reland`` [(name, why)], ``orphans`` [claim dicts], ``matched``
    [names] and ``not_submitted`` [named documents that are not submitted],
    each sorted.
    """
    by_batch = {}
    for claim in claims:
        by_batch.setdefault(claim["batch_id"], claim)
    submitted_batches = {d["batch_id"] for d in documents}

    wanted = None if names is None else set(names)
    reland, matched = [], []
    for doc in documents:
        if wanted is not None and doc["name"] not in wanted:
            continue
        batch = doc["batch_id"]
        rows = int(landed.get(batch, 0))
        if batch not in by_batch:
            reland.append((doc["name"], NO_CLAIM))
        elif rows == 0:
            reland.append((doc["name"], "claimed, but no rows landed"))
        elif rows != int(doc["row_count"]):
            reland.append((doc["name"], f"{rows} rows landed, the document has {int(doc['row_count'])}"))
        else:
            matched.append(doc["name"])

    orphans = [c for b, c in by_batch.items() if b not in submitted_batches]
    known = {d["name"] for d in documents}
    return {
        "reland": sorted(reland),
        "orphans": sorted(orphans, key=lambda c: c["batch_id"]),
        "matched": sorted(matched),
        "not_submitted": sorted((wanted or set()) - known),
    }


#: The document's totals are Floats shown to 2 places; the file is exact.
_TOTAL_TOLERANCE = 0.005


def file_problem(recorded, rows, total_debit, total_credit):
    """Why the file read back is not what the document landed, or None.

    ``recorded`` is the document's ``row_count``, ``total_debit`` and
    ``total_credit``, set from the file at submit. ``rows`` and the totals are
    what the file reads back as now. Re-landing writes what the file says, so
    a file that no longer says what the document recorded (a replaced
    attachment) would change the numbers under a submitted document: the
    re-land refuses it by name and leaves the document's claim alone
    (PR #381 review).
    """
    if int(rows) != int(recorded["row_count"]):
        return f"the file has {int(rows)} rows, the document recorded {int(recorded['row_count'])}"
    for side, total in (("debit", total_debit), ("credit", total_credit)):
        expected = float(recorded[f"total_{side}"] or 0)
        if abs(float(total) - expected) > _TOTAL_TOLERANCE:
            return f"the file's {side}s total {float(total):.2f}, the document recorded {expected:.2f}"
    return None


def orphan_verdict(claim, document):
    """None when ``claim`` is an orphan; otherwise why it is kept.

    ``document`` is the Trial Balance Submission the claim names, read with a
    lock (``docstatus``, ``batch_id``), or None when there is none. Only a
    claim whose document does not exist or is cancelled is an orphan, plus a
    claim under a submitted document's name for another batch: a submitted
    document's batch_id never changes, so that claim double-counts and cannot
    be a submit in flight. A draft is kept: on_submit claims before its
    transaction commits, so a submit in flight reads as a draft from outside
    it (PR #381 review, finding 4).
    """
    if document is None:
        return None
    docstatus = int(document["docstatus"])
    name = claim["submission_name"]
    if docstatus == 0:
        return f"{name} is a draft: a submit may be in flight"
    if docstatus == 1 and document["batch_id"] == claim["batch_id"]:
        return f"{name} is submitted with this batch"
    return None
