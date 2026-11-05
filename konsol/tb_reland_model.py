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
