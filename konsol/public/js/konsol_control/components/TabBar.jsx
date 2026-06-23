import * as React from "react";

export function TabBar({ tab, data, onTab }) {
	const overdue = (data?.reminders || []).filter((r) => r.severity === "overdue").length;
	const active = Object.values(data?.processes || {}).some(
		(p) => p.machine_status === "running"
	);

	const tabs = [
		["overview", "Overview", null],
		[
			"setup",
			"Setup & readiness",
			overdue ? <span className="kc-tab-badge">{overdue}</span> : null,
		],
		[
			"monitor",
			"Live monitor",
			active ? <span className="kc-dot kc-tab-dot-active" /> : null,
		],
		["history", "History", null],
	];

	return (
		<nav className="kc-nav">
			{tabs.map(([id, label, badge]) => (
				<button
					key={id}
					type="button"
					className={`kc-tab ${tab === id ? "active" : ""}`}
					onClick={() => onTab(id)}
				>
					{label} {badge}
				</button>
			))}
		</nav>
	);
}