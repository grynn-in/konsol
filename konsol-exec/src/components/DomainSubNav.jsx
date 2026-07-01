import * as React from "react";
import { DOMAIN_SUBVIEWS } from "../constants";
import { getDomainMeta, getDomainReminders, getProcess } from "../domain";

/**
 * Segmented sub-view control for a domain. The "execute" segment is relabelled
 * to the process verb (Build / Publish cycle / …) so it's never a generic
 * "Execute" — you always know what the plane runs.
 */
export function DomainSubNav({ section, subview, data, onSubview }) {
	const meta = getDomainMeta(section);
	const proc = getProcess(data, section);
	const overdue = getDomainReminders(data, section).filter((r) => r.severity === "overdue").length;
	const active = proc && ["running", "paused"].includes(proc.machine_status);

	return (
		<div className="kc-seg" role="tablist" aria-label={`${meta.label} views`}>
			{DOMAIN_SUBVIEWS.map(([id, label]) => {
				const text = id === "execute" ? meta.verb : label;
				let badge = null;
				if (id === "setup" && overdue) badge = <span className="kc-seg-badge">{overdue}</span>;
				if (id === "monitor" && active) badge = <span className="kc-st kc-run" />;
				return (
					<button
						key={id}
						type="button"
						role="tab"
						aria-selected={subview === id}
						className={`kc-seg-btn ${subview === id ? "kc-on" : ""}`}
						onClick={() => onSubview(id)}
					>
						{text} {badge}
					</button>
				);
			})}
		</div>
	);
}
