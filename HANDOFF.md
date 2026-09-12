# konsol / konsolidat — status and next steps

_Written 12 September 2026. Everything below was verified against the running
stack. The first task for the next session is in "Pick up here"._

## Pick up here

**Nothing is in flight.** F2, F3 and konsolidat#146 are all merged, both mains
are CI-green, and no PR is open in either repo except konsolidat#137 (a docs PR
from 2 July — merge or close it).

**Do this first, before any work:** deploy from `main`. The containers hold
hot-copied files from three merged pieces of work. They match main for everything
those touched, but only for those files.

    cd ~/Documents/grynn/konsolidat/repo
    KONSOL_REPO=~/Documents/frappe-bench/bench-15/apps/konsol \
    KONSOL_BRANCH=main ./deploy.sh

**Then: konsol #110 — connector-less entities cannot reach consolidation.**

F8 shipped a CSV trial-balance intake for subsidiaries with no ERP connector,
and those entities then vanish at the consolidation chokepoint:
`gold_consolidated_trial_balance` INNER JOINs `silver_legal_entities`, an
ERP-sourced table, to get each entity's accounting currency. An entity that
exists only in konsol has no row there. The interim guard
(`assert_tb_submission_entities_consolidatable`) makes it loud rather than
silent, so the feature is half-usable rather than wrong.

`Entity` already has a `functional_currency` field, so the fix is half-enabled.
The likely shape is a konsol write-through of Entity into a staging table the
model reads instead of, or alongside, `silver_legal_entities`.

**Build #110 before #103, and the reason is not obvious.** They look like one
decision — both say konsol governs a master and the warehouse consumes it — but
they share no schema: the rate join is keyed on currency codes, and #103's grain
has no entity reference at all. The coupling is #103's proposal point 3, *"a
period cannot close until every currency appearing in that period's ledgers has
a Closing and Average rate"*. Evaluating that needs every consolidating entity's
accounting currency, which today comes from the very table #110 exists because
connector-less entities are missing from. A close gate built first has a hole
exactly where connector-less entities live.

#110 is safe to ship alone: `assert_translation_rate_resolved` already catches
the 1.0 parity fallback (a genuine cross-currency rate is never exactly
1.000000), so an entity in a currency the ERP never quoted fails the build
loudly rather than translating wrong. But until #103 exists, the only fix is to
add a rate inside D365 — the coupling both issues exist to remove. **#110 is a
prerequisite for #103's gate being correct; #103 is a prerequisite for #110
being usable.** Neither blocks the other's code.

## The standing delivery loop (user rule, pre-authorized)

Build → open PR(s) → code review → fix all findings → squash-merge → verify main
CI → record in Engram. No per-PR permission needed.

**Run the review, and verify on the running stack before merging.** Every review
this month caught a feature-blocking bug, and three times a finding was worse
than the review could see from source alone. More uncomfortably: across the last
two PRs, **three findings were introduced while fixing earlier findings**. The
recurring shape is applying a lesson in one place and not the adjacent one — so
write tests that ENUMERATE (every Link field on the doctype, every write-through
controller, every in_budget dimension) rather than naming the two cases you
happened to think of.

## Where things are

| what | where |
|---|---|
| konsol (Frappe app) — EDIT HERE | `~/Documents/frappe-bench/bench-15/apps/konsol` |
| konsolidat (dbt/ClickHouse/deploy) | `~/Documents/grynn/konsolidat/repo` |
| Deploy-owned checkout — NEVER EDIT | `repo/docker/frappe/konsol` (deploy.sh hard-resets it) |
| Engram memory | `.claude/memory/` in the konsol bench checkout |
| Local stack | `http://localhost:8069`, site `konsolidat.local`, creds `repo/.credentials` |

Frappe never runs natively on the Mac. Local loops:
`.venv/bin/python scripts/run-host-tests.py` (794/794 across 72 files) and
`cd konsol-exec && node --test src/*.test.mjs src/orchestrator/*.test.mjs`
(34/34). gh CLI: `gh auth switch --user grynn-in`.

**Driving the live stack without a deploy** — how everything this month was
verified, and much faster than a deploy cycle:

    docker cp konsol/<file>.py konsolidat_backend:/home/frappe/frappe-bench/apps/konsol/konsol/<file>.py
    docker cp dbt_project/models/... konsolidat_backend:/home/frappe/dbt_project/models/...
    docker exec konsolidat_backend bash -lc \
      'cd /home/frappe/frappe-bench/sites && ../env/bin/python /home/frappe/<script>.py'
    docker exec konsolidat_backend bash -lc \
      'cd /home/frappe/dbt_project && /home/frappe/frappe-bench/env/bin/dbt build --profiles-dir . --project-dir .'

`bench console` is IPython and mangles tracebacks — write a plain script with
`frappe.init(site="konsolidat.local"); frappe.connect()`. Never leave a script at
`/tmp/inspect.py` or any stdlib name: `/tmp` is on `sys.path`.

**A full `dbt build` SKIPS the consolidation chain** behind the baseline errors,
so a new test there never executes and a green run means nothing about it. Run
`dbt run --select +<model>+ --exclude gold_spread_budget+` then
`dbt test --select <names>`.

## Contracts a future session must not break

1. **`dbt_project.yml` has two konsol-managed marker regions.** konsol splices
   ONLY inside them and REFUSES to write when they are absent. Names are
   validated before interpolation; an empty managed set renders as bare markers.
2. **`erp_sources` is a committed engineering decision.** Never re-derive it from
   Connector state — that broke the whole build (#139).
3. **One write-through mechanism.** `clickhouse.resolve_sync_filters(doctype)` is
   the single answer to which rows belong in ClickHouse, read by BOTH the
   document hooks and `reconcile_all`. Governed doctypes subclass
   `GovernedReferenceDocument`, which holds the only sync call.
   `ensure_reference_tables()` is the idempotent upgrade path — **keep
   `_REFERENCE_TABLE_DDL` byte-identical to konsolidat's `clickhouse/init-db.sql`**,
   and a table in `_RETIRED_COLUMNS` must also be in `_REFERENCE_TABLE_DDL`.
4. **Ownership lives only in Ownership Period.** It describes a NODE — how much
   of it its parent owns — keyed on `(consolidation_group, data_area_id)`, blank
   entity for a group node. Roots take no period. Consolidation Group is
   structure; do not put a percentage back on it.
5. **Consolidation is multi-level** through `epm_staging.consolidation_ancestry`,
   the link closure konsol's tree walk writes. `gold_entity_ownership` multiplies
   each link's dated percentage and is the only ownership source; every
   consolidated model reads it, period-keyed.
6. **`dbt_project/seeds/` does not exist.** Every table is derived by dbt or
   written by konsol. A seed materialises into `epm_gold`, so re-adding one
   silently creates a second writer for whatever konsol already writes there.
7. **`konsol/fixtures/` is force-reimported on every migrate**, whatever the
   `fixtures` hook lists. Reference data a site is not expected to edit belongs
   there; anything a user edits belongs in `konsol/demo_data/`, seeded once —
   and seeded BEFORE `_reconcile_clickhouse()`, because a document saved during
   migrate never reaches ClickHouse.

## Scoreboard

| item | status |
|---|---|
| F1, F4, F5, F6, F8, FX, CI | merged (earlier) |
| **F3 one metadata path** | merged 11 Sep (#112/#144), 10 review findings fixed |
| **F2 ownership + multi-level consolidation** | merged 11 Sep (#116/#145), 17 findings fixed |
| **konsolidat#146 eleven seeds** | merged 12 Sep (#119/#147/#150), 13 findings fixed |
| F7 roles/workspaces (half), F9 layer vocabulary, F10 IC elimination audit | open |

dbt full-build baseline: **2 pre-existing errors** —
`assert_silver_gl_debit_credit_balance` and `assert_equity_rate_coverage`, both
demo-data tensions. The second is caused by konsol#82. Anything beyond those two
is new breakage. `assert_cta_zero_for_same_currency` fails (24) when the chain is
run directly, because CTA is `-sum(group_amount)` and AMHQ's local GL is out of
balance by 17.4m — a symptom of the first error, not a third.

## Priority list

**P1** — konsol **#110** (above), then konsolidat **#148** (IC elimination is
dormant at 0 rows AND the engine over-eliminates when woken: measured 2,614,288
against a 2,561,389 balance, because it joins every entity pair using full
balances with no counterparty filter).

**P2** — konsol **#104** (blank measure fails 8 of 10 scenarios), **#106**
(forecast Dataset, one fixture row), **#108** (TB snapshot truncates silently at
10 000 rows), **#120** (six doctypes sync from `on_trash`, so a delete
re-publishes the deleted row — measured on Spread Profile: Frappe 24→23,
ClickHouse 24→**24**).

**P3** — konsol **#82** + konsolidat **#92** together (historical equity rates;
#82 is why `epm_staging.historical_equity_rates` sits at **0 rows** and is the
direct cause of a baseline build failure — the doctype is empty so every
reconcile truncates whatever demo-data inserted; #92's six findings are 4 fixed,
#3 partly, #6 to confirm). Then konsol **#93** — entity scoping is latent on
**14** doctypes that are System Manager-only, not the four the issue names.

**P4, decisions that need the user** — konsol #103, #105, #107, #111, #113,
#117; konsolidat #91 (only part C remains, gated on *"do accountants need to
hand-key rates D365 does not supply?"*), #93.

**P5 backlog** — konsol #97–#101, #56, #59, #74; konsolidat #57, #90, #137, #149.

**Two dependencies worth knowing.** konsol#113 proposes `partner_data_area_id`
required on IC accounts — that is precisely the counterparty konsolidat#148's
elimination engine is missing, so deciding them separately gives two
incompatible answers. And #111's remaining decisions now only matter for ERP-fed
entities; #113 settles the same question for file submitters by rejecting the
file, and nobody has decided how you enforce a mandatory dimension when there is
no file to reject.

## Traps (full list in memory/semantic/konsol-gotchas.md)

- **`["in", ["", None]]` does not match NULL** — it compiles to `x IN ('', NULL)`
  and SQL never matches NULL through IN. Use `["is", "not set"]`. A blank **Link**
  is stored as NULL, not `''`.
- **An unset field must be written as the `DEFAULT` keyword** — not NULL (works
  only while `input_format_null_as_default` is on, and is rejected *after* the
  TRUNCATE) and not `''` (right for String, breaks every Date).
- **ClickHouse `Date` holds 1970-01-01..2149-06-06 and CLAMPS SILENTLY**;
  `DateTime` has one-second resolution and rejects microseconds with a 400, also
  after the TRUNCATE.
- **A fixture that imports is not a fixture that is correct.** Import sets
  `ignore_links`, so a shipped row can reference a record that does not exist,
  load happily, and then be unsaveable from the desk. Caught three times.
- **Frappe never drops a column whose field left the DocType JSON.** A patch has
  to. `patches.txt` has no section headers so every patch runs `pre_model_sync`,
  which is what lets a patch read the column it is retiring.
- **A Float column is `not null default 0`** — "unset" and 0 are
  indistinguishable. Never write `x or 100`.
- **`join_use_nulls=0`**: an unmatched LEFT JOIN fills with the column DEFAULT,
  not NULL, so `left join ... where x is null` never returns a row. Use `NOT IN`
  or cast to `Nullable`.
- **ClickHouse `JOIN ... ON` accepts only equality conjunctions** — `in (a, b)`
  or `OR` there raises UNSUPPORTED_METHOD; put it in `WHERE`.
- **A test file that stubs `sys.modules["konsol"]` without restoring it poisons
  every LATER test file** — the runner counts them as missing deps and skips
  them silently (750 → 630 across 15 files).
- **Assertions that a name is ABSENT keep matching the docstring explaining its
  removal.** Strip string literals with `ast` first.
- **Harmonized dims bake in at INCREMENTAL bronze** — rebuild
  `bronze_general_journal_account_entries+ --full-refresh`.
- **`git stash` on a clean tree stashes nothing**, and the following `git stash
  pop` pops someone else's older stash. konsolidat's list holds two.
- **Check the remote, not just the commit.** Three konsol commits sat unpushed
  while their konsolidat halves were on a PR; merging that pair would have
  deleted seeds with nothing writing the tables. A peer session caught it.
