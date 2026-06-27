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
**Done:** PRD-1, PRD-2, PRD-3, PRD-4, PRD-5, PRD-6, PRD-7. Suite: **61 tests passing.**

**PRD-7 (Pipeline Run params):** `konsol/pipeline/doctype/pipeline_run/pipeline_run.json` now carries a new "Run Parameters" section (inserted right before the `extract_section` Section Break) with the run-level fields the orchestrator feeds into `build_plan`: `pipeline_definition` (Data, `in_standard_filter`), `fiscal_year` (Int, `in_standard_filter`), `fiscal_period` (Int), `scope` (Data), then a Column Break (`params_col_break`) and the two flags `full_refresh` (Check, default 0), `skip_sync` (Check, default 0). These mirror `plan.build_plan` param keys (`skip_sync`, `full_refresh`, `scope`→`select`, `fiscal_year`/`fiscal_period`→`vars`). `pipeline_run.json` has **no `field_order`** — plain `fields` list (like `pipeline_step.json`), so fields were inserted positionally; all pre-existing fields untouched. Test: `konsol/tests/test_orchestrator_pipelinerun_doctype.py` (10 tests) loads the JSON and asserts field presence + fieldtypes + labels; no frappe needed.

**PRD-6 (Run Step doctype fields):** `konsol/pipeline/doctype/pipeline_step/pipeline_step.json` now carries the orchestrator per-step persistence fields appended after `output`: `step_id` (Data), `step_type` (Data), `depends_on` (Small Text, JSON array of upstream ids), `params` (Code/options JSON), `retry_count` (Int, default 0), `started_at` (Datetime), `ended_at` (Datetime), `error` (Small Text). All `read_only: 1`. Pre-existing grid fields (`stage`, `step`, `status`, `rows`, `duration`, `output`) untouched. The doctype has **no `field_order`** — it's a plain `fields` list, so new fields were just appended. Test: `konsol/tests/test_orchestrator_runstep_doctype.py` (12 tests) loads the JSON with `json.load` and asserts field presence + fieldtypes; no frappe needed.

**Module map / public API already built:**
- `executor.py` — `StepContext(step)` (exposes `.step` + `.params`); `Executor(registry, sink=None)` drives a `RunState` to a settled state via `.run(state) -> state`. Loop: while not `is_done()` and not cancelled, take the first `state.runnable()`, mark `Running`, build `StepContext`, call `registry.get(step.type)(ctx)`, mark `Success`/`Failed` from `result.ok`, notify sink. Handler exceptions → `StepResult(ok=False, error=str(exc))` (no crash). `registry` = anything with `.get(type)` (the `handlers` module works directly). `sink` is optional/duck-typed: `on_step_start(step)`, `on_step_result(step, result)` (both checked via `hasattr`). `.cancel()` sets a flag (`.cancelled` property) that stops launching new steps. Failure leaves descendants blocked (state machine handles it). Pure-python; **real Frappe sink + enqueue binding is PRD-9.**
- `dag.py` — `Step(id, type, depends_on=[], params={})`; `Dag(steps)` with `.get(id)`, `.roots()`, `.dependents(id)`, `.descendants(id)`, `.toposort()`; raises `DagError` on cycle / unknown dep / duplicate id.
- `plan.py` — `DEFAULT_DEFINITION` (extract→seed→silver→gold→assertions→signoff); `build_plan(definition, params)` applies `skip_sync` (drop airbyte + rewire), `full_refresh` (on dbt_run/build), `scope`→`select` (transforms), `fiscal_year`/`fiscal_period`→`vars`. Non-mutating. Type sets: `DBT_TYPES`, `DBT_TRANSFORM_TYPES`, `DBT_INCREMENTAL_TYPES`, `VARS_TYPES`.
- `state.py` — `Status` (Pending/Running/Success/Failed/Skipped/Cancelled); `RunState(dag, statuses=None)` with `.status(id)`, `.mark(id, s)`, `.runnable()`, `.running()`, `.failed()`, `.has_failed()`, `.is_done()`, `.is_success()`, `.retry(id)`, `.resume_from(id)`, `.snapshot()`. Skipped deps satisfy dependents; failed deps block descendants.
- `handlers.py` — `StepResult(ok, rows=0, log="", error="")` dataclass; `Handler` runtime-checkable Protocol `__call__(ctx)->StepResult`; `register(type)` decorator (raises `ValueError` on dup) over `_REGISTRY`; `get(type)` (raises `KeyError` if missing); `registered_types()`; `BUILTIN_TYPES` tuple — all 7 built-in types pre-registered as no-op stubs returning `StepResult(ok=True)`. **Stub bodies are placeholders — real command building lands in PRD-8.** Pure-python.

## Next
**PRD-8 — Real handlers (command building).** Replace the no-op stubs in `konsol/orchestrator/handlers.py` with real command-building logic for the built-in step types: `airbyte_sync` (records/writes back a `last_sync_at`), `dbt_seed` / `dbt_run` / `dbt_build` / `dbt_test` (build the dbt CLI argv honoring `select` (from `scope`), `full_refresh`, and `vars` (from `fiscal_year`/`fiscal_period`) carried on the step's `params`), `close_assertions`, and `signoff`. The handler signature is `__call__(ctx) -> StepResult` where `ctx` is a `StepContext` exposing `.step` and `.params` (see `executor.py`). **The testable core is the pure dbt-command builder** — factor a pure function (e.g. `build_dbt_command(verb, params) -> list[str]`) that maps params → argv (`["dbt", verb, "--select", ...]`, `--full-refresh`, `--vars '{...}'`) so it can be unit-tested on the host with **no frappe**. Keep `handlers.py` pure-python (no top-level `import frappe`); if a handler genuinely needs to touch the DB/subprocess, put that import inside the function and guard any frappe-dependent test with `frappe = pytest.importorskip("frappe")`. Note the design intent (HANDOFF "Module map"): the stub bodies currently return `StepResult(ok=True)`; PRD-8 makes them build/return real commands + row/log info while staying unit-testable. RED first: add tests (e.g. `konsol/tests/test_orchestrator_handlers_commands.py`) asserting the dbt-command builder output for select/full_refresh/vars combinations and that each built-in type still resolves via `handlers.get(type)`. GREEN: implement. Then regress `python3 -m pytest konsol/tests/test_orchestrator_*.py -q` (all green), commit with the konsol#57 format, tick PRD-8, update this file.

Tip: read `konsol/orchestrator/plan.py` first to see exactly which param keys land on a step (`select`, `full_refresh`, `vars`) and the `DBT_TYPES` / `DBT_TRANSFORM_TYPES` / `DBT_INCREMENTAL_TYPES` / `VARS_TYPES` sets — the command builder should mirror those so plan output and handler input stay consistent. Also re-read `handlers.py` for the existing `StepResult`/`register`/`get` shapes and `executor.py` for the `StepContext` (`.params`) contract.
