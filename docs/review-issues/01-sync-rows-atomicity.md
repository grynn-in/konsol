# sync_rows: DELETE-then-INSERT is not atomic — partial failure leaves half-deleted rows

## Summary

`sync_rows()` in `konsol/clickhouse.py` does a plain `DELETE WHERE <key>` followed by an `INSERT` of the new rows, with no transaction wrapping the pair. A failure between the two (timeout, connection drop, HTTP error mid-batch) leaves the key's rows deleted with nothing to replace them — the same class of failure that #194 fixed for full-table syncs, but only the full-table path got the temp-table-and-swap treatment.

## Why it matters

- `sync_rows` is the incremental path used by budget input and other per-doc syncs, so it runs on every document save — far more often than `sync_table`.
- The failure is silent from the caller's perspective: `sync_rows` catches connection/timeout/HTTP errors, logs them, and returns without raising. The document save succeeds; ClickHouse is just wrong.
- There is no recovery path: the deleted rows are gone, and the next successful sync only writes the new rows — the gap is never detected by the watermark (which only stamps on success) or by dbt.

## Suggested direction

Mirror the `sync_table` pattern: write into a temp table, then swap. For keyed incremental sync the equivalent is probably a staging table per key or a single-transaction approach if the ClickHouse HTTP interface supports it (it doesn't natively — so temp-table + EXCHANGE, or a `ReplacingMergeTree` keyed table where failed inserts are harmless because the old version survives until the new one merges in).

## Acceptance criteria

- [ ] A forced failure between DELETE and INSERT does not leave the key's rows missing from the live table.
- [ ] The failure is visible to monitoring (not just a log line) — e.g. a watermark/reconcile signal.
- [ ] Unit test covering the mid-failure branch.

## Context

Found during a source review of the konsol repo. Related: #194 (full-table sync left tables empty — fixed via EXCHANGE TABLES swap).
