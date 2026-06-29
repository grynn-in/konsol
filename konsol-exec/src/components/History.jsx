import * as React from "react";
import { useMachine } from "@xstate/react";
import { STATUS } from "../constants";
import { getDomainMeta, getDomainRuns } from "../domain";
import { runDetailMachine } from "../machines";
import { RunDetail } from "./RunDetail";

// Friendly card titles per run_type (consolidation surfaces two). Runs without
// a run_type render as a single "All … runs" card (budgeting / forecasting).
const RUN_TYPE_META = {
	build: { label: "Consolidation runs" },
	assertion: { label: "Assertions" },
};
const RUN_TYPE_ORDER = ["build", "assertion"];

/** Group runs by run_type (preserving order), or one group when untyped. */
function groupRuns(runs) {
	if (!runs.some((r) => r.run_type)) return [{ key: "all", runs }];
	const groups = {};
	runs.forEach((r) => {
		const k = r.run_type || "other";
		(groups[k] = groups[k] || []).push(r);
	});
	const keys = [
		...RUN_TYPE_ORDER.filter((k) => groups[k]),
		...Object.keys(groups).filter((k) => !RUN_TYPE_ORDER.includes(k)),
	];
	return keys.map((k) => ({ key: k, runs: groups[k] }));
}

export function History({ domain, data }) {
	const meta = getDomainMeta(domain);
	const runs = getDomainRuns(data, domain);
	const [state, send] = useMachine(runDetailMachine);
	const { selected, detail, error } = state.context;

	React.useEffect(() => {
		send({ type: "DOMAIN_CHANGED", domain });
	}, [domain, send]);

	const loading = state.matches("loading");

	const renderRow = (run) => {
		const st = STATUS[run.status] || STATUS.idle;
		const active = selected?.id === run.id;
		const docCount = (run.related_docs || []).length;
		return (
			<button
				type="button"
				key={run.id}
				className={`kc-history-row kc-history-row-btn ${active ? "active" : ""}`}
				onClick={() =>
					send(active ? { type: "DESELECT" } : { type: "SELECT", domain, run })
				}
			>
				<span className="kc-run-id">{run.id}</span>
				<span>{run.period}</span>
				<span className="kc-history-started">{run.started}</span>
				<span>{run.duration}</span>
				<span>{run.rows}</span>
				<span className="kc-history-by">{run.by}</span>
				<span className="kc-related-count">
					{docCount} doc{docCount === 1 ? "" : "s"}
				</span>
				<span className="kc-pill" style={{ color: st.color, background: st.bg }}>
					{st.label}
				</span>
			</button>
		);
	};

	const groups = groupRuns(runs);

	return (
		<div className="kc-domain-space" style={{ "--domain-accent": meta.accent }}>
			{groups.map((g) => {
				const title =
					g.key === "all"
						? `All ${meta.label.toLowerCase()} runs`
						: RUN_TYPE_META[g.key]?.label || g.key;
				return (
					<div className="kc-history-group" key={g.key}>
						<div className="kc-section-label">
							{title} · {g.runs.length}
						</div>
						<div className="kc-table kc-history-table">
							<div className="kc-history-row kc-history-head">
								<span>Run ID</span>
								<span>Period</span>
								<span>Started</span>
								<span>Duration</span>
								<span>Rows</span>
								<span>By</span>
								<span>Docs</span>
								<span>Status</span>
							</div>
							{g.runs.length ? (
								g.runs.map(renderRow)
							) : (
								<div className="kc-row kc-empty">No runs yet</div>
							)}
						</div>
					</div>
				);
			})}

			{selected ? (
				<>
					<div className="kc-section-label">Run detail · {selected.id}</div>
					<RunDetail
						detail={detail}
						loading={loading}
						error={error}
						accent={meta.accent}
						onRetry={() => send({ type: "RETRY" })}
					/>
				</>
			) : (
				<div className="kc-run-hint">
					Select a run to drill down into steps, logs, and documents
				</div>
			)}
		</div>
	);
}
