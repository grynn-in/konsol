import * as React from "react";
import { PRIMARY_NAV, STATUS, SECTION_OVERVIEW, DOMAINS } from "../constants";
import { getProcess } from "../domain";

export function PrimaryNav({ section, data, onSection }) {
	return (
		<nav className="kc-nav kc-primary-nav" aria-label="Main navigation">
			{PRIMARY_NAV.map((item) => {
				const active = section === item.id;
				let badge = null;
				if (item.id !== SECTION_OVERVIEW) {
					const proc = getProcess(data, item.id);
					const st = proc ? STATUS[proc.machine_status] || STATUS.idle : STATUS.idle;
					if (["running", "paused"].includes(proc?.machine_status)) {
						badge = <span className="kc-dot kc-tab-dot-active" />;
					} else if (proc?.blockers) {
						badge = (
							<span className="kc-tab-badge">{proc.blockers}</span>
						);
					} else {
						badge = <span className="kc-dot" style={{ background: st.color }} />;
					}
				}
				const domain = DOMAINS.find((d) => d.id === item.id);
				return (
					<button
						key={item.id}
						type="button"
						className={`kc-tab ${active ? "active" : ""}`}
						style={domain ? { "--domain-accent": domain.accent } : undefined}
						onClick={() => onSection(item.id)}
					>
						{item.label} {badge}
					</button>
				);
			})}
		</nav>
	);
}