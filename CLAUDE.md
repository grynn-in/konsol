# konsol — working notes for Claude

konsol is the Frappe v15 app for Konsolidat (EPM: consolidation, budgets,
allocation). The warehouse (ClickHouse + dbt + Cube) lives in the konsolidat repo.
Read `HANDOFF.md` first each session; project memory is in `.claude/memory/`
(Engram: `bash .claude/scripts/engram-start "<topic>"`).

## Where things are

| what | where |
|---|---|
| konsol (edit here) | `~/Documents/frappe-bench/bench-15/apps/konsol` |
| konsolidat (dbt, ClickHouse, deploy, CLI) | `~/Documents/grynn/konsolidat/repo` |
| Deploy-owned checkout: never edit | `repo/docker/frappe/konsol` |
| Live stack | `http://localhost:8069`, site `konsolidat.local`, creds `repo/.credentials` |

Frappe never runs natively on the Mac (host Python 3.14; the containers run
**Python 3.11**, so no backslashes inside f-string expressions in scripts you
copy in).

## Testing: host tests, then live. Never deploy.

- Host tests: `.venv/bin/python scripts/run-host-tests.py`. JS:
  `cd konsol-exec && node --test src/*.test.mjs src/orchestrator/*.test.mjs`.
- **Do not run `deploy.sh`** to verify anything (user rule; it takes 10–20 min
  and OOMs without `COMPOSE_PARALLEL_LIMIT=1`). Test on the running stack:
  - `docker cp` changed files into `konsolidat_backend`, **and into
    `konsolidat_worker`** for anything that runs as an RQ job, then
    `docker restart konsolidat_worker`.
  - After changing `hooks.py`, call `frappe.clear_cache()` in your script:
    hooks are cached in Redis.
  - Run plain scripts, not `bench console` (IPython mangles tracebacks):
    `frappe.init(site="konsolidat.local"); frappe.connect()`. Never name a
    script after a stdlib module or put it in `/tmp`.
- **Static tests are not enough for hook code.** 814 AST tests passed while
  every Entity save raised TypeError. Live-verify every hook, controller and
  model change, and prove a fix with an A/B (old code shows the bug, new code
  doesn't) where you can.
- **Test data ("ZZ data"):** throwaway records a test script creates, with
  codes starting `ZZ` (e.g. entity `ZZOP`): obviously fake, sorted last, and
  found by cleanup with `LIKE 'ZZ%'`. Clean up in the same script: delete what
  you created, run `konsol.clickhouse.reconcile_all()` afterwards, and cancel
  any Build Approvals your test caused (a pending one debounces every later
  build for its scope). `konsolidat.local` is the test site. ClickHouse is not
  per-site, so a second Frappe site would clobber its warehouse tables.
- **dbt changes:** build them from a copy of the branch's `dbt_project` copied
  into the container OUTSIDE the bind mount (`/home/frappe/dbt_project` IS the
  deploy checkout). `chown -R frappe:frappe` the copy, or dbt exits 2 with no
  output. Require the literal `OK created` line; CI's `dbt parse` does not
  validate SQL. A full `dbt build` now runs the consolidation chain: measured 21 Sep 2026 on
  `main`, **PASS=319 WARN=2 ERROR=0 SKIP=0 in 19.85s** (24s wall), with
  `gold_consolidated_trial_balance` and `gold_fully_consolidated_tb` both
  built. The old advice here — that a full build skips the chain, so scope it
  with `dbt run --select +<model>+` — was true when error-severity tests were
  failing and dbt skipped their children; it is not true now, and following it
  costs a workaround nobody needs. Scope a build to save time, not to reach
  the chain: three deal-layer models are 53% of the run
  (`gold_business_combination_journal` 5.5s, `gold_business_disposal_journal`
  2.9s, `gold_ic_reconciliation` 2.1s), while
  `gold_consolidated_trial_balance` itself takes 0.21s.

## Frappe rules that have bitten this project

- **On submit, Frappe runs `on_update` BEFORE `on_submit`, and the controller
  method before `doc_events` hooks.** A commit in either lands `docstatus=1`
  before `on_submit` has run.
- **Never commit inside a document hook or a helper a hook calls.** Let the
  request or job commit. Work that must follow a commit goes in
  `frappe.db.after_commit.add(fn)` or `frappe.enqueue(..., enqueue_after_commit=True)`.
- **A GET request rolls back at the end.** Any `@frappe.whitelist()` method
  that writes must be `@frappe.whitelist(methods=["POST"])`, and its callers
  (konsol CLI, MCP) must POST.
- **`frappe.enqueue`'s own parameters** (`method`, `queue`, `timeout`,
  `event`, `is_async`, `job_name`, `now`, `enqueue_after_commit`, `on_success`,
  `on_failure`, `at_front`, `job_id`, `deduplicate`) cannot be job kwargs.
  `deduplicate=True` queries Redis at call time and needs `job_id`.
- `CallbackManager.add` (after_commit) does not dedupe.
- **Update-after-submit:** on a submitted doc, `self.save()` runs as an update
  and rejects changes to fields that aren't `allow_on_submit`; only
  `on_update_after_submit` fires. A cancelled doc cannot be saved at all.
- `rename_doc` fires `before_rename` / `after_rename` only, never `on_update`.
- `frappe.get_all` ignores permissions; `frappe.get_list` applies them.
- Links case-correct (`usd` → `USD`); a blank Link is stored as NULL, and
  `["in", ["", None]]` never matches NULL (use `["is", "not set"]`).
- A Float column is `not null default 0`: unset and 0 are the same.
- `konsol/fixtures/` is force-reimported on EVERY migrate. It holds reference
  data only (enforced by `test_fixtures_reference_only.py`).
- Frappe never drops a column removed from the DocType JSON; a patch must. A
  DocType JSON change re-imports on migrate when the file's hash changes (v15, `import_file.py`); `modified` need not be bumped.
- MariaDB here is REPEATABLE READ: a plain read in a transaction can miss rows
  committed after its first read. A check-then-insert needs a locking read
  (`SELECT … FOR UPDATE`) on the rows it checks.

## Doctype design (user standard, 14 Sep 2026)

- Repeating rows are a **child table** of a parent doctype (ERPNext style),
  not a standalone doctype keyed on the parent.
- Forms follow ERPNext UX: Tab Breaks for the main areas, labelled Section
  Breaks, Column Breaks for two columns; Title Case labels with help text on
  non-obvious fields; `reqd` only when always required, `mandatory_depends_on`
  when conditional, `read_only` for system-set fields; `title_field`,
  `search_fields`, list indicators, `track_changes`.
- Every doctype design or plan includes its tab/section/field table
  (required/optional/read-only) for review before building.

## Write-through to ClickHouse

- One mechanism: `clickhouse.resolve_sync_filters(doctype)` decides which rows
  belong in the warehouse; `reconcile_all` repairs every table after migrate.
- Sync after the commit, once per transaction (Entity is the reference;
  konsol#124 tracks the 13 controllers that still sync in-transaction).
- Delete sync goes in `after_delete`, never `on_trash` (#120).
- Keep `_REFERENCE_TABLE_DDL` identical to konsolidat's `clickhouse/init-db.sql`.
- Build requests go through `konsol.tasks.queue_consolidation_build` (a job
  after commit, #126). Nothing else calls `on_consolidation_doc_update`.

## Submittable and workflow doctypes (decided 12 Sep 2026)

1. **Submit is the approval.** Review happens while the document is a draft:
   `Draft (0) → Pending Approval (0) → [Approve = submit] → Approved (1)`;
   Reject is `Pending Approval (0) → Draft (0)`. The approver role holds the
   submit permission. Nothing changes status after submit, so nothing is an
   update-after-submit.
2. **Cancel only while the period is open; cancel removes the row from the
   warehouse.** `before_cancel` calls `konsol.period_status.assert_open(...)`
   (for a date-keyed doctype, the period containing its date). Submittable
   doctypes sync `docstatus = 1` rows only, so a cancelled document leaves
   ClickHouse at the next sync, and Frappe keeps it as the audit trail. After
   close, a correction is a NEW document in an open period (e.g. a reversing
   adjustment); amend creates the new version.
3. **Workflows are installed once**, idempotently (create if missing, never
   overwrite), so a site may customise them. Warehouse decisions therefore
   read `docstatus`, never `workflow_state` names. The mechanism is
   `konsol.workflows.install_workflows()`, run from `after_install` and
   `after_migrate`, not `patches.txt`: a fresh install marks every patch done
   without running it. The definition is `<doctype>_workflow.json` beside the
   doctype; add the doctype to `INSTALLED` to switch it on.
4. **Always:**
   - Never `self.save()` inside `on_submit` / `on_cancel`. Set status in
     `before_submit` / `before_cancel`, or let the Workflow set it.
   - Sync on `on_submit` and `on_cancel`, after the commit; never on a draft
     `on_update`.
   - Walk every workflow transition in a live test, including the refused
     cancel of a closed period.

Existing doctypes that don't follow this yet (Consolidation Adjustment, #131)
are brought in line one PR at a time.

## ClickHouse / dbt traps

- `join_use_nulls=0`: an unmatched LEFT JOIN fills the column DEFAULT (''/0),
  not NULL, so `coalesce` / `is null` fail across it. Use UNION + GROUP BY or
  `NOT IN`.
- An alias that matches a column read by a sibling aggregate raises
  CYCLIC_ALIASES. `anyIf` over no rows returns the type default.
- `x IN (col, …)` in `JOIN ON` is refused; `AND` non-equalities and `OR` work.
- `Date` holds 1970-01-01…2149-06-06 and clamps silently; write unset
  values as `DEFAULT`.
- `gold_consolidated_trial_balance` is incremental delete+insert and keeps
  slices that left the SELECT (#154). Full-refresh when verifying removals.

## Environment traps

- The Bash tool's shell is **zsh**: `$VAR args` and `set -- $x` don't
  word-split. Write a `bash` script, or loop explicitly.
- RTK (a global hook) condenses command output. Grep exact counts, and use
  `rtk proxy <cmd>` when output is suspiciously empty.
- `~/Documents` is iCloud-synced and spawns `name 2.ext` copies. Before any
  deploy, merge or dbt run: `find . -name "* 2.*" -not -path "./.git/*"`, and
  delete only copies that `cmp` identical to their original.
- Chained shell commands: use `set -e`, or check each step. A `git add` that
  refuses an ignored file must not be followed by deleting the original.

## Delivery

Standing loop (pre-authorized): build → PR → review → fix all findings →
live-verify → squash-merge → verify main CI → record in Engram and HANDOFF.
Re-review fix commits: fixes here have introduced bugs repeatedly. Report
findings with the issue format in `.claude/memory` (What happened / Root cause
with file:line / Suggested fix / Repro for issues).

Run it token-efficiently (user request, 13 Sep 2026; ~74% of spend was
implement/fix, mostly re-verification and re-reading resumed agents' context):
- Small fix (a few lines) → a fresh agent with a tight brief, or do it
  yourself; don't resume a 500K+ context agent for it.
- Plan file-by-file first for anything spanning both repos or ~10+ files.
- One re-review per PR pair per round; review doc/comment-only fixes yourself.
- Full live A/B once, near-final; earlier rounds use host tests and targeted
  checks. Build only the touched dbt models in scratch schemas.
- Before expensive work (fan-out, full re-verify, another review round), tell
  the user the rough cost and the cheaper option.
- Customer names never go into konsol/konsolidat (public): grep before committing.

Small-task loop (standing rule, 13 Sep 2026): plan the feature, then keep a
task list file (`.claude/memory/active/tasks-<feature>.md`). Each task is tiny
(one behaviour, one or two files) with its test and a done-command. One fresh
agent per task gets only that task's spec, writes the failing test, makes it
pass, commits, reports briefly. The coordinator runs the done-command; green
ticks it, red becomes a new tiny task. Independent tasks in parallel (max 3).
One PR-level review and one live A/B at the end.

Test first (standing rule, 13 Sep 2026):
- Bug fix: commit the reproducing test first, failing on current code, then
  the fix. The PR body shows the red run and the green run.
- Pure rule modules (no frappe/ClickHouse, e.g. `*_model.py`,
  `fx_reference.py`): tests before the implementation, same evidence.
- dbt rule changes: the singular test on a fixture first, shown failing on
  main, then the model change.
- Hook timing and live ClickHouse behaviour still need the live A/B; add an
  automated test wherever one can express it.
- Reviewers treat missing red-then-green evidence as a finding.
