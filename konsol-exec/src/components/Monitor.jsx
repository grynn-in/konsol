import * as React from "react";
import { STATUS } from "../constants";
import { getDomainMeta, getProcess } from "../domain";

export function Monitor({ domain, data, onAction }) {
	const meta = getDomainMeta(domain);
	const proc = getProcess(data, domain);
	if (!proc) return null;

	const run = proc.run || {};
	const st = STATUS[proc.machine_status] || STATUS.idle;

	const steps = (run.steps || []).map((step, i) => {
		const dot =
			step.state === "running" ? (
				<div className="kc-spin" />
			) : (
				<span
					className="kc-step-dot"
					style={{
						background:
							step.state === "done"
								? "var(--green)"
								: step.state === "error"
									? "var(--red)"
									: "transparent",
						border: step.state === "pending" ? "2px solid var(--bd2)" : "none",
						color: "#fff",
					}}
				>
					{step.state === "done" ? "✓" : step.state === "error" ? "✗" : ""}
				</span>
			);

		return (
			<div className="kc-step-row" key={`${step.num}-${i}`}>
				<div className="kc-step-head">
					{dot}
					<div className="kc-col-flex">
						<span className="kc-step-num">{step.num}</span>
						<strong className="kc-step-name">{step.name}</strong>
						<div className="kc-muted">{step.detail || ""}</div>
					</div>
					<span className="kc-muted">{step.rows || ""}</span>
					<span className="kc-step-pct">{step.pct ? `${step.pct}%` : ""}</span>
				</div>
				<div className="kc-bar kc-bar-step">
					<div
						className="kc-bar-fill"
						style={{
							width: `${step.pct || 0}%`,
							background:
								step.state === "error"
									? "var(--red)"
									: step.state === "done"
										? "var(--green)"
										: meta.accent,
						}}
					/>
				</div>
				{step.error ? <div className="kc-step-error">{step.error}</div> : null}
			</div>
		);
	});

	const logs = (run.logs || []).map((l, i) => {
		const col =
			l.level === "error"
				? "#ff6b6b"
				: l.level === "ok"
					? "#5fd99a"
					: l.level === "warn"
						? "#f0b13b"
						: "#7db8f0";
		return (
			<div key={i}>
				<span className="kc-log-t">{l.t} </span>
				<span style={{ color: col }}>{l.text}</span>
			</div>
		);
	});

	let primary = "Start run";
	if (proc.machine_status === "running") primary = "❚❚ Pause (view only)";
	else if (proc.machine_status === "error") primary = "↻ Retry — start new run";
	else if (!proc.runnable) primary = "⚠ Resolve setup";

	return (
		<div className="kc-domain-space" style={{ "--domain-accent": meta.accent }}>
			<div className="kc-card kc-monitor-head">
				<div>
					<div className="kc-monitor-title">
						<span>{meta.label} monitor</span>
						<span className="kc-pill" style={{ color: st.color, background: st.bg }}>
							{st.label}
						</span>
					</div>
					<div className="kc-muted">
						{run.step_done || 0} / {run.step_total || 0} steps · {run.name || "No run yet"}
					</div>
				</div>
				<button
					type="button"
					className="kc-btn kc-btn-primary"
					style={
						!proc.runnable && proc.machine_status === "idle"
							? { background: "var(--amber)" }
							: { background: meta.accent }
					}
					onClick={() => onAction(domain, primary, proc)}
				>
					{primary}
				</button>
			</div>
			<div className="kc-table kc-monitor-steps">
				{steps.length ? (
					steps
				) : (
					<div className="kc-row kc-empty">No active {meta.label.toLowerCase()} run</div>
				)}
			</div>
			<div className="kc-section-label">{meta.label} console</div>
			<div className="kc-console">
				{logs.length ? (
					logs
				) : (
					<div className="kc-muted">Waiting for run output…</div>
				)}
			</div>
		</div>
	);
}