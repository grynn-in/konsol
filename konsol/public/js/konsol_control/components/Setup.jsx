import * as React from "react";
import { SETUP, PROCESS_IDS, LAYER_STATE_LABEL } from "../constants";
import { openDoctype } from "../api";

export function Setup({ data, setupSel, onSetupSel, onRemind }) {
	const proc = data?.processes?.[setupSel];
	if (!proc) return null;

	const pills = PROCESS_IDS.map((id) => {
		const p = data.processes[id];
		const active = id === setupSel;
		return (
			<button
				key={id}
				type="button"
				className="kc-pill-btn"
				style={{
					background: active ? "var(--card)" : "transparent",
					color: active ? "var(--ink9)" : "var(--ink5)",
					borderColor: active ? "var(--bd2)" : "transparent",
				}}
				onClick={() => onSetupSel(id)}
			>
				<span
					className="kc-dot kc-dot-lg"
					style={{ background: p.blockers ? "var(--red)" : "var(--green)" }}
				/>
				{p.name}
			</button>
		);
	});

	const rows = (proc.prerequisites || []).map((it, i) => {
		const sm = SETUP[it.status] || SETUP.missing;
		return (
			<div className="kc-row" key={`${it.doctype}-${i}`}>
				<span className="kc-setup-glyph" style={{ background: sm.bg, color: sm.color }}>
					{sm.glyph}
				</span>
				<div className="kc-col-flex">
					<div className="kc-doctype">{it.doctype}</div>
					<div className="kc-mono">{it.location}</div>
				</div>
				<div className="kc-col-owner">{it.owner}</div>
				<span
					className="kc-pill kc-col-status"
					style={{ color: sm.color, background: sm.bg }}
				>
					{it.status_label}
				</span>
				<div className="kc-row-actions">
					<button
						type="button"
						className="kc-btn kc-btn-ghost"
						onClick={() => openDoctype(it.doctype)}
					>
						Open in Konsol
					</button>
					{it.actionable ? (
						<button
							type="button"
							className="kc-btn kc-btn-primary kc-btn-sm"
							onClick={() => onRemind(it.owner, it.doctype)}
						>
							Remind
						</button>
					) : null}
				</div>
			</div>
		);
	});

	let rounds = null;
	if (setupSel === "budgeting" && data.budget_rounds) {
		rounds = (
			<div className="kc-budget-rounds">
				<div className="kc-section-label">Budget rounds · layered</div>
				<div className="kc-table">
					{(data.budget_rounds.rounds || []).map((r) => {
						const st = LAYER_STATE_LABEL[r.state] || r.state;
						const col =
							r.state === "approved"
								? "var(--green)"
								: r.state === "submitted"
									? "var(--blue)"
									: "var(--amber)";
						return (
							<div className="kc-row" key={r.key}>
								<div className="kc-col-flex">
									<strong>{r.layer}</strong>{" "}
									<span className="kc-muted-inline">{r.role}</span>
									<div className="kc-muted">
										{r.owner} · {r.week}
									</div>
								</div>
								<span className="kc-amount">{r.amount}</span>
								<span className="kc-pill" style={{ color: col }}>
									{st}
								</span>
								<button
									type="button"
									className="kc-btn kc-btn-ghost"
									onClick={() => openDoctype("Budget Sheet")}
								>
									Open sheets
								</button>
							</div>
						);
					})}
				</div>
				{data.budget_rounds.locked ? (
					<div className="kc-locked-note">
						■ Budget cycle locked — forecasting unblocked
					</div>
				) : null}
			</div>
		);
	}

	return (
		<>
			<div className="kc-pills">{pills}</div>
			<div className="kc-card kc-readiness-card">
				<div>
					<div className="kc-readiness-title">{proc.name} — prerequisites</div>
					<div className="kc-readiness-sub">
						Configuration doctypes required before a pipeline run will publish.
					</div>
				</div>
				<div className="kc-readiness-score">
					<div
						className="kc-readiness-num"
						style={{ color: proc.blockers ? "var(--red)" : "var(--green)" }}
					>
						{proc.ready_count} / {proc.total_count}
					</div>
					<div className="kc-muted">
						configured · {proc.blockers} blocking
					</div>
				</div>
			</div>
			<div className="kc-table">{rows}</div>
			{rounds}
		</>
	);
}