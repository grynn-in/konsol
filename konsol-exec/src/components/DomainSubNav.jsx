import * as React from "react";
import { DOMAIN_SUBVIEWS } from "../constants";
import { getDomainMeta, getDomainReminders, getProcess } from "../domain";

export function DomainSubNav({ section, subview, data, onSubview }) {
	const meta = getDomainMeta(section);
	const proc = getProcess(data, section);
	const overdue = getDomainReminders(data, section).filter((r) => r.severity === "overdue").length;
	const active = proc && ["running", "paused"].includes(proc.machine_status);

	return (
		<nav
			className="kc-nav kc-sub-nav"
			style={{ "--domain-accent": meta.accent }}
			aria-label={`${meta.label} views`}
		>
			{DOMAIN_SUBVIEWS.map(([id, label]) => {
				let badge = null;
				if (id === "setup" && overdue) {
					badge = <span className="kc-tab-badge">{overdue}</span>;
				}
				if (id === "monitor" && active) {
					badge = <span className="kc-dot kc-tab-dot-active" />;
				}
				return (
					<button
						key={id}
						type="button"
						className={`kc-tab ${subview === id ? "active" : ""}`}
						onClick={() => onSubview(id)}
					>
						{label} {badge}
					</button>
				);
			})}
		</nav>
	);
}