import * as React from "react";
import { STATUS, PROCESS_IDS } from "../constants";

export function Monitor({ data, selected, onSelect, onAction }) {
	const proc = data?.processes?.[selected];
	if (!proc) return null;

	const pills = PROCESS_IDS.map((id) => {
		const p = data.processes[id];
		const active = id === selected;
		const st = STATUS[p.machine_status] || STATUS.idle;
		return (
			<button
				key={id}
				type="button"
				className="kc-pill-btn"
				style={{
					background: active ? "var(--card)" : "transparent",
					borderColor: active ? "var(--bd2)" : "transparent",
				}}
				onClick={() => onSelect(id)}
			>
				<span className="kc-dot kc-dot-lg" style={{ background: st.color }} />
				{p.name}
			</button>
		);
	});

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
										: proc.accent,
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
		<>
			<div className="kc-pills">{pills}</div>
			<div className="kc-card kc-monitor-head">
				<div>
					<div className="kc-monitor-title">
						<span>{proc.name}</span>
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
							: undefined
					}
					onClick={() => onAction(proc.id, primary, proc)}
				>
					{primary}
				</button>
			</div>
			<div className="kc-table kc-monitor-steps">
				{steps.length ? (
					steps
				) : (
					<div className="kc-row kc-empty">No active run — start from Overview</div>
				)}
			</div>
			<div className="kc-section-label">Console</div>
			<div className="kc-console">
				{logs.length ? (
					logs
				) : (
					<div className="kc-muted">Waiting for run output…</div>
				)}
			</div>
		</>
	);
}