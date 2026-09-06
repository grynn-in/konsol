# konsol / konsolidat — status and next steps

_Written 6 September 2026. Everything below was verified against a running
stack, not inferred from the code._

## Where things are

| what | where |
|---|---|
| konsol (Frappe app) | `~/Documents/grynn/konsolidat/repo/docker/frappe/konsol` — its own git repo |
| konsolidat (dbt, ClickHouse, deploy) | `~/Documents/grynn/konsolidat/repo` |
| Design docs this work came from | `~/Downloads/files.zip` — **not committed anywhere yet** |
| Architecture review | https://claude.ai/code/artifact/0ee1a977-bca6-4344-b1d8-5f22d7b32827 |
| UI critique | https://claude.ai/code/artifact/ae51ede5-b9db-4958-8a6b-4bce6182aa0f |
| Local memory (Engram) | `.claude/memory/` in the konsol repo — gitignored |

Running stack: `http://konsolidat.local:8069`, site `konsolidat.local`,
credentials in `repo/.credentials`. See `.claude/memory/procedural/local-stack.md`.

## Shipped — konsol main is at `e78659e`

| PR | What |
|----|------|
| #88 | All 13 `rename_doc` patches guarded. One inapplicable rename used to stop migrate dead and silently skip every patch after it. |
| #89 | Three host tests refreshed that main had outgrown. They had been red for months — there is no CI. |
| #90 | `Entity` master (tree, backfilled from Consolidation Group) and `data_area_id` converted to `Link → Entity` on six doctypes. |
| #92 | Entity-scoped access: sub-tree filtering via `lft`/`rgt`, `permission_query_conditions` + `has_permission` on all seven doctypes. Closes #91. |
| #87 | konsol-exec rebuilt on Vue 3 + frappe-ui + XState, rearranged close-first. |
| #94 | Gitignore local Engram memory. |

Tests on main: **696 host**, **34 JS**, **38 bench** — all green.
Run them: `python3 scripts/run-host-tests.py`, `cd konsol-exec && yarn test`,
`bench --site konsolidat.local run-tests --module konsol.tests.<module>`.

## Open work, highest value first

### 1. CSV trial balance submission — DECIDED, not started

Engine-log P2 ("file upload vs direct ERP extraction") is **resolved: both**.
The repo had quietly settled on extraction only; you want submission as well.

This is architecture-review **F8** and is the largest remaining piece. Build
§8 of the control-panel design:

- `Trial Balance Submission` DocType (submittable): entity, period, File
  attachment, `row_count`, `total_debit`, `total_credit`, `validation_status`.
- Validate synchronously against Frappe data — account exists in the group
  chart (`silver_main_accounts`, no new DocType needed), entity matches the
  user's permission, period is Open (`Period Status` now exists), debits equal
  credits, no duplicate account rows.
- Insert rows to a ClickHouse raw landing table with a generated `batch_id`.
- **The control-table pattern is the important part.** ClickHouse has no
  transactions, so: insert to raw freely; on `on_submit` write the `batch_id`
  to `raw_submission_control`; **bronze reads only claimed batches**; reap
  unclaimed rows after seven days. A crash mid-insert then leaves rows nobody
  reads, and cancelling a submission (`docstatus 2`) deletes the control row so
  the batch vanishes from consolidation without touching raw data.
- Retro-fit the same claim step onto the Airbyte path so both ingestion routes
  share one contract instead of forking the bronze layer.
- Resubmission is a **new** submission with a new `batch_id`, never an edit.

Depends on: `Entity` (#90) and `Period Status` (#92) — both landed.

### 2. FX rates are 100× wrong — konsolidat issue #138

**Consolidated statements for every foreign subsidiary are wrong by two orders
of magnitude.** A 41.9M USD entity consolidates as 365K CHF.

The scaling is handled twice with contradictory assumptions:
`stg_d365_fo__exchange_rates.sql` scales conditionally (only when
`ConversionFactor='One'`), then `silver_exchange_rates.sql:14` divides by 100
unconditionally. The seeded raw data holds already-correct rates tagged
`'Hundred'`, so they pass through staging and get divided anyway.

Fix it in the source adapter (`ConversionFactor` is a D365 concept) and delete
the unconditional division from silver. Add a range assertion — the existing
`assert_exchange_rate_positive` passes, because 0.00935 is positive.

### 3. Entity scoping is latent on four doctypes — konsol issue #93

`Consolidation Group`, `Ownership Period`, `Consolidation Adjustment` and
`Allocation Driver` grant read to `System Manager` only, and that is a bypass
role — so #92's filtering can never fire there. Not a hole; the opposite. But
the code implies a protection that cannot engage. Either grant non-admin read
or drop them from `ENTITY_SCOPED_DOCTYPES`.

Scoping works today on `Entity`, `Budget Sheet` and `Historical Equity Rate`.

### 4. Loose ends

- **`fix/ensure-budget-monthly-input-table`** — unmerged branch, no PR, dated
  6 Sep. Adds a patch creating `epm_gold.budget_monthly_input` on migrate plus
  tests. Not written by this session. Needs a PR or it will be lost.
- **`wip/local-exec-embedding`** — your CSRF fallback and edge-to-edge bleed
  CSS, rescued before `deploy.sh` could hard-reset over it. Pushed, unreviewed.
- **konsolidat PR #137** — docs-only, open since 2 July, ahead 1 / behind 0.
- **The two design docs are still only in `~/Downloads/files.zip`.** They should
  live in `konsolidat/docs/design/`.

## Architecture review scorecard

| # | Finding | Status |
|---|---|---|
| F1 | Entity has no identity | **Done** — #90, #92 |
| F2 | Tree/DAG ownership split half-built | Not started |
| F3 | Frappe writes into the dbt repo; dual metadata path | Not started |
| F4 | D11 (MySQL table engine) should be reversed | Decision recorded; nothing to build |
| F5 | D13 (`Map` dimensions) should be reversed | Decision recorded; nothing to build |
| F6 | Fiscal Period too thin to govern a close | **Done — but not as specified.** See below. |
| F7 | One workspace, organised by schema | **Half.** Permissions done (#92); roles and workspaces untouched. |
| F8 | No submission surface | Not started — **now decided, see above** |
| F9 | Layer vocabulary collides three ways | Not started |
| F10 | IC elimination already built | Audit never done. Verify elimination fires at the lowest common ancestor. |

**Where the review was wrong, and worth not repeating:**

- **F6 said to add `status`/`start_date`/`end_date`/`fiscal_year` to
  `Fiscal Period`.** That DocType is a *template* — `format:FP-{fiscal_period}`
  gives exactly 14 records (OPN, P1–P12, CLS) reused by every year. A status
  there would have made closing September 2024 also close September 2025.
  Shipped as a separate `Period Status` keyed (fiscal_year, fiscal_period).
- **Issue #91 said absent configuration should mean "see nothing".** It should
  not — in Frappe a User Permission is an opt-in restriction, and inverting
  that would lock out every existing site on upgrade. Deny-by-default is
  available via `EPM Settings.restrict_entities_by_default`, off by default.

## Traps that cost real time

Full list in `.claude/memory/semantic/konsol-gotchas.md`. The four worst:

1. **`patches.txt` has no section headers**, so every patch runs
   `pre_model_sync`. A patch touching a new DocType silently no-ops **and still
   records itself as run** unless it calls `frappe.reload_doc` first.
2. **`frappe.get_all` ignores permissions; `frappe.get_list` applies them.** A
   permission test written with `get_all` passes whether or not the filter
   works. This produced a false "it works" during #92.
3. **`bench restart` does not restart the web workers here** — it uses
   supervisor, which is not how the container runs. Hot-copied Python keeps
   serving the old module until `docker compose restart frappe_backend`.
4. **`./deploy.sh` hard-resets `docker/frappe/konsol`** to `KONSOL_BRANCH`.
   Commit or stash first. It takes `KONSOL_REPO`/`KONSOL_BRANCH`, so it can
   deploy any branch.

## There is no CI

konsol has no `.github/` at all, and konsolidat's two workflows do not run
konsol's Python tests. Three tests were red for months unnoticed (#89). Adding
a workflow that runs `scripts/run-host-tests.py` and `yarn test` would be cheap
and would have caught every one of them.
