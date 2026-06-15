# Run State Model — build & close-assertion progress in Frappe Desk

Status: design + initial implementation (branch `feat/run-state-model`)
Related: `docs/prd/PRD-CLOSE-ASSERTION-SUITE.md` (§6.10) in the konsolidat repo.

## Problem

Two long-running, multi-step operations have no first-class *state* surface in
the app:

1. **dbt builds** (`Pipeline Run` / `Pipeline Build Request`) — today a flat
   `status` Select plus an `error_log` blob. You cannot see *which model* is
   building, how far along it is, or stream the log.
2. **The close assertion suite** (§6.10) — 60 dbt singular tests. Today the only
   place the green/red lives is the dbt CLI (`Done. PASS=58 ERROR=6`). There is
   no record of *which* assertion failed, by how much, or which rows broke.

## Pattern borrowed from `frappe/press`

Press's `Deploy Candidate` models a build as a parent doc plus a child table
`Deploy Candidate Build Step` (`istable:1`) where each row is one step with its
own `status` Select (`Pending/Running/Success/Failure`), `duration`, and an
`output` Code field holding that step's log. The list view colors the parent by
status via `get_indicator`. We adopt the same three ideas:

1. parent `status` Select + list-view `get_indicator` (colored dots),
2. a child table of steps, each carrying status + captured output,
3. live updates via `frappe.publish_realtime` → `frappe.realtime.on` in the form.

## State machines

```
Pipeline Run:    Queued → Running → (Completed | Failed)
Pipeline Step:   Pending → Running → (Success | Failure | Skipped)
Close Run:       Queued → Running → (Green | Red | Error)
Assertion:       (Pass | Fail | Error)          # terminal, per node
```

## Doctypes

### Pipeline Step (child, module Pipeline) — NEW
| field | type | options |
|---|---|---|
| stage | Data | Seed / Bronze / Silver / Gold / Test |
| step | Data | node (model/test) name |
| status | Select | Pending / Running / Success / Failure / Skipped |
| rows | Int | rows produced |
| duration | Float | seconds |
| output | Code | captured log for this node |

### Pipeline Run (module Pipeline) — EXTENDED
Adds `progress_pct` (Int), `log` (Code, full streamed log), and `steps`
(Table → Pipeline Step). Existing fields are untouched (backward compatible).

### Assertion Result (child, module Consolidation) — NEW
| field | type | options |
|---|---|---|
| assertion | Data | e.g. `assert_end_to_end_bs_balances` |
| dimension | Select | Consolidation / Allocation / FX / Ownership / Data Quality / Other |
| severity | Select | error / warn |
| status | Select | Pass / Fail / Error |
| rows_failed | Int | dbt "Got N results" |
| message | Small Text | one-line why |
| failures_table | Data | `--store-failures` table to drill into |

### Close Run (parent, module Consolidation) — NEW
| field | type | notes |
|---|---|---|
| status | Select | Queued / Running / Green / Red / Error |
| fiscal_year / fiscal_period | Int | which close (optional) |
| total / passed / failed / errored | Int | the `58 / 6` headline |
| started_at / completed_at / duration_seconds | Datetime/Float | |
| pipeline_run | Link → Pipeline Run | ties results to the build |
| log | Code | streamed dbt test log |
| results | Table → Assertion Result | one row per assertion |

## Dimension classification

Assertions are bucketed by filename prefix/keywords so the UI can group reds:
`ic_*`/`consolidat*`/`nci_*`/`cta_*`/`equity_*` → Consolidation;
`alloc*`/`step*`/`tier*`/`pool*`/`reciprocal*`/`driver*` → Allocation;
`*rate*`/`*currency*`/`cta*` → FX; `ownership*` → Ownership;
`*null*`/`*schema*`/`*chart*`/`*unique*` → Data Quality; else Other.

## Realtime contract (the seam with the §6.10 UI)

The worker emits, per node:

```python
frappe.publish_realtime(
    "close_run_update",
    {"run": name, "assertion": a, "status": s, "rows_failed": n, "line": log_line},
    doctype="Close Run", docname=name,
)
```
and `pipeline_run_update` with `{run, step, status, line}` for builds. The §6.10
visual workspace renders `Close Run` / `Assertion Result` records and subscribes
to these events; this layer owns producing them.

## "Which rows broke" — drill-down

The close runner invokes `dbt test --store-failures`. Each failing assertion
writes its offending rows to `epm_gold.dbt_test__audit__<assertion>`; that table
name is stored on the `Assertion Result.failures_table` field so a click shows
the exact GL lines that failed (e.g. the 12 rows of `assert_end_to_end_bs_balances`).
