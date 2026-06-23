import * as React from "react";

export function Header({ data, dark, onToggleTheme }) {
	return (
		<div className="kc-header">
			<div className="kc-brand">
				<div className="kc-logo">K</div>
				<div>
					<div className="kc-title">
						konsol <span className="kc-title-muted">control</span>
					</div>
					<div className="kc-subtitle">
						Financial close orchestration · FY{data?.fiscal_year}
					</div>
				</div>
			</div>
			<div className="kc-header-actions">
				<div className="kc-worker-chip">
					<span
						className="kc-dot"
						style={{
							background: data?.worker_healthy ? "var(--green)" : "var(--red)",
						}}
					/>
					worker · {data?.worker_healthy ? "healthy" : "degraded"}
				</div>
				<button type="button" className="kc-btn kc-btn-ghost" onClick={onToggleTheme}>
					{dark ? "☀ Light" : "☾ Dark"}
				</button>
			</div>
		</div>
	);
}