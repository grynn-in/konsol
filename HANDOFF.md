# konsol / konsolidat — status and next steps

_Written 12 September 2026, refreshed that night, on 13 September, again for the role home (F7), for group 2 (13 Sep evening), for the group chart (13 Sep night), and for the declared fiscal calendar (14 Sep), for the deal layer and the one-customer-or-many audit (15 Sep), and for the default measure (17 Sep). Everything below was verified against the running stack._

## Pick up here

**Update (22 Sep, later): the two deploy-path defects are fixed and merged (konsolidat#242 as `a73e143`, closing #239 and #240).** `deploy.sh:395` now writes its dbt log to `mktemp "${TMPDIR:-/tmp}/konsolidat-dbt.XXXXXX"`, and `docker/frappe/Dockerfile` installs `file`. Two lines. Red-then-green measured on GNU coreutils 9.1, in a root container and as a normal user.

**Do not add `set -o pipefail` to `deploy.sh`.** konsolidat#239 suggested it and it would break step 5: the step reads `${PIPESTATUS[0]}` on purpose so `docker compose … | tee` reports dbt's status rather than `tee`'s, and then tells a compilation error (abort the deploy) from data-quality failures on demo data (tolerate). Under `pipefail` with `set -e` the script exits at the pipeline and that classification never runs — the bug konsolidat#139 was filed for. Three tests in `tests/test_deploy_portability.py` exist to keep it out.

**Two claims in those issues did not survive measurement, and both PRDs record it.** #239 said the deploy "still exits 0": it exits **1**, because `deploy.sh:14` sets `set -e` and line 395 is top level. It reads as 0 only through a pipe. #240 said the missing `file(1)` breaks `./deploy.sh restore --from <path>`: it does not — `deploy.sh:87-128` never calls bench, it pipes the dump into the `mariadb` client and `curl`s the ClickHouse natives. What it breaks is `bench restore`, which is what the 22 Sep rebuild actually needed.

**konsolidat#240 ships with no automated test, deliberately.** Three guards were written for it and all three were parsers that read the Dockerfile. The last one passed `RUN apt-get install -y curl && echo 'writing file' > /tmp/x` with the package absent, and failed a correct Dockerfile with a blank line inside a continuation — wrong in both directions. A guard that can pass while the bug is present is worse than none; it is the shape konsol#248 and konsolidat#227 were filed for, and #240 is itself an instance of it. The only honest check is to build the image and look, and nothing in CI builds it: **konsolidat#244**. Until then `file(1)` on the demo stays a hand-install that the next image rebuild discards and restores.

**The lesson, because it cost five review rounds and 31 findings — every one in the guard tests, none in the two fixes.** Three separate times the guard contained a worse bug than the one it watched, the worst being a capture that would have executed `$(mktemp -d && chmod 777 /tmp/x)` on the CI runner. **When a guard cannot understand a line, refuse loudly; do not parse harder.** An annoying red is cheap; a green on a broken line is what this project keeps filing issues about. The rule is written into the test module.

**Filed along the way, all open:** konsolidat#241 (a restore smoke test — back up a seeded site, restore it, assert a row count), **#243** (nine shell scripts have no portability guard; do it with shellcheck, not another hand-written parser), **#244** (the Frappe image is never built in CI, so nothing can guard what it contains).

**Two live findings recorded 22 Sep. Five issues came out of them, all open.**

*Budget and forecast.* Every budget table on the live site is empty — `budget_annual_input`, `budget_monthly_input`, `gold_spread_budget`, `gold_budget_at_hierarchy_node`, `silver_budget_entries`, all 0 rows. `gold_variance_analysis` holds 47,308 rows (the trial-balance count) with `budget_amount` NULL in all of them. **That is correct output, not a defect (Deepak, 22 Sep): no budget means budget zero, so unbudgetted actuals are an adverse variance.** What is undecided is the other side: konsol *refuses* such a read (`"No active budget scenario belongs to FY{year}"`) while Cube, Excel ODBC and any direct ClickHouse reader see the correct adverse variance. Same data, two answers by door. Also: **there is no budget intake** — no upload, no `budget_bulk.py`; budget arrives cell-by-cell through the Excel add-in or hand-typed in the Desk. And **forecast is not a thing to build**: no doctype, no model, no table, no Dataset with `scenario_key: "forecast"`, so a flat forecast read is refused; `HIERARCHY_SCENARIO_CONFIG["forecast"]` is byte-identical to `["budget"]`. konsol#106 is a decision, not a build.

*konsol-exec.* A full task inventory was taken from the code. **What it does that the Frappe Desk cannot is three things:** set a build's stage range (`from_stage`/`to_stage` are enqueue arguments, not fields on `Pipeline Run`), the live step rail with per-step Retry/Resume, and the one-file-many-entities TB loader. Everything else has a Desk equivalent, and much of the app *is* the Desk — every month-queue action is `window.open('/app/…')`. Of ~30 xstate states across its five machines (not six), **five** reflect server state; `closeMachine` (338 lines) has none. Defects found and not yet filed: five writes reachable by GET (`send_reminder`, `start_run`, `retry_step`, `resume_run`, `cancel_run` — three of which defeat the rollback with an explicit commit); three reads with no role guard at all (`get_snapshot`, `get_run_detail`, `get_run` — any authenticated user can read run console logs and every process's blockers); a green toast saying "Reminder sent" when `send_reminder` only writes a log line; the closed-period guard is dead because the app sends neither `fiscal_year` nor `fiscal_period`; two buttons labelled "Build" that create materially different runs; `runExecMachine` and `runDetailMachine` have zero tests.

**Filed 22 Sep, from reading the demo rather than the code.** Opening
`/konsol-exec/2025/0` beside `/konsol-exec/2025/1` showed two byte-identical
pages, and pulling that thread produced five issues, two of which outrank most
of what is on konsol#216's triage:

- **konsol#289 — a valid trial balance for an entity with no ownership period is
  accepted, then silently dropped.** Measured on the demo: 20 submitted, Valid
  trial balances across `CA_OVIVO` (FY2025 P07-P12), `US_OVIVO` (same) and
  `US_CHAMP` (FY2013-FY2020 P12). The close reports `41 of 306 in` for Jul 2025
  while two of those entities submitted for that exact month — they are in
  **neither the numerator nor the denominator**, because every count is scoped to
  `in_close = leaves & covered` (`home_api.py:253`). The only trace is a queue
  row reading "Ownership missing", which looks like a setup chore. This is data
  loss with no signal, in accounting software.
- **konsolidat#245 — nine of `gold_fully_consolidated_tb`'s ten layers hardcode
  blank dimensions.** One `dim_select`, nine `dim_empty_strings`: IC eliminations
  (both legs), CTA, top-side, equity method and all four deal journals. So
  slicing the consolidated TB by any dimension returns **entity balances only**,
  and every adjustment falls in the `''` bucket. Separately,
  `gold_tb_at_hierarchy_node` reads `gold_trial_balance` — the entity-level fact —
  so a management hierarchy never meets an elimination at all. **This is a
  decision, not a defect**: three candidate policies are on the issue, and it must
  be settled before konsol#292's child table is built.
- **konsol#290** — `Ownership Period.consolidation_group` is required,
  unvalidated free text, and part of the document name. A typo creates submitted
  ownership that covers nothing, so the queue keeps saying "ownership missing"
  while the record plainly exists.
- **konsol#291** — the close queue renders one configuration gap as N recurring
  monthly tasks. All 30 items on the demo are period-invariant (23 entities with
  no ownership period at all, 5 date-anchored rates, 2 placeholders), and every
  fix is a `window.open` into the Desk with no refetch.
- **konsol#292** — `Consolidation Adjustment` is a journal line, not a journal:
  one account with both a debit and a credit, and a two-sided entry is two
  documents sharing a free-text `journal_id`. Nothing checks the journal balances
  until a dbt test after the build, and the demo shows `Assertions: Not run`.
  Submit approves one leg. Zero rows exist on either site, so the restructure is
  free today and the warehouse contract does not change.

**The demo's numbers, for whoever looks next.** 329 active non-group entities,
306 with ownership covering the month, 23 without. At most 41 of the 306 have
ever delivered a trial balance (FY2024 P12 and FY2025 P07-P12); every other
period reads `0 of 306 in`. So the close is permanently red for reasons that are
mostly data, not code.


## Decisions register

Every decision the user has made, newest first, with where the full record
lives. **This table is the index; the issue comment named in the last column is
the record.** A decision not in this table has not been made — do not infer one
from code, and never attribute a decision to the user that is not recorded here.

The project memory under `.claude/memory/` is git-ignored and is **not** visible
to anyone else, so nothing is considered recorded until it is in an issue
comment and listed here.

| date | decision | record |
|---|---|---|
| 18 Sep | **The DAG orchestrator is kept and layered on the governed build.** A multi-step close *requests* Build Approvals; Build Approval stays the only thing that invokes dbt. `Pipeline Run` splits into `Close Run`/`Close Step` (orchestration) and `Build Run`/`Build Step` (renamed). Deleting the DAG, making it primary, and running both in parallel were all rejected. | konsol#258 |
| 18 Sep | **Materiality floor is group-declared, defaulting to half the currency's minor unit.** Its own field on the group root, not an overload of `ic_difference_tolerance`. A flat 0.005 default was rejected. | konsolidat#209 |
| 18 Sep | **Partial-period treatment is declared per group** — `partial_period_treatment` = Whole period / Pro-rate by days / Stub trial balance, required once the group has a Business Combination, no default. Pro-rating for everyone was rejected: it assumes even accrual, invisibly. | konsolidat#171 |
| 18 Sep | **Every K.EPM read names its currency.** Mandatory argument, position five, no default and no omitted case. A selector (`"local"` or the group's reporting currency), never a converter. | konsol#253 |
| 18 Sep | **A trial balance declares its currency**; a mismatch with the Entity's `functional_currency` refuses the entity-period. | konsol#252 |
| 18 Sep | **The semantic model is site-owned.** Measure, Dataset, Scenario and Spread Profile are seeded create-if-missing, never force-reimported. Build Scope, Build Model, Pipeline and ISO Currency stay immutable. A per-row `owner` marker was rejected — "structural" is a dependency, not an ownership property. | konsol#230 |
| 17 Sep | **A fact's default measure lives on its Dataset** (`default_measure`), not in code or a per-scenario dict. | konsol#105 |
| 17 Sep | **The ERP staging tree is being removed.** Do not repair defects inside it. | konsolidat#221 |
| 17 Sep | **Principle: refuse what makes a number wrong, report what makes it unexplained, never let data create configuration.** | konsol#247 |
| 17 Sep | **konsol ships no Dimensions**; each site declares its own. | konsol#230 |
| 16 Sep | **Canonical intake only** — konsol does not map source codes. | konsol#218 |
| 16 Sep | **A fixed raw contract**; a configurable Source Profile was rejected. | konsol#218 |
| 15 Sep | **konsol translates, it does not remeasure.** IAS 21 step 2 only; step 1 happens in the subsidiary's own ledger. | konsol#222 |
| 15 Sep | **For multi-tenant, the dbt project moves into the Frappe app**, not ClickHouse. | see "One customer or many" below |
| 13 Sep | **FX rates have one source of truth, via konsol.** | see "Group 2" below |
| 13 Sep | **The group chart of accounts owes nothing to the ERP chart.** | konsol#182 |

### Context that changes how options are weighed

**There are zero customers as of 18 September 2026** (user, 18 Sep). Breaking
changes cost a find-and-replace inside these two repos, not a migration. Do not
down-rank an option for breaking backward compatibility, and do not propose a
version marker or a compatibility shim to protect callers that do not exist.
Prefer the correct shape now; say explicitly when a decision is cheap *because*
of this, because the cost rises with the first customer.

### Open, blocking work

| | question | blocks |
|---|---|---|
| konsol#246 | does the canonical contract need a version marker? Arguably moot at zero customers | nothing urgent |
| unfiled | the 55 `abs(a-b) > 0.01` tie-out tolerances across the assertion suite are **not** materiality and are absolute — the same defect as konsol#180 | not filed |

### Ready to build, no decision needed

**konsol#258 must land before any orchestrator work** — `definition=None` silently falls back to `plan.DEFAULT_DEFINITION`, and the `silver` and `gold` steps are the same bare `dbt_run`, so wiring `pipeline_definition` today ships a double full build.

**All of that is now done.** konsolidat#220 (it was 53 call sites across five macros in three
failure shapes, not 40 of 48), konsol#230, konsol#261, konsol#263's sibling konsol#264,
konsol#265 and konsolidat#223 have all landed. See the 21 Sep update below.

**The queue now starts at konsolidat#227** — the integration suite, which had never run, and
which reported 7 failed / 3 errored / 5 skipped the first time it was given a ClickHouse.


**Update (22 Sep): the demo was rebuilt on the canonical intake, and six defects came out of doing it.** `demo.konsolidat.com` had been six days and 53 commits behind, with three entities, one fiscal year and no chart of accounts — its data had arrived through the ERP/bronze path, which konsolidat#221 deletes. It now runs konsol `d05436b` and konsolidat `a8b552b` against 47,308 trial-balance rows and 45,928 consolidated, matching the development site exactly, on a green build (`PASS=320 WARN=1 ERROR=0 SKIP=0`). The one warning is the historical-rate gap that the development site also carries.

**The sequence, because the order mattered.** The host had no backup and no snapshot and rebuild protection was off, so a snapshot was taken first (image `434872591`). Code was pulled and `deploy.sh` run — about six minutes on a warm cache, not the ten to twenty the old note warned of. The site was then restored from a development backup, which immediately broke ClickHouse: **a cross-site restore carries `EPM Settings`, and with it the source site's ClickHouse password**, so every write-through failed with `AUTHENTICATION_FAILED` until the demo's own password was put back. `reconcile_all()` then took staging from ten entities to 329 and the chart from nothing to 87.

**The trial balances were reloaded through `tb_bulk`, not a script.** A restore brings the Frappe documents but not `epm_raw` — those rows are written once by `on_submit()`, and a restored document never submits again. Rather than call private methods, the six original `Trial Balance Upload` documents were reset and reloaded: 836 submissions, 0 failed, 36,483 raw rows, `amount_basis` = `Period-end balance` taken from the development site's claim control table rather than inferred. **The demo is therefore loaded the way a customer loads it**, and the uploads are visible in `/app/trial-balance-upload` with their reports. Two pieces of stale ClickHouse state had to be cleared first: the ERP source tables `epm_raw.general_journal_account_entry_bi_entities` and `..._entry_bi_entities`, which kept rebuilding the old demo's three entities into silver, and two orphaned rows in `epm_gold.budget_monthly_input` that no document backed.

**Six issues, every one found by running a path nobody had run.** **konsolidat#239** — `deploy.sh:395` uses `mktemp -t konsolidat-dbt`, which needs three trailing `X`s on GNU coreutils, so step 5 of 5 fails on every Linux host and `deploy.sh` still exits 0; the dbt build has never run on a server. **konsolidat#240** — `bench restore` shells out to `file(1)`, which the Dockerfile does not install, so `deploy.sh restore --from <path>` has never worked; the admin guide documents it as the recovery path. **konsol#281** — `Trial Balance Upload.loaded_count` is counted from the stored report JSON rather than from submissions that exist, so six uploads reported 836 successful loads having created nothing. **konsol#282** — `reconcile_all()` cannot clear `epm_gold.budget_monthly_input`, though it does clear `budget_annual_input`. **konsol#283** — there is no supported way to rebuild `epm_raw` from submitted trial balances. **konsol#285** — an entity cannot declare its dimension values.

**konsol#255 does not unblock management reporting on its own, and konsol#285 is the cheaper half.** A hierarchy leaf is a dimension value, and a row joins it only if the row carries that value; `gold_tb_at_hierarchy_node` filters on `where dim != ''`. If the value changes row by row — a cost centre, a department — it must be on the row, which is #255. If it is the same on every row of an entity — a business unit, a segment — then storing it once per entity and joining it on is enough, which is #285. Business unit is the second kind: all 44 entities carrying trial balances fall into one family each and never change family. **Therefore #255 alone leaves a management hierarchy empty.** #285 is also cheaper, because an entity attribute is constant per entity and never enters `silver_tb_movements` — the model #255 must widen, and the one place where a careless change leaves every amount wrong and still balanced. An implementation plan for #255 is on the issue; the join precedent for #285 is `silver_gl_entries` lines 68 and 186, which already join an entity-keyed table twice.

**Two things about the demo a later session must not assume.** `file(1)` is installed **by hand** in the running container, so konsolidat#240 re-breaks the moment the image is rebuilt. And the demo can no longer show management reporting: the previous demo could, only because its rows came through the ERP path, and moving to the supported intake traded that for working consolidation.

**Update (21 Sep): the signal phase is finished, and the install blocker with it.** Nine issues closed in three days and they were the ones everything else was waiting behind. **konsol#248** — the host runner counted a file that stopped importing as *skipped*, left it out of the denominator, and still printed `N/N passed`; its first CI run found that CI had been running **eighteen fewer test files than a laptop** (`2131/2131 across 141 files` against 159 locally), because CI had no pytest. Both now report `2360/2360 across 159`. **konsol#265** — a dbt `warn` is Amber and must be acknowledged, not reported as an error with its rows hidden; twenty of the 126 assertions are `warn`, so a sixth of the rules had been unreadable. **konsolidat#181** — CI now builds the whole warehouse on a throwaway ClickHouse. **konsolidat#178** — five singular tests had been running against stale models in a domain-selected build. **konsolidat#220** and **konsol#230** — a site with zero dimensions can build, and the semantic model is seeded create-if-missing instead of force-reimported, so a site can retire what it does not want. **konsol#261** — publishing or unpublishing a Dimension no longer empties the gold fact tables. **konsolidat#185** — the report takes its expense sub-sections from the chart's declaration rather than account-number prefixes. **konsolidat#226** and **konsol#264** — allocation and its leftover database are gone.

**What Phase 0 immediately produced: konsolidat#227.** `tests/integration` had never run — its fixture skipped the suite whenever ClickHouse was unreachable, and no job had ever supplied one. Given one, it reported **7 failed, 3 errored, 5 skipped**. Partially repaired in `81b468c`; the issue is still open and it is the last thing standing between this project and a test suite whose green means something. Do it before anything in the tiers below.

**Update (21 Sep): a full `dbt build` no longer skips the consolidation chain.** Measured on `main` (`435b968`), in a copy outside the bind mount, against the live ClickHouse: **PASS=319 WARN=2 ERROR=0 SKIP=0 in 19.85 seconds** (24s wall) across 5 incremental models, 64 table models, 9 view models and 241 data tests. `gold_consolidated_trial_balance` and `gold_fully_consolidated_tb` both built. The advice in `CLAUDE.md` to reach the chain with `dbt run --select +<model>+` was true when error-severity tests were failing and dbt skipped their children; it is not true now (konsol#276 corrects it). Scope a build to save time, not to reach the chain: **three deal-layer models are 53% of the run** — `gold_business_combination_journal` 5.5s, `gold_business_disposal_journal` 2.9s, `gold_ic_reconciliation` 2.1s — while the consolidated trial balance itself takes 0.21s.

**The two warnings that build raised are real and unaddressed.** `assert_declared_historical_has_a_rate` returned **773 rows** and `assert_equity_rate_coverage` **329**. The chart declares four accounts `fx_method = historical`; three of them carry trial-balance data, across **42 (entity, account) pairs**. Thirteen Historical Equity Rate drafts exist, all for account `3000`, and **all thirteen now validate** — konsol#240's fix (`is_group = 0` was the wrong membership test) unblocked them on 18 Sep. None is submitted, so `epm_staging.historical_equity_rates` holds **zero rows** and every one of those 42 pairs translates equity at the wrong rate. Submitting the thirteen leaves **29 pairs with no draft at all**. That is konsol#242 (derive the anchor date from the deal or the ownership start and propose drafts), which should move out of the deferred pile: without it someone hand-enters 29 rates and repeats the exercise at every acquisition.

**The ERP staging tree is half removed.** `models/staging` is down to five files from thirty; **`models/bronze` still holds sixteen models** and nothing tracks the second half. konsolidat#221 is a standing rule, not an issue with a scope. konsol#200 — build preflight still gating on Airbyte/connector status when the canonical source is the trial-balance upload — sits downstream of it.

**Update (18 Sep): the eight Business Combination drafts are no longer carrying goodwill equal to the whole consideration (konsol#260).** Three of them held a consideration and an empty Acquired Balance Sheet, so net assets were zero and goodwill had absorbed the entire payment — 13,596,000,000 across the three. Each was run through the product's own **Get Balances from Trial Balance** action (`get_balances_from_trial_balance`), nothing hand-written: net assets are now 8,606,658,301 and goodwill 4,989,341,699. All three validate cleanly and each carries a `balance_sheet_source` note naming the entity, the period derived through, the latest period with data, and the retained-earnings account the period's result was folded into. **Nothing was submitted** — submit is the approval and it is the owner's.

Three things a future session must not assume here. **The `validate()` guard already existed** and refuses an empty Acquired Balance Sheet by name, so these drafts were never submittable; the konsol#206 / konsol#207 fixes left stale *values*, not an open door, and konsol#260 needs no code change. **The five drafts with no consideration remain Draft and remain blocked**, correctly: none has a submitted trial balance at or before its acquisition period, so Get Balances cannot help them, and each needs a decision — a real deal awaiting data, or a stub to delete. Submitting one would create an Ownership Period. **The derived balances are at the acquisition period's end, not the acquisition date**; for the December deal that is the whole month's closing position, which is exactly what konsolidat#171 decided should become a declared `partial_period_treatment` policy. One of the three derives 95.7% goodwill against 26.7% and 33.7% for the others, because its entity's trial balance only begins in the acquisition year — worth a human eye before it is submitted.

**Update (18 Sep): a re-triage of every open issue, grouped by consequence, is on konsol#216** (cross-referenced from konsolidat#210). It replaces "what order should we work in" with "what does this stop": Tier 0 cannot install for a second customer, Tier 1 makes a number wrong or unreadable, Tier 2 is groups that cannot be modelled, Tier 3 cannot be operated safely. Read it before picking work — the tiers, not the issue numbers, are the point. **konsolidat#220 is the recommended start**: it needs no decision and it gates konsol#230, because removing the shipped Dimensions before the 40 trailing commas are fixed breaks every build.

**Filed 18 Sep.** **konsol#255** — a trial balance cannot carry declared dimensions while the budget path already can (`Dimension.in_budget` drives a Custom Field onto Budget Line; there is no equivalent for the TB intake), so every `dim_*` column downstream of the trial balance is structurally unfillable and any Reporting Hierarchy rolls up zero. **konsol#258** — the shipped `Group Close` pipeline's `silver` and `gold` steps are the same bare `dbt_run`, and the definition exists three times (`fixtures/pipeline.json`, `plan.DEFAULT_DEFINITION`, a mirror in `lineage.py`); not reachable today, but it must land before any orchestrator work. **konsol#260** — the drafts above.

**konsol#106 stays open and undecided, with the evidence now recorded on it.** `HIERARCHY_SCENARIO_CONFIG["forecast"]` is a byte-for-byte duplicate of `["budget"]` — same table, same measures, same flags — which argues for dropping `forecast` as a scenario rather than registering a second Dataset pointing at the same place. The issue body also claims ten Scenario records; there are three. There is no budget or forecast data on the site, so neither option can be validated. Out of scope for the consolidation track.

**Update (17 Sep): a fact's default measure lives on its Dataset (konsol#105 Decision 1, PR #249, `9903cbd`; closes konsol#104).** Dataset gains `default_measure`, a Link to Measure validated against that dataset's *own* `fact_measures` — and deliberately **not** skipped under the install/migrate/import flags, so a bad value in `fixtures/dataset.json` fails the migrate instead of installing quietly. The nine shipped datasets declare theirs. Both read paths now fill a blank measure *after* resolving the fact, through one helper each (`_measure_for` flat, `_hierarchy_measure` for the hierarchy branches of `epm_value` and `epm_batch`), so the measure that is validated is the measure that is queried — a fill confined to `_resolve_and_validate` validates one and queries a blank, which dies at the identifier check. Both hardcoded `period_net_amount` literals are gone (the string now occurs **0** times in `api.py`) and so is the whole per-scenario `default_measure` dict in `hierarchy_query.py`, which was unreachable dead code. `hierarchy_query` must not reach the registry on the request path: importing `konsol.api` there makes `test_hierarchy_query.py` stop importing on a host, which is how konsol#248 was found.

**Forecast is the one scenario the registry cannot answer, and that is deliberate (konsol#106, still open).** A forecast hierarchy read naming no measure used to return `0` from the deleted dict; it now errors naming the scenario. Do **not** "fix" that by registering a forecast Dataset — that decides #106 by fixture. The evidence says forecast is already modelled as budget rows filtered by a scenario id (the hierarchy config points it at `gold_budget_at_hierarchy_node` with `has_scenario_id`, and the add-in's `scenarioId` tooltip says "budget/forecast"), but there are no customers and no forecast data, so the question was left open rather than answered on no evidence. Note #106's text is wrong on one fact: `fixtures/scenario.json` ships **three** Scenario rows (ACTUAL, BUDGET, FORECAST), not ten.

**Still split, filed as konsol#250:** the *default* now has one source of truth, but the *allowed set* does not. Flat reads validate against the Dataset's `fact_measures` ∩ Published Measures; hierarchy reads validate against the hand-written `HIERARCHY_SCENARIO_CONFIG[sc]["measures"]`. A customer declaring a legal default outside that hand-written set gets working flat reads and failing hierarchy reads.

**Four more filed this session, two of them live-data defects.** **konsol#248** — `scripts/run-host-tests.py` counts a file that fails to import as *skipped*, so the headline still reads `N/N passed` while whole files leave the run; it printed `2328/2328` with no failures while 15 hierarchy tests had silently gone. Only two files are in `MUST_RUN`. **Check the skipped list and compare totals; `N/N passed` alone is not evidence.** **konsol#251** — `_resolve_period` accepts 1–12 and `FY` expands to `(1…12)`, so fiscal period 13 is unreachable; that is the year-end close, which is *correct* to exclude for P&L but not for the balance sheet, where retained earnings' final balance lives only at period 13 (JP_ECL FY2024 account 3100: readable `2,886,469,851`, actual closing `−2,348,774,018`). **konsol#252** — a TB upload cannot declare its currency (the contract is six columns), so a USD file against a EUR entity translates at the EUR rate and trips nothing. **konsol#253** — `gold_trial_balance` has no currency column, so `K.EPM` returns unlabelled numbers across the 15 functional currencies the 44 loaded entities use.

**konsolidat#221 is a standing rule now: do not repair a defect whose only site is in the ERP staging tree — close it citing #221.** A corrective sweep already closed konsolidat#190, #173 and #206. One caveat recorded on konsol#189: two of #190's three `coalesce` sites are in `silver_gl_entries`, which #221 lists as **surviving**, and they read `entity_fiscal_calendars` — a konsol doctype synced by konsol, not ERP staging. Deleting the tree will not carry them away; PR3 is the right place to decide them.

**Update (16 Sep): the Assertion Run surface is role-gated (konsol#166, #165).** Starting a close run needs Close Lead (`EPM Admin`) or System Manager and is POST-only, because it writes: it inserts a run, commits and enqueues the suite. The launch form's options need the roles that launch pipelines, because they are read with `frappe.get_all`, which ignores permissions. Each failed step stores up to 20 offending rows from the dbt failure table, and about 30 of those tables carry an entity column while Assertion Run is not entity-scoped, so that field sits at permission level 1, granted to System Manager and EPM Admin only; the other roles keep the run, its steps and their counts. This is the app's first permission level, so it takes effect on migrate, and a test that reads the role matrix must filter permlevel rows out. A new whitelisted endpoint here needs its own `frappe.only_for`, and one that writes needs `methods=["POST"]`.

**Update (16 Sep): Excel reads and warehouse syncs (konsol#194, #231).** The Excel read helper sends its SQL in the POST body; as a URL parameter it passed ClickHouse's 128 KiB form-field limit, and every chunk of a dense sheet failed with "HTML Form Exception: Field value too long". Keep new read paths on that helper rather than building a second client. A warehouse sync no longer truncates the live table: it fills `<table>_sync_tmp` and swaps it in, so a failure part way leaves the previous rows. The temp table is dropped and rebuilt from the live one each sync, because the swap exchanges the two names and column changes only ever reach the live name. A sync forced by reconcile or a manual run now raises instead of returning quietly; a sync triggered by a document save stays best-effort, so a save never fails when ClickHouse is down. A batch read groups by fact, measure and dimensions but no longer by period: the period is selected and each row is matched back to the cell that asked for it, so a sheet of twelve months is one query, and a cell asking for a range still sums its own periods.

**Update (16 Sep): management trees are dated (konsol#220).** A Reporting Hierarchy Member has Effective From (required) and Effective To (blank = open). When a division is renamed, moves to another parent or ends, add a second row with the same Member Code; don't edit the old one. End the old row the day before the new one starts. konsol refuses overlapping rows of one code, a child whose dates its parent code does not cover, a parent cycle through dated rows, and a parent edit or delete that would leave a child without a parent. With the konsolidat change that ships alongside it, the warehouse rolls every trial-balance, budget and variance period up the tree as it stood in that period, with that period's labels. Without that change the warehouse ignores the dates, so deploy both together. The tree API takes `as_of`. Load the first customer tree with its dates; the site had no hierarchy when this landed, so nothing needed restating.

**Update (15 Sep, night): variance compares actuals with every declared budget, and konsol reads one budget at a time (konsolidat#206 + konsol#214). D365 is off by default (konsolidat#207).**

- **Variance (warehouse).** The warehouse variance models pick actuals and budgets by each scenario's declared `scenario_type` (active only), no longer by the codes `'ACTUAL'`/`'BUDGET'`. Budgets authored in konsol now reach variance. Each budget scenario keeps its own rows (`budget_scenario_id`), and actuals pair only with budget scenarios that budget that entity and year. Where a budget has no line, the budget amount is empty, not 0.
- **Variance (konsol).** konsol's variance readers (hierarchy query, `variance_analysis` Dataset, the Excel `epmVariance`) filter on one budget scenario:
  - The named one, which must be an active budget.
  - With none named, the active budget on the request year's Budget Cycle.
  - None or several are refused, with the reason stated.
- **ClickHouse errors.** A failed ClickHouse query shows only its error code and name. The full reply goes to the Error Log.
- **Deploy order.** Merge and build konsolidat first. The `budget_scenario_id` column exists only after that build. Then deploy this konsol change.
- **Also in konsolidat:**
  - A `materiality_floor()` macro replaces the literal 0.005.
  - Intercompany NCI posts to the group root's declared NCI Account. A group without one posts to the placeholder `NCI`, and a warning names it.
  - `erp_sources` defaults to `[]`: the trial-balance upload is the canonical source. List `d365_fo` or `erpnext` to build a connector's staging.
**Update (15 Sep, night): Build Approval is approved through a Frappe Workflow (konsol#215).** Approve and Reject are buttons for EPM Admin; the role and self-approval are set in the Workflow record ("Build Approval Workflow"), not in code. A new request goes in as Draft and takes the workflow's Request transition (low risk to Approved, high risk to Pending Review); the build job's own moves (Start, Complete, Fail) are Administrator-only transitions it takes under `build_lock.build_writer()`. Deploy: migrate (after_migrate installs the workflow once); a site that edits the workflow keeps its edits.

**Update (15 Sep, night 2): SCOPE DECISION — konsol translates, it does not
remeasure (user, 15 Sep). konsol#222; konsol#224 closed.**

IAS 21 has two steps and they belong to different parties: **remeasuring** local
books into the entity's functional currency happens in the subsidiary's own
ledger, before any trial balance is sent; **translating** functional into the
presentation currency is the group's job. konsol does step 2 only, which is what
it already assumed — one `functional_currency` per Entity, and a canonical trial
balance with no currency column because the amounts are implicitly in it.

**So an entity whose functional currency is not its country's currency is not a
problem to model.** 18 of 329 are like this — 14 booking EUR across Latin
America, Asia and Russia, 2 booking CNY, plus 2 with no functional currency at
all. A limited-risk distributor buying from a European principal, priced and
financed in EUR, genuinely is EUR-functional wherever it sits; so is an entity
in a hyperinflationary economy holding a stable functional currency. Their books
arrive in that currency and konsol translates.

**What is actually wrong is the chart's tagging.** Under translation-only,
`fx_method = historical` is right for **equity accounts only**; `average` for
P&L; `closing` for everything else — including goodwill and fair-value
adjustments, which IAS 21.47 makes assets of the foreign operation, translated
at closing. **The work: re-tag 14 assets and 1 liability from `historical` to
`closing`**, in the source workbook as well as the loaded chart, or the next
chart upload undoes it. Six equity accounts stay historical.

**NCI stays at closing.** `3400` being the only equity leaf not declared
historical is correct: NCI is a residual interest measured as the minority's
share of net assets, which translate at closing. Record the reasoning on the
account so nobody "fixes" it.

**konsolidat#213, found while deciding this — a latent bug that goes live on the
first deal submit.** The two repos disagree about what equity means:
`silver_main_accounts.is_equity` is `fx_method = 'historical'` (21 accounts)
while konsol's deal layer uses `account_type == "Equity"` (7). They diverge on
15. `gold_business_combination_journal` splits an acquired balance sheet on the
konsolidat definition — `-sumIf(book_amount, is_equity = 1) as net_assets` — so
land, buildings, machinery, goodwill and the intangibles would be **eliminated
as pre-acquisition equity**, and the journal's goodwill would disagree with the
goodwill stored on the document. Nothing compares the two. Re-tagging makes them
coincide, but the definitions should be separated regardless.

**Historical rates now shrink to equity only** — 6 accounts, tranched by date,
one row per account per entity per event. A fraction of the ~5,500 the
unconditional reading implied.

**Still open:** `KE_EC` and `MA_EC` have no functional currency at all, because
the ISO seed is missing KES and MAD (konsol#190) — they cannot translate until
it is fixed. Hyperinflation (IAS 29: restate, then translate at closing) remains
its own question; konsol#176 covers the FX magnitude guard's side.

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

**The site has no Reporting Hierarchy yet, so the first tree you load should
carry its dates from the start.** There are currently **0 Reporting Hierarchies
and 0 members** on the stack, and that is the whole reason this is cheap:
nothing to migrate, no report to restate, nobody reconciling a before and an
after. It is a doctype change plus a first load that already knows about dates.

Load an undated tree first and the cost inverts — every report built on it is
computed under one structure, and adding dates later means re-deriving each of
them and explaining why last quarter's divisional P&L moved. The dates are not
something to invent later either: the source sheet already carries
`effective_from` and `effective_to` per node, waiting for somewhere to go.

The legal tree already solves the same problem — `Ownership Period` is dated and
consolidation resolves it per period (`macros/ownership_resolution.sql` is the
pattern to copy).

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

## Excel read path: measured, fixed, and why not Cube (16 Sep)

**Shipped (`9347eea`, konsol#194 + #231).** The Excel read helper
`api._clickhouse_query` was still sending its SQL in the **URL** — konsol#193
fixed `clickhouse.execute()` but never this second, separate client. Above about
1,500 cells in one group the URL exceeded what ClickHouse accepts and the whole
chunk failed with `HTML Form Exception: Field value too long`. The add-in chunks
at `MAX_BATCH_SIZE = 2000`, so **every dense sheet was guaranteed to hit it**,
and `_batch_query_clickhouse` swallowed it into a per-cell
`errors: ['ClickHouse query failed']` that named nothing.

| cells, one group | before | after |
|---:|---|---|
| 300 | 18 ms | 15 ms |
| 1,200 | 88 ms | 74 ms |
| 2,000 | **failed** | **167 ms** |

Also shipped: a forced sync now raises instead of failing silently, and a sync
fills a sibling table and swaps it in, so a failed sync leaves the previous rows
instead of an empty table. And konsol#231 took `periods` out of the batch
grouping key, so a year of monthly cells is one query rather than twelve.

**Cube was evaluated and declined — konsol#232, labelled `don't-fix`.** It is
deployed, idle, has **zero pre-aggregations** defined, and **nothing in konsol
calls it**. Its schema files are hand-written and duplicate the
Dataset/Measure/Dimension registry. Reopen only if a measured query crosses
about a second, a non-Excel client needs the semantic layer, or concurrency
rather than latency becomes the limit — and even then a Redis cache inside
`_batch_query_clickhouse` reuses infrastructure that already exists.

**A measurement trap worth not repeating.** An earlier benchmark of mine varied
group count and cell count *together* (1 group / 400 cells versus 60 groups /
24,000 cells) and concluded cost scaled with groups at ~29 ms each. A controlled
run held cells at 1,200 and varied only groups: **about 88 ms either way.**
**Cells dominate; groups are close to free.** The "~29 ms per group" figure and
everything extrapolated from it are void. Hold one variable fixed.

**Nothing caches.** The same batch three times cost 280, 273, 280 ms. That
measurement was not confounded and is the real argument if concurrency ever
becomes the limit.

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

**Also shipped since:** konsol#215 gave Build Approval a real Frappe Workflow —
`Pending Review --Approve--> Approved` is **EPM Admin only** — which closes
konsol#168, the "approving a full rebuild is a dropdown edit" finding from the
doctype map below.

**Public-repo hygiene, 15 Sep:** a sweep of every issue body and comment in both
repos found the customer named in 8 issue bodies and 3 comments, all from
earlier sessions. All sanitised; both repos now grep clean. Note that GitHub
keeps edit history, so this removes the name from search and the API but is not
full redaction. **Grep before writing a customer's name, entity codes, or its
reporting-segment names into an issue — segment names identify a customer as
surely as its own name does.**

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

**Local stack right now (17 Sep):** konsol `main` @ `9903cbd` (konsol#105
Decision 1) hot-copied into backend and worker — `api.py`,
`hierarchy_query.py`, the Dataset doctype JSON and controller,
`fixtures/dataset.json` and the add-in `functions.json`, each md5-verified in
both containers. `reload_doc` ran for Dataset, so `tabDataset.default_measure`
exists, and the nine shipped datasets carry their value. **Those nine were set
by a targeted write, not a fixture import** — `sync_fixtures` re-imports every
konsol fixture (chart, measures, scenarios, spread profiles) and konsol#230
documents that it force-overwrites whatever a site holds; the next real migrate
re-imports `fixtures/dataset.json` and should land on identical values. Cache
cleared and both containers restarted, verified by the new code being loaded
(`period_net_amount` occurs 0 times in the container's `api.py`). On
konsolidat.local the Consolidation Adjustment workflow carries the F7 roles
(EPM Analyst drafts, EPM Admin approves); no test users are left. The dbt
project is bind-mounted from the konsolidat checkout. The AMIT and ZZ test data
are gone. Nothing was redeployed.

**Verified on live data, not on empty result sets:** actuals blank →
`-24,640,361,228.18`, identical to explicit `period_net_amount` and different
from `period_debit`; consolidated blank → `-12,278,292,000.06`, matching an
independent ClickHouse measurement; cashflow blank → `-8,532,000,000.0`.
Variance's blank and explicit reads give the *identical* downstream refusal (no
active budget scenario for FY2024, konsol#214 working), so the default
resolved. The three driver datasets fail with `UNKNOWN_TABLE` —
`epm_staging.fact_headcount`, `fact_area_sqm` and `fact_revenue_by_product` do
not exist on this site. A first pass read `US_ECL / 4000`, which has no rows,
and got `0.0` everywhere: **an empty result returns `0.0` for any valid
measure, so it proves nothing. Pick keys with data and compare blank vs the
named measure vs a different one.**

Local test loops: `.venv/bin/python scripts/run-host-tests.py` → **2359/2359 passed across 158 files** on main at `9903cbd`, with 8 files skipped (the known live-site/pytest set — check that list, see konsol#248); `cd konsol-exec && node --test src/*.test.mjs
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
