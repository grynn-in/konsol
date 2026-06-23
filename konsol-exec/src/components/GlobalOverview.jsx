import * as React from "react";
import { DOMAINS, STATUS } from "../constants";
import { getDomainStats, getProcess } from "../domain";

export function GlobalOverview({ data, onOpenDomain, onPrimary }) {
	const active = data?.stats?.active ?? 0;
	const errors = data?.stats?.errors ?? 0;
	const doneToday = data?.stats?.done_today ?? 0;

	const cards = DOMAINS.map((meta) => {
		const proc = getProcess(data, meta.id);
		if (!proc) return null;
		const st = STATUS[proc.machine_status] || STATUS.idle;
		const stats = getDomainStats(proc);
		const ready = proc.runnable;

		let cta = "Open";
		if (proc.machine_status === "error") cta = "Retry";
		else if (["running", "paused"].includes(proc.machine_status)) cta = "Monitor";
		else if (!ready) cta = "Setup";

		return (
			<div
				key={meta.id}
				className="kc-proc-card kc-overview-domain-card"
				style={{ "--domain-accent": meta.accent }}
			>
				<div className="kc-proc-head">
					<div className="kc-proc-title">
						<span className="kc-proc-num" style={{ background: meta.accent }}>
							{meta.num}
						</span>
						<span className="kc-proc-name">{meta.label}</span>
					</div>
					<span className="kc-pill" style={{ color: st.color, background: st.bg }}>
						{st.label}
					</span>
				</div>
				<div className="kc-proc-desc">{meta.desc}</div>
				<div className="kc-overview-domain-stats">
					<span>Readiness {stats.readiness}</span>
					<span>{stats.blockers} blocker{stats.blockers === 1 ? "" : "s"}</span>
					<span>{stats.runLabel}</span>
				</div>
				<div
					className="kc-readiness"
					style={{ color: ready ? "var(--green)" : "var(--amber)" }}
				>
					<span
						className="kc-dot"
						style={{ background: ready ? "var(--green)" : "var(--amber)" }}
					/>
					{ready ? "Ready to run" : "Setup incomplete"}
				</div>
				<div className="kc-proc-actions">
					<button
						type="button"
						className="kc-btn kc-btn-primary"
						style={{ background: meta.accent }}
						onClick={() => onPrimary(meta.id, cta)}
					>
						{cta}
					</button>
					<button
						type="button"
						className="kc-btn kc-btn-ghost"
						onClick={() => onOpenDomain(meta.id)}
					>
						Open {meta.label}
					</button>
				</div>
			</div>
		);
	});

	return (
		<div className="kc-global-overview">
			<div className="kc-grid3">
				<div className="kc-card">
					<div className="kc-stat-label">Active runs</div>
					<div className="kc-stat-val">
						{active}
						<span className="kc-stat-suffix">in progress</span>
					</div>
				</div>
				<div className="kc-card">
					<div className="kc-stat-label">Needs attention</div>
					<div
						className="kc-stat-val"
						style={{ color: errors ? "var(--red)" : "inherit" }}
					>
						{errors}
						<span className="kc-stat-suffix">failed</span>
					</div>
				</div>
				<div className="kc-card">
					<div className="kc-stat-label">Completed today</div>
					<div className="kc-stat-val">
						{doneToday}
						<span className="kc-stat-suffix">runs</span>
					</div>
				</div>
			</div>
			<div className="kc-section-label">Close processes</div>
			<div className="kc-overview-domains">{cards}</div>
		</div>
	);
}