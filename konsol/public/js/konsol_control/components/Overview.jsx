import * as React from "react";
import { STATUS, PROCESS_IDS } from "../constants";

export function Overview({ data, onPrimary, onOpenSetup, onRemind }) {
	const procMap = data?.processes || {};

	const cards = PROCESS_IDS.map((id, idx) => {
		const p = procMap[id];
		if (!p) return null;
		const st = STATUS[p.machine_status] || STATUS.idle;
		const run = p.run || {};
		const done = run.step_done || 0;
		const total = run.step_total || 1;
		const pct = Math.round((done / total) * 100);
		const ready = p.runnable;

		let cta = "Start run";
		let ctaColor = "var(--blue)";
		if (p.machine_status === "error") {
			cta = "Retry failed step";
			ctaColor = "var(--red)";
		} else if (["running", "paused"].includes(p.machine_status)) {
			cta = "Open monitor";
		} else if (!ready) {
			cta = "Resolve setup";
			ctaColor = "var(--amber)";
		} else if (p.machine_status === "done") {
			cta = "Run again";
		}

		return (
			<React.Fragment key={id}>
				<div className="kc-pipeline-item">
					<div className="kc-proc-card">
						<div className="kc-proc-head">
							<div className="kc-proc-title">
								<span className="kc-proc-num" style={{ background: p.accent }}>
									{p.num}
								</span>
								<span className="kc-proc-name">{p.name}</span>
							</div>
							<span className="kc-pill" style={{ color: st.color, background: st.bg }}>
								{st.label}
							</span>
						</div>
						<div className="kc-proc-desc">{p.desc}</div>
						<div
							className="kc-readiness"
							style={{ color: ready ? "var(--green)" : "var(--amber)" }}
						>
							<span
								className="kc-dot"
								style={{ background: ready ? "var(--green)" : "var(--amber)" }}
							/>
							{ready
								? "Setup ready"
								: `${p.ready_count}/${p.total_count} ready · ${p.blockers} blocker${p.blockers === 1 ? "" : "s"}`}
						</div>
						<div className="kc-step-meta">
							<span>
								{done} / {total} steps
							</span>
							<span>{pct}%</span>
						</div>
						<div className="kc-bar">
							<div
								className="kc-bar-fill"
								style={{
									width: `${pct}%`,
									background:
										p.machine_status === "error" ? "var(--red)" : p.accent,
								}}
							/>
						</div>
						<div className="kc-proc-actions">
							<button
								type="button"
								className="kc-btn kc-btn-primary"
								style={{ background: ctaColor }}
								onClick={() => onPrimary(id, cta)}
							>
								{cta}
							</button>
							<button
								type="button"
								className="kc-btn kc-btn-ghost"
								onClick={() => onOpenSetup(id)}
							>
								Open
							</button>
						</div>
					</div>
				</div>
				{idx < 2 ? <div className="kc-arrow">→</div> : null}
			</React.Fragment>
		);
	});

	const reminders = (data?.reminders || []).map((r, i) => (
		<div className="kc-row" key={`${r.process}-${r.what}-${i}`}>
			<span className="kc-col-process">{r.process}</span>
			<span className="kc-col-flex">{r.what}</span>
			<span className="kc-col-owner">{r.owner}</span>
			<span className="kc-col-due">{r.due || ""}</span>
			<span
				className="kc-pill kc-col-severity"
				style={{
					color:
						r.severity === "overdue"
							? "var(--red)"
							: r.severity === "warn"
								? "var(--amber)"
								: "var(--ink5)",
					background:
						r.severity === "overdue"
							? "var(--redS)"
							: r.severity === "warn"
								? "var(--amberS)"
								: "var(--card2)",
				}}
			>
				{r.severity === "overdue" ? "Overdue" : r.severity === "warn" ? "Action" : "Open"}
			</span>
			<button
				type="button"
				className="kc-btn kc-btn-primary kc-btn-sm"
				onClick={() => onRemind(r.owner, r.what)}
			>
				Remind
			</button>
		</div>
	));

	return (
		<>
			<div className="kc-grid3">
				<div className="kc-card">
					<div className="kc-stat-label">Active runs</div>
					<div className="kc-stat-val">
						{data?.stats?.active}
						<span className="kc-stat-suffix">in progress</span>
					</div>
				</div>
				<div className="kc-card">
					<div className="kc-stat-label">Needs attention</div>
					<div
						className="kc-stat-val"
						style={{ color: data?.stats?.errors ? "var(--red)" : "inherit" }}
					>
						{data?.stats?.errors}
						<span className="kc-stat-suffix">failed</span>
					</div>
				</div>
				<div className="kc-card">
					<div className="kc-stat-label">Completed today</div>
					<div className="kc-stat-val">
						{data?.stats?.done_today}
						<span className="kc-stat-suffix">runs</span>
					</div>
				</div>
			</div>
			<div className="kc-section-label">Close pipeline</div>
			<div className="kc-pipeline">{cards}</div>
			<div className="kc-reminders">
				<div className="kc-section-label">Reminders — owners to nudge</div>
				<div className="kc-table">
					{reminders.length ? (
						reminders
					) : (
						<div className="kc-row kc-empty">No open reminders</div>
					)}
				</div>
			</div>
		</>
	);
}