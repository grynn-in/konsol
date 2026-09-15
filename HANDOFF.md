# konsol / konsolidat — status and next steps

_Written 12 September 2026, refreshed that night, on 13 September, again for the role home (F7), for group 2 (13 Sep evening), for the group chart (13 Sep night), and for the declared fiscal calendar (14 Sep), and for the deal layer and the one-customer-or-many audit (15 Sep). Everything below was verified against the running stack._

## Pick up here

**Update (15 Sep, night): Build Approval is approved through a Frappe Workflow (konsol#215).** Approve and Reject are buttons for EPM Admin; the role and self-approval are set in the Workflow record ("Build Approval Workflow"), not in code. A new request goes in as Draft and takes the workflow's Request transition (low risk to Approved, high risk to Pending Review); the build job's own moves (Start, Complete, Fail) are Administrator-only transitions it takes under `build_lock.build_writer()`. Deploy: migrate (after_migrate installs the workflow once); a site that edits the workflow keeps its edits.

**Update (15 Sep, late): the workbook ships TWO trial balances, and only the
statutory one was loaded.**

- **09_TB_STAT_Local** — entity x year x account, no dimension columns. This is
  what the 836 submissions came from.
- **09b_TB_MGMT_Local** — **55,321 rows, never loaded.** Its own header:
  *"Exploded from 09_TB_STAT_Local. Country opcos split P&L + working
  capital across the four reportable divisions using regional priors … Weights
  sum to 1 so MGMT ties to STAT by account."* Columns include `division, subdivision, region, country, platform,
  movement_type, accounting_currency, fx_method, fx_rate, scenario_id`.

**So the source DOES split within an entity** — deliberately, as a test fixture,
and it is **self-checking**, because the weights sum to 1. It is the test data
konsol#113 (TB_MGMT intake) needs, sitting ready. Do not repeat the claim that
this customer's data is dimensionless: the *statutory* TB is, the *management*
TB is not.

Measured on the stack: `gold_consolidated_trial_balance` holds 45,928 rows with
exactly **one** distinct value for each of `dim_business_unit`,
`dim_cost_center` and `dim_department` — and it is empty.
`epm_raw.trial_balance_submissions` has **no dimension columns at all**, in
either repo's DDL, so a dimension-carrying row has nowhere to land. Three
Dimensions are declared and Published; all three are unused. `Entity` has no
division / region / platform field, so the per-entity management attributes in
`02_LegalEntities` have no home either.

**konsol#220 — the division tree is temporal and konsol cannot store that.**
`03_DivisionHierarchy` carries `effective_from` / `effective_to` per node, and
its 34 rows contain four real changes: one division **renamed** on 2025-01-01;
one **elevated** to reportable on 2025-01-01; one that ran 2017-01-01 to
2024-12-31 and **ended**; and one **discontinued** 2013-04-01 to 2020-06-03 (the
same split-off the deal layer carries as a Business Disposal). A subdivision
**moves parent** in 2025 as well. `Reporting Hierarchy Member` has only `reporting_hierarchy,
parent_member, member_code, member_label, is_group`: one parent, one label, no
dates. Load the tree as it stands and 2010-2024 reports under the 2025
structure.

**Fix it before loading, not after.** There are currently **0 Reporting
Hierarchies and 0 members** on the stack, so this costs a doctype change today
and a restatement of every report later. The legal tree already solves the same
problem — `Ownership Period` is dated and consolidation resolves it per period
(`macros/ownership_resolution.sql` is the pattern to copy).

Workbook reference sheets are in the container at `/tmp/refdata/ref/` (21 CSVs).

**Update (15 Sep, night): deal inputs are declared, not inferred (konsol#206, #207, #203, #205, #204, #208).**
- A Business Combination with an empty Acquired Balance Sheet is refused. Until now it validated whenever the entity had an earlier trial balance, with net assets of 0 and goodwill equal to the whole consideration (#206).
- **Get Balances from Trial Balance** (Draft only) fills the Acquired Balance Sheet from the warehouse trial balance (`epm_gold.gold_trial_balance`, cumulative through the acquisition period). It folds the period's result into the chart's Retained Earnings Account and places the declared **Fair Value Adjustment Total** on the group's Fair Value Adjustment Account, or spreads it by a **Fair Value Allocation Profile** (#207, #208). The lines stay editable, and **Balance Sheet Source** records where they came from. On save, the lines' fair value adjustments must add up to the declared total.
- Acquisition costs can be capitalised only under a Local framework; IFRS and US GAAP expense them (#203).
- NCI measurement can be set per deal with **NCI Measurement for This Deal**; blank uses the group's value, and US GAAP allows only full (#205). Under full with less than 100% acquired, the deal must declare **NCI Fair Value**. konsol no longer grosses the consideration up (#204).
- Review hardening: the button runs only on a Draft (not Pending Approval), accepts POST only, and saves unsaved edits first. Net assets are translated from the entity's Functional Currency at the acquisition period's closing rate. The retained-earnings account is taken from the trial balance's own chart. Accounts the chart does not have are refused by name. A Fair Value Adjustment Total that disagrees with the lines warns on a Draft save and is refused at approval.
- Write-through: `epm_staging.business_combinations` gains `nci_measurement` and `nci_fair_value`, added on migrate. konsolidat's acquisition journal reads both (paired konsolidat PR).

On the site: the migrated Drafts need a Fair Value Adjustment Total (each description states the old figure) before Get Balances from Trial Balance. Five of the eight have no warehouse trial balance at or before their acquisition period, so those need hand-entered balances.


**What is left on the deal layer (verified live, 15 Sep night):**

- The **Consolidation Policy is set** on the group root and is correct: IFRS,
  partial NCI, Impairment only, Expense, 12 months, Recognise gain, and all
  nine accounts. Every field was blank beforehand, so nothing was overwritten.
  **One correction outstanding:** `fair_value_adjustment_account` was set to
  *Other noncurrent assets* before the chart's purpose-built *Fair value
  adjustment on acquisition* account was noticed. One field, reversible,
  presentation only.
- **All 9 drafts still need their Acquired Balance Sheet.** After the fix all
  8 Business Combinations refuse — confirmed by running validate against each.
  Before it, three validated silently with goodwill equal to the whole
  consideration: **13.6bn across them**. The disposal validates today and is
  the first real test of whether the deal layer moves the year whose
  published-figure gap was never explained.
- **Five** of the eight need more than the button: they have no trial balance
  at or before their acquisition date. Count them from the validation
  messages, not from the migration note, which said two.
- **Historical Equity Rates: zero records exist.** 21 chart accounts declare
  `fx_method = historical`; 5,481 rows in the consolidated trial balance sit on
  those accounts translated at a rate other than 1, across **194 distinct
  rates** — i.e. every one is taking its period's closing rate. The fallback is
  deliberate and documented in `gold_consolidated_trial_balance`, but with
  equity at closing the CTA never arises (`3300` holds 21 rows). **Two
  decisions before anyone enters rates:** 14 assets and 1 liability are
  declared `historical`, which is the temporal/remeasurement treatment rather
  than IAS 21.39 current-rate — deliberate or a workbook artefact? And NCI is
  the only equity leaf *not* declared historical. The answers change the job
  from ~6 rates per entity to ~21.

**Two traps that cost time reading trial balances back out:**
- **Latest, not earliest.** `derive_acquired_balances` gets this right; a
  hand-written query may not. An entity with monthly files has several
  submissions, and taking the first silently uses a mid-year balance sheet.
  Entities with one annual P12 file hide the bug, because earliest and latest
  are the same file. This produced a wrong goodwill figure that a peer caught.
- **`epm_raw.trial_balance_submission_control` is a ReplacingMergeTree.**
  Joining it to `trial_balance_submissions` without `FINAL` returns duplicated
  claim rows and doubles every sum. Filtering on `batch_id` alone is safe.

**Update (15 Sep, later): deals are documents (konsol PR #202, konsolidat#198).**
An acquisition is a **Business Combination** and a sale a **Business
Disposal** — submittable doctypes with child tables (consideration
components, acquired balances with fair-value adjustments, acquisition
costs / proceeds components), a Draft → Pending Approval → Approved →
Cancelled workflow, and IFRS 3 arithmetic computed on validate from the
group's **Consolidation Policy**. The policy lives on the group root's
Consolidation Group (new tab): framework, NCI measurement (partial/full),
goodwill treatment (Impairment-only/Amortise + years), acquisition-cost
treatment, measurement period, bargain-purchase handling, and nine accounts
by role (goodwill, fair-value adjustment, investment, NCI, bargain-purchase
gain, disposal gain/loss, deal settlement, amortisation expense, acquisition
costs). Nothing defaults: a deal cannot be submitted until every policy
field and account its journal needs is declared, and the refusal names the
field. Approval creates or closes the Ownership Period (its seven deal
fields are now read-only, set only by the deal); cancel undoes exactly that.
Write-through: `epm_staging.business_combinations` (+3 child tables),
`business_disposals` (+1), policy columns on `epm_gold.consolidation_groups`.
Deploy order: migrate (the patch `migrate_deals_to_business_combinations`
turns each Ownership Period that carries deal figures into a **Draft**
Business Combination / Disposal — nothing is submitted for the user), then
on the site fill the group root's Consolidation Policy and accounts, review
each Draft (its consideration is the old acquisition price as one component;
acquired balances are empty and must be entered from the acquisition-date
balance sheet), and submit. konsolidat reads only submitted deals; until a
deal is submitted the acquisition layer posts nothing for it.

**Update (15 Sep): every trial balance declares its Amount Basis
(konsolidat#199).** The warehouse read every row as a period movement; the
site's uploads were period-end balances, so the balance sheet double-counted
every prior year-end. Now a Trial Balance Submission (and a Trial Balance
Upload, and the konsol-exec upload page) declares `Period movement`,
`Year-to-date movement` or `Period-end balance`; the claim row carries it;
konsolidat (PR #200, merged) normalises to movements and, for balance files,
posts each year's close into the chart's **Retained Earnings Account** in the
calendar's Closing period. Deploy order after this PR merges: migrate (adds
`amount_basis` to the control table and `is_retained_earnings` to the chart);
on the site, Trial Balance Submission list → **Set Amount Basis…** for the
existing batches (this site: Period-end balance), and tick **Retained
Earnings Account** on the retained-earnings account (3100 here); then
fast-forward konsolidat and approve a **full** build. Until the batches are
declared the preflight refuses the build by name — by design. Follow-ups:
konsol#200 (retire the Airbyte/connector gating from the preflight),
konsolidat#198 (acquisition/disposal journals; design in the bench's
`.claude/memory/active/design-konsolidat-198.md`, Business Combination
doctype decided).

**Update (14 Sep evening): the full governed build is one deploy away from
green.** After the data load, a full build failed on three things; all are
fixed on main now.

- Data (done on the site): the top entity of the group had no Ownership
  Period (added, 100% full from 1990-01-01); 43 balance-sheet accounts had no
  Cash Flow Category (created from the chart's own `cf_*` fields, cash account
  flagged); one 2012 CAD→USD average rate was 1.0 (cancelled and amended to
  1.0005, the Federal Reserve G.5A figure, reason recorded).
- konsol #195 → PR #198: a scoped build ran tests of models it did not build.
  `dbt_build_command` adds `--indirect-selection cautious` to every scoped
  build (and the orchestrator's `build_dbt_command` does the same).
- konsol #196 → PR #199: the group chart is the source of the cash-flow
  mapping. Saving a Published balance-sheet account keeps its Cash Flow
  Category row in step; the patch `fill_cash_flow_categories_from_chart`
  backfills existing sites on migrate. #197 tracks dropping the unread `sign`.
- konsolidat #195 → PR #196 and #194 → PR #197: two dbt singular tests fixed
  (`assert_cta_not_zero_when_rates_differ` keyed on rows that used a
  non-closing rate; `assert_translation_rate_resolved` no longer rejects a
  cross-currency rate of exactly 1.0). Fixtures for singular tests live in
  `dbt_project/test_fixtures/` (see its README).

To see it on the stack: fast-forward the deploy checkout to konsolidat main
(≥ e625a95), deploy konsol main (≥ #199) and migrate, then approve a **full**
Build Approval. Still open for the business: Historical Equity Rates (0 rows,
329-entity warning), account 2300's `historical` FX method, account 1000's
`is_cash` in the chart, account 3500 under the equity heading, FY2026 not
declared, and whether one financing entity's trial balance (debt and retained
earnings only) is complete.

**Update (14 Sep): fiscal periods are declared, not implied** (konsol#189 PR1,
konsol **#191**; see "Fiscal Year with declared periods" below). A period
exists only as a row of an EPM Fiscal Year and is open only when the row and
its year are both Open. After deploying #191 and migrating, run
`fiscal_calendar.declare_years_in_use(include_warehouse=True, dry_run=True)`
and review its list before applying it.

**Update (13 Sep night): the group chart of accounts is live in konsol** (see
"Group chart of accounts" below). The site no longer depends on an ERP chart:
upload the chart, publish it, approve the build, then load trial balances. The
remaining blocker for the load is the unbalanced trial balances (below), which
is the user's decision.

**The user's instruction, verbatim:** *"First empty the site completely.
Re-set up with a correct set of data.
<the customer workbook, kept outside the repo>"*

It came straight after we found that **every number in the current system is
wrong**. Every GL credit is booked as a debit (konsolidat **#155**, below). The
Contoso/Alpine demo data is being replaced, not repaired.

### Before wiping anything

1. **#110 is done** (konsol #123, konsolidat #151). Connector-less customer
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
| 00_Cover | design notes. Entity roster from the customer's published annual report; amounts are **synthetic** allocations of the published consolidated figures | — |
| 01_CoA | group chart `ECL_GROUP_GAAP`: posting accounts 1000–9000 plus parents; account_type, normal_balance D/C, time_balance, **fx_method closing/average/historical**, cf_category, allow_ic | 87 |
| 02_LegalEntities | data_area_id (`US_ECL`, `US_USA`, …), accounting_currency, parent_data_area, ownership_pct, is_tb_entity, region/division/platform | 83 |
| 03_DivisionHierarchy | management dimension (GW, GIS, Pest, LS …); keep it **off** the legal tree | 27 |
| 04_AcquisitionEvents | acquisitions and disposals with price and goodwill | 12 |
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
  - Group balance-sheet surplus: +3.6bn in 2010, +9.9bn in 2011 (a large acquisition's assets arrive with no funding).
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
- **FX intake:** map `AVERAGE`/`CLOSING` to `Average`/`Closing` (plus `Default`?) and give each a `valid_from`. 06 is USD per unit (from = local, to = USD). Load them as Group Exchange Rates (konsol #174): quote plus "quoted per", approved, published as true rates.
- **Hyperinflation** (A10): TRY/ARS/EGP should use closing-rate P&L. Since konsol#182 an account declares its `fx_method`, so a P&L account can be declared at `closing`.
- **The chart (01_CoA)** loads through konsol's chart upload (Main Account list → Upload chart). Its columns already match the upload. It passes today's rules, including `allow_ic=1` on every postable account and accounts whose type differs from their heading's (both pinned by tests).

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

## The role home (F7)

The user approved the design (artifact "Konsol Role Home", version 2) and every
suggestion in it:

- konsol-exec is the home: a dark title bar with the path (no period dropdown,
  the user's explicit wish), a left navigator of fiscal years holding OPN,
  P01–P12 and CLS with their close state, a month view, and a status bar.
- A month is **eight ordered stages**: source data, trial balances, ownership
  & rates, intercompany, adjustments, consolidate, assertions, sign off.
  Budget is an annual cycle and leaves the monthly list.
- Job titles on screen (Close Lead, Group Accountant, Entity Accountant,
  Budget Reviewer, Viewer, System); role names unchanged (EPM Admin, EPM
  Analyst, …). New role **Entity Accountant**; Budget Submitter stays as an
  alias for the base layer.
- Consolidation Adjustment: **EPM Analyst drafts** (and amends a reversed
  one); **EPM Admin approves, rejects and reverses**.

| PR | what | state |
|---|---|---|
| konsol **#146** | Roles and access: every role in `install.ROLES`, created on install and migrate; DocPerms opening consolidation doctypes to business roles; the adjustment workflow split; an upgrade that moves an untouched System-Manager-only workflow to the new roles and grants EPM Admin + EPM Analyst to the site's existing System Managers (Role Profile users are reported, not granted); an unassigned Entity Accountant sees no entity | merged `29a1950` |
| konsol **#147** | `konsol/home_api.py`: GET `whoami`, `period_tree`, `month` (lane, "mine" and "waiting" queues, health); rules in `home_model.py`. Konsol roles only; entity codes and connector detail only for group roles | merged `dc4b094` |
| konsol **#148** | konsol-exec shell: title bar, navigator, MonthView, StatusBar, `homeMachine`; the checklist no longer picks the period | merged `831719b` |

Every PR was reviewed, fixed and re-reviewed, and live-verified on
konsolidat.local with rolled-back test users. **The shell has not been seen in
a browser**: the local stack asks for a login, and credentials are never
typed. Log in and open `/konsol-exec/2026/9`.

**Deferred** (not started): intercompany differences (IC Balance entities are
free text, need Links + a warehouse query + a tolerance), a status on Budget
Sheet and the budget year folder, a close calendar (working-day deadlines),
trial balance upload inside the detail panel, nudging a person instead of a
role, per-role desk workspaces, and konsol #149 (below).

**Rules the home relies on:** an entity is in a month's close when a
submitted ownership period covers the month's first day; it owes a manual
trial balance unless an enabled connector's legal entities include it. Builds
carry no period, so every open month shows the latest build, labelled as the
latest. Actions are offered disabled only where the server refuses them.

**Lesson:** a machine that throws on import passes every test that doesn't
import it. `homeMachine.test.mjs` runs the machine with stub actors; do the
same for any new machine.

## Bulk trial balance upload (konsol #151)

Asked for by the user: the customer's trial balance covers hundreds of entities,
and one file per entity is not workable. **This is the path for loading
Grok's corrected workbook** (sheet 09, local currency) once it balances.

- One CSV or Excel file (first sheet): `data_area_id, fiscal_year,
  fiscal_period, main_account, debit, credit[, description]`; `entity`,
  `year`, `period`, `account` are accepted too. Amounts in each entity's own
  currency, both columns positive, periods 1–12.
- konsol-exec **/konsol-exec/uploads** (navigator "Upload trial balances", and
  a button on the month view). Check first: every entity-period is run
  through the single-upload rules (the same validator), plus entity access,
  group nodes, closed periods and already-submitted entity-periods. Nothing
  loads until "Load N" (or "Load N, skip M").
- The load is a background job, run as the user: one ordinary Trial Balance
  Submission per entity-period, each committed on its own, progress saved
  after every row, stopping itself before the RQ time limit. A stopped or
  partly loaded upload resumes without loading anything twice; claims left
  by a submission that never committed are released first (under the
  entity lock).
- Doctype **Trial Balance Upload** is the audit trail. Close Lead (EPM
  Admin) and System Manager only; a user limited to some entities sees only
  uploads they own or whose every entity they may see.
- Also changed on the single path: Excel "CSV UTF-8" files (byte-order
  mark) are read; Trial Balance Submission locks its entity and does a
  locking duplicate read, indexed on (data_area_id, fiscal_year,
  fiscal_period), so two submissions can never both claim one entity-period.

- Each generated per-entity file is named `<upload>-<entity>-<year>-P<period>.csv`
  and carries a `source_upload` column (ignored by the parser). That keeps
  every upload's files unique (Frappe reuses a File with the same content)
  and lets a resume recognise its own rows.

State: **merged `c2f2a16`**, reviewed four times, every finding fixed and live-verified
(`live_tbu*.py` in the session scratchpad; FY2099 test data, cleaned up).

**Trap:** twice a whole-file rewrite put a raw, invisible U+FEFF into a
Python string literal where `"\ufeff"` was meant. It still runs, but check
after writing code that mentions the byte-order mark. On macOS, `grep -P`
silently finds nothing; this form works (tested):

    LC_ALL=C grep -rl $'\xef\xbb\xbf' konsol --include='*.py'

## Hierarchy node resolution is strict (konsol #155, konsolidat #164)

K.EPM's `node` with `hierarchy` left blank now resolves only when exactly one
Published Reporting Hierarchy holds that code; several is `#VALUE!` naming
them, none says so. It used to take the tree edited most recently, so a value
could flip when someone edited another tree. `is_default` plays no part in
formula resolution (it drives the unassigned-members check only). Group member
codes are unique within a tree like leaf codes (the closure joins on code),
and a named hierarchy is passed on in its stored spelling (ClickHouse is
case-sensitive). Live site at merge: one tree (MGMT_DEMO), no shared codes.

Host test runner (same PR): `scripts/run-host-tests.py` now imports konsol,
skips a file only when it needs frappe, pytest or a third-party module (each
skipped file is listed with its reason), fails the run on any other load
error such as a broken konsol import, and resets frappe/konsol modules after
each file so one file's stub frappe cannot leak into the next. Before, three
files that import konsol never ran and the run still reported green.

## VBA Excel client retired (konsol #153, konsolidat #163)

The user retired VBA: konsol's Office add-in (`konsol/public/excel-addin`,
functions `K.EPM`, `K.EPM_BUDGET`, `K.EPM_VARIANCE`, `K.EPM_DEBIT`,
`K.EPM_CREDIT`, `K.CF`, `K.EPMSAVE`) is the only Excel client.

- konsolidat: `excel/OpenEPM.bas`, its parity test, the VBA-only
  `Budget_Forecast_2024.xlsx` and its generator are gone. The VBA guide is now
  `docs/user-guide/excel-formulas-guide.md`; every other page describes the
  add-in. `docs/prd/` is left as history.
- **Still to do in Excel (by a person):** `excel/Open_EPM_PnL.xlsx` calls the
  old `=EPM()` (shows `#NAME?`; replace with `=K.EPM(`, same arguments), and
  `excel/Open_EPM_Template.xlsx` still has VBA instruction text.
- **The shipped add-in manifest points at `https://demo.konsolidat.com`.** The
  functions call the server the add-in was loaded from, so a local or
  production install must replace every demo URL in `manifest.xml` first.
- The K.EPM redesign (named parameters via HSTACK, any dimension, basis, OPN
  and CLS, named errors instead of silent zeros) is in the reporting-bases
  proposal artifact, not built yet.

## Group 1: controls and integrity (13 Sep)

The user ranked the open issues and asked for group 1 one PR per issue, merged
as each clears review ("keep going, merge them as they clear"). Every PR went
through several review → fix → re-review rounds and a live A/B on
konsolidat.local.

| PR | issue | what | state |
|---|---|---|---|
| konsol **#160** | #149 | Consolidation Adjustment, IC Balance and Allocation Run refuse a submit into a closed period (`assert_open` first in `before_submit`). The home disables only what the server refuses and annotates the rest ("Can't approve: Dec 2099 is closed."); a TB draft's note depends on whether the viewer may delete it | merged `41747cc` |
| konsol **#161** | #142 | A fresh site fills its warehouse staging: `after_sync` queues `install.reconcile_warehouse` (long queue, 1500 s) after the install commits; `setup_epm_settings` queues it again when the ClickHouse target changes. Runs are serialised by a per-database Redis lock (wait 600 s); one `SELECT 1` probe first; the install-time run on the untouched default target only warns | merged `b84634e` |
| konsol **#162** | #135 | A budget-dimension publish no longer commits mid-transaction: the Budget Line Custom Field sync runs as a job after the commit, as Administrator, under a per-database MariaDB named lock (commit after `GET_LOCK`, rollback before `RELEASE_LOCK`, lock retried inside the job). Standalone `apply_schema` is POST-only and syncs as Administrator; `run_dbt` goes through `cint` (`"0"` no longer triggers dbt) | merged `55fd23b` |
| konsol **#163** | #158 | One entity-access rule for every ClickHouse read (`entity_permissions.entity_read_scope`). **Deliberate tightening:** a restricted user reading a blank entity on flat `epm_value` is refused (it used to read blank-entity variance rows). Source-only security checks moved to `test_security_source.py` so CI runs them | merged `abc1e30` |
| konsol **#164** | #140 | A build that fails to start keeps the changes it absorbed: every pending state is flagged, a start failure (or a lost job) gets a flagged follow-up, capped at 3 in a row, then an Error Log; a failed start marks its Pipeline Run Failed; a reset of a started row clears its timing (and the flag only if it came from Completed/Failed). **A Running build can no longer be moved by hand**: only the build job (`build_lock.build_writer()`) and the reaper may; `Pipeline Run.build_approval` is indexed | merged `da2f40f` |
| konsolidat **#165** | #152 | `deploy.sh` builds the Frappe image once (`<project>-frappe:latest`, only `frappe_backend` has `build:`, `pull_policy: never`) instead of six parallel builds that OOM-killed ClickHouse. `./deploy.sh backup` never builds: it fails loudly if the image is missing | merged `e2e996e` |
| konsolidat **#166** | (part of #158) | `scripts/generate_demo_data.py` deleted; docs say there is no demo data (single TB: Trial Balance Submission in Desk; bulk: `/konsol-exec/uploads`, Close Lead or System Manager) | merged `b1f1414` |

**⚠ Before any `./deploy.sh backup`, run `./deploy.sh` once.** Since #165 the
backup needs `repo-frappe:latest`, which only the next full deploy builds; until
then a backup exits 1 with "Frappe image repo-frappe:latest not found". The old
per-service `repo-*` images can be removed after that deploy.

**Filed from group 1 reviews:** konsol **#170** (dbt can still run twice: cancel during dbt, the legacy schema-apply build and close assertions skip the single-flight gate), **#165** (Assertion Run failure samples
expose entity rows to every role), **#166** (`trigger_close_run` and
`launch_options` have no role check), **#167** (a publish or `apply_schema`
rewrites tracked dbt files in the deploy checkout; `_staging__sources.yml` loses
its comments), **#168** (Build Approval state is freely editable in Desk; no
Workflow), **#169** (a budget field whose column ALTER failed is never repaired).

**Host test runner (`scripts/run-host-tests.py`), since #155/#163:** it imports
konsol; skips a file only when it needs frappe, pytest or a third-party module
(each listed with its reason); fails on any other load error; resets konsol and
stub-frappe modules per file (never real frappe: re-importing it overflows
frappe's gc threshold); and has a **must-run set**
(`test_entity_access_host.py`, `test_security_source.py`) that fails the run if
either is skipped. CI runs it on every PR.

## Group 2: customer-load critical path (13 Sep)

Same loop as group 1: one PR per issue, reviewed and re-reviewed until clean,
live A/B on konsolidat.local, merged as each cleared. The user approved
decisions 1–14 (the decision tables in the session; recorded in
`.claude/memory/active/current-tasks.md`).

| PR | issue | what | state |
|---|---|---|---|
| konsolidat **#167** | #161 | The consolidation report's entity columns read only the ownership window | merged `50f9319` |
| konsolidat **#168** | #158 | Sign-convention follow-ups: adapter contract, a balance test for every ERP, stale #64 comment | merged `1eac5f2` |
| konsol **#172** | konsolidat #93 | The presentation currency lives on the Consolidation Group node, a validated Link to ISO Currency; EPM Settings' `consolidation_currency` is gone; a tree view for Consolidation Group | merged `11a846b` |
| konsol **#174** | #103, #175 | **Group Exchange Rate**: the group's governed translation rates. See "FX rates" below | merged `c80b1e9` |
| konsolidat **#176** | #93, konsol #103 | The warehouse translates only with the governed rates | merged `cb180f2` |
| konsol **#173** | #159 | Intercompany counterparty: Intercompany Account pairs, partner on trial balances, difference settings on the group node | merged `cb58a98` |
| konsolidat **#175** | #148 | Intercompany reconciliation and eliminations by partner. See "Intercompany" below | merged `8b2f8fe` |

### FX rates: one source of truth, via konsol (user decision, 13 Sep)

- A rate is entered as a quote per 1/10/100/1,000/10,000 units ("quoted per",
  like D365's ConversionFactor). konsol publishes only approved rows, as the
  TRUE rate (units of the group currency per 1 unit of the entity currency,
  decimal division), to `epm_staging.group_exchange_rates`. The warehouse
  never scales or inverts. ERP quotes are only a pre-fill input.
- `api.fx_rates` and `orchestrator.fx.get_fx_rates` return the governed rates.
- Magnitude rule: `ISO Currency.usd_log10`; refused when
  `abs(log10(rate) - (usd_log10(to) - usd_log10(from))) > 1`. No reference =
  NaN, or 0 for a code other than USD. Pegged PAB/BSD/BMD ship at 0.001. One
  definition per repo (`konsol/fx_reference.py`, dbt `fx_is_implausible` /
  `fx_has_reference`) and one 13-case table in each repo, kept identical by
  hand (konsol #177 asks for a check).
- Reference values live in `konsol/reference_data/iso_currencies.json`, not
  fixtures (fixtures are force-reimported every migrate). A seed fills only
  unset values, so a site's edit survives.
- A publish takes a per-site lock, reads the approved rows on a separate
  connection, builds a shadow table and swaps it in with `EXCHANGE TABLES`.
- The adoption patch turns the rates the warehouse already translated with
  into approved Group Exchange Rates on upgrade. It plans first, checks
  references only for currencies it will enter, and refuses (migrate stops,
  reruns next time) naming each currency that needs a USD Reference.
- konsolidat: the guard pre-hook refuses a missing, duplicate, invalid or
  more-than-10x-off rate BEFORE the model's DELETE, listing up to 50 keys.
  A newly submitted foreign-currency TB blocks full builds until its Closing
  and Average rates are approved.

**Deploy consequence (said once):** the next `./deploy.sh` step 3 migrate runs
the adoption patch. Step 5's pre-check stops the deploy only when
`epm_staging.group_exchange_rates` does not exist; missing keys are listed as a
warning, and step 5 then fails with the consolidated TB and what reads it
keeping their last figures.

**Limit:** one reference per currency for all periods; a currency moving more
than 100x over its history (ARS, LBP) can't pass every period (konsol #176).

### Intercompany

- konsol: an **Intercompany Account** pairs a receivable/revenue account with
  its counterpart; publish checks both sit in the group chart and never on the
  group's IC difference account (a serialising lock, then indexed locking
  reads). Trial balance rows carry `partner_data_area_id`. The group node
  holds the tolerance (booking differences only) and the difference account;
  a node that carries an entity refuses them.
- konsolidat: `gold_trial_balance_by_partner`, `gold_ic_reconciliation`,
  `gold_ic_eliminations`, `gold_ic_unmatched`, and the eliminations in
  `gold_fully_consolidated_tb`.
- Decision 12: match on the full translated amount. The group view (ownership
  weighted) posts a `matched` entry at the share both sides hold and an `nci`
  entry moving the rest to the NCI line (pseudo-account `NCI`, a dbt var maps
  it); `elimination_view = 'nci'` entries let group + `nci_amount` read as a
  full 100% consolidation. The NCI line nets to zero per group and period.
- Decision 13: different functional currencies = `fx`; same currency =
  `booking` when local amounts don't net, else `fx`. Only `booking` counts
  against the tolerance. A cross-currency booking error is labelled `fx`
  (a trial balance carries no transaction currency).
- Decision 14: balance-sheet pairs compare the balance to date and post each
  period's change; P&L pairs match on the period movement.
- Membership comes from ownership windows per (group, period), not from who
  submitted data. A quiet member counts as 0 (a visible booking difference);
  any exit (disposal, sub-group sale, move to equity) gets a `left` row that
  reverses everything. The cash flow drops balance-sheet pairs' NCI entries
  only.
- Proven: live A/B on ZZ data (80%, 70/80, EUR, quiet period, acquisition,
  disposal, sub-group sale, move to equity) and the integration fixture with
  pinned expectations (0 mismatches). That fixture is the only check of
  membership-by-window, and the suite doesn't run it while `dbt build` stops
  on the demo D365 test (konsolidat #182).
- Not handled: pre-acquisition balances and disposal derecognition
  (konsolidat #179, #180); the NCI pseudo-account's real chart account is a
  customer choice.

### Filed from group 2 reviews

konsol **#176** (effective-dated currency references), **#177** (per-process
sync health, publish connection settings, cross-repo case parity); konsolidat
**#178** (five singular tests read stale models in a domain build), **#179**
(an acquired entity's pre-acquisition balances never reach the consolidated
TB), **#180** (a disposed entity's balances are not derecognised), **#181**
(CI should run `dbt build` and the integration tests).

**Lessons:**
- Two PRs that meet at a table need one joint review: #174's `inverse_quote`
  was never read by #176, so JPY would have been ~22,900x too large.
- A clean git merge can leave a module-level name assigned twice
  (`_ADDED_COLUMNS` after the IC rebase). Grep for it after any rebase that
  touches registry dicts.
- Shared containers: restoring them to main and migrating deleted another
  branch's DocType as an orphan. Snapshot first and check whose files are in.

## Group chart of accounts (konsol#182, 13 Sep)

The trial balance load stopped because a wiped site refused every trial
balance: the only chart came from the ERP feed, and there was none. The fix is
a group chart governed in konsol. The plan, the direction change and the
corrections are all on konsol#182.

**User decision (13 Sep), verbatim:** *"i dont want anything to do with
erpnext or airbyte or d365. I want to freshly look at it. For now the canonical
path is upload of the trial balance via CSV. Any source from now on has to
follow the shape defined by konsol."* So: no ERP fallback and no ERP adoption
in new work. The existing ERP, Airbyte and D365 code stays in the repos, unused
by this path, until someone decides to remove it.

| PR | what | state |
|---|---|---|
| konsol **#183** | **Main Account** (a tree; Draft/Published/Inactive; the Close Lead publishes). Written through to `epm_staging.main_accounts`. One chart reader, `group_chart.chart_accounts()` (MariaDB, Published rows only), used by single and bulk TB validation, Consolidation Group and Intercompany Account. The chart upload (`chart_upload.py`: check, load, publish; all or nothing; parents first; never deletes by omission). The `chart` build scope. Preflight changes. In-use refusals | merged `e50ed7d` |
| konsolidat **#186** | `silver_main_accounts` reads only the published konsol chart (same 11 contract columns, new ones at the end). Translation follows each account's declared `fx_method` (historical / average / closing). A guard refuses unusable or disagreeing chart rows before anything is replaced. Warn test names TB accounts missing from the chart | merged `c51420e` |
| konsolidat **#188** | The first build on a fresh trial-balance-only site: `alloc_results` builds with no allocation rules; the two ERP-quote tests pass when that table is absent. A CI job builds an empty site on every PR | merged `3332f57` |
| konsolidat **#184** | ERPNext `Income` accounts count as P&L and read as `Revenue` (found while analysing the chart) | merged `a35cccf` |
| konsol **#184**, **#185** | Removed the customer's name and identifying details from this file and a code comment (public repo; older history still has them) | merged |

**Loading a chart (the canonical path).** Main Account list → **Upload chart**
→ **Check** (every problem at once, nothing written) → **Load** → **Publish
chart** → approve the `chart` Build Approval. Columns: `main_account,
account_name, chart_of_accounts` (required), then `parent_account,
account_type, statement_section, sub_section, normal_balance, time_balance,
fx_method, cf_category, cf_line_item, is_posting, allow_ic`. Rules: balance
sheet → `closing` or `historical` and `balance`; P&L → `average` or `closing`
and `flow`; a code named as a parent is a heading (not postable, no IC); a
child matches its heading on `statement_section` only; unknown columns are
refused.

**Rules to keep:**
- **Build scope `chart` = `@silver_main_accounts`.** That's the chart, everything it classifies, and everything those read, so it works on a site that has never built. It has its own connector gate: an enabled connector that has never synced, is Failed or is Running blocks it. With no enabled connector it passes, even before any TB exists.
- **Raw-dependent scopes:** submitted trial-balance rows count as raw data. The check runs after the connector gate and counts rows in ClickHouse joined to the control table. A global Airbyte status of Failed or Running still refuses. The refusal names **Skip Airbyte Sync** (EPM Settings) as the way out for a site that no longer uses Airbyte.
- **In-use refusals.** A Published account can't be unpublished, set Inactive, deleted, or turned into a heading while any of these use it:
  - submitted trial balances (the refusal names the entities and periods);
  - a Published Intercompany Account;
  - a Consolidation Group's IC difference account.
- **The DDL of `epm_staging.main_accounts`** is identical in konsol `clickhouse.py` and konsolidat `init-db.sql`. Tests on both sides pin it.
- **Deploy order:** konsol #183 with or before konsolidat #186 (which alone empties the chart), then #188 before the first chart build on a fresh site.

**Verified live (konsolidat.local):**
- On main, a trial balance was refused as "ClickHouse unreachable" and the preflight as "Airbyte never synced".
- After #183, uploading and publishing a test chart made the trial balance accepted; a heading was refused.
- `dbt build --select @silver_main_accounts` after the merges: PASS=268, ERROR=0. The chart table is empty until the user publishes.

**Open (not started):**
- The plan's PR4 (TB warnings: suspended account, against the normal balance, partner rows on a non-IC account).
- PR5 (fold Cash Flow Category into the chart; Main Account Category, the budget permission key, reads the chart; `allow_ic` tied to Intercompany Account).
- konsolidat **#187** (variance full-outer-join loses keys).
- konsolidat **#172** (spread tests; they pass on today's data).
- konsolidat **#185** (ERPNext expense sections in the report).
- `map_account_type` has no caller left.

**Lessons:**
- **ClickHouse 24.8 "identical" checks can lie.** `sum(cityHash64(*))` skips rows with a NULL, and `count()` over `EXCEPT` returns 0 under the new analyzer. Compare `toString(tuple(*))` as a multiset, and prove the check fails on a deliberate change first.
- **A clean git merge can leave a module-level dict assigned twice.** This happened with `_ADDED_COLUMNS` after a rebase; grep for it.
- **Resuming a long-lived agent re-reads its whole transcript,** which cost 500–750K tokens per round. Fresh agents with tiny briefs did comparable fixes in 50–170K tokens (see the loop below).
- **Squash-merging a base PR makes the stacked PR conflict.** Rebase only the stacked PR's own commits onto main (`git rebase --onto origin/main <old base head>`).

## Fiscal Year with declared periods (konsol#189, 14 Sep)

The question that started it: if P14 is opened after 25 months, does it show as
open in every earlier year? It did. Status lived in standalone Period Status
records keyed on (year, period), and a missing record meant Open, so any
period number was "open" everywhere. Now a period exists only if it is
declared, and nothing defaults to Open. The plan and the design (with the
field tables) are on konsol#189.

| PR | what | state |
|---|---|---|
| konsol **#191** (PR1) | **EPM Fiscal Year** (parent) + **EPM Fiscal Year Period** (child): Details / Periods / Closing / Connections tabs; Generate Periods (Monthly, 13 × 4 weeks, 4-4-5, optional Opening/Closing); Close / Lock / Reopen for a period or the whole year (role-gated, a reason to reopen, the group-rate gate before closing, all-or-nothing for a year). Every period doctype refuses an undeclared period (a contract test keeps it that way). Readers use the declared calendar. Written through to `epm_staging.fiscal_periods`. Migration patch declares the years documents use. Period Status is read-only history. Four review rounds, every finding fixed test-first and the exploits proven live before and after | merged 14 Sep |
| konsol **#192** (PR2) | The readers use the declared calendar: both fx readers take a rate's date from the declared period's `start_date` (one row per period from `epm_staging.fiscal_periods`), never month arithmetic; the home view's current period is the declared Regular period containing today (or none) and each year's past/current/planning comes from its declared dates; the SPA opens on that period (or a "No declared period covers today" page), shows the server's period codes and labels, treats only Regular periods as accounting periods, picks the newest year by value, and shows "Not declared" instead of Open. Built as a Ralph loop (fixed prompt, one self-contained row per iteration) | merged 14 Sep |
| PR3 (konsolidat) | `fiscal_periods` source, `silver_group_periods`, replace `build_date_from_year_period`, guard in the TB-only first build | not started |
| PR4 | Remove Period Status, the Fiscal Period template and `_build_fiscal_vars` | not started |

**Rules to keep:**
- **A period is declared or it doesn't exist.** `period_status.period_row` raises `PeriodNotDeclared`; never add an "Open if missing" fallback, a calendar-year guess or a month-number range. `test_no_stale_period_readers` sweeps for them (its allow-list names functions, not lines).
- **Effective status = the stricter of the row and its year.** Status fields change only through the actions; the guard permits exactly the changes an action declares.
- **Actions act on the database copy.** Each whitelisted action locks the year row `FOR UPDATE`, reloads, decides, and saves; nothing the client sent reaches a decision. A year with no name (built in Python) or `__islocal` (desk) is new: Generate Periods inserts it.
- **Every read a gate decides on is a locking read.** `period_row` locks the year and the period row; the used-period freeze locks the year, then reads documents `LOCK IN SHARE MODE`.
- **Used periods are frozen:** a row a document uses can't be renumbered, re-dated or deleted, and a used year can't be deleted.

**This site has no FY2026 declared.** After deploying #192 the SPA opens on "No declared period covers today" until someone declares FY2026 in EPM Fiscal Year (a data step, not a code change).

**Verified live (konsolidat.local):** migrate declared 16 years (2010–2025), 224 rows, equal to `epm_staging.fiscal_periods`; a 38-step walkthrough on FY 2099; the two-process race (submit vs close) refused after the fix; the review's three exploits reproduced on the old code and refused on the new; the bench test 19/19. All test data rolled back or deleted (0 left).

**Lessons:**
- **Locking the parent row doesn't refresh a child-table read.** Under REPEATABLE READ the plain read of the period row returned the old snapshot after a concurrent close committed; only the live race showed it.
- **Frappe `is_new()` is falsy for a document built in Python** (it returns `__islocal`). Host stubs must mirror that.
- **A test gate can pass on 0/0** (zsh doesn't word-split `$VAR`); gates fail on `0/0` and skip lines, one file per argument.
- **A share lock read through a covering index doesn't block a lock on the row.** `SELECT name … WHERE fiscal_year=… LOCK IN SHARE MODE` locks only the unique-index entry; the close's `WHERE name=… FOR UPDATE` didn't wait, so the first deadlock fix still deadlocked live. Lock the same record the other side locks (select a non-indexed column, or lock by primary key).
- **A Ralph loop needs the coordinator's gate to be a real review.** PR2 ran one fixed prompt over self-contained rows; the gates (re-running each row's check and reading its diff) found five follow-ups the rows hadn't asked for — a loading-state breadcrumb, year kinds still by calendar year, an empty home route, a calendar-year fallback, and year order — and one loop agent correctly wrote its out-of-scope find up as a new row instead of fixing it.
- **konsol-exec's Vite build isn't byte-reproducible**: a rebuild can swap the two font files' numbered names and the CSS line pointing at them. Commit the JS a change needs; ignore that churn.
- **An agent must never change the shared environment.** A loop iteration installed pytest into `.venv` unasked (it now runs 25 more test files; kept). The fixed prompt now says: stop and report instead.
- **Verify a hot-copy by a symbol it adds.** A checksum check that compared two empty hashes passed while nothing had been copied, and a live B arm then ran the old code.

## State on 13 Sep — merged, open, next

**User rule (13 Sep): no work on the D365 write-back itself** (`konsol/d365_writeback.py`): it will be dumped and redesigned. Budget Cycle may change, but its D365 push/withdraw calls stay as they are.

**User rules (12 Sep):** remove the demo; fill with the customer's data when Grok's
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

`test_workflow_convention.py` and `test_cancel_period_gate.py` enforce what they can; every submittable doctype now follows them (#143). Submit into a closed period is refused too (#149, PR #160).

**Next (the user's priority order of 13 Sep, group 2 onwards):**
- Group 2 (customer-load critical path) is **done**; see "Group 2" above.
- Group chart (konsol#182) PRs 1–3 are **done**; see "Group chart of accounts" above. Next there: the chart upload by the user, then PR4 and PR5, plus konsolidat #187, #172 and #185.
- Excel correctness: konsol **#105** (decision) → **#104** → **#106**; then **#108**, **#107**.
- Waiting on the four reporting-bases decisions: konsol **#113**, **#111**, **#114**, **#117**.
- Security follow-ups from group 1: konsol **#165**, **#166**; integrity: **#167**, **#168**, **#169**.

**Open questions for the user:**
- Move the repos off iCloud-synced `~/Documents`?
- The wipe scope.
- (Decided 13 Sep: `generate_demo_data.py` deleted, konsolidat #166.)

**Lessons:**
- Live-verify every hook and model change. 814 static tests missed an enqueue TypeError, and `dbt parse` missed a SYNTAX_ERROR.
- A test file that needs frappe is skipped by the host runner (and listed). Load the module by path, or exec the controller against a stub frappe (`test_consolidation_adjustment_lifecycle.py`). Check that a new test file is counted; add it to the runner's must-run set if it guards security.
- Hot-copied request-path code is not live until gunicorn reloads: `docker exec konsolidat_backend` then send HUP to the master (PID 19; confirm via `/proc/<pid>/status`). Restart `konsolidat_worker` for job code.
- Frappe v15's `RetryBackgroundJobError` path ends every retried job in `AttributeError: job`; retry inside the job instead.
- A security source test that blocks bad shapes one at a time is an arms race; pin the exact shape, count every binding, check the loaded function, and test the rule by behaviour.
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

## One customer or many: the product-level audit (15 Sep)

**Decision (user, 15 Sep): for multi-tenant, the dbt project moves into the
Frappe app — it is the right home for it — and the ELT/ETL layer stays out.**

Today the stack is **single-tenant by construction**: ClickHouse creates fixed
`epm_*` databases with no tenant, site or customer column anywhere in
`init-db.sql`; konsol rewrites the vars block of `dbt_project.yml` in place;
Cube carries no security context. A second Frappe site clobbers the warehouse.
So "many customers" currently means many deployments, and any per-customer
setting that lives in `dbt_project.yml` is a deployment artefact rather than
product configuration. Moving dbt into the app sharpens that: the project
becomes one file shipped identically to every tenant, so a dbt var can only
ever hold a product-wide default — never a customer's chart account.

### What is already right (do not spend effort here)

- **Zero customer identifiers in code** — 0 hits across every `.sql`, `.py`,
  `.yml`, `.js` and `.json` in konsolidat.
- **Account roles are declared, not hardcoded.** konsolidat#198 removed the
  one-sided debits to literal `'1800'`/`'1900'`; the deal journals and IC
  eliminations read `goodwill_account`, `nci_account`, `investment_account`
  and `ic_difference_account` from the group root.
- **Statement classification comes from the chart** (`statement_section`), not
  from account-number ranges. No prefix or `between` logic on account codes
  anywhere in 106 models.
- **No hardcoded currency** in the warehouse at all.
- **EPM Fiscal Year already supports** Monthly (12), 13 Periods (4 Weeks),
  4-4-5 and Custom, with arbitrary start and end dates.

### Filed (15 Sep)

| issue | what |
|---|---|
| konsolidat **#206** | Variance analysis filters `scenario_id = 'BUDGET'`, but `gold_spread_budget` emits `BUDGET_2024`/`FORECAST_*` — the model's own comment says they are disjoint. So variance works for a D365-sourced budget and **silently returns nothing for the product's own budgeting module**. Fix: filter `data_source`, or join `scenario_definitions` on `scenario_type`. |
| konsolidat **#207** | D365 cannot be switched off: 11 bronze models `ref()` `stg_d365_fo__*` directly, so `erp_sources` is asymmetric by accident. With ELT out of scope for multi-tenant this is a blocker, not an inefficiency. |
| konsolidat **#208** | The NCI account is declared twice: `var('ic_nci_account', 'NCI')` in dbt and `consolidation_groups.nci_account` from konsol#202. `ic_difference_account` is the pattern to copy. |
| konsolidat **#209** | Materiality is the literal `0.005` in 33 places across 8 models; `ic_difference_tolerance` on the group root is the precedent for declaring it. |
| konsol **#210** | The declared fiscal calendar is not honoured below EPM Fiscal Year: `build_date_from_year_period` forces FY = calendar year and clamps periods to 1–12; `budget_periods.PERIOD_FIELDS` is `period_01..12`, a **doctype column shape**, so a 13-period customer cannot budget at all; `report_compiler` hardcodes Jan–Dec. konsol#189 PR3 covers only the dbt third. |
| konsol **#211** | ~~Product report templates hardcode account codes `4010`/`5010`~~ — **shipped `d37e6b0`**: templates now take their P&L lines from the chart. |

**Sequencing:** konsolidat#207 and #209 touch the same model tree the
dbt-into-the-app move will touch, so they are cheaper done as part of it.
konsolidat#206 and konsol#211 are independent and can go any time; #206 is the
one where a shipped feature returns nothing for any customer not on D365.

### Also filed this session, from the deal layer — all shipped in `31d6a5f`

| issue | what |
|---|---|
| konsol **#203** | `Capitalise` accepted under IFRS and US GAAP; both require expensing. Found by running all 96 policy combinations: 64 accepted, refused only by the two framework rules. |
| konsol **#204** | Full-method NCI is grossed up from the consideration (`consideration ÷ share × (1 − share)`); IFRS 3 wants NCI at its own fair value, which is lower because of the control premium. **Name clash: konsolidat#204 is a different issue** on the adjacent PRD-4 partial-share seam. Always say which repo. |
| konsol **#205** | NCI measurement is group-wide; IFRS 3.19 makes it a per-combination election. |
| konsol **#206** | A waived Acquired Balance Sheet gives net assets of zero, so goodwill absorbs the whole consideration. The waiver was written; the derivation it promises was not. |
| konsol **#207** | Derive the Acquired Balance Sheet from the trial balance, as `_has_tb_at_or_before`'s own docstring already promises. |
| konsol **#208** | Fair Value Allocation Profile — reusable weights for placing a deal's step-up, modelled on Spread Profile. |

### The doctype map

`docs/design/doctype-planes.md` sorts all 60 doctypes into three planes —
**configuration (27), control (6), operating (13)** — with the build order each
one enforces and nowhere states, the operations that actually exist as buttons,
and eight verified findings. Written from `main` at `fb58daa`. The sharpest of
them: **Build Approval, which can rebuild every gold table, has no role check
and no buttons** — `workflow_state` is a plain Select, nothing checks a role on
`Pending Review → Approved`, there is no `has_permission` hook, and DocPerms
grant EPM Analyst write. Approving a `full` rebuild is a dropdown edit.

## The standing delivery loop (user rule, pre-authorized)

Build → open PR(s) → code review → fix all findings → squash-merge → verify
main CI → record in Engram. No per-PR permission needed. **Re-review the fix
commits too.** This session the second review found three bugs introduced by
fixes, the same shape as last month.

**How the loop runs since 13 Sep (user rules):**
- **Small tasks.** Plan first, then keep a task list file (`.claude/memory/active/tasks-<feature>.md`). Each task is tiny, with its test and a done-command. One fresh agent per task gets only that task's brief. The coordinator runs the done-command; red becomes a new tiny task. At most 3 in parallel.
- **Test first.** For bug fixes, pure rule modules and dbt rules, the failing test is committed first, and the PR shows the red run, then the green run.
- **Token care.** Don't resume large agents for small fixes. Run one review per PR pair. Run the full live A/B once, near-final. Say the cost before expensive work.

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

**Local stack right now:** konsol `main` (through #148, the role home)
hot-copied into backend and worker, including the built konsol-exec bundle.
On konsolidat.local the Consolidation Adjustment workflow already carries the
F7 roles (EPM Analyst drafts, EPM Admin approves); no test users are left. The dbt project is
bind-mounted from the konsolidat checkout at `9e263a3`, so the next build uses
#162's models. The Consolidation Adjustment workflow is
installed on konsolidat.local. The AMIT and ZZ test data are gone. Nothing was
redeployed.

Local test loops: `.venv/bin/python scripts/run-host-tests.py` → **897/897 passed across 82 files** on main after #146; `cd konsol-exec && node --test src/*.test.mjs
src/orchestrator/*.test.mjs` → 48/48 with #148.

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
