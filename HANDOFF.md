# konsol / konsolidat — status and next steps

_Written 11 September 2026, superseding the earlier handoff of the same day.
Everything below was verified against the running stack. The first task for the
next session is in "Pick up here"._

## Pick up here

**F3 is merged.** konsol #112 (`9073ba0`) and konsolidat #144 (`9cd5126`) are on
main, both mains CI-green, all ten review findings fixed and proven live.
Nothing is left open on F3.

**Next: F2 — ownership.** Three known gaps: the ownership tree disagrees with
the ownership *periods*; joint ventures / multi-parent structures are not
modelled; effective ownership percentage is never computed (only the direct
percentage is stored). Same delivery loop as F3 and F8.

Before starting, **deploy once from `main`** so the containers stop running
hot-copied files:

    cd ~/Documents/grynn/konsolidat/repo
    KONSOL_REPO=~/Documents/frappe-bench/bench-15/apps/konsol \
    KONSOL_BRANCH=main ./deploy.sh

(The running containers currently hold files byte-identical to main for
everything F3 touched — verified by sha1 — but only for those files.)

## The standing delivery loop (user rule, pre-authorized)

For each architecture-review F item: build → open PR(s) → code review → fix all
findings → squash-merge → verify main CI → record in Engram. No per-PR
permission needed. Reviews have caught feature-blocking bugs on every run so
far; do not skip them. **And verify on the running stack before merging** — the
F3 review's own suggested fix for finding 2 was wrong, and only a live run
showed it.

## Where things are

| what | where |
|---|---|
| konsol (Frappe app) — EDIT HERE | `~/Documents/frappe-bench/bench-15/apps/konsol` |
| konsolidat (dbt/ClickHouse/deploy) | `~/Documents/grynn/konsolidat/repo` |
| Deploy-owned checkout — NEVER EDIT | `repo/docker/frappe/konsol` (deploy.sh hard-resets it) |
| Engram memory | `.claude/memory/` in the konsol bench checkout |
| Local stack | `http://localhost:8069`, site `konsolidat.local`, creds `repo/.credentials` |

Frappe never runs natively on the Mac (bench Python 3.14 vs Frappe's 3.10-12;
konsol deliberately NOT in `sites/apps.txt`). Local loops:
`.venv/bin/python scripts/run-host-tests.py` (748/748) and
`cd konsol-exec && node --test src/*.test.mjs src/orchestrator/*.test.mjs`
(34/34). gh CLI: `gh auth switch --user grynn-in` (default pyy3 cannot create
PRs).

**Driving the live stack without a deploy** — this is how F3 was verified, and
it is much faster than a deploy cycle:

    docker cp konsol/<file>.py konsolidat_backend:/home/frappe/frappe-bench/apps/konsol/konsol/<file>.py
    docker cp dbt_project/models/... konsolidat_backend:/home/frappe/dbt_project/models/...
    docker exec konsolidat_backend bash -lc \
      'cd /home/frappe/frappe-bench/sites && ../env/bin/python /home/frappe/<script>.py'
    docker exec konsolidat_backend bash -lc \
      'cd /home/frappe/dbt_project && /home/frappe/frappe-bench/env/bin/dbt build --profiles-dir . --project-dir .'

`bench console` is IPython and mangles tracebacks — write a plain script with
`frappe.init(site="konsolidat.local"); frappe.connect()` instead. Do **not**
leave a script at `/tmp/inspect.py` (or any stdlib name): `/tmp` is on
`sys.path` and it shadows the stdlib module for every later run.

## What F3 changed — contracts every future session must know

1. **`dbt_project.yml` has two konsol-managed marker regions**
   (`# --- BEGIN/END konsol-managed vars ---` and
   `# --- BEGIN/END konsol-managed model domains ---`). konsol's writers splice
   ONLY inside them and REFUSE to write when markers are absent. Everything
   outside — comments, `erp_sources`, `cluster_enabled` — is hand-owned and
   preserved byte for byte. Proven again post-merge: a full `bench migrate`
   leaves the file byte-identical (same sha256, all 41 comments intact).
   Model/domain names are validated before interpolation, and an empty managed
   set renders as bare markers (`yaml.dump({})` is `{}`, which is invalid there).
2. **`erp_sources` is a committed engineering decision.** The connector-derived
   builder is deleted; `config_service.list_erp_sources` reports the registry
   read-only. Never re-derive it from Connector state — that broke the whole
   build (#139).
3. **The three seeds are gone.** `dimension_mappings`, `cash_flow_categories`,
   `reporting_hierarchies` are `epm_staging` tables written through from their
   doctypes, auto-discovered by `reconcile_all`, declared as dbt sources.
   `init-db.sql` owns fresh-install DDL; `clickhouse.ensure_reference_tables()`
   is the idempotent upgrade path for volumes that predate F3 (KEEP THE TWO IN
   SYNC).
4. **Dimension mappings are entity-aware** (konsol #111): `entity String` sits
   in the sort key; blank = ERP-wide default; entity-specific beats default via
   a two-tier join (NOT an OR — that would fan out). The uniqueness key
   includes entity on both sides, and `gold_unmapped_dimension_values` resolves
   coverage the same way.
5. **One write-through mechanism.** `clickhouse.resolve_sync_filters(doctype)`
   is the single answer to "which rows belong in ClickHouse": a controller's
   `CH_SYNC_FILTERS` first, then `docstatus=1` for submittables. Both the
   document hooks and `reconcile_all` read it. Governed reference doctypes
   subclass `GovernedReferenceDocument` (konsol/governed_reference.py), which
   owns `publish` / `unpublish` / `on_update` / `after_delete` and contains the
   **only** sync call. Do not re-add a per-controller copy.

## Scoreboard

| item | status |
|---|---|
| F1 Entity identity, F4 D11 reversal+watermark, F5, F6 Period Status | merged (earlier) |
| F8 Trial Balance Submission | merged 10 Sep (#109/#143) with 16 review findings fixed |
| FX #138 + guards, CI (both repos), watermark+reconcile | merged 10 Sep |
| **F3 one metadata path** | **merged 11 Sep (#112/#144) with 10 review findings + 3 more found live** |
| F2 ownership (tree vs periods, JV/multi-parent, effective % not computed) | open — NEXT |
| F7 roles/workspaces (half), F9 layer vocabulary, F10 IC elimination audit | open |
| konsol #103 group rate governance (design), #110 connector-less entity registry, #111 dimension scope (rest) | parked designs |
| konsolidat #137 (July docs PR), orphaned `budget_monthly_input` branch (blocks gold_spread_budget) | housekeeping |

dbt full-build baseline: **3 pre-existing errors** (gold_spread_budget needs the
orphaned branch; assert_silver_gl_debit_credit_balance and
assert_equity_rate_coverage are demo-data tensions). Anything beyond those three
is new breakage. Note `assert_cf_categories_equal_net_change` returns 84 rows
when run standalone; in a governed full build it is SKIPped behind
gold_spread_budget, which is why it is not a fourth baseline error. It fails
identically with and without the F3 changes.

## Traps (full list in memory/semantic/konsol-gotchas.md)

- **`["in", ["", None]]` does not match NULL.** It compiles to
  `x IN ('', NULL)`, and SQL never matches NULL through IN. To mean "blank or
  unset" in a Frappe filter use `["is", "not set"]`, which renders as
  `x IS NULL OR x = ''`. A blank **Link** field is stored as NULL, not `''`.
- **ClickHouse `DateTime` has one-second resolution** and rejects a microsecond
  timestamp with a 400 — and `_sync_table_inner` TRUNCATEs *before* it INSERTs,
  so the rejection empties the table. Frappe's `now_datetime()` carries
  microseconds. `clickhouse._sql_value()` truncates; use it for any new INSERT
  builder.
- **A sync helper that does not `return` its `sync_table(...)` result reports a
  failure as success.** Both `resync_staging` implementations had this bug, in
  opposite directions.
- **Harmonized dims bake in at INCREMENTAL bronze.** After changing a dimension
  mapping, rebuild
  `--select bronze_general_journal_account_entries+ --full-refresh` — refreshing
  stg (a view) + silver alone shows stale dims.
- **`insert()` with `status="Published"` used to bypass `publish()`** and its
  sync. `GovernedReferenceDocument.on_update` now covers it; keep it that way
  for any new governed reference doctype.
- Host tests load controllers via **stubbed-module imports**
  (test_budget_grain / test_write_through_contract pattern). A test that does
  `from <module> import x` where the module imports frappe is silently counted
  as a *skipped missing dependency*, not a failure — that is how a test
  importing a deleted function "passed" for months.
- **Background review forks die with the session** and with rate limits —
  findings arrive as task notifications; if a fork dies, just re-run it.
- `deploy.sh`'s in-image `bench build` is flaky; `docker compose build
  frappe_backend` standalone works, then recreate + `bench migrate`.
- **`git stash` when the tree is clean stashes nothing, and a following
  `git stash pop` pops someone else's older stash.** `git stash list` in
  konsolidat holds two entries that must not be popped by accident.
