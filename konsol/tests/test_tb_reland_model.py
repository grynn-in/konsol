"""konsol#283: compare the submitted trial balances with what epm_raw holds.

The rule is pure (konsol/tb_reland_model.py): given the submitted documents
and what ClickHouse holds per batch, say which documents must be re-landed,
which claims have no document behind them, and which match.
"""
from konsol import tb_reland_model as m


def _doc(name, batch, rows=3):
    return {"name": name, "batch_id": batch, "row_count": rows}


def _claim(batch, name, fy=2099, fp=1):
    return {"batch_id": batch, "submission_name": name, "fiscal_year": fy, "fiscal_period": fp}


def test_a_restored_site_with_an_empty_warehouse_re_lands_every_document():
    out = m.plan([_doc("TBS-1", "b1"), _doc("TBS-2", "b2")], claims=[], landed={})
    assert out["reland"] == [("TBS-1", m.NO_CLAIM), ("TBS-2", m.NO_CLAIM)]
    assert out["orphans"] == [] and out["matched"] == []


def test_a_claimed_batch_with_the_right_row_count_matches():
    out = m.plan([_doc("TBS-1", "b1", rows=3)], claims=[_claim("b1", "TBS-1")], landed={"b1": 3})
    assert out == {"reland": [], "orphans": [], "matched": ["TBS-1"], "not_submitted": []}


def test_a_claim_with_no_rows_or_the_wrong_count_is_re_landed_and_says_why():
    docs = [_doc("TBS-1", "b1", rows=3), _doc("TBS-2", "b2", rows=3)]
    claims = [_claim("b1", "TBS-1"), _claim("b2", "TBS-2")]
    out = m.plan(docs, claims=claims, landed={"b2": 6})
    assert out["reland"] == [
        ("TBS-1", "claimed, but no rows landed"),
        ("TBS-2", "6 rows landed, the document has 3"),
    ]
    assert out["matched"] == []


def test_a_claim_with_no_submitted_document_is_an_orphan():
    """Rows with no document (a restore of an older backup over a newer
    warehouse): bronze still counts them until the claim goes."""
    claims = [_claim("b1", "TBS-1"), _claim("b9", "TBS-9", fp=4)]
    out = m.plan([_doc("TBS-1", "b1")], claims=claims, landed={"b1": 3, "b9": 5})
    assert out["matched"] == ["TBS-1"]
    assert out["orphans"] == [_claim("b9", "TBS-9", fp=4)]


def test_names_limit_the_documents_but_never_hide_an_orphan_outside_them():
    """Orphans are found from the whole control table: a claim whose document
    is gone cannot be named, because there is no document to name."""
    docs = [_doc("TBS-1", "b1"), _doc("TBS-2", "b2")]
    out = m.plan(docs, claims=[_claim("b9", "TBS-9")], landed={}, names=["TBS-2"])
    assert out["reland"] == [("TBS-2", m.NO_CLAIM)]
    assert [o["batch_id"] for o in out["orphans"]] == ["b9"]


def test_a_named_document_that_is_not_submitted_is_reported_not_dropped():
    out = m.plan([_doc("TBS-1", "b1")], claims=[], landed={}, names=["TBS-1", "TBS-7"])
    assert out["not_submitted"] == ["TBS-7"]


def test_duplicate_claim_rows_of_one_batch_are_one_claim():
    """The control table is a ReplacingMergeTree: an amount-basis re-claim
    adds a row with the same batch_id until the parts merge."""
    claims = [_claim("b1", "TBS-1"), _claim("b1", "TBS-1")]
    out = m.plan([_doc("TBS-1", "b1")], claims=claims, landed={"b1": 3})
    assert out["matched"] == ["TBS-1"] and out["orphans"] == []


def test_the_output_is_sorted_so_two_runs_compare():
    docs = [_doc("TBS-2", "b2"), _doc("TBS-1", "b1")]
    claims = [_claim("b8", "TBS-8"), _claim("b7", "TBS-7")]
    out = m.plan(docs, claims=claims, landed={})
    assert [n for n, _ in out["reland"]] == ["TBS-1", "TBS-2"]
    assert [o["batch_id"] for o in out["orphans"]] == ["b7", "b8"]


# -- PR #381 review: what the re-land may trust, and what is an orphan ----------------------------

def _recorded(rows=2, debit=100.5, credit=100.5):
    return {"row_count": rows, "total_debit": debit, "total_credit": credit}


def test_a_file_that_reads_back_as_the_document_recorded_has_no_problem():
    assert m.file_problem(_recorded(), 2, 100.5, 100.5) is None
    # the document's totals are Floats shown to 2 places; the file is exact
    assert m.file_problem(_recorded(debit=0.1 + 0.2, credit=0.3), 2, 0.3, 0.3) is None


def test_a_file_that_is_not_what_the_document_landed_is_refused_by_name():
    """Re-landing writes what the file says. When the file no longer says
    what the document recorded at submit (a replaced attachment), landing it
    would change the numbers under a submitted document: refuse instead."""
    assert m.file_problem(_recorded(rows=3), 2, 100.5, 100.5) == (
        "the file has 2 rows, the document recorded 3")
    assert m.file_problem(_recorded(), 2, 99.5, 100.5) == (
        "the file's debits total 99.50, the document recorded 100.50")
    assert m.file_problem(_recorded(), 2, 100.5, 7) == (
        "the file's credits total 7.00, the document recorded 100.50")


def test_a_claim_whose_document_does_not_exist_or_is_cancelled_is_an_orphan():
    assert m.orphan_verdict(_claim("b9", "TBS-9"), None) is None
    assert m.orphan_verdict(_claim("b9", "TBS-9"), {"docstatus": 2, "batch_id": "b9"}) is None


def test_a_claim_whose_document_is_a_draft_is_kept_it_may_be_a_submit_in_flight():
    """on_submit writes the claim before its transaction commits, so a reader
    outside that transaction sees the document as a draft (or, through a
    locking read, waits for it). Removing that claim would drop a TB that is
    being submitted right now (PR #381 review, finding 4)."""
    why = m.orphan_verdict(_claim("b9", "TBS-9"), {"docstatus": 0, "batch_id": "b9"})
    assert why == "TBS-9 is a draft: a submit may be in flight"


def test_a_claim_of_a_submitted_document_under_another_batch_is_an_orphan():
    """A submitted document claims its own batch_id, which never changes once
    submitted; a second claim under its name for another batch double-counts
    the entity-period and cannot belong to a submit in flight."""
    assert m.orphan_verdict(_claim("b8", "TBS-9"), {"docstatus": 1, "batch_id": "b9"}) is None
    assert m.orphan_verdict(_claim("b9", "TBS-9"), {"docstatus": 1, "batch_id": "b9"}) == (
        "TBS-9 is submitted with this batch")
