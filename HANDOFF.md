# konsol / konsolidat — status and next steps

_Written 12 September 2026. Everything below was verified against the running stack._

## Pick up here

**The user's instruction, verbatim:** *"First empty the site completely.
Re-set up with a correct set of data.
/Users/deepakpai/Downloads/Ecolab_Konsolidat_CoA_TB_2010_2025.xlsx"*

It came straight after we found that **every number in the current system is
wrong**. Every GL credit is booked as a debit (konsolidat **#155**, below). The
Contoso/Alpine demo data is being replaced, not repaired.

### Before wiping anything

1. **Finish #110 first. The Ecolab load depends on it.** The Ecolab entities
   have no ERP connector, so their TBs enter through Trial Balance Submission,
   and until #110 a connector-less entity has no currency and is dropped at
   consolidation. State: konsol **#123** (`464f71f`) and konsolidat **#151**
   (`6400e15`) are open, MERGEABLE and CI-green. Fix the re-review items below,
   then **merge konsol #123 first**, then #151.
2. **Confirm the wipe's scope with the user, and back up first**
   (`docker compose --profile backup run --rm backup` in `repo/`). "Completely"
   means the MariaDB site *and* the ClickHouse volume. It can't be undone.
3. **Emptying won't stick without a code change.** `konsol/fixtures/` is
   force-reimported on **every** migrate (contract 7), and it carries the demo:
   Consolidation Group, Connector, Scenario, IC Elimination Rule, Allocation
   Rule/Driver, Spread Profile, Budget Sheet, Dimension Mapping, Reporting
   Hierarchy (`hooks.py:14-58`). `konsol/demo_data/` seeds ownership and annual
   budget once. The Entity backfill patch rebuilds Entities from Consolidation
   Group. And `scripts/generate_demo_data.py` fills `epm_raw` in konsolidat. Split
   *reference* fixtures (ISO Currency, Fiscal Period, Dimension, Build
   Scope/Model, Pipeline, Entity Fiscal Calendar?) from *demo* ones, and remove
   or gate the demo set, or the next migrate brings Contoso back.

### The workbook (inspected, not yet loaded)

| sheet | what | rows |
|---|---|---|
| 00_Cover | design notes. Entity roster from Ecolab's FY2025 10-K Ex. 21.1; amounts are **synthetic** allocations of the published consolidated figures | — |
| 01_CoA | group chart `ECL_GROUP_GAAP`: posting accounts 1000–9000 plus parents; account_type, normal_balance D/C, time_balance, **fx_method closing/average/historical**, cf_category, allow_ic | 87 |
| 02_LegalEntities | data_area_id (`US_ECL`, `US_USA`, …), accounting_currency, parent_data_area, ownership_pct, is_tb_entity, region/division/platform | 83 |
| 03_DivisionHierarchy | management dimension (GW, GIS, Pest, LS …); keep it **off** the legal tree | 27 |
| 04_AcquisitionEvents | acquisitions and disposals with price and goodwill (Nalco 2011, Champion 2013–2020, Purolite 2021, Ovivo 2025 …) | 12 |
| 05_OwnershipPeriods | group `ECL_GROUP`; methods **`parent`** / `full`; 100%; dates 1924-02-18 … 2025-12-16, end 2020-06-03 / **9999-12-31** | 83 |
| 06_FX_Rates | **USD per 1 unit**, AVERAGE + CLOSING, annual 2010–2025, 20 currencies | 640 |
| 07_PublishedAnchors | 10-K consolidated targets, USD **millions** | 34 |
| 08_TB_Annual_USD | the TB in **USD thousands**; reference only | 29,720 |
| **09_TB_Annual_Local** | **the load file (user, 12 Sep)**: the same TB in functional currency, **thousands of local units**. `local = usd ÷ fx_rate_used` (closing for BS, average for P&L, historical for some equity). 40 entities, 15 currencies, 559 entity-years, period 12, scenario ACTUAL | 29,720 |
| 10_IC_Rules | 5 IC rules: AR/AP, rev/COS, royalty, **URP at 28%**, investment vs equity on US_ECL. Written for `seeds/`, which no longer exists; load them into IC Elimination Rule | 5 |
| 11_Control_Checks | TB vs 10-K per year (USD m) | 16 |
| 12_EntityYearFlag | perimeter flag per entity-year: 1,126 active, of which **559 have a TB** and 567 don't (satellites, pre-acquisition) | 83 |
| 13_Assumptions | A01–A19; read before loading (A10 hyperinflation ignored, A16 `3500` is a $500m placeholder, A17 annual only, A18 units) | 19 |

### ⚠ The trial balances don't balance, so the workbook is not load-ready

Measured on 12 Sep from sheets 08 and 09 (stdlib xlsx parse; there is no openpyxl on the Mac):

- **0 of 559** entity-years net to zero, in USD or in local, and **every one is debit-heavy** (559/559). The 77 USD-functional entity-years don't balance either, so FX isn't the cause: 09 is exactly 08 ÷ `fx_rate_used`, row by row.
- The gap isn't the year's NI counted twice (0/559 match), and the balance sheet doesn't net on its own (0/559). Gap ÷ P&L total has a median of −10×, ranging from −704× to +4×.
- **Cause: the credit side of equity and financing was never generated.** Of the posting equity accounts, only `3000` Common stock and `3100` Retained earnings appear in the TB. `3010` APIC, `3020` Treasury, `3200`/`3300` AOCI (including CTA) and `3400` NCI never do.
  - US_USA 2015 (USD k): assets +3,557,447; liabilities −1,150,698; equity −477,424; revenue −3,152,284; expense +2,930,177, so **+1,707,218 unbalanced**.
  - Group balance-sheet surplus: +3.6bn in 2010, +9.9bn in 2011 (Nalco's assets arrive with no funding).
- **Consequence:** Trial Balance Submission refuses every file (`validate_tb_rows`: debits ≠ credits). Any plug the loader invents makes the consolidated balance sheet, and CTA, meaningless.
- 11_Control_Checks doesn't foot and contradicts its own note. Sales come out **141–471m below** published in every year, while the note says TB sales should *exceed* published by the IC share. 2020 NI is off by +1.8bn and assets by ±2bn.

**This is the user's call, not the loader's.** The options:
- (a) Regenerate the workbook with the equity and financing side populated so each entity-year balances. This is the honest fix.
- (b) Load it with an explicit, named balancing line per entity-year (e.g. to `3010` APIC or a suspense account). P&L stays meaningful; the balance sheet and CTA don't.
- (c) Load P&L only.

Put these to the user; don't pick one.

### Other decisions to put to the user before loading

- **Units:** 08/09 are in thousands and 07 in millions (A18). Store units, not thousands, and say so in the loader.
- **Annual only, period 12** (A17). Is a `balance` account's period-12 figure the closing balance or the year's movement? The YTD and cash-flow models assume period movements.
- **Ownership method `parent`** isn't an Ownership Period option; roots take no period (contract 4). The dates 1924-02-18, 1900-01-01 and 9999-12-31 will be **refused** by `OwnershipPeriod._validate_dates_representable` (ClickHouse `Date` holds 1970–2149); a blank end_date means open.
- **The 43 entities without a TB:** give them Entity rows, and take the perimeter from 12_EntityYearFlag.
- **FX intake:** map `AVERAGE`/`CLOSING` to `Average`/`Closing` (plus `Default`?) and give each a `valid_from`. 06 is USD per unit (from = local, to = USD). There is no governed rate path yet (konsol #103, undecided).
- **Hyperinflation** (A10): TRY/ARS/EGP should use closing-rate P&L; the model uses average for all.

### How to load it (proposed)

Chart → `silver_main_accounts`: TB Submission validates accounts against it
(`_chart_accounts`). Then Entities with `functional_currency` →
Consolidation Group tree → Ownership Periods → FX → fiscal calendar and periods
for 2010–2025 (raw FiscalCalendarYears had 1 row) → **Trial Balance
Submissions**: one per entity-year, CSV `main_account,debit,credit`, sign split,
**559** of them (the entity-years with a TB), scripted from **09**, and only once
they balance.

The TB-submission path does **not** go through the #155 GL-sign bug
(`silver_gl_entries` takes `tbs.net_amount`). Verify that on the silver TB
branch before relying on it.

**Then prove it against 07_PublishedAnchors.** Build a dbt test that group net
sales, operating income and NI per year match the 10-K within a tolerance. This
session's big lesson is that "unchanged from before" is not "correct"; the
anchors are the oracle this project never had.

## State at end of 12 Sep — merged, open, next

**User rules added today:** remove the demo; fill with Ecolab data when Grok's
corrected workbook is ready; until then keep fixing bugs and merging. **Test
live, but DON'T DEPLOY**: no `deploy.sh`, it wastes time. Hot-copy into the
containers and run scripts or dbt from a copy.

| PR | what | state |
|---|---|---|
| konsol **#123** / konsolidat **#151** | #110 entity registry | **merged** (`becb5a2` / `71d7dc7`) |
| konsol **#127** / konsolidat **#156** | demo fixtures removed / raw schema replaces demo-data.sql | **merged** (`d13f420` / `1c84114`) |
| konsol **#128** | #126: the build trigger queues a job after commit; TBS mapped; `_enqueue_build` after commit (likely #125's root cause) | **merged** `4796146`; #126 closed. Live A/B: a failed submit left `docstatus` 1 on main, 0 after |
| konsolidat **#157** | #155: every D365 voucher nets to zero at staging; demo BU hack removed; not_null on the key and amount | **merged** `dbfcacf`; #155 closed. A SYNTAX_ERROR that CI's `dbt parse` missed was caught live and fixed |

**Since #157:** local dbt builds show a third error (the voucher test
FAILs 927 on the unsigned demo raw data), and GL layers stay frozen on the demo
data until the wipe. `severity='error'` was kept on purpose; `warn` is one line
away if quiet builds are wanted meanwhile.

**The wipe** (still to confirm with the user):
- Bronze GL is incremental delete+insert, so after wiping raw run `dbt run --select bronze_general_journal_account_entries+ --full-refresh`, or the demo rows persist.
- `gold_consolidated_trial_balance` needs a full refresh too (konsolidat#154).

**Next bugs:**
- konsol **#130** (request_governed_rebuild commits in hooks: same class as #126)
- **#129** (the debounce absorbs requests during a Running build: affects TB changes)
- **#131** (Consolidation Adjustment approval never reaches staging)
- **#124** (13 in-transaction write-throughs)
- **#125** (Build Approval reaper; #128 probably removes the cause but not a stuck row)
- konsolidat **#154**, **#153**

**Open questions for the user:**
- Delete `scripts/generate_demo_data.py`?
- Move the repos off iCloud-synced `~/Documents`?
- The wipe scope.

**Lessons:**
- Live-verify every hook and model change: 814 static tests missed an enqueue TypeError, and `dbt parse` missed a SYNTAX_ERROR.
- A `docker cp`'d dir must be `chown`ed to frappe, or dbt exits 2 silently.
- Grep for `OK created`, not just PASS/FAIL.

## Found this session (all filed)

| issue | what |
|---|---|
| konsolidat **#155** | **P0.** Every GL credit is booked as a debit. `4caf9aa` (#118, 29 Jun) dropped `IsCredit` from `stg_d365_fo__gl_entries`; the raw data carries the sign **only** there (927/927 vouchers balance with the flag, 0/927 as signed). This is baseline error `assert_silver_gl_debit_credit_balance`, **not** a "demo-data tension". Budgets lose their sign in the generator itself. |
| konsol **#124** | 32 write-through hook syncs across 14 controllers run *before* commit, so a rollback leaves a ghost row in ClickHouse. Entity is fixed in #123. |
| konsol **#129** | The debounce counts a Running build as pending, so a TB change during a build can miss gold |
| konsol **#130** | `request_governed_rebuild` commits inside hooks (Allocation Run before_submit, schema publish) |
| konsol **#131** | Approving a Consolidation Adjustment (update-after-submit) never reaches staging or requests a rebuild |
| konsol **#126** | The build trigger commits inside document hooks. On submit, Frappe runs `on_update` before `on_submit`, so `docstatus=1` lands before the ClickHouse sync. Affects Ownership Period, IC Balance, Historical Equity Rate, Consolidation Adjustment and Allocation Run, and blocks mapping Trial Balance Submission. The fix is the job pattern #123 built for Entity. |
| konsol **#125** | A Build Approval stuck in `Approved` suppresses every auto-build of its scope forever. `BAPR-00001` (staging) has been stuck since 6 Sep. |
| konsolidat **#152** | deploy.sh OOM: six services build the same image in parallel, and ClickHouse is OOM-killed with them (`RestartCount=3`). |
| konsolidat **#153** | `build_consolidation_report.py` has been broken since F2 (reads retired columns). |
| konsolidat **#154** | `gold_consolidated_trial_balance` delete+insert never deletes a key that left the SELECT, so stale slices stay until `--full-refresh`. |

## The standing delivery loop (user rule, pre-authorized)

Build → open PR(s) → code review → fix all findings → squash-merge → verify
main CI → record in Engram. No per-PR permission needed. **Re-review the fix
commits too.** This session the second review found three bugs introduced by
fixes, the same shape as last month.

**Don't wave a failing baseline test through as "known".** Query what it
measures. `countIf(period_credit > 0) = 0` would have exposed #155 weeks ago.

## Where things are

| what | where |
|---|---|
| konsol (Frappe app), EDIT HERE | `~/Documents/frappe-bench/bench-15/apps/konsol`, on `feat/110-entity-registry` @ 464f71f, clean |
| konsolidat #110 work | **worktree** `~/Documents/grynn/konsolidat/wt-110` @ 6400e15. Remove it after merge (`git worktree remove`) |
| konsolidat deploy checkout | `~/Documents/grynn/konsolidat/repo` on `main` @ 3474a1e, **dirty with the #110 dbt files**. The container's `/home/frappe/dbt_project` is a **bind mount** of `repo/dbt_project`, so every `docker cp` there, and the `rm` of the retired guard, wrote into this checkout. Once #151 merges, `git -C repo checkout -- dbt_project && git -C repo clean -fd dbt_project && git -C repo pull`. Don't deploy from it before then |
| Deploy-owned checkout, NEVER EDIT | `repo/docker/frappe/konsol` |
| Engram | `.claude/memory/` in the bench checkout (gitignored) |
| Local stack | `http://localhost:8069`, site `konsolidat.local`, creds `repo/.credentials` |

**Deploy needs `COMPOSE_PARALLEL_LIMIT=1`** until #152 is fixed:

    cd ~/Documents/grynn/konsolidat/repo
    COMPOSE_PARALLEL_LIMIT=1 KONSOL_REPO=~/Documents/frappe-bench/bench-15/apps/konsol \
      KONSOL_BRANCH=main ./deploy.sh > log 2>&1; echo "exit $?"

Capture deploy's own exit code; a trailing `echo` reports 0 even on failure.

**Local stack right now:** deployed `main` ac0c24a plus #110 branch files
hot-copied (konsol entity.py, entity.json, clickhouse.py, hooks.py, tasks.py;
the dbt model, tests and ymls; the retired guard deleted in the container). Test
data AMIT / CG-AMG-AMIT / OP-AMG-AMIT-1970-01-01 / TBS-AMIT-2024-P12-012 is
still present. All of it goes with the wipe.

Local test loops: `.venv/bin/python scripts/run-host-tests.py` → **811/811
across 73 files**; `cd konsol-exec && node --test src/*.test.mjs
src/orchestrator/*.test.mjs` → 34/34.

Drive the live stack without a deploy by `docker cp` into
`konsolidat_backend` (dbt files there land in `repo/dbt_project`, which is bind-mounted) plus a plain script with
`frappe.init(site="konsolidat.local"); frappe.connect()`. After hot-copying
`hooks.py`, call `frappe.clear_cache()`, because hooks are cached in redis.
Verify a model *dropping* rows with `--full-refresh` (#154). A full `dbt build`
SKIPS the consolidation chain: run
`dbt run --select +<model>+ --exclude gold_spread_budget+` and then
`dbt test --select <names>`.

## Contracts a future session must not break

1. `dbt_project.yml` has two konsol-managed marker regions. konsol splices
   only inside them and refuses when they're absent.
2. `erp_sources` is a committed engineering decision. Never re-derive it from
   Connector state (#139).
3. One write-through mechanism: `resolve_sync_filters(doctype)` decides which
   rows sync; `reconcile_all` discovers `CH_TABLE`+`CH_FIELD_MAP`. Keep
   `_REFERENCE_TABLE_DDL` identical to konsolidat's `init-db.sql`
   (`epm_staging.entities` is now in both). New syncs go through
   `frappe.db.after_commit`, once per transaction (#124).
4. Ownership lives only in Ownership Period, keyed on
   `(consolidation_group, data_area_id)`. Roots take no period.
5. Consolidation is multi-level through `epm_staging.consolidation_ancestry`;
   `gold_entity_ownership` is the only ownership source.
6. `dbt_project/seeds/` does not exist.
7. `konsol/fixtures/` is force-reimported on every migrate, and holds
   **reference data only** since #127 (the demo is gone). A site's own data
   (ownership, budgets, groups, mappings) never goes there. Anything seeded
   during migrate must run BEFORE `_reconcile_clickhouse()`; nothing is seeded
   now. `test_fixtures_reference_only.py` enforces the directory's contents.
8. **(new)** An entity's currency comes from `silver_entity_currencies`
   (konsol's Entity first, the ERP second). `gold_consolidated_trial_balance`
   joins **resolved** currencies only. Never join `silver_legal_entities`
   directly again.

## Traps (full list in memory/semantic/konsol-gotchas.md)

- `["in", ["", None]]` never matches NULL; a blank Link is stored as NULL.
- Write an unset field as `DEFAULT`, not NULL or `''`.
- ClickHouse `Date` holds 1970-01-01…2149-06-06 and clamps silently.
- `join_use_nulls=0`: an unmatched LEFT JOIN fills with the DEFAULT, so
  `coalesce` and `is null` don't work across it. Use UNION + GROUP BY or `NOT IN`.
- An alias equal to a column read by a sibling aggregate raises
  **CYCLIC_ALIASES**. `anyIf` over no rows returns `''`.
- Frappe case-corrects Links (`usd` → `USD`); `rename_doc` never fires
  `on_update`; `frappe.get_all` ignores permissions.
- The Bash tool's shell is **zsh**: `$VAR args` doesn't word-split. The
  containers have no `ps`; use `/proc/*/cmdline`.
- RTK (global Bash hook) condenses output. Grep exact counts; use
  `rtk proxy <cmd>` if a number looks collapsed.
- **`~/Documents` is iCloud-synced.** iCloud leaves "name 2.ext" copies,
  and has brought back files `git clean` removed. A `* 2.sql` in dbt_project
  is a model with a space in its name (#139's failure). Before any
  deploy/dbt/merge, run `find <repo> -name "* 2.*" -not -path "*/.git/*"`, and
  delete only copies that `cmp` identical to the original.
- **Verify dbt changes from a copy outside the bind mount**
  (`docker cp` to `/home/frappe/dbt_project_<x>`, then `--project-dir`), not in
  `/home/frappe/dbt_project`. That directory IS the deploy checkout.
- Check the remote, not just the commit. konsolidat's worktree tracks only
  `main`, so verify pushes with `git ls-remote`.
