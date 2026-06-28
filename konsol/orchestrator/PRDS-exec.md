# konsol-exec orchestration plane — PRD backlog (ralph loop)

Goal: enhance the **existing `konsol-exec` Vite SPA** into the single execution plane that runs complete orchestrator runs (launch → live stepped timeline → retry/resume/cancel), Airbyte/Press-style; retire the throwaway Desk page. Context + harness + loop protocol: `HANDOFF-exec.md`. Branch `feat/orchestrator-p1`, PR konsol#60.

Two test kinds: **pure ESM core** → `node --test` (real unit tests); **React/machine/api wiring** → Python static-assertion in `konsol/tests/test_exec_*.py`. Keep logic in the pure core.

## Pure core (node --test)
- [x] **E1 — Run-params builder.** `konsol-exec/src/orchestrator/params.js` → `buildRunArgs(form) -> {definition, params}` (definition or null; fiscal_year/fiscal_period/scope only when truthy; full_refresh/skip_sync always 0/1). Test `params.test.mjs`.
- [x] **E2 — Status model.** `konsol-exec/src/orchestrator/status.js` → `statusTone(status)` (Success→green, Failed/Cancelled→red, Running→blue, Queued→amber, Pending/Skipped→gray, default gray), `isTerminal(status)` (Completed/Failed/Cancelled/Success), `isRunning(status)`. Test `status.test.mjs` (every status → tone; terminal/running booleans).
- [x] **E3 — Run view-model.** `konsol-exec/src/orchestrator/runModel.js` → `normalizeRun(doc) -> {name, status, steps:[{id,type,status,startedAt,endedAt,rows,output,error}]}` (map child rows; tolerate missing fields), `progressPct(steps)` (terminal-success / total, 0 when empty), `orderSteps(steps)` (stable input order). Test `runModel.test.mjs` (normalize a sample doc; progress math; empty doc).

## API + realtime
- [x] **E4 — Backend get_run + SPA api client.** Add whitelisted `get_run(run_name)` to `konsol/orchestrator/api.py` returning `{name, status, steps:[...]}` (frappe-guarded test). In `konsol-exec/src/api.js` add `startRun(definition, params)`, `getRun(name)`, `retryStep(name, stepId)`, `resumeRun(name, stepId)`, `cancelRun(name)` via `frappeCall("konsol.orchestrator.api.<fn>", ...)`, plus `onRunStep(cb)` wrapping `frappe.realtime?.on("orchestrator_step", cb)` (guard when realtime absent). Static-assert: each api.js fn exists and references the right backend method string; pytest-guard the python `get_run`.

## Machine + React (static-assert; logic delegates to pure core)
- [x] **E5 — Exec state machine.** `konsol-exec/src/machines/runExecMachine.js` (XState) with context `{run, error}` and events `LAUNCH` (invoke startRun → WATCH), `REFRESH`/realtime → getRun→normalizeRun, `RETRY_STEP`, `RESUME_FROM`, `CANCEL`, settling on terminal status. Export from `machines/index.js`. Static-assert states/events/actor names + that it imports the pure core + api.
- [ ] **E6 — Launch panel.** `konsol-exec/src/components/ExecuteLaunch.jsx` — form (Fiscal Year/Period, Scope, Pipeline Definition, Full Refresh + Skip Airbyte Sync checks) + "Start Run" → `buildRunArgs` → machine `LAUNCH`. Static-assert fields + buildRunArgs use + Start Run action.
- [ ] **E7 — Live step timeline.** `konsol-exec/src/components/RunTimeline.jsx` — Press/Airbyte-style: run header (name + status pill), one card per step (id, type, `statusTone` pill, started→ended, rows, output/error) + per-step Retry/Resume and a Cancel control; consumes `normalizeRun`/`progressPct`; refreshes off `onRunStep`. Static-assert step rendering + the 3 controls + core usage.
- [ ] **E8 — Wire into the app as the "Execute" view.** Add an `execute` subview to `App.jsx` (+ `DomainSubNav` tab + `constants.js`) rendering `ExecuteLaunch` + `RunTimeline` under the `runExecMachine`. Static-assert App imports/renders both + the subview is registered.

## Retire the wrong surface
- [ ] **E9 — Delete the Desk page.** Remove `konsol/pipeline/page/konsol_exec/` (the PRD-11 Desk page) and its `konsol/tests/test_orchestrator_spa_js.py`; assert the dir/files are gone and nothing references `pages/konsol_exec` or `frappe.pages["konsol-exec"]`. (The real plane is the Vite SPA at `/konsol-exec/`.)

## Maintainer gate (not an agent PRD)
- vite build verification (`npm run build` in konsol-exec) + redeploy after merge.
