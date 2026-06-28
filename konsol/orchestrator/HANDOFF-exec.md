# konsol-exec orchestration plane — durable handoff (read me first)

You are a fresh agent enhancing the **existing `konsol-exec` Vite SPA** into the *single* execution plane (Airbyte/Press-style) that drives complete orchestrator runs. You start with no prior memory — this file + `PRDS-exec.md` are the source of truth. Do **exactly one PRD**, then update this file for the next agent.

## Goal (why this exists)
The orchestrator BACKEND is built and works (`konsol/orchestrator/*`, doctypes Pipeline Run/Step + Pipeline Definition/Schedule, whitelisted API `konsol.orchestrator.api.start_run/retry_step/resume_run/cancel_run`, live `orchestrator_step` realtime). A previous mistake built a throwaway **Desk page** at `/app/konsol-exec`; we are deleting that and instead enhancing the real SPA at **`konsol-exec/`** (served at `/konsol-exec/`) to: launch a run with params, show a **live stepped timeline** (status pills, durations, rows, logs), and **retry/resume/cancel** — one well-organized view.

## Where things are
- **Working dir:** `/home/pd/open_epm/docker/frappe/konsol`  (branch `feat/orchestrator-p1`, never commit to main)
- **The SPA:** `konsol-exec/src/` — Vite + React + XState. Key files: `api.js` (backend wrapper `frappeCall(method,args)`), `App.jsx` (nav: sections + subviews setup/monitor/history via `konsolAppMachine`), `machines/konsolAppMachine.js` + `machines/runDetailMachine.js`, `components/*.jsx` (Monitor, RunDetail, History, …), `constants.js`, `domain.js`.
- **New pure core you add:** `konsol-exec/src/orchestrator/*.js` (ESM, **no React import** — pure functions only).
- **Backend API (already there):** `konsol/orchestrator/api.py` — `start_run(definition, params)`, `retry_step(run_name, step_id)`, `resume_run(run_name, step_id)`, `cancel_run(run_name)`. Pipeline Run child table `steps` rows carry: `step_id, step_type, status, started_at, ended_at, rows, output, error`. Status vocab: Pending/Running/Success/Failed/Skipped/Cancelled.

## TDD harness (two kinds — match the PRD)
1. **Pure ESM core** (`konsol-exec/src/orchestrator/*.js`): write tests as `konsol-exec/src/orchestrator/<name>.test.mjs` using `node:test` + `node:assert`. Run from `konsol-exec/`: `node --test src/orchestrator/*.test.mjs`. Confirm RED first. **No npm install needed.**
2. **React / machine / api wiring** (can't build in-loop): write a **static-assertion** Python test in `konsol/tests/test_exec_<area>.py` that reads the source file(s) and asserts the required exports/JSX/strings/imports/method names are present (this matches the repo's existing `test_orchestrator_spa_js.py` convention). Run: `python3 -m pytest konsol/tests/test_exec_*.py -q` from the konsol root. Keep the real LOGIC in the pure ESM core so components stay thin.
3. **Backend additions** (if a PRD adds to `api.py`): guard frappe tests with `import pytest; frappe = pytest.importorskip("frappe")`.

Do NOT run `vite build` (node_modules may be absent / installing) — it is the maintainer's final gate, not yours.

## Loop protocol (one PRD, strict TDD)
1. Open `PRDS-exec.md`, take the first unchecked `- [ ] **E-N`.
2. RED: write the test (node --test for pure core, or pytest static-assert for wiring); run it; confirm it fails.
3. GREEN: implement; iterate until that test passes.
4. REGRESS: run BOTH suites — `cd konsol-exec && node --test src/orchestrator/*.test.mjs` (if any pure tests exist) AND `python3 -m pytest konsol/tests/test_exec_*.py konsol/tests/test_orchestrator_*.py -q`. All green.
5. COMMIT (format below), tick the PRD `- [x]` in `PRDS-exec.md`, rewrite this file's **Current state** + **Next**, commit docs too.
6. STOP.

## Conventions
- Commit subject: `feat(konsol-exec): E-N <short title> (konsol#60)` + body + `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Pure core = no React, no fetch, no frappe — just data in/out (testable with node --test).
- Reuse existing app patterns (XState events, `frappeCall`, component style). Don't add new heavy deps.
- Bash safety: no `$()` inside other commands' args.
- Scope: only `konsol-exec/`, `konsol/orchestrator/api.py`, `konsol/tests/test_exec_*.py`, and (E9) removing the Desk page. Do NOT touch dbt_project or other repos.

## Current state (update me each PRD)
**Done:**
- **E1 — Run-params builder** (commit on `feat/orchestrator-p1`). New pure-ESM core dir `konsol-exec/src/orchestrator/`. `params.js` exports `buildRunArgs(form) -> {definition, params}` (definition or null; fiscal_year/fiscal_period/scope only when truthy; full_refresh/skip_sync always 0/1 ints) — mirrors the old Desk page `collect_params`. Unit test `params.test.mjs` (5 tests, node:test) green: empty form, full form, checkbox truthiness, omitted empty strings, definition fallback.

Backend orchestrator + P1/P2/P3 already complete on this branch. The two exec-plane planning docs (`HANDOFF-exec.md`, `PRDS-exec.md`) originated on `main` (commit 3b19ad5); they were brought onto `feat/orchestrator-p1` alongside E1 so PR konsol#60 is self-contained.

**Harness note:** `cd konsol-exec && node --test src/orchestrator/*.test.mjs` runs the pure core suite (no npm install needed). Python regress: `python3 -m pytest konsol/tests/test_orchestrator_*.py -q` (no `test_exec_*.py` exist yet — E4+ will add them).

## Next
**E2 — Status model.** Create `konsol-exec/src/orchestrator/status.js` (pure ESM, no React) exporting:
- `statusTone(status)`: Success→`green`, Failed/Cancelled→`red`, Running→`blue`, Queued→`amber`, Pending/Skipped→`gray`, default→`gray`.
- `isTerminal(status)`: true for Completed/Failed/Cancelled/Success.
- `isRunning(status)`: true for Running.

Test `konsol-exec/src/orchestrator/status.test.mjs` (node --test): assert every status string maps to the right tone, and the terminal/running booleans for each status. RED first (`cd konsol-exec && node --test src/orchestrator/*.test.mjs`), then implement, then GREEN. Backend status vocab is Pending/Running/Success/Failed/Skipped/Cancelled (see file map above); also tolerate `Queued`/`Completed` since the realtime layer uses them. After GREEN, regress both suites, commit `feat(konsol-exec): E2 status model (konsol#60)`, tick E2 in `PRDS-exec.md`, and repoint this Next section at **E3 — Run view-model** (`runModel.js`: `normalizeRun`/`progressPct`/`orderSteps`).
