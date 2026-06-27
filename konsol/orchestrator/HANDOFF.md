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
**Done:** PRD-1, PRD-2, PRD-3, PRD-4. Suite: **30 tests passing.**

**Module map / public API already built:**
- `dag.py` — `Step(id, type, depends_on=[], params={})`; `Dag(steps)` with `.get(id)`, `.roots()`, `.dependents(id)`, `.descendants(id)`, `.toposort()`; raises `DagError` on cycle / unknown dep / duplicate id.
- `plan.py` — `DEFAULT_DEFINITION` (extract→seed→silver→gold→assertions→signoff); `build_plan(definition, params)` applies `skip_sync` (drop airbyte + rewire), `full_refresh` (on dbt_run/build), `scope`→`select` (transforms), `fiscal_year`/`fiscal_period`→`vars`. Non-mutating. Type sets: `DBT_TYPES`, `DBT_TRANSFORM_TYPES`, `DBT_INCREMENTAL_TYPES`, `VARS_TYPES`.
- `state.py` — `Status` (Pending/Running/Success/Failed/Skipped/Cancelled); `RunState(dag, statuses=None)` with `.status(id)`, `.mark(id, s)`, `.runnable()`, `.running()`, `.failed()`, `.has_failed()`, `.is_done()`, `.is_success()`, `.retry(id)`, `.resume_from(id)`, `.snapshot()`. Skipped deps satisfy dependents; failed deps block descendants.
- `handlers.py` — `StepResult(ok, rows=0, log="", error="")` dataclass; `Handler` runtime-checkable Protocol `__call__(ctx)->StepResult`; `register(type)` decorator (raises `ValueError` on dup) over `_REGISTRY`; `get(type)` (raises `KeyError` if missing); `registered_types()`; `BUILTIN_TYPES` tuple — all 7 built-in types pre-registered as no-op stubs returning `StepResult(ok=True)`. **Stub bodies are placeholders — real command building lands in PRD-8.** Pure-python.

## Next
**PRD-5 — Executor (pure core).** `konsol/orchestrator/executor.py`: an `Executor(registry, sink)` that drives the state machine to completion. Inject the handler registry (use `handlers.get` or a registry object with a `.get(type)`) and a `sink` (an observer object the executor calls to report progress — e.g. `sink.on_step_start(step)`, `sink.on_step_result(step, result)`; keep it a tiny duck-typed interface so a no-op/recording fake works in tests). Loop: while not `state.is_done()`, pick the next `state.runnable()` step, mark it `Running`, call its handler with a `ctx` (give the handler access to the step, e.g. `ctx.step` / `ctx.params`), record the `StepResult` → mark `Success`/`Failed` from `result.ok`, notify the sink. On failure, downstream stays blocked (state machine already does this) — executor must NOT crash, just settle. Honor cancellation: expose a way to cancel (e.g. `executor.cancel()` or a `cancelled` flag / checking `state` for `Cancelled`) so the loop stops launching new steps. Build on `dag.py` + `state.py` + `handlers.py`; pure-python (no frappe). Tests in `konsol/tests/test_orchestrator_executor.py`: happy-path runs all steps to Success via a recording sink + fake registry; a failing handler settles the run with descendants left Pending/blocked and `has_failed()` true; sink receives start/result callbacks in order; cancel stops further steps. Real Frappe sink + enqueue binding is PRD-9 — keep this file frappe-free.
