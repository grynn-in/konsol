# konsol / konsolidat — status and next steps

_Written 12 September 2026, refreshed that night and on 13 September. Everything below was verified against the running stack._

## Pick up here

**The user's instruction, verbatim:** *"First empty the site completely.
Re-set up with a correct set of data.
/Users/deepakpai/Downloads/Ecolab_Konsolidat_CoA_TB_2010_2025.xlsx"*

It came straight after we found that **every number in the current system is
wrong**. Every GL credit is booked as a debit (konsolidat **#155**, below). The
Contoso/Alpine demo data is being replaced, not repaired.

### Before wiping anything

1. **#110 is done** (konsol #123, konsolidat #151). Connector-less Ecolab
   entities get their currency from konsol's Entity, so their Trial Balance
   Submissions consolidate.
2. **Confirm the wipe's scope with the user, and back up first**
   (`docker compose --profile backup run --rm backup` in `repo/`). "Completely"
   means the MariaDB site *and* the ClickHouse volume. It can't be undone.
3. **The demo no longer comes back on migrate** (konsol #127, konsolidat
   #156): `konsol/fixtures/` holds reference data only, and `raw-schema.sql`
   replaced demo-data.sql. `scripts/generate_demo_data.py` still exists; ask
   whether to delete it.

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

## State on 13 Sep — merged, open, next

**User rule (13 Sep): no work on the D365 write-back itself** (`konsol/d365_writeback.py`): it will be dumped and redesigned. Budget Cycle may change, but its D365 push/withdraw calls stay as they are.

**User rules (12 Sep):** remove the demo; fill with Ecolab data when Grok's
corrected workbook is ready; until then keep fixing bugs and merging. **Test
live, but DON'T DEPLOY**: no `deploy.sh`, it wastes time. Hot-copy into the
containers and run scripts or dbt from a copy.

| PR | what | state |
|---|---|---|
| konsol **#123** / konsolidat **#151** | #110 entity registry | merged (`becb5a2` / `71d7dc7`) |
| konsol **#127** / konsolidat **#156** | demo fixtures removed / raw schema replaces demo-data.sql | merged (`d13f420` / `1c84114`) |
| konsol **#128** | #126: the build trigger queues a job after commit; TBS mapped | merged `4796146` |
| konsolidat **#157** | #155: every D365 voucher nets to zero at staging | merged `dbfcacf` |
| konsol **#133** + konsol-cli **#5** | #130: a governed build request commits with its caller. The 14 `cli_api` writes are POST-only (the CLI/MCP POST them). Both debounces take one global lock (the `tabDocType` 'Build Approval' row), then a locking read of `tabBuild Approval` (indexed on `build_scope`) | merged `b5e0786` / `01721c0` |
| konsol **#134** / konsolidat **#159** | #131: Consolidation Adjustment follows the conventions below. The workflow is installed by `konsol/workflows.py`; `approve_adjustment` / `reverse_adjustment` go through `apply_workflow`; amend works (`amended_from` added) | merged `530fe16` / `9911947` |
| konsol **#137** | #125: a Build Approval reaper (30 min; spares a job still in RQ; releases the governed Pipeline Run); `run_governed_build` builds only an Approved row; the build job is named and deduplicated | merged `951b0b8` |
| konsol **#139** | #129: a change made while a build runs gets one more build (`rebuild_requested` on the Running approval, re-read under lock at the finish; the reaper follows up a flagged dead build) | merged `859be1f` |
| konsol **#141** | #124: every write-through sync runs after the commit, once per transaction (`clickhouse.after_commit_once`); deletes from `after_delete`; nothing queued during install/import/migrate; a failed sync is logged, never raised | merged `8534c52` |
| konsolidat **#160** | #153: the consolidation report reads ownership from `gold_entity_ownership` | merged `d96c08a` |
| konsolidat **#162** | #154: the four consolidation models delete their scope (pre_hook + append; `gold_fully_consolidated_tb` is a table), so a key that left the SELECT leaves the table. Accepted: a failed run leaves its slice empty until the next run | merged `9e263a3` |
| konsol **#143** | #136: cancel only while the period is open (IC Balance, Allocation Run, Historical Equity Rate, Ownership Period; `period_status.assert_open_on` for dates); Budget Cycle locks in `before_submit` and pushes after the commit . Ranges are gated (an ownership period to its end date or the next period; a rate to the next rate); the Trial Balance Submission cancel is gated too | merged `ea3304b` |

**Conventions for submittable and workflow doctypes (decided 12 Sep):**
1. **Submit is the approval.** Review states are docstatus 0; Approve = submit; nothing changes after submit.
2. **Cancel only while the period is open** (`before_cancel` → `period_status.assert_open`). A cancelled document leaves the warehouse; after close, correct with a new document.
3. **Workflows are installed once** (create if missing, never overwrite), from `after_install` and `after_migrate` via `konsol/workflows.py`, because patches never run on a fresh install. Only doctypes in `INSTALLED` get theirs.
4. Never `self.save()` in `on_submit` / `on_cancel`; set fields in `before_submit` / `before_cancel`; sync after the commit; walk every transition live.

`test_workflow_convention.py` and `test_cancel_period_gate.py` enforce what they can; every submittable doctype now follows them (#143). Pre-existing, unrelated: `test_budget_cycle_reshape.py::test_dashboard_workflow_card_order` fails, in a file the host runner skips.

**Next bugs:**
- konsol **#135** (a budget-dimension publish commits mid-transaction: Custom Field → `updatedb` commits)
- konsol **#140** (a build that fails to start loses the changes absorbed while Approved; don't re-request blindly, it can loop)
- konsol **#142** (a fresh site's staging stays empty until the first `bench migrate`)
- konsolidat **#161** (the report's entity columns ignore the ownership window)

**Open questions for the user:**
- Delete `scripts/generate_demo_data.py`?
- Move the repos off iCloud-synced `~/Documents`?
- The wipe scope.

**Lessons:**
- Live-verify every hook and model change. 814 static tests missed an enqueue TypeError, and `dbt parse` missed a SYNTAX_ERROR.
- The host runner **silently skips** a test file whose imports fail, and any `from konsol… import` needs frappe. Load the module by path, or exec the controller against a stub frappe (`test_consolidation_adjustment_lifecycle.py`). Check that a new test file is counted.
- `validate_workflow` never checks a state's docstatus, and a direct `submit()` under a workflow isn't a transition. Guard both in the controller.
- A `docker cp`'d dir must be `chown`ed to frappe, or dbt exits 2 silently. Grep for `OK created`, not just PASS/FAIL.
- `frappe.db.after_commit` callbacks run after the last commit: one that raises breaks a committed request, and anything they write is never committed. Log with `log_error(..., defer_insert=True)`, and check the install/migrate flags when queuing, not when running.
- A contract test over controllers must merge inherited methods (Reporting Hierarchy inherits `on_update` and overrides the `_resync` it calls). Mutation-check a new test against the pre-fix code.
- dbt `delete+insert` never removes a key that left the SELECT. Replacing a slice means deleting the slice itself (#154).

## Found this session (all filed)

| issue | what |
|---|---|
| konsolidat **#155** | **P0.** Every GL credit is booked as a debit. `4caf9aa` (#118, 29 Jun) dropped `IsCredit` from `stg_d365_fo__gl_entries`; the raw data carries the sign **only** there (927/927 vouchers balance with the flag, 0/927 as signed). This is baseline error `assert_silver_gl_debit_credit_balance`, **not** a "demo-data tension". Budgets lose their sign in the generator itself. |
| konsol **#140** | A build that fails to start loses the changes it absorbed while Approved |
| konsol **#142** | A fresh site's warehouse staging stays empty until the first `bench migrate` |
| konsolidat **#161** | The consolidation report's entity columns include periods outside the ownership window |
| konsol **#135** | A budget-dimension publish commits mid-transaction: `apply_schema`'s Budget Line Custom Field sync commits through `frappe.db.updatedb` |
| konsol **#136** | Five more submittable doctypes break the conventions (sync in the transaction, `db_set` after submit, no period gate) |
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
| konsol (Frappe app), EDIT HERE | `~/Documents/frappe-bench/bench-15/apps/konsol` on `main`. Each PR gets a worktree, `~/Documents/frappe-bench/konsol-wt-<issue>`, removed after merge |
| konsol CLAUDE.md | **local only**: `konsol/.gitignore` ignores it on purpose (public repo). It holds the Frappe rules and the conventions above. Never force-add it |
| konsolidat deploy checkout | `~/Documents/grynn/konsolidat/repo` on `main` @ `9911947`, clean. The container's `/home/frappe/dbt_project` is a **bind mount** of `repo/dbt_project`: a `docker cp` there writes into this checkout |
| Deploy-owned checkout, NEVER EDIT | `repo/docker/frappe/konsol` |
| Engram | `.claude/memory/` in the bench checkout (gitignored) |
| Local stack | `http://localhost:8069`, site `konsolidat.local`, creds `repo/.credentials` |

**Deploy needs `COMPOSE_PARALLEL_LIMIT=1`** until #152 is fixed:

    cd ~/Documents/grynn/konsolidat/repo
    COMPOSE_PARALLEL_LIMIT=1 KONSOL_REPO=~/Documents/frappe-bench/bench-15/apps/konsol \
      KONSOL_BRANCH=main ./deploy.sh > log 2>&1; echo "exit $?"

Capture deploy's own exit code; a trailing `echo` reports 0 even on failure.

**Local stack right now:** konsol `main` (through #141) hot-copied into
backend and worker, plus #143's controllers (unmerged). The dbt project is
bind-mounted from the konsolidat checkout at `9e263a3`, so the next build uses
#162's models. The Consolidation Adjustment workflow is
installed on konsolidat.local. The AMIT and ZZ test data are gone. Nothing was
redeployed.

Local test loops: `.venv/bin/python scripts/run-host-tests.py` → **875/875 passed across 80 files** on main; `cd konsol-exec && node --test src/*.test.mjs
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
