# konsol / konsolidat — status and next steps

_Written 11 September 2026, superseding the earlier handoff of the same day.
Everything below was verified against the running stack. The first task for the
next session is in "Pick up here"._

## Pick up here

**F2 is merged.** konsol #116 (`875557f`) and konsolidat #145 (`934d264`) are on
main, both CI-green, all seventeen review findings across the two PRs fixed and
proven live. Nothing is left open on F2 or F3.

**Deploy once from `main` before starting anything.** The containers hold
hot-copied files from the F2 work; they match main for everything F2 touched
(verified by sha1) but only for those files:

    cd ~/Documents/grynn/konsolidat/repo
    KONSOL_REPO=~/Documents/frappe-bench/bench-15/apps/konsol \
    KONSOL_BRANCH=main ./deploy.sh

**Next: pick from the open list.** F7 (roles/workspaces, half done), F9 (collapse
five dbt layer folders to three — `epm_staging` is a third meaning of
"staging"), F10 (verify IC elimination fires at the lowest common ancestor —
never audited; `gold_ic_eliminations` now emits `debit_entity`/`credit_entity`,
which is the audit trail F10 needs). Or konsolidat #146, which is a live
data-loss bug and small once the data decisions are made.

## The standing delivery loop (user rule, pre-authorized)

For each architecture-review F item: build → open PR(s) → code review → fix all
findings → squash-merge → verify main CI → record in Engram. No per-PR permission
needed.

**Run the review, and verify on the running stack before merging.** Both
reviews this session caught feature-blocking bugs, and both times a finding was
worse than the review could see from the source: F3's suggested fix for the
blank-entity guard did not work, and F2's fixture fix did not work either.
Neither was visible without running it.

## Where things are

| what | where |
|---|---|
| konsol (Frappe app) — EDIT HERE | `~/Documents/frappe-bench/bench-15/apps/konsol` |
| konsolidat (dbt/ClickHouse/deploy) | `~/Documents/grynn/konsolidat/repo` |
| Deploy-owned checkout — NEVER EDIT | `repo/docker/frappe/konsol` (deploy.sh hard-resets it) |
| Engram memory | `.claude/memory/` in the konsol bench checkout |
| Local stack | `http://localhost:8069`, site `konsolidat.local`, creds `repo/.credentials` |

Frappe never runs natively on the Mac. Local loops:
`.venv/bin/python scripts/run-host-tests.py` (775/775) and
`cd konsol-exec && node --test src/*.test.mjs src/orchestrator/*.test.mjs`
(34/34). gh CLI: `gh auth switch --user grynn-in`.

**Driving the live stack without a deploy** — this is how F2 and F3 were
verified and it is much faster than a deploy cycle:

    docker cp konsol/<file>.py konsolidat_backend:/home/frappe/frappe-bench/apps/konsol/konsol/<file>.py
    docker cp dbt_project/models/... konsolidat_backend:/home/frappe/dbt_project/models/...
    docker exec konsolidat_backend bash -lc \
      'cd /home/frappe/frappe-bench/sites && ../env/bin/python /home/frappe/<script>.py'
    docker exec konsolidat_backend bash -lc \
      'cd /home/frappe/dbt_project && /home/frappe/frappe-bench/env/bin/dbt build --profiles-dir . --project-dir .'

`bench console` is IPython and mangles tracebacks — write a plain script with
`frappe.init(site="konsolidat.local"); frappe.connect()`. Never leave a script at
`/tmp/inspect.py` or any stdlib name: `/tmp` is on `sys.path`.

**A full `dbt build` SKIPS the whole consolidation chain** behind the three
baseline errors, so new tests there never execute. To exercise them:
`dbt run --select +<model>+ --exclude gold_spread_budget+` then
`dbt test --select <names>`.

## The contracts F2 and F3 established

1. **`dbt_project.yml` has two konsol-managed marker regions.** konsol splices
   ONLY inside them and REFUSES to write when they are absent; everything else
   is preserved byte for byte. Model/domain names are validated before
   interpolation; an empty managed set renders as bare markers.
2. **`erp_sources` is a committed engineering decision.** Never re-derive it
   from Connector state — that broke the whole build (#139).
3. **Governed reference data writes through to `epm_staging`.**
   `resolve_sync_filters(doctype)` is the single answer to which rows belong in
   ClickHouse, read by BOTH the document hooks and `reconcile_all`. Governed
   doctypes subclass `GovernedReferenceDocument`, which holds the only sync
   call. `clickhouse.ensure_reference_tables()` is the idempotent upgrade path —
   **keep `_REFERENCE_TABLE_DDL` byte-identical to konsolidat's
   `clickhouse/init-db.sql`**; a table in `_RETIRED_COLUMNS` must also be in
   `_REFERENCE_TABLE_DDL` or its ALTER fails on a fresh volume.
4. **Ownership lives in exactly one place: Ownership Period.** It describes a
   NODE — how much of it its parent owns — keyed on
   `(consolidation_group, data_area_id)`, blank entity for a group node. Roots
   take no period. Consolidation Group is structure; do not put a percentage
   back on it.
5. **Consolidation is multi-level** through `epm_staging.consolidation_ancestry`,
   the link closure konsol's tree walk writes. `gold_entity_ownership`
   multiplies each link's dated percentage: one row per (ancestor group, entity,
   period), with `effective_ownership_pct`, `direct_ownership_pct`,
   `owner_group`, `has_complete_chain` and `outside_ownership_window`. Every
   consolidated model reads it, period-keyed.
6. **Dimension mappings are entity-aware.** Blank entity = ERP-wide default;
   entity-specific beats default via a two-tier join (NOT an OR — that fans out).

## Scoreboard

| item | status |
|---|---|
| F1, F4, F5, F6, F8, FX, CI | merged (earlier) |
| **F3 one metadata path** | merged 11 Sep (#112/#144), 10 review findings fixed |
| **F2 ownership + multi-level consolidation** | merged 11 Sep (#116/#145), 17 review findings fixed |
| F7 roles/workspaces (half), F9 layer vocabulary, F10 IC elimination audit | open |
| konsol #117 JV / multi-parent ownership | filed, designed, not built |
| konsolidat #146 five more seed/write-through collisions | filed with evidence — live data loss |
| konsol #103 group rate governance, #110 connector-less entity registry, #111 dimension scope (rest) | parked designs |
| konsolidat #137 (July docs PR), orphaned `budget_monthly_input` branch | housekeeping |

dbt full-build baseline: **3 pre-existing errors** — `gold_spread_budget` (needs
the orphaned branch), `assert_silver_gl_debit_credit_balance` and
`assert_equity_rate_coverage` (demo-data tensions). Anything beyond those three
is new breakage. `assert_cta_zero_for_same_currency` fails (24) when the chain is
run directly: CTA is `-sum(group_amount)` and AMHQ's local GL is out of balance
by 17.4m, so it is a symptom of the second baseline error, not a fourth.

## Traps (full list in memory/semantic/konsol-gotchas.md)

- **`["in", ["", None]]` does not match NULL** — it compiles to `x IN ('', NULL)`
  and SQL never matches NULL through IN. Use `["is", "not set"]`. A blank
  **Link** field is stored as NULL, not `''`.
- **An unset field must be written as the `DEFAULT` keyword** — not NULL (works
  only while `input_format_null_as_default` is on, and is rejected *after* the
  TRUNCATE) and not `''` (right for String, breaks every Date).
- **ClickHouse `Date` holds 1970-01-01..2149-06-06 and CLAMPS SILENTLY**;
  `DateTime` has one-second resolution and rejects a microsecond timestamp with
  a 400 — also after the TRUNCATE.
- **Everything in `konsol/fixtures/` is force-reimported on every migrate**,
  whatever the `fixtures` hook lists (the hook is read only when exporting), and
  the import force-deletes first — bypassing the submitted-document guard. Data
  users edit belongs in `konsol/demo_data/`, seeded once. See its README.
- **A document saved during migrate never reaches ClickHouse** (`sync_table`
  no-ops while `in_migrate` is set). `reconcile_all` passes `force=True`, so
  anything seeded in `after_migrate` must run BEFORE `_reconcile_clickhouse()`.
- **Frappe never drops a column whose field left the DocType JSON.** A patch has
  to do it, and `patches.txt` has no section headers so every patch runs
  `pre_model_sync` — which is what lets a patch read the column it is retiring.
- **A Float column is `not null default 0`**, so "unset" and 0 are
  indistinguishable. Never write `x or 100`.
- **`join_use_nulls=0`**: an unmatched LEFT JOIN fills a non-nullable column with
  its DEFAULT, not NULL, so `left join ... where x is null` never returns a row.
  Use `NOT IN`, or cast to `Nullable`.
- **A test file that stubs `sys.modules["konsol"]` without restoring it poisons
  every LATER test file** — the runner counts them as missing deps and skips
  them, so the suite shrinks silently (750 → 630 across 15 files).
- **Assertions that a name is ABSENT keep matching the docstring explaining its
  removal.** Strip string literals with `ast` first.
- **Harmonized dims bake in at INCREMENTAL bronze** — after changing a dimension
  mapping rebuild `bronze_general_journal_account_entries+ --full-refresh`.
- **`git stash` on a clean tree stashes nothing**, and the following
  `git stash pop` pops someone else's older stash. konsolidat's list holds two.
- `deploy.sh`'s in-image `bench build` is flaky; `docker compose build
  frappe_backend` standalone works, then recreate + `bench migrate`.
