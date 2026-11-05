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
