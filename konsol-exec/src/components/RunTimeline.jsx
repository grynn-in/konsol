import * as React from "react";
import { statusTone } from "../orchestrator/status";
import { normalizeRun, progressPct } from "../orchestrator/runModel";
import { onRunStep } from "../api";

/**
 * RunTimeline — the Press/Airbyte-style live step timeline for the konsol-exec
 * execution plane.
 *
 * Renders a single orchestrator run: a header (run name + a status pill via
 * `statusTone`), a progress indicator (`progressPct`), and one card per step
 * (id, type, a `statusTone` pill, started→ended, rows, output/error). Each step
 * exposes **Retry** (`RETRY_STEP`) and **Resume** (`RESUME_FROM`) controls; a
 * run-level **Cancel** dispatches `CANCEL`. A realtime subscription via
 * `onRunStep` drives a `RUN_STEP` refresh as `orchestrator_step` events land.
 *
 * All data shaping lives in the pure ESM core (`normalizeRun`/`progressPct`/
 * `statusTone`); this component just wires the view-model to the machine.
 *
 * @param {{ run: object, send: Function, accent?: string }} props `run` is the
 *   normalized view-model from the machine context. Re-normalized defensively
 *   so the component also tolerates a raw doc.
 */
export function RunTimeline({ run, send, accent }) {
	// Refresh off realtime: each orchestrator_step event nudges the machine.
	React.useEffect(() => {
		if (typeof send !== "function") return undefined;
		const off = onRunStep(() => send({ type: "RUN_STEP" }));
		return off;
	}, [send]);

	if (!run) return null;

	// Tolerate either a normalized view-model or a raw doc.
	const model =
		Array.isArray(run.steps) && run.steps.length && "id" in run.steps[0]
			? run
			: normalizeRun(run);

	const steps = model.steps || [];
	const pct = progressPct(steps);
	const headTone = statusTone(model.status);

	return (
		<div className="kc-run-timeline" style={{ "--domain-accent": accent }}>
			<div className="kc-run-timeline-head">
				<div className="kc-col-flex">
					<div className="kc-run-detail-id kc-mono">{run.name}</div>
					<div className="kc-muted">{steps.length} steps</div>
				</div>
				<span className={`kc-pill kc-tone-${headTone}`}>{model.status || "—"}</span>
				<button
					type="button"
					className="kc-btn kc-btn-ghost kc-btn-sm"
					onClick={() => send && send({ type: "CANCEL" })}
				>
					Cancel
				</button>
			</div>

			<div className="kc-progress" role="progressbar" aria-valuenow={Math.round(pct)}>
				<div className="kc-progress-track">
					<div
						className="kc-progress-bar"
						style={{ width: `${pct}%`, background: accent }}
					/>
				</div>
				<span className="kc-progress-label">{Math.round(pct)}%</span>
			</div>

			<div className="kc-table kc-timeline-steps">
				{steps.length ? (
					steps.map((step, i) => {
						const tone = statusTone(step.status);
						return (
							<div className="kc-step-card" key={`${step.id}-${i}`}>
								<div className="kc-step-head">
									<div className="kc-col-flex">
										<strong className="kc-step-name">{step.id}</strong>
										<span className="kc-muted">{step.type}</span>
									</div>
									<span className={`kc-pill kc-tone-${tone}`}>
										{step.status || "—"}
									</span>
								</div>
								<div className="kc-step-meta kc-muted">
									<span>
										{step.startedAt || "—"} → {step.endedAt || "—"}
									</span>
									<span>{step.rows} rows</span>
								</div>
								{step.output ? (
									<div className="kc-step-output">{step.output}</div>
								) : null}
								{step.error ? (
									<div className="kc-step-error">{step.error}</div>
								) : null}
								<div className="kc-step-actions">
									<button
										type="button"
										className="kc-btn kc-btn-ghost kc-btn-sm"
										onClick={() =>
											send && send({ type: "RETRY_STEP", stepId: step.id })
										}
									>
										Retry
									</button>
									<button
										type="button"
										className="kc-btn kc-btn-ghost kc-btn-sm"
										onClick={() =>
											send && send({ type: "RESUME_FROM", stepId: step.id })
										}
									>
										Resume
									</button>
								</div>
							</div>
						);
					})
				) : (
					<div className="kc-row kc-empty">No steps yet</div>
				)}
			</div>
		</div>
	);
}
