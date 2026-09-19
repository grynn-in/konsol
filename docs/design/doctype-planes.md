# The konsol doctype map — configuration, control, operating

_Analysis written 15 September 2026 by reading all 60 doctype JSONs, every
doctype controller docstring, every `add_custom_button`, the whitelisted API
surface and `konsol/dashboard.py`. Nothing here was changed; this is a
description of `main` at **fb58daa**._

_Correction: passes 1 and 2 were first drafted against a checkout six commits
stale (448cee1). Everything below is re-verified against 625821f, which adds
`EPM Fiscal Year` / `EPM Fiscal Year Period` (konsol#189 PR1–PR2) and
`amount_basis` (konsol#201 / konsolidat#199). Both change the picture
materially, and the stale-tree conclusions have been removed rather than
annotated._

konsol has 60 doctypes — 46 top-level and 14 child tables. The Desk workspace
groups them into 8 process cards, which is a useful menu but hides the thing a
newcomer most needs: **what must exist before what**. This document sorts the
same 43 into three planes and gives each plane its build order and its list of
operations, where an "operation" means a button a person clicks, not a function.

## The three planes

| Plane | Rule that defines it | Count |
|---|---|---|
| **Configuration** | Set up once per site. Governs *how* numbers are computed. Not dated. Mostly `Draft → Published → Inactive`, and Publish is what pushes it to ClickHouse. | 27 |
| **Control** | Owns *state and permission to proceed*: is the period open, may this build run, is the close signed off. Holds no accounting data. | 6 |
| **Operating** | The dated, per-period documents carrying actual numbers. Mostly submittable; `docstatus = 1` is what the warehouse reads. | 13 |

The planes cut across the workspace cards. "Reference Data" mixes the chart and
Entity (configuration) with Period Status (control); the "Consolidation" card
puts Ownership Period and IC Balance (operating) next to IC Elimination Rule
(configuration).

---

# Pass 1 — the configuration plane

## Build order

```
  L0  SHIPPED WITH THE APP — installed, not authored. No operation to click.
  ═══════════════════════════════════════════════════════════════════════════
   ┌──────────────┐ ┌──────────────┐ ┌───────────┐ ┌───────────────┐
   │ EPM Settings │ │Fiscal Period │ │ Scenario  │ │ Spread Profile│
   │   (Single)   │ │ FP-1 … FP-12 │ │  fixture  │ │    fixture    │
   │ CH host, Air-│ │→dbt_project  │ │ ACTUAL,   │ │ month weights │
   │ byte, D365,  │ │  .yml vars   │ │ BUDGET …  │ │ for top-down  │
   │ perm doctypes│ └──────────────┘ └─────┬─────┘ └───────┬───────┘
   └──────┬───────┘                        │               │
   ┌──────────────┐ ┌──────────────┐ ┌─────┴─────────┐     │
   │ ISO Currency │ │ Build Scope  │→│  Build Model  │     │
   │ seeded @inst │ │  6 domains   │ │  40 gold mdls │     │
   └──────┬───────┘ └──────────────┘ └───────────────┘     │
          │              (governs what a build rebuilds)   │
          │                                                │
  ════════╪════════════════════════════════════════════════╪═══════════════
  L0b THE FISCAL YEAR — konsol#189. Declare it or nothing dated works.
          │
          │  ┌──────────────────────────────────────────────────────────┐
          │  │ EPM Fiscal Year   (+ EPM Fiscal Year Period child rows)  │
          │  │  ▸ Generate Periods   ▸ Close / Lock / Reopen Year       │
          │  │  ▸ Close / Lock / Reopen Period                          │
          │  │  period_pattern, opening + closing period flags          │
          │  │  A year or period that is NOT declared raises            │
          │  │  PeriodNotDeclared — it is never assumed Open. ◀── the   │
          │  │  single biggest change from the old Period Status model  │
          │  └──────────────────────────────────────────────────────────┘
          │        (this is also the control plane's period gate — see Pass 2)
          │
  ════════╪════════════════════════════════════════════════╪═══════════════
  L1  THE GROUP CHART — first thing you actually author.   │
          │                                                │
          │    ┌─────────────────────────────────────┐     │
          │    │  Main Account  (tree, governed)     │     │
          │    │  ▸ Upload chart → Check → Load      │ ◀── the one real
          │    │  ▸ Publish chart   (list view)      │     bulk operation
          │    │  ▸ Publish / Unpublish (per account)│     │
          │    │  → epm_staging.main_accounts        │     │
          │    │  → build scope "chart"              │     │
          │    └──────────────┬──────────────────────┘     │
          │                   ╎ (free-text account code, NOT a Link)
          │      ┌────────────┴────────────┐  ┌────────────────────┐
          │      │Cash Flow Category       │  │Intercompany Account│
          │      │ BS account → CF line    │  │ IC flag + counter- │
          │      │ governed, no button     │  │ part; scope "conso"│
          │      └─────────────────────────┘  └────────────────────┘
          │
  ════════╪══════════════════════════════════════════════════════════════
  L2  THE ENTITY MASTER — needs ISO Currency for functional currency.
          ▼
   ┌──────────────────────────┐        ┌───────────────────────────┐
   │ Entity  (tree, but MUST  │        │ Entity Fiscal Calendar    │
   │ be flat: is_group=0)     │        │ ERP data area → calendar  │
   │ functional_currency ─────┘        │ governed, no button       │
   │ → epm_staging.entities            └───────────────────────────┘
   └──────────┬───────────────┘
              │
  ════════════╪══════════════════════════════════════════════════════════
  L3  THE GROUP STRUCTURE — needs Entity *and* the chart.
              ▼
   ┌────────────────────────────────────────────────────┐
   │ Consolidation Group   (tree — the ONE structure)   │
   │  • every is_group node needs reporting_currency    │
   │  • ic_diff accounts validated against the chart ◀── the only doctype
   │  • ic tolerance                                       that checks it
   │  → epm_gold.consolidation_groups                   │
   └────────────────────────────────────────────────────┘
        ↑ gates Group Exchange Rate (operating, Pass 3)

  ══════════════════════════════════════════════════════════════════════
  L4  THE SEMANTIC MODEL — independent of L1–L3; reporting, not accounting.

   ┌───────────┐  Publish   ┌────────────────────┐
   │ Dimension │──────────▶ │ Dimension Mapping  │  raw ERP value →
   │  Publish/ │            │ governed, no button│  canonical value
   │ Unpublish │            └────────────────────┘
   └─────┬─────┘
         │ must be Published first
         ├──────────────▶ ┌──────────────────────┐    ┌──────────────────┐
         │                │ Reporting Hierarchy  │───▶│ Reporting        │
         │                │ Publish / Unpublish  │    │ Hierarchy Member │
         │                │ scope "reporting"    │    │ (recursive tree) │
         │                └──────────────────────┘    └──────────────────┘
         │
   ┌─────┴─────┐          ┌──────────────────────────────────┐
   │  Measure  │─────────▶│ Dataset  (+Dataset Dimension/    │
   │  Publish/ │          │  Dataset Measure child rows)     │
   │ Unpublish │          │ publish() exists — NO button ◀── gap
   └───────────┘          │ refuses unpublished dim/measure  │
                          └──────────────────────────────────┘

  ══════════════════════════════════════════════════════════════════════
  L5  THE RULES — pure config, all account codes free text.

   ┌──────────────────────┐   ┌─────────────────────┐   ┌───────────────┐
   │ IC Elimination Rule  │   │ Allocation Rule     │──▶│Allocation Tier│
   │ AR/AP, rev/COS, URP… │   │ step_order, driver  │   │ (child rows)  │
   │ no lifecycle, no btn │   │ no lifecycle, no btn│   └───────────────┘
   └──────────────────────┘   └─────────────────────┘

  ══════════════════════════════════════════════════════════════════════
  L6  INGESTION — OPTIONAL. A CSV/TB-only site skips all of it.

   ┌────────────────────────────────────┐   ┌──────────────────────┐
   │ Connector                          │   │ Pipeline             │
   │  ▸ Test Extract                    │   │  (+ Pipeline Step)   │
   │  ▸ Test Writeback                  │   └──────────┬───────────┘
   │  ▸ Test & Provision Airbyte        │              ▼
   │  + Connector Legal Entity (child)  │   ┌──────────────────────┐
   │  + Connector Dimension Map (child) │   │ Pipeline Schedule    │
   └────────────────────────────────────┘   └──────────────────────┘
```

## The clickable path, in order

The whole configuration plane as operations a person performs. Everything not
listed here is a plain save.

| # | Operation | Where the button is | Prerequisite |
|---|---|---|---|
| 1 | *(nothing)* — EPM Settings, Fiscal Period, Scenario, Spread Profile, ISO Currency, Build Scope/Model arrive on install | — | — |
| 1b | **Generate Periods** on a new EPM Fiscal Year | EPM Fiscal Year form | — |
| 2 | **Upload chart → Check → Load** | Main Account **list view** | — |
| 3 | **Publish chart** | Main Account list view | a loaded draft chart |
| 4 | *(or)* **Publish / Unpublish** one account | Main Account form | — |
| 5 | Create Entities (functional currency) | plain form | ISO Currency |
| 6 | Build the Consolidation Group tree | tree view | Entity + chart |
| 7 | **Publish / Unpublish** Dimension | Dimension form | — |
| 8 | **Publish / Unpublish** Measure | Measure form | — |
| 9 | **Publish / Unpublish** Reporting Hierarchy | form | Published Dimension |
| 10 | **Test Extract / Test Writeback / Test & Provision Airbyte** | Connector form | EPM Settings |

Eleven operations. Everything else in this plane is a plain save.

## Findings from this pass

Each verified in source, not inferred.

1. **Seven governed doctypes have a `publish()` that no button and no CLI can
   reach.** `GovernedReferenceDocument.publish/unpublish` is whitelisted on Cash
   Flow Category, Dimension Mapping, Entity Fiscal Calendar, Intercompany
   Account, ISO Currency and Budget Annual Input — none has a `.js`, and
   `config_service.export_config` / `apply_config` cover only Dimension,
   Measure, Dataset and Connector. Those rows can leave Draft only by a raw
   `frappe.call`. **Dataset is the sharpest case:** it is a coloured shortcut
   tile on the workspace and its Publish is API-only. Cash Flow Category is
   the mildest case since konsol#196/#199: a chart publish now fills its rows
   (`main_account.py:218`), so hand-publishing is the exception rather than the
   rule.

2. **The chart is governed, but almost nothing points at it.** `Main Account` is
   a proper tree with a lifecycle, yet `Cash Flow Category.main_account`,
   `Intercompany Account.main_account`, `IC Elimination Rule.debit_account` /
   `credit_account` and `Allocation Rule.source_account` / `target_account` are
   all `Data`, not `Link`. `Consolidation Group._validate_ic_accounts`
   (`konsol/consolidation/doctype/consolidation_group/consolidation_group.py:130`)
   is the only place in the app that checks an account against the chart.
   Rename or unpublish an account and five config doctypes keep pointing at a
   code that no longer exists, silently.

3. **Seven top-level doctypes are unreachable from the workspace**, though
   `konsol/dashboard.py:88` states "Every top-level konsol doctype appears in
   exactly one card": `ISO Currency`, `Entity Fiscal Calendar`,
   `Group Exchange Rate`, `Trial Balance Submission`, `Trial Balance Upload`,
   `Budget Annual Input` and now `EPM Fiscal Year` — the doctype a site must
   create *first*, and the one carrying the close buttons. Trial Balance Submission is the primary intake for a
   connector-less site; it is reachable only by URL or through konsol-exec.

4. **The Desk gives no hint of the build order above.** That is why the first real
   customer load needed the order written down in local project memory rather
   than discovering it from the UI.

---

# Pass 2 — the control plane

Six doctypes. None of them holds an amount. Each one answers a single question
of the form "may this proceed?", and between them they gate every write in the
operating plane.

## The gates and what they guard

```
  ╔═══════════════════════════════════════════════════════════════════════╗
  ║  EPM FISCAL YEAR + EPM FISCAL YEAR PERIOD — the master gate (#189)    ║
  ║                                                                       ║
  ║   Year:    Open ──▶ Closed ──▶ Locked      ▸ Close/Lock/Reopen Year   ║
  ║   Period:  Open ──▶ Closed ──▶ Locked      ▸ Close/Lock/Reopen Period ║
  ║                                                                       ║
  ║  • A period's EFFECTIVE status is the STRICTER of its own and its     ║
  ║    year's (fiscal_status_model.effective_status).                     ║
  ║  • Periods are DECLARED, never assumed. An undeclared year or period  ║
  ║    raises PeriodNotDeclared — it is not treated as Open. This is the  ║
  ║    inversion of the old model, where an absent record meant Open.     ║
  ║  • Closing runs group_rates.assert_rates_complete() ◀── the last      ║
  ║    human-catchable moment for a missing FX rate.                      ║
  ║                                                                       ║
  ║  PERIOD STATUS (legacy) is being retired into this: it still exists,  ║
  ║  `fiscal_migration_model.plan()` works out what to declare from its   ║
  ║  rows, and konsol#189 PR4 removes it.                                 ║
  ╚═══════════════════════╤═══════════════════════════════════════════════╝
                          │ assert_open() / assert_open_between()  (konsol/period_status.py,
                        │  now reading tabEPM Fiscal Year / …Period)
      ┌───────────────────┼───────────────────┬──────────────┬───────────┐
      ▼                   ▼                   ▼              ▼           ▼
 Trial Balance      Consolidation         IC Balance    Allocation   Group
 Submission         Adjustment                          Run          Exchange
 (validate: even    (approve + reverse)   (submit +     (submit +    Rate
  a SAVE refused)                          cancel)       reverse)    (approve+
      │                                                              cancel)
      └── Ownership Period & Historical Equity Rate use the *date-range*
          variant, assert_open_between(), because they are dated, not periodic.

      Also gated: control_api.start_process() — so the konsol-exec Run
      button cannot post into a closed period either.

  ╔═══════════════════════════════════════════════════════════════════════╗
  ║  BUILD APPROVAL — the gate on every governed dbt build.  BAPR-#####   ║
  ║                                                                       ║
  ║   Draft ──┬─ risk low  (scope "staging")  ──▶ Approved (auto)         ║
  ║           └─ risk high (actuals, scenarios, consolidation,            ║
  ║                         chart, full)        ──▶ Pending Review        ║
  ║                                                      │                ║
  ║                                                      ▼                ║
  ║              Approved ──▶ Running ──▶ Completed / Failed              ║
  ║                              │                                        ║
  ║                              └─ 30-min reaper; manual exit refused    ║
  ║                                 unless flagged build_writer           ║
  ║   rebuild_requested: a request arriving mid-build is absorbed, not    ║
  ║   lost — the flag survives every save until a start spends it.        ║
  ╚═══════════════════════════════════════════════════════════════════════╝
      ▲ requested by: chart publish (scope "chart"), governed publish
        (scope "full"), Reporting Hierarchy ("reporting"), Intercompany
        Account + Allocation Run ("consolidation"),
        konsol.tasks.queue_consolidation_build, control_api.start_process.

  ╔═══════════════════════════════════════════════════════════════════════╗
  ║  BUDGET CYCLE — the ONLY budget lock. scenario × fiscal year.         ║
  ║  Open (docstatus 0) ──submit──▶ Locked (1) ──cancel──▶ reopened       ║
  ║  Submit locks every sheet, syncs ClickHouse, fires D365 write-back.   ║
  ║  No per-line, no per-owner workflow. Refuses an "actual" scenario.    ║
  ╚═══════════════════════════════════════════════════════════════════════╝

  ╔═══════════════════════════════════════════════════════════════════════╗
  ║  ASSERTION RUN — the close attestation.  (+ Assertion Step children)  ║
  ║  Queued ▶ Running ▶ Green / Red / Error                               ║
  ║       ▸ Run Suite       (button; refuses a concurrent run)            ║
  ║       ▸ Sign Off        (button; Green only)                          ║
  ║       ▸ Override Sign-off (button; EPM Admin only, reason required,   ║
  ║                            audited — the only way past a Red close)   ║
  ║  assert_close_signed_off() exists but has NO CALLER ◀── see findings  ║
  ╚═══════════════════════════════════════════════════════════════════════╝

  ┌───────────────────────────────┐   ┌──────────────────────────────────┐
  │ PIPELINE RUN (+ Run Step)     │   │ CONNECTOR HEALTH                 │
  │ Queued ▶ Extracting ▶         │   │ derived, NEVER hand-edited       │
  │ Transforming ▶ Running ▶      │   │ 5-min cron refresh_connector_    │
  │ Completed / Failed / Cancelled│   │ health; one row per enabled      │
  │  ▸ Run Pipeline (button, new) │   │ Connector                        │
  │  live log over frappe.realtime│   │ Succeeded/Running/Never/Stale/   │
  │                               │   │ Failed, colour-coded in the list │
  └───────────────────────────────┘   └──────────────────────────────────┘
      observability, not enforcement — neither one refuses anything
```

## The three control surfaces

The same gates are driven from three places, and they do not offer the same
operations:

| Surface | What it drives | Operations |
|---|---|---|
| **Desk** | the doctypes directly | Run Suite, Sign Off, Override Sign-off, Run Pipeline. That is all four. |
| **konsol-exec** (`control_api.py`) | 4 registered PROCESSES — Budgeting (scope `scenarios`), Forecasting (`actuals`), Consolidation (orchestrator "Group Close"), Assertions | `get_snapshot`, `set_period_status`, `start_process`, `get_run_detail`, `send_reminder` |
| **konsol-exec home** (`home_api.py`) | the month as **8 ordered stages** | source data ▸ trial balances ▸ ownership & rates ▸ intercompany ▸ adjustments ▸ consolidate ▸ assertions ▸ sign off |

`home_model.py` also encodes, as data, which doctypes the period gate actually
refuses — `SUBMIT_NEEDS_OPEN_PERIOD` (5 doctypes) and `SAVE_NEEDS_OPEN_PERIOD`
(Trial Balance Submission alone), each pinned by a host test that runs the real
controller against a closed period. That is the cleanest gate documentation in
the app, and it lives in the SPA's model rather than beside the gate.

## Findings from this pass

1. **The highest-risk gate in the app has no buttons and no role check.**
   `build_approval.py:4` states "high-risk scopes require EPM Admin approval",
   but `workflow_state` is a plain editable `Select` (no Frappe Workflow —
   `workflows.INSTALLED` holds only Consolidation Adjustment), `build_approval.js`
   only rewires the dashboard's Trigger link, nothing in `before_save` checks a
   role on `Pending Review → Approved`, there is no `has_permission` hook for the
   doctype, and the DocPerms grant **EPM Analyst** write
   (`build_approval.json` permissions). `on_update` enqueues the build on the
   state value alone. So an EPM Analyst approves a `full`-scope rebuild by
   picking "Approved" from a dropdown and saving. Approving a build is a
   dropdown edit for every role that can write.

2. **`start_process` self-approves for System Managers.** `control_api.py:216`
   creates the Build Approval, and if it lands in `Pending Review` and the caller
   has System Manager, immediately sets `Approved` + `approved_by = self`. The
   review step is skipped by the caller who requested the build. Defensible for
   an admin, but it means the konsol-exec Run button and the Desk path have
   different governance.

3. **Stage 8 gates nothing.** `assert_close_signed_off()`
   (`assertion_run.py:212`) raises unless the period's latest run is signed off —
   and has no caller. Its own docstring says so: the integration point is
   PRD §6.5, which is not built. Sign-off is currently an audit record, not a
   gate; nothing downstream refuses to run because a close was never signed off.
   Deliberate and documented, but the 8-stage lane in konsol-exec presents it as
   the final gate.

4. **The period gate is the one control doctype that is properly built.**
   `EPM Fiscal Year` carries seven real operations — Generate Periods,
   Close/Lock/Reopen Year, Close/Lock/Reopen Period — each a POST-whitelisted
   controller method behind a button, with `closed_by` / `closed_on` stamping
   and a reopen reason. It is the model the other five control doctypes should
   be measured against, and it makes finding 1 sharper by contrast: the same
   codebase that built this left approving a `full` rebuild as a dropdown edit.
   The one blemish is that it is absent from the workspace (Pass 1, finding 3).

5. **Two gates are advisory by construction.** Connector Health and Pipeline Run
   report state; neither refuses work. The enforcement that *does* exist for raw
   data lives in `konsol/tasks.py` (the Airbyte/connector readiness gate on the
   build scopes), not in these two doctypes — so an operator watching the
   Connector Health list is not watching the thing that blocks builds.

# Pass 3 — the operating plane

Thirteen doctypes. Every one is dated, every one carries an amount, and every one
is gated by Pass 2. Nine of the eleven write through to ClickHouse; the two
that do not are staging for the ones that do.

## The month, as the product actually runs it

The eight stages in `home_model.STAGES` are the spine. Each stage is one or two
operating doctypes plus the control gate that releases it.

```
 STAGE          OPERATING DOCTYPE(S)                WRITES TO          GATE
 ─────────────────────────────────────────────────────────────────────────────
 1 Source data  (Connector / Pipeline Run)          epm_raw.*          Connector
                 ingestion only — no konsol doc                        Health
                          │
                          ▼
 2 Trial        ┌────────────────────────────┐
   balances     │ Trial Balance Upload       │  TBU-#####   not submittable
                │  ▸ Check file  ▸ Load      │  one file, many entity-periods
                │  Draft▸Checked▸Loading▸    │  konsol-exec upload page
                │  Loaded / Partly / Failed  │  resumable; skip_invalid
                └─────────────┬──────────────┘
                              │ creates + submits one per entity-period
                              ▼
                ┌────────────────────────────────────────────────┐
                │ Trial Balance Submission   TBS-{entity}-{FY}-  │
                │ P{P}-{###}          SUBMITTABLE                │
                │  amount_basis: Period movement / Year-to-date  │
                │   movement / Period-end balance — NO default   │
                │   (konsol#201; EPM Settings holds a site-wide  │
                │    default, copied onto new docs only)         │
                │  → epm_raw.trial_balance_submissions           │
                │  → epm_raw.trial_balance_submission_control    │
                │     (batch_id claim = the commit point)        │
                └────────────────────────────────────────────────┘
                   ▲ the ONLY operating doctype that lands in epm_raw,
                     and the only one using a claim/control table rather
                     than TRUNCATE+INSERT. Also the only one whose *save*
                     (not just submit) a closed period refuses.
                          │
                          ▼
 3 Ownership    ┌──────────────────────┐  ┌──────────────────────────────┐
   & rates      │ Ownership Period     │  │ Group Exchange Rate          │
                │ OP-{grp}-{ent}-{date}│  │ SUBMITTABLE — submit IS the  │
                │ SUBMITTABLE          │  │ approval (konsol#103)        │
                │ ownership_pct 0–100  │  │  ▸ Pre-fill from ERP (list)  │
                │ method, acquisition, │  │    → drafts only, nothing    │
                │ disposal             │  │      applies itself          │
                │ → epm_staging.       │  │ quote + quoted_per + source  │
                │   ownership_periods  │  │ → epm_staging.group_exchange │
                └──────────────────────┘  │   _rates                     │
                ┌──────────────────────┐  └──────────────────────────────┘
                │ Historical Equity    │   IAS 21 historical rates for
                │ Rate   SUBMITTABLE   │   equity accounts
                │ → epm_staging.historical_equity_rates
                └──────────────────────┘  ◀── in NO close stage (finding 3)
                          │
                          ▼
 4 Inter-       ┌──────────────────────────────────────────────┐
   company      │ IC Balance   ICB-{seller}-{buyer}-{FY}-P{P}  │
                │ SUBMITTABLE · ic_sales_amount,               │
                │ ending_inventory_from_ic                     │
                │ → epm_staging.ic_balances                    │
                └──────────────────────────────────────────────┘
                          │
                          ▼
 5 Adjustments  ┌──────────────────────────────────────────────┐
                │ Consolidation Adjustment  CADJ-{journal}-####│
                │ SUBMITTABLE · the ONLY installed Frappe      │
                │ Workflow in the app:                         │
                │   Draft ─Send for Approval▶ Pending Approval │
                │   Pending ─Reject▶ Draft                     │
                │   Pending ─Approve▶ Approved (docstatus 1)   │
                │   Approved ─Reverse▶ Reversed (docstatus 2)  │
                │   EPM Analyst drafts · EPM Admin approves    │
                │ → epm_staging.consolidation_adjustments      │
                └──────────────────────────────────────────────┘
                ┌──────────────────────────────────────────────┐
                │ Allocation Run  ARUN-{FY}-P{P}-####          │
                │ SUBMITTABLE · Draft/Active/Reversed          │
                │ workflow JSON exists but is DORMANT by       │
                │ design (workflows.INSTALLED) → no button     │
                │ → epm_staging.allocation_runs                │
                │   reads Allocation Driver (AD-…-{FY}-P{P})   │
                └──────────────────────────────────────────────┘
                          │
                          ▼
 6 Consolidate  Build Approval, scope "consolidation"  ─── Pass 2
                 → gold_consolidated_trial_balance and the other 40 models
                          │
                          ▼
 7 Assertions   Assertion Run ▸ Run Suite               ─── Pass 2
                          │
                          ▼
 8 Sign off     Assertion Run ▸ Sign Off / Override     ─── Pass 2
                 then EPM Fiscal Year ▸ Close Period

 OFF THE MONTHLY LANE — budget is an annual cycle (F7 decision):
   Budget Annual Input  (hash-named, governed, → epm_gold.budget_annual_input)
        │ spread by Spread Profile
        ▼
   Budget Sheet (+ Budget Line child rows, wide)  → epm_gold.budget_monthly_input
        edited through the Excel add-in: api.budget_save / budget_cell_save /
        budget_save_batch. Locked by Budget Cycle submit; D365 write-back fires.
```

## The deal layer (konsol#202)

Business Combination and Business Disposal joined this plane on 15 Sep, both
submittable, both with an installed Workflow — which doubles the app's stock of
real approval UIs from one to three. They read a new **Consolidation Policy**
tab on the Consolidation Group root: five policy Selects, a goodwill
amortisation term, and nine accounts by role. Every one of those nine is a
`Link → Main Account`, which makes this the first configuration in konsol that
actually links the chart instead of holding a free-text account code — the
direct answer to Pass 1, finding 2, and the pattern the five older config
doctypes should follow.

## Operations in this plane

| Operation | Doctype | Where |
|---|---|---|
| **Check file → Load** | Trial Balance Upload | konsol-exec upload page (`tb_bulk.check_file` / `load`) |
| Submit / Cancel / Amend | Trial Balance Submission | standard Frappe submit bar |
| **Pre-fill from ERP** | Group Exchange Rate | **list view** inner button |
| Submit = approve | Group Exchange Rate | submit bar |
| Submit / Cancel | Ownership Period, Historical Equity Rate, IC Balance | submit bar |
| **Send for Approval / Reject / Approve / Reverse** | Consolidation Adjustment | Frappe workflow bar |
| Draft ▸ submit | Business Combination, Business Disposal | Frappe workflow bar (installed #202) |
| Cell + batch save | Budget Sheet | Excel add-in |

## Findings from this pass

1. **The primary intake has no Desk presence at all.** Trial Balance Upload —
   the path that loaded all 836 submissions of the first real customer load — has no `add_custom_button`
   (its `.js` only pre-fills `amount_basis` from EPM Settings) and is not on the
   workspace. Check and Load exist solely as `tb_bulk` endpoints driven from
   konsol-exec. A Desk-only operator cannot bulk-load a trial balance, and
   cannot discover that the capability exists.

2. **Three of thirteen operating doctypes have a real approval UI.** Consolidation
   Adjustment was the only installed Frappe Workflow until #202.
   konsol#202 added two more (Business Combination, Business Disposal).
   Everything else expresses "submit is the approval" through the bare Frappe
   submit bar, so an approval, a correction and a data entry all look identical.
   Allocation Run's workflow is written and deliberately dormant, leaving its
   two operations reachable only by API.

3. **Historical Equity Rate is in no close stage.** It is period-gated
   (`assert_open_between`) and feeds `epm_staging.historical_equity_rates`, but
   `home_api` never surfaces it, so nothing in the month view tells a Close Lead
   that an equity account is translating at a stale historical rate. Ownership &
   rates (stage 3) covers Ownership Period and Group Exchange Rate only.

4. **Trial Balance Submission is the odd one out, deliberately and correctly.**
   It is the only operating doctype landing in `epm_raw`, the only one using a
   batch claim rather than TRUNCATE+INSERT, and the only one whose *save* a
   closed period refuses. That claim pattern is why a failed load resumes
   instead of double-loading — and konsol#193 showed the cost of the
   TRUNCATE+INSERT model everywhere else, where one failed resync emptied
   `consolidation_ancestry` outright.

5. **`amount_basis` has no default, on purpose.** konsol#201 gives the field
   three explicit options and no default, with a site-wide default on EPM
   Settings copied onto new documents where a person can see and change it. The
   2025 deviation seen in the first customer load (−90.6%) is a pre-#201 artefact; the product now
   refuses to guess.

---

# What the three passes add up to

```
  CONFIGURATION (27)          CONTROL (6)              OPERATING (13)
  ══════════════════          ═══════════                ══════════════
  EPM Fiscal Year ─────────▶ (its own period gate) ──┐
  Main Account (chart) ─┐                            │
  Entity ───────────────┼─▶ Build Approval ◀─────────┼── every dbt build
  Consolidation Group ──┘         ▲                  │
  Dimension/Measure/          Budget Cycle ──────────┼── Budget Sheet
   Dataset/Hierarchy          Assertion Run ─────────┼── (stage 7-8)
  IC + Allocation rules       Pipeline Run           │
  Connector / Pipeline        Connector Health       ▼
                                              TBS · TBU · FX · Ownership
                                              IC Balance · Adjustment
                                              Allocation Run · Budgets
```

Three structural observations across all three passes:

- **Governance is strongest where the data is, weakest where the power is.**
  The chart, the entity master and the fiscal year are carefully governed with
  publish lifecycles, declared periods and audited closes. Build Approval —
  which can rebuild every gold table in the warehouse — is an editable dropdown
  that EPM Analyst can write.
- **The Desk and konsol-exec are not the same product.** The Desk has 26
  buttons across 60 doctypes; the bulk trial balance intake, the period tree
  and the eight-stage close exist only in konsol-exec; `start_process`
  self-approves where the Desk path would not. A site's governance depends on
  which surface its people use.
- **The build order is real and nowhere stated in the UI.** Fiscal year →
  chart → entities → group tree → rates → trial balances → build → assertions →
  close. Every arrow in Pass 1 and Pass 2 is enforced by a `frappe.throw`, but
  the workspace presents 8 unordered process cards, and 7 top-level doctypes —
  including EPM Fiscal Year, the one you need first — are not on it at all.
