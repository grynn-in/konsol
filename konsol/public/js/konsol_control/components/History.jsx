import * as React from "react";
import { STATUS } from "../constants";

export function History({ data }) {
	const rows = (data?.history || []).map((h, i) => {
		const st = STATUS[h.status] || STATUS.idle;
		return (
			<div className="kc-history-row" key={`${h.process}-${h.started}-${i}`}>
				<span>
					<span className="kc-dot" style={{ background: h.accent, marginRight: 8 }} />
					{h.process}
				</span>
				<span>{h.period}</span>
				<span className="kc-history-started">{h.started}</span>
				<span>{h.duration}</span>
				<span>{h.rows}</span>
				<span className="kc-history-by">{h.by}</span>
				<span className="kc-pill" style={{ color: st.color, background: st.bg }}>
					{st.label}
				</span>
			</div>
		);
	});

	return (
		<>
			<div className="kc-section-label">Run history</div>
			<div className="kc-table">
				{rows.length ? rows : <div className="kc-row kc-empty">No runs yet</div>}
			</div>
		</>
	);
}