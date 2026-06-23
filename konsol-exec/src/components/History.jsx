import * as React from "react";
import { useMachine } from "@xstate/react";
import { STATUS } from "../constants";
import { getDomainMeta, getDomainRuns } from "../domain";
import { runDetailMachine } from "../machines";
import { RunDetail } from "./RunDetail";

export function History({ domain, data }) {
	const meta = getDomainMeta(domain);
	const runs = getDomainRuns(data, domain);
	const [state, send] = useMachine(runDetailMachine);
	const { selected, detail, error } = state.context;

	React.useEffect(() => {
		send({ type: "DOMAIN_CHANGED", domain });
	}, [domain, send]);

	const loading = state.matches("loading");

	const rows = runs.map((run) => {
		const st = STATUS[run.status] || STATUS.idle;
		const active = selected?.id === run.id;
		const docCount = (run.related_docs || []).length;
		return (
			<button
				type="button"
				key={run.id}
				className={`kc-history-row kc-history-row-btn ${active ? "active" : ""}`}
				onClick={() =>
					send(
						active
							? { type: "DESELECT" }
							: { type: "SELECT", domain, run }
					)
				}
			>
				<span className="kc-run-id">{run.id}</span>
				<span>{run.period}</span>
				<span className="kc-history-started">{run.started}</span>
				<span>{run.duration}</span>
				<span>{run.rows}</span>
				<span className="kc-history-by">{run.by}</span>
				<span className="kc-related-count">{docCount} doc{docCount === 1 ? "" : "s"}</span>
				<span className="kc-pill" style={{ color: st.color, background: st.bg }}>
					{st.label}
				</span>
			</button>
		);
	});

	return (
		<div className="kc-domain-space" style={{ "--domain-accent": meta.accent }}>
			<div className="kc-section-label">
				All {meta.label.toLowerCase()} runs · {runs.length}
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
				{rows.length ? (
					rows
				) : (
					<div className="kc-row kc-empty">No {meta.label.toLowerCase()} runs yet</div>
				)}
			</div>

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
				<div className="kc-run-hint">Select a run to drill down into steps, logs, and documents</div>
			)}
		</div>
	);
}