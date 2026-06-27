# konsol-exec Orchestrator — PRD backlog (ralph loop)

Design: `open_epm/docs/developer-guide/design/konsol-exec-orchestrator.md` · Epic konsol#56 (P1 #57 / P2 #58 / P3 #59).

**Loop protocol (ralph):** pick the first unchecked PRD → write failing test (TDD) → implement → tests green → commit → check it off → repeat. Pure-python core runs on host `pytest`; frappe-bound pieces get container smoke tests.

## P1 — Step engine over the existing pipeline (#57)

- [x] **PRD-1 — DAG core.** `orchestrator/dag.py`: `Step(id,type,depends_on,params)`, build graph, `toposort()`, cycle detection, validation (unknown dep, dup id). Pure-python.
- [x] **PRD-2 — Run-plan resolution.** `orchestrator/plan.py`: `build_plan(definition, params)` → ordered steps; `skip_sync` drops `airbyte_sync`; `full_refresh` sets flag on dbt steps; `scope`→`select`; fiscal_year/period→`vars`. Pure-python.
- [x] **PRD-3 — Execution state machine.** `orchestrator/state.py`: from step statuses compute `runnable()`, `is_done()`, `failed()`; `retry(step)` resets a failed step + its descendants; `resume_from(step)`. Pure-python.
- [x] **PRD-4 — Handler registry.** `orchestrator/handlers.py`: `register(type)` decorator + `get(type)`; `Handler` protocol `run(ctx)->StepResult`; built-in types registered as stubs. Pure-python.
- [x] **PRD-5 — Executor (pure core).** `orchestrator/executor.py`: `Executor(registry, sink)` drives state machine: run next runnable, record result, stop downstream on failure, honor cancel. Injected registry+sink → fully unit-testable. Pure-python.
- [x] **PRD-6 — Run Step doctype fields.** Extend `pipeline_step`: `step_id`, `step_type`, `depends_on`, `params(JSON)`, `retry_count`, `started_at`, `ended_at`, `error`. JSON field-presence test.
- [x] **PRD-7 — Pipeline Run params.** Add to `pipeline_run`: `fiscal_year`, `fiscal_period`, `scope`, `full_refresh`, `skip_sync`, `pipeline_definition`. JSON field-presence test.
- [x] **PRD-8 — Real handlers (command building).** `airbyte_sync` (writes back `last_sync_at`), `dbt_seed/run/build/test` (select/full_refresh/vars), `close_assertions`, `signoff`. Test the pure dbt-command builder.
- [x] **PRD-9 — Frappe executor binding.** `orchestrator/run.py`: `run_pipeline(run_name)` enqueued — load run, `build_plan`, drive `Executor` with a Frappe sink (update child rows + `publish_realtime`). Container smoke test.
- [x] **PRD-10 — Whitelisted API.** `start_run(definition, params)`, `retry_step`, `resume_run`, `cancel_run`. Whitelist/signature presence test.
- [ ] **PRD-11 — SPA param form + timeline.** konsol-exec: launch form (year/period/scope/flags) + step timeline w/ live logs + retry/resume/cancel. JS presence test.

## P2 — Definitions, schedules, resume (#58)

- [ ] **PRD-12 — Pipeline Definition + Step Definition doctypes** (DAG template).
- [ ] **PRD-13 — Definition→plan loader** (replace hardcoded plan with definition-driven).
- [ ] **PRD-14 — Scheduling** (Frappe Scheduler cron → enqueue run).
- [ ] **PRD-15 — Resume-from-step UI/API** as first-class action on finished runs.

## P3 — Lineage, resources, FX, multi-ERP (#59)

- [ ] **PRD-16 — Per-step metrics + lineage** (rows/durations, step→model map).
- [ ] **PRD-17 — Resource/Connection management** (Airbyte/dbt/CH as doctypes + UI).
- [ ] **PRD-18 — FX surfacing** (konsolidat#91 B/C: surface step + read view).
- [ ] **PRD-19 — Multi-ERP** (multiple extract sources → one transform DAG).
