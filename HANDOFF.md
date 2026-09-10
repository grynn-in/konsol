# konsol / konsolidat — status and next steps

_Written 10 September 2026, superseding the 6 September handoff. Everything
below was verified against the running stack, not inferred from the code._

## Where things are

| what | where |
|---|---|
| konsol (Frappe app) | `~/Documents/frappe-bench/bench-15/apps/konsol` — **moved here 6 Sep**, see below |
| konsolidat (dbt, ClickHouse, Cube, deploy) | `~/Documents/grynn/konsolidat/repo` |
| Deploy-owned checkout | `repo/docker/frappe/konsol` — a `--depth 1` clone deploy.sh hard-resets. **Never edit it.** |
| Backup of the pre-move checkout | `~/Documents/grynn/konsolidat/konsol-old-checkout-backup-20260906` (131M, all branches + stash) |
| Architecture review (F1–F10) | https://claude.ai/code/artifact/0ee1a977-bca6-4344-b1d8-5f22d7b32827 |
| Local memory (Engram) | `.claude/memory/` in this repo — gitignored |

Running stack: `http://localhost:8069`, site `konsolidat.local`, credentials in
`repo/.credentials`.

## The bench move — read this first

konsol development moved out of `repo/docker/frappe/konsol` because
**deploy.sh clones and hard-resets that directory** (deploy.sh:224,234) — it is
a build artifact, not a working tree. Developing there is what caused work to
be rescued last session and left local `main` 34 commits behind.

**Frappe is never run natively on the Mac.** bench-15 is an *editing* location
only: konsol is deliberately not in `sites/apps.txt` and not installed on
`dev.local`. The bench's Python is 3.14.2; Frappe v15 targets 3.10–3.12. Do not
run `bench get-app` or `bench install-app` there.

Test on the Mac, deploy in Docker:

| loop | command | last result |
|---|---|---|
| Python host tests | `.venv/bin/python scripts/run-host-tests.py` | 704/704 |
| JS tests | `cd konsol-exec && node --test src/*.test.mjs src/orchestrator/*.test.mjs` | 34/34 |
| Bench tests | need a live site — Docker only | n/a |

**deploy.sh reads `KONSOL_REPO` from the environment, not `.env`.** Putting it
in `.env` silently deploys GitHub main instead of your work:

```bash
cd ~/Documents/grynn/konsolidat/repo
KONSOL_REPO=~/Documents/frappe-bench/bench-15/apps/konsol \
KONSOL_BRANCH=<your branch> ./deploy.sh
```

Verified end to end: a commit existing only on this laptop reached the running
container. Worth a shell alias — forgetting it fails by *succeeding* with the
wrong code.

## Open PRs — all MERGEABLE, none merged

| PR | repo | what |
|---|---|---|
| **#139** | konsolidat | The dbt build was producing nothing and deploy.sh called it success |
| **#140** | konsolidat | Sync watermark + drop the seed fallback. **Stacks on #139** |
| **#96** | konsol | Stamp the watermark; reconcile every write-through table after migrate |
| #137 | konsolidat | docs-only, open since 2 July. Merge or close |

Merge order: **#139 → #140**, and #96 alongside #140 (the watermark is useless
without the writer).

### What #139 fixed

`var()` inside a `dbt_project.yml` config does not see that file's own `vars:`
block — it falls back to the default. So `+enabled` for the erpnext models
evaluated against `['d365_fo']` and disabled them, while the canonical models'
UNION loop (ordinary model SQL, which *does* see the var) ref'd them anyway →
`Compilation Error … depends on a node which is disabled` → dbt parsed nothing
and built no models at all. deploy.sh swallowed it as
`[WARN] … this is normal for demo data` and printed a green success banner.
`epm_gold` had been stale for 11 hours with every deploy reporting success.

Fix: `enabled` moved into each erpnext model, where `var()` resolves. deploy.sh
now separates compilation errors (fatal) from test failures (warn), reading
`PIPESTATUS[0]` — `set -e` is on but `pipefail` is not.

### What #140 / #96 fixed

`epm_staging.ownership_periods` held three rows dated 19 June for Ownership
Period records Frappe no longer has (**0 records**, still). AMDE at 75% from
that ghost drove every consolidated statement while Frappe's Consolidation
Group said 100%. Cause: sync only fires on document events, so a doctype that
empties never re-syncs and its ClickHouse rows live forever.

- `reconcile_all()` re-syncs every write-through table after migrate
- `sync_watermark` records each successful sync
- `assert_staging_not_stale` fails on EMPTIED and LAGGING tables
- the `consolidation_groups` seed fallback is gone from
  `gold_consolidation_hierarchy`

**AMDE now consolidates at 100%.** All 49 populated `epm_gold` tables rebuilt.

## Open work, highest value first

### 1. F3 — Frappe writes into the dbt repo. Do this next.

Every `bench migrate` rewrites `dbt_project.yml`: **28 comment lines → 0**,
`erp_sources` back to `[d365_fo, erpnext]`. It happened twice during the last
session, undoing #139's file each time. It also deleted `cluster_enabled` and
flipped `transaction_count.cube_type` from `sum` to `count` — the wrong value
for Cube. Three seed CSVs are rewritten too.

#139's fix holds (the build no longer *breaks*), but the churn is untouched.

Two routes carry the same metadata — write-through into `epm_staging`, and CSV
seeds in the repo — and models pick whichever is populated. Recommendation:
delete the seed fallbacks, stop generating seeds from Frappe, keep only
engineering-owned seeds. The test: *if finance would need a pull request to
change it, it does not belong in `seeds/`.*

`regenerate_vars()` rewriting `dbt_project.yml` is a separate and worse problem
— that file is engineering config, not finance data, and F3's recommendation
would not stop it. Scope it on its own.

Note: `dimension_mappings`, `cash_flow_categories` and `reporting_hierarchies`
have **no** `epm_staging` equivalent yet, so their write-through leg must be
built before the CSVs can go. Six model/macro files read them.

### 2. reconcile_all misses `CH_STAGING_TABLE` — four doctypes

Some controllers push to **two** ClickHouse tables. `CH_TABLE` is a flat copy
driven by `CH_FIELD_MAP`; `CH_STAGING_TABLE` is *computed* by hand-written code
(`_sync_hierarchy()` walks the Frappe tree for `hierarchy_level`, `path`).
`reconcile_all()` only knows the generic pattern, so these stay unreconciled:

| doctype | CH_STAGING_TABLE |
|---|---|
| Consolidation Group | `epm_staging.consolidation_hierarchy` |
| Consolidation Adjustment | `epm_staging.consolidation_adjustments` |
| IC Elimination Rule | `epm_staging.ic_elimination_rules` |
| Allocation Rule | `epm_staging.allocation_rules` |

Also: reconcile discovers **7 of 10** controllers declaring `CH_TABLE`.
Allocation Rule, Consolidation Adjustment and IC Elimination Rule are missing —
not yet investigated. **PR #96 does not cover either gap.**

### 3. `effective_ownership_pct` does not compute effective ownership

`consolidation_group.py` `_sync_hierarchy()` writes `d.ownership_pct or 100`
into a column named `effective_ownership_pct`. Nothing multiplies down the
tree, so a grandchild held 80% through a subsidiary held 50% reads 80%, not
40%. The demo hides it — AMDE is a direct child. This is **F2**.

### 4. FX rates are 100× wrong — konsolidat #138

`stg_d365_fo__exchange_rates.sql` scales conditionally, then
`silver_exchange_rates.sql:14` divides by 100 unconditionally. Fix in the
source adapter (`ConversionFactor` is a D365 concept) and delete the
unconditional division. Add a range assertion —
`assert_exchange_rate_positive` passes, because 0.00935 is positive.

### 5. Cube schema generator is destructive and wrong

`cube_type` reaches Cube through `dbt_project.yml` vars →
`scripts/generate_cube_schemas.py` → `cube/schema/*.yml`, run only by
`make cube-schema`. Nobody runs it, because it would:

- delete the hand-written `segments:` block in `trial_balance.yml`
- emit `type: count` for `transaction_count`, which is already `count(*)` in
  gold — Cube's `count` counts rows and ignores `sql:`, so `sum` is correct
- write views to `cube/views/` while the repo keeps them in `cube/schema/`

Also 9 of 13 Frappe Measures never reach dbt or Cube (13 fixtures, 4 vars).

### 6. Orphaned branch — `fix/ensure-budget-monthly-input-table`

Creates `epm_gold.budget_monthly_input` on migrate, plus tests. No PR, dated
6 Sep, not written by any recent session. **`gold_spread_budget` currently
fails without it** and 6 models skip behind it. Needs a PR or it is lost.

### 7. Loose ends

- `assert_silver_gl_debit_credit_balance` fails on the demo data. Being a test,
  it gates the whole gold layer in `dbt build` — that is why `dbt run` is
  needed to materialise. Demo GL that does not balance is worth a look.
- konsol issue **#93** — entity scoping latent on four doctypes: `System
  Manager` is the only role with read, and that is a bypass role, so the filter
  can never fire.
- `wip/local-exec-embedding` — CSRF + bleed-CSS work, unmerged, unreviewed.
- `stash@{0}` in konsolidat holds a 23 Jun `entrypoint.sh` change, superseded
  by upstream `refresh-assets.sh`. Safe to drop.

## Traps that cost real time

Full list in `.claude/memory/semantic/konsol-gotchas.md`. The worst:

1. **`dbt run` on an incremental model appends.** `gold_consolidated_trial_balance`
   and `bronze_general_journal_account_entries` are incremental — stale rows
   survived three rebuilds and made a fix look like it had failed. Use
   `--full-refresh` when correcting data.
2. **`demo-data.sql` writes `epm_staging`, not just `epm_raw`.** 10 statements
   hit `consolidation_hierarchy`, `ownership_periods`, `historical_equity_rates`,
   `ic_balances`, `ic_elimination_rules`. Loading it duplicates write-through
   tables and re-creates ghost rows. Run `reconcile_all()` + `_sync_hierarchy()`
   afterwards.
3. **`epm_raw` may be older than the code.** The volume held 19-June PascalCase
   tables while konsolidat 6523605 (26 Jun, #103) moved d365 sources to
   snake_case. `demo-data.sql` is mounted at `/docker-entrypoint-initdb.d/`, so
   ClickHouse runs it **only on first boot with an empty data dir**.
4. **`patches.txt` has no section headers** — every patch runs
   `pre_model_sync`, and a patch touching a new DocType silently no-ops **and
   records itself as run** unless it calls `frappe.reload_doc` first.
5. **`frappe.get_all` ignores permissions; `frappe.get_list` applies them.** A
   permission test written with `get_all` passes either way.
6. **`bench restart` does not restart the web workers** in the container stack —
   use `docker compose restart frappe_backend`.
7. **`frappe.get_controller` does not exist at top level in v15** — it is
   `frappe.model.base_document.get_controller`. A broad `except` around it hid
   this and made `reconcile_all` silently do nothing.
8. **Stray `* 2.*` files break dbt** with "Resource names cannot contain
   spaces". Four appeared (mode 600, all byte-identical duplicates); origin
   unknown. Watch for them.

## There is still no CI

konsol has no `.github/` at all, and konsolidat's workflows do not run konsol's
Python tests. A workflow running `scripts/run-host-tests.py` and `yarn test`
would be cheap and would have caught every red test found so far.

## gh CLI

Default account is `pyy3`, which is **not** a collaborator on grynn-in — `git
push` works but `gh pr create` fails with "must be a collaborator". Switch with
`gh auth switch --user grynn-in`. That switch persists.
