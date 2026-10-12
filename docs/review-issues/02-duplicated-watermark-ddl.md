# Duplicated watermark DDL in clickhouse.py and dbt project — drift hazard

## Summary

The `epm_staging.sync_watermark` table definition is maintained in two places:

1. `_WATERMARK_DDL` in `konsol/clickhouse.py`
2. `dbt_project.yml` `on-run-start` in the konsolidat dbt project

The clickhouse.py module header explicitly says: *"konsolidat's dbt_project.yml on-run-start creates the same table; keep the two bodies identical."* That is a manual invariant with no enforcement.

## Why it matters

- `assert_staging_not_stale` (the dbt-side staleness check) depends on the watermark having exactly the shape and semantics the Python side writes — including the `reconcile_run_id` column added in #246 and its position in the sorting key.
- If the two bodies drift (a column added on one side, an ORDER BY change on the other, an engine change), the staleness check either false-positives or silently stops working. The #246 comment in clickhouse.py shows how much care went into the column semantics (String not nullable, in the sorting key, no DEFAULT clause) — all of which would be lost if one side changed without the other.
- This is the same class of bug as identifier/value escaping drift: invisible until a production incident.

## Suggested direction

- Single source of truth: generate one body from the other (e.g. Python emits the DDL and dbt reads it, or a shared SQL file both reference).
- Or a CI check that diffs the two bodies on every PR touching either.
- At minimum, a test that parses both and asserts column set, types, and ORDER BY match.

## Acceptance criteria

- [ ] One canonical definition; the other is derived or checked.
- [ ] CI fails when the two bodies diverge.
- [ ] Existing #246 semantics (run id column, sorting key, no DEFAULT) preserved.

## Context

Found during a source review. The clickhouse.py comment acknowledges the duplication risk but relies on human discipline to manage it.
