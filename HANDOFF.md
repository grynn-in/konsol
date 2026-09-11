# konsol / konsolidat — status and next steps

_Written 11 September 2026, superseding the 10 September handoff. Everything
verified against the running stack. The first task for the next session is in
"Pick up here"._

## Pick up here

**F3 is built, PR'd, CI-green, and NOT merged.** The delivery loop (below) was
mid-review when this session ended — a background review fork does not survive
a session restart, so:

1. Run `/code-review` on branch `feat/f3-one-metadata-path` in BOTH repos —
   konsol commits b0b18b3+9216df6 (PR **grynn-in/konsol#112**) and konsolidat
   commits 8db68b9+da7184a (PR **grynn-in/konsolidat#144**). Two halves of one
   change.
2. Fix anything confirmed, push, wait for CI.
3. Squash-merge **both together** (#112 + #144), pull local mains, verify CI
   on main. Merging inside this loop is pre-authorized — see the standing
   rule below.

## The standing delivery loop (user rule, pre-authorized)

For each architecture-review F item: build → open PR(s) → code review → fix
all findings → squash-merge → verify main CI → record in Engram. No per-PR
permission needed. Reviews have caught feature-blocking bugs on every run so
far; do not skip them.

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
`.venv/bin/python scripts/run-host-tests.py` (728/728) and
`cd konsol-exec && node --test src/*.test.mjs src/orchestrator/*.test.mjs`
(34/34). Deploy: `KONSOL_REPO=<bench checkout> KONSOL_BRANCH=<branch>
./deploy.sh` — env vars, NOT .env. gh CLI: `gh auth switch --user grynn-in`
(default pyy3 cannot create PRs).

**The running containers hold hot-copied code ≈ the F3/F8 branches.** After
merging #112/#144, deploy once from `KONSOL_BRANCH=main` to align.

## What F3 changed — contracts every future session must know

1. **`dbt_project.yml` has two konsol-managed marker regions**
   (`# --- BEGIN/END konsol-managed vars ---` and
   `# --- BEGIN/END konsol-managed model domains ---`). konsol's writers
   splice ONLY inside them and REFUSE to write when markers are absent.
   Everything outside — comments, `erp_sources`, `cluster_enabled` — is
   hand-owned and preserved byte for byte. Proven: `bench migrate` leaves the
   file byte-identical (it used to strip 28 comments to 0, three times/day).
2. **`erp_sources` is a committed engineering decision.** The
   connector-derived builder is deleted; `config_service.list_erp_sources`
   reports the registry read-only. Never re-derive it from Connector state —
   that broke the whole build (#139).
3. **The three seeds are gone.** `dimension_mappings`, `cash_flow_categories`,
   `reporting_hierarchies` are `epm_staging` tables written through from
   their doctypes (Published rows only), auto-discovered by `reconcile_all`,
   declared as dbt sources. `init-db.sql` owns fresh-install DDL.
4. **Dimension mappings are entity-aware** (konsol #111, first slice):
   `entity String` sits in the sort key; blank = ERP-wide default;
   entity-specific beats default via a two-tier join (NOT an OR — that would
   fan out). Uniqueness key includes entity on both sides.

## Scoreboard

| item | status |
|---|---|
| F1 Entity identity, F4 D11 reversal+watermark, F5, F6 Period Status | merged (earlier) |
| F8 Trial Balance Submission | merged 10 Sep (#109/#143) with 16 review findings fixed |
| FX #138 + guards, CI (both repos), watermark+reconcile | merged 10 Sep |
| **F3 one metadata path** | **built + PR'd (#112/#144), review pending — PICK UP HERE** |
| F2 ownership (tree vs periods, JV/multi-parent, effective % not computed) | open — next after F3 |
| F7 roles/workspaces (half), F9 layer vocabulary, F10 IC elimination audit | open |
| konsol #103 group rate governance (design), #110 connector-less entity registry, #111 dimension scope (rest) | parked designs |
| konsolidat #137 (July docs PR), orphaned `budget_monthly_input` branch (blocks gold_spread_budget) | housekeeping |

dbt full-build baseline: **3 pre-existing errors** (gold_spread_budget needs
the orphaned branch; assert_silver_gl_debit_credit_balance and
assert_equity_rate_coverage are demo-data tensions). Anything beyond those
three is new breakage.

## Traps (additions this session — full list in memory/semantic/konsol-gotchas.md)

- **Harmonized dims bake in at INCREMENTAL bronze.** After changing a
  dimension mapping, rebuild `--select bronze_general_journal_account_entries+
  --full-refresh` — refreshing stg (a view) + silver alone shows stale dims.
- **`insert()` with `status="Published"` bypasses `publish()`** and therefore
  the ClickHouse sync — reconcile repairs. Scripted setups must call
  `doc.publish()` or run `reconcile_all()`.
- **Background review forks die with the session** and with rate limits —
  findings arrive as task notifications; if a fork dies, just re-run it.
- Host tests load controllers via **stubbed-module imports**
  (test_budget_grain pattern) — never the AST-extraction trick, which once
  silently dropped a whole test file from the suite.
- `deploy.sh`'s in-image `bench build` is flaky; `docker compose build
  frappe_backend` standalone works, then recreate + `bench migrate`.

## Merged-this-session context worth having

F8's review fixed, among 16 findings: non-idempotent landing (retry doubled a
batch), the Period Status convention inversion (absent record = OPEN), claim
fan-out, NaN validation bypass, and the silent drop of connector-less entities
at consolidation (loud guard now; real fix = #110). The FX pipeline scales
exactly once, in the D365 adapter, guarded by magnitude + factor-domain tests.
