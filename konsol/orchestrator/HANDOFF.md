# Orchestrator build — durable handoff (read me first)

You are a fresh agent continuing the **konsol-exec orchestrator** build. This file is the single source of truth for context — you start with no memory of prior work. Read this, then `PRDS.md` (the backlog), then do **exactly one PRD** and update this file for the next agent.

## Where things are
- **Working dir:** `/home/pd/open_epm/docker/frappe/konsol`
- **Branch:** `feat/orchestrator-p1` (never commit to `main`)
- **Core package:** `konsol/orchestrator/` — pure-python, **no top-level `frappe` import** (must import & test on host without a bench)
- **Tests:** `konsol/tests/test_orchestrator_*.py`
- **Backlog + progress:** `konsol/orchestrator/PRDS.md` (checkboxes)
- **Design:** `open_epm/docs/developer-guide/design/konsol-exec-orchestrator.md`
- **Epic:** konsol#56 (P1 #57, P2 #58, P3 #59)

## The loop protocol (do ONE PRD, strict TDD)
1. Open `PRDS.md`, find the **first unchecked** `- [ ] **PRD-N`.
2. **Red:** write/extend `konsol/tests/test_orchestrator_<area>.py` with failing tests for that PRD. Run `python3 -m pytest konsol/tests/test_orchestrator_<area>.py -q` and confirm it fails.
3. **Green:** implement the code (under `konsol/orchestrator/`, or doctype JSON for doctype PRDs). Iterate until that file passes.
4. **Regress:** run the whole suite `python3 -m pytest konsol/tests/test_orchestrator_*.py -q` — all must pass.
5. **Commit** (format below).
6. Tick the PRD `- [x]` in `PRDS.md` and update the **Current state** + **Next** sections of this file. Commit that too (amend onto the same commit is fine).
7. Stop. Do not start the next PRD.

## Conventions
- **Commit message:**
  ```
  feat(orchestrator): PRD-N <short title> (konsol#57)

  <2-4 line what/why>. <count> TDD tests (<total> total).

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```
- Pure-python core only in `dag.py`/`plan.py`/`state.py`/`executor.py`/`handlers.py`. If a PRD genuinely needs frappe (e.g. PRD-9 binding, PRD-10 API runtime), put frappe imports **inside functions**, and guard frappe-dependent tests with `frappe = pytest.importorskip("frappe")` so the host suite stays green.
- Doctype JSON lives at `konsol/<module>/doctype/<name>/<name>.json`. Tests for doctype PRDs just load the JSON and assert fields exist (no frappe needed). `pipeline_step` is under `konsol/pipeline/doctype/pipeline_step/`, `pipeline_run` under `konsol/pipeline/doctype/pipeline_run/`.
- Bash safety: no `$()` inside other commands' args; redirect to temp files; the `python3 -m pytest ...` form is fine.
- Keep changes scoped to the orchestrator. Do NOT touch `open_epm/dbt_project`, other repos, or unrelated konsol code.

## Current state (update me each PRD)
**Done:** PRD-1, PRD-2, PRD-3, PRD-4, PRD-5, PRD-6, PRD-7, PRD-8. Suite: **77 tests passing.**

**PRD-8 (Real handlers / command building):** `konsol/orchestrator/handlers.py` now ships real handlers instead of no-op stubs, while staying pure-python (no top-level frappe). New public API:
- `build_dbt_command(verb, params) -> list[str]` — the **pure testable core**. Maps `["dbt", verb]` + `--select <select>` (skipped if falsy/empty) + `--full-refresh` (if `params["full_refresh"]`) + `--vars <json>` (`json.dumps(vars, sort_keys=True)` → deterministic). Mirrors the param keys `plan.build_plan` emits (`select`, `full_refresh`, `vars`).
- `DBT_VERB_BY_TYPE` = `{dbt_seed: seed, dbt_run: run, dbt_build: build, dbt_test: test}` (mirrors `plan.DBT_TYPES`).
- Handlers read ctx via tolerant accessors `_params(ctx)` / `_runner(ctx)` (`_ctx_get` works on a `StepContext` **or** a bare `dict`, so the existing `get(t)({})` host tests still pass). dbt handlers (`_make_dbt_handler`) build the argv; if `ctx` has **no `runner`** they return `StepResult(ok=True, log="<joined cmd>")` (planning / host-safe), otherwise they delegate to `runner(argv) -> StepResult`. `airbyte_sync` / `close_assertions` / `signoff` are the same shape (no runner → ok no-op; runner present → `runner([type]) -> StepResult`). **Real subprocess/Airbyte/frappe execution is injected as that `runner` by PRD-9** — `StepContext` does not set `.runner` yet, so on host everything is a pure command-builder.
- Test: `konsol/tests/test_orchestrator_dbt_cmd.py` (16 tests) — builder output for select/full_refresh/vars/combined/empty, verb map, all builtins resolve + are host-safe with `{}`, dbt handler writes cmd into `log`, and an injected `runner` receives the exact argv. No frappe needed.

**PRD-7 (Pipeline Run params):** `konsol/pipeline/doctype/pipeline_run/pipeline_run.json` now carries a new "Run Parameters" section (inserted right before the `extract_section` Section Break) with the run-level fields the orchestrator feeds into `build_plan`: `pipeline_definition` (Data, `in_standard_filter`), `fiscal_year` (Int, `in_standard_filter`), `fiscal_period` (Int), `scope` (Data), then a Column Break (`params_col_break`) and the two flags `full_refresh` (Check, default 0), `skip_sync` (Check, default 0). These mirror `plan.build_plan` param keys (`skip_sync`, `full_refresh`, `scope`→`select`, `fiscal_year`/`fiscal_period`→`vars`). `pipeline_run.json` has **no `field_order`** — plain `fields` list (like `pipeline_step.json`), so fields were inserted positionally; all pre-existing fields untouched. Test: `konsol/tests/test_orchestrator_pipelinerun_doctype.py` (10 tests) loads the JSON and asserts field presence + fieldtypes + labels; no frappe needed.

**PRD-6 (Run Step doctype fields):** `konsol/pipeline/doctype/pipeline_step/pipeline_step.json` now carries the orchestrator per-step persistence fields appended after `output`: `step_id` (Data), `step_type` (Data), `depends_on` (Small Text, JSON array of upstream ids), `params` (Code/options JSON), `retry_count` (Int, default 0), `started_at` (Datetime), `ended_at` (Datetime), `error` (Small Text). All `read_only: 1`. Pre-existing grid fields (`stage`, `step`, `status`, `rows`, `duration`, `output`) untouched. The doctype has **no `field_order`** — it's a plain `fields` list, so new fields were just appended. Test: `konsol/tests/test_orchestrator_runstep_doctype.py` (12 tests) loads the JSON with `json.load` and asserts field presence + fieldtypes; no frappe needed.

**Module map / public API already built:**
- `executor.py` — `StepContext(step)` (exposes `.step` + `.params`); `Executor(registry, sink=None)` drives a `RunState` to a settled state via `.run(state) -> state`. Loop: while not `is_done()` and not cancelled, take the first `state.runnable()`, mark `Running`, build `StepContext`, call `registry.get(step.type)(ctx)`, mark `Success`/`Failed` from `result.ok`, notify sink. Handler exceptions → `StepResult(ok=False, error=str(exc))` (no crash). `registry` = anything with `.get(type)` (the `handlers` module works directly). `sink` is optional/duck-typed: `on_step_start(step)`, `on_step_result(step, result)` (both checked via `hasattr`). `.cancel()` sets a flag (`.cancelled` property) that stops launching new steps. Failure leaves descendants blocked (state machine handles it). Pure-python; **real Frappe sink + enqueue binding is PRD-9.**
- `dag.py` — `Step(id, type, depends_on=[], params={})`; `Dag(steps)` with `.get(id)`, `.roots()`, `.dependents(id)`, `.descendants(id)`, `.toposort()`; raises `DagError` on cycle / unknown dep / duplicate id.
- `plan.py` — `DEFAULT_DEFINITION` (extract→seed→silver→gold→assertions→signoff); `build_plan(definition, params)` applies `skip_sync` (drop airbyte + rewire), `full_refresh` (on dbt_run/build), `scope`→`select` (transforms), `fiscal_year`/`fiscal_period`→`vars`. Non-mutating. Type sets: `DBT_TYPES`, `DBT_TRANSFORM_TYPES`, `DBT_INCREMENTAL_TYPES`, `VARS_TYPES`.
- `state.py` — `Status` (Pending/Running/Success/Failed/Skipped/Cancelled); `RunState(dag, statuses=None)` with `.status(id)`, `.mark(id, s)`, `.runnable()`, `.running()`, `.failed()`, `.has_failed()`, `.is_done()`, `.is_success()`, `.retry(id)`, `.resume_from(id)`, `.snapshot()`. Skipped deps satisfy dependents; failed deps block descendants.
- `handlers.py` — `StepResult(ok, rows=0, log="", error="")` dataclass; `Handler` runtime-checkable Protocol `__call__(ctx)->StepResult`; `register(type)` decorator (raises `ValueError` on dup) over `_REGISTRY`; `get(type)` (raises `KeyError` if missing); `registered_types()`; `BUILTIN_TYPES` tuple — all 7 built-in types registered as **real (PRD-8)** handlers. `build_dbt_command(verb, params)->argv` (pure) + `DBT_VERB_BY_TYPE`; handlers build their command and delegate execution to an optional `ctx.runner` (none on host → return the planned command, `ok=True`). Tolerant ctx accessors (`StepContext` or bare dict). Pure-python; **the real `runner` (subprocess/Airbyte/frappe write-back of `last_sync_at`) is injected in PRD-9.**

## Next
**PRD-9 — Frappe executor binding.** Create `konsol/orchestrator/run.py` with an enqueue-able `run_pipeline(run_name)` that ties the pure core to Frappe: load the Pipeline Run doc, read its run params (PRD-7 fields: `pipeline_definition`, `fiscal_year`, `fiscal_period`, `scope`, `full_refresh`, `skip_sync`) into a params dict, call `plan.build_plan(DEFAULT_DEFINITION, params)`, build a `Dag` + `RunState`, then drive `Executor(handlers, sink=<FrappeSink>)`. The **FrappeSink** is the observer the executor already supports (`on_step_start(step)` / `on_step_result(step, result)`): it should upsert/update the Pipeline Run's child rows (the PRD-6 `pipeline_step` fields: `step_id`, `step_type`, `status`, `started_at`, `ended_at`, `rows`, `output`, `error`, `retry_count`) and `frappe.publish_realtime(...)` for live UI. This is where the **`runner`** that PRD-8 handlers expect gets injected onto the `StepContext` — i.e. extend `StepContext` (or pass a per-run runner) so dbt handlers actually shell out to the dbt CLI in the bench/dbt-project dir and `airbyte_sync` triggers Airbyte + writes back `last_sync_at`. Keep frappe imports **inside functions**; guard any frappe-dependent test with `frappe = pytest.importorskip("frappe")` (container smoke test) so the host suite stays green. RED first (e.g. `konsol/tests/test_orchestrator_run.py` — at minimum assert `run_pipeline` exists/imports without frappe at module load, params-mapping is pure, and a fake-frappe sink records the right child-row updates). GREEN, regress `python3 -m pytest konsol/tests/test_orchestrator_*.py -q`, commit konsol#57 format, tick PRD-9, update this file.

Tip: re-read `executor.py` (the `sink` duck-typing + `StepContext` — you'll add `.runner` here), `handlers.py` (handlers expect `ctx.runner(argv) -> StepResult`; with no runner they only *build* the command, so PRD-9 must inject a real runner to actually execute), and `plan.py` (param keys: `skip_sync`, `full_refresh`, `scope`, `fiscal_year`, `fiscal_period`). The PRD-6/PRD-7 doctype field names are documented above under their Current-state entries.
