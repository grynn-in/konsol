# Orchestrator hardening (konsol) — durable handoff (read me first)

You are a fresh agent. This file + `ORCH-PRDS.md` are the source of truth. Do **exactly one PRD** (or one review/fix pass), then stop.

## Goal
Close residual orchestrator follow-ups from the ×2 reviews of merged PRs #60/#66/#69 (issues #65, #64, #67, #70). Frappe app `konsol` (repo grynn-in/konsol). Pure-python orchestrator core + thin Frappe shell.

## Where to work
- **Worktree (cwd):** `/home/pd/konsol-wt-hardening` — branch `feat/orchestrator-hardening` (off origin/main; never commit to main).
- **Orchestrator package:** `konsol/orchestrator/` — key files:
  - `run.py` — `plan_run(params, definition=None)` (line ~81) builds the DAG via `build_plan(DEFAULT_DEFINITION if definition is None else definition, ...)`.
  - `plan.py` — `DEFAULT_DEFINITION` (list of Steps), `build_plan`.
  - `definition.py` — `load_definition(name) -> List[Step]` **already exists** (resolves a Pipeline Definition doctype → Steps). The #65 gap is that the name coming from `start_run(definition="Group Close")` isn't run through `load_definition` before `build_plan`.
  - `api.py` — `start_run(definition=None, params=None)` (~160) → `_assert_no_active_run()` (~96, a SELECT-then-INSERT single-flight that is TOCTOU; comments at ~46/86 explain prior #66/#67 fixes).
  - `reaper.py` — `reap_stale_runs()` (~54) decides staleness from the run's modified time; #70 wants an intra-step heartbeat.

## TDD harness (host)
- `cd /home/pd/konsol-wt-hardening && python3 -m pytest konsol/tests/ -q`. Tests are host-run; frappe-dependent tests `importorskip`/skip without a bench — that's expected. New tests must run on host: mock `frappe.db`/`frappe.get_doc` where the logic touches the DB, and unit-test the **pure decision logic** (which definition is loaded, single-flight refusal, staleness/heartbeat math, cancel transitions).
- **Strict TDD:** failing test first (confirm RED), implement, GREEN, full suite stays green (skips are fine), commit.
- Existing orchestrator tests to mirror: `konsol/tests/test_orchestrator_*.py`.

## Optional container smoke (for the concurrency PRD)
Host tests can't exercise real DB locking. Where a PRD's correctness depends on DB behavior, ALSO note in the commit/PR how you verified (or that it needs a bench smoke test). If feasible: `docker cp konsol/orchestrator/<file> konsolidat_backend:/home/frappe/frappe-bench/apps/konsol/konsol/orchestrator/` then `docker exec konsolidat_backend bench --site konsolidat.local execute <fn>` — but DO NOT leave the container patched; primary verification is host pytest.

## Conventions
- Backward compatible: keep `DEFAULT_DEFINITION` as the fallback when no definition is named; don't change public signatures.
- Idiomatic Frappe locking: prefer `frappe.db.get_value(..., for_update=True)` / a DB unique constraint / `frappe.db.sql(... FOR UPDATE)` over advisory hacks; explain the atomicity argument in a comment.
- Type hints + docstrings; match existing style.
- One PRD per commit (+ a docs commit ticking the PRD and repointing this file's *Current state*/*Next*).
- Commit subject `feat(orchestrator): B-N <title>` / `fix(orchestrator): B-N <title>` + `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Loop protocol
**Build:** first unchecked PRD in `ORCH-PRDS.md`. RED→GREEN→full-suite-green→commit→tick `[x]`→update this file→commit docs→STOP. Return the status object.
**Review:** independent, adversarial. Read the PRD's diff (`git show`), RE-RUN the suite, scrutinize the concurrency/atomicity argument (does the guard actually close the TOCTOU window? is the heartbeat updated where claimed? are tests real, not shape-only?), backward compat. Return blocking findings (empty = approved). Don't edit.
**Fix:** apply blocking findings, keep green, commit `fix(orchestrator): B-N address review`, STOP.

## Current state
B1 (#65) DONE — commit `1cfe7bd`. `run.plan_run(params, definition=...)` is now the single definition-resolution point: a definition **name** (str) is resolved to Steps via `definition.load_definition`; `None`->`DEFAULT_DEFINITION`; an explicit `List[Step]` is used verbatim (backward compatible, signature unchanged). `run.run_pipeline` no longer pre-loads — it passes `params.get("pipeline_definition")` straight to `plan_run`. Only the str branch touches frappe (function-local import), pure branches stay host-importable. Tests: 5 new `plan_run` cases in `test_orchestrator_run_binding.py` (mock `load_definition` via monkeypatch — function-local import re-resolves the patched attr) + updated source-check in `test_orchestrator_definition_loader.py::test_run_pipeline_loads_definition_when_set` (resolution moved to plan_run). Orchestrator suite: 88 passed / 2 skipped. Pre-existing, unrelated failures remain on host (not touched by B1): `test_cash_flow_category` (autoname stale), `test_pipeline_run` status options, and 2 collection errors from modules importing `konsol.api` (top-level `frappe.utils`).
## Next
B2 (#64+#67+#70) — concurrency hardening: single-flight TOCTOU (atomic guard in `api._assert_no_active_run`/`single_flight_lock`), cancel race, reaper intra-step heartbeat, host concurrency tests (mock `frappe.db`). See `ORCH-PRDS.md` B2.
