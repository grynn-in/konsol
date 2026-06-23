import * as React from "react";
import { STATUS } from "../constants";
import { roleLabel } from "../domain";
import { openDoc } from "../api";

export function RunDetail({ detail, loading, error, accent, onRetry }) {
	if (loading) {
		return <div className="kc-run-detail kc-run-detail-loading">Loading run…</div>;
	}
	if (error) {
		return (
			<div className="kc-run-detail kc-run-detail-error">
				Failed to load run: {error?.message || String(error)}
				{onRetry ? (
					<div style={{ marginTop: 12 }}>
						<button type="button" className="kc-btn kc-btn-ghost" onClick={onRetry}>
							Retry
						</button>
					</div>
				) : null}
			</div>
		);
	}
	if (!detail) return null;

	const run = detail.run || {};
	const st = STATUS[detail.status] || STATUS.idle;

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

	const related = (detail.related_docs || []).map((doc, i) => (
		<div className="kc-row kc-related-row" key={`${doc.doctype}-${doc.name}-${i}`}>
			<span className="kc-related-role">{roleLabel(doc.role)}</span>
			<div className="kc-col-flex">
				<div className="kc-doctype">{doc.doctype}</div>
				<div className="kc-mono">{doc.name}</div>
			</div>
			<button
				type="button"
				className="kc-btn kc-btn-ghost kc-btn-sm"
				onClick={() => openDoc(doc.doctype, doc.name)}
			>
				Open
			</button>
		</div>
	));

	return (
		<div className="kc-run-detail" style={{ "--domain-accent": accent }}>
			<div className="kc-run-detail-head">
				<div>
					<div className="kc-run-detail-id">{detail.id}</div>
					<div className="kc-muted">
						{detail.kind} · {detail.status_raw || detail.status} · {detail.by}
					</div>
				</div>
				<span className="kc-pill" style={{ color: st.color, background: st.bg }}>
					{st.label}
				</span>
			</div>

			<div className="kc-grid3 kc-run-detail-stats">
				<div className="kc-card">
					<div className="kc-stat-label">Period</div>
					<div className="kc-stat-val kc-stat-val-sm">{detail.period || "—"}</div>
				</div>
				<div className="kc-card">
					<div className="kc-stat-label">Started</div>
					<div className="kc-stat-val kc-stat-val-sm">{detail.started}</div>
				</div>
				<div className="kc-card">
					<div className="kc-stat-label">Duration</div>
					<div className="kc-stat-val kc-stat-val-sm">{detail.duration}</div>
				</div>
			</div>

			<div className="kc-section-label">Related documents</div>
			<div className="kc-table kc-related-table">
				{related.length ? (
					related
				) : (
					<div className="kc-row kc-empty">No linked documents</div>
				)}
			</div>

			{steps.length ? (
				<>
					<div className="kc-section-label">Steps</div>
					<div className="kc-table kc-monitor-steps">{steps}</div>
				</>
			) : null}

			<div className="kc-section-label">Console</div>
			<div className="kc-console">
				{logs.length ? logs : <div className="kc-muted">No log output for this run</div>}
			</div>
		</div>
	);
}