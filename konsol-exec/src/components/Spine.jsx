import * as React from "react";
import { DOMAINS, SECTION_OVERVIEW, STATUS } from "../constants";
import { getProcess, getDomainStats } from "../domain";

/**
 * Left process spine — persistent fleet status. Brand up top, then Overview and
 * each close process with a live state dot, so you always see what's moving
 * without opening anything. Worker health + theme toggle sit at the foot.
 */
export function Spine({ section, data, dark, onSection, onToggleTheme }) {
	const item = (id, label, extra) => {
		const on = section === id;
		return (
			<button
				key={id}
				type="button"
				className={`kc-nav-item ${on ? "kc-on" : ""}`}
				aria-current={on ? "page" : undefined}
				onClick={() => onSection(id)}
			>
				{extra?.dot ?? <span className="kc-st" />}
				<span className="kc-nav-label">{label}</span>
				{extra?.tag ? <span className="kc-nav-tag kc-mono">{extra.tag}</span> : null}
			</button>
		);
	};

	return (
		<aside className="kc-spine">
			<div className="kc-brand">
				<div className="kc-mark">K</div>
				<div className="kc-brand-n">
					konsol <span>control</span>
					<small className="kc-mono">ORCHESTRATION · FY{data?.fiscal_year ?? "—"}</small>
				</div>
			</div>

			<div className="kc-spine-lbl">Plane</div>
			{item(SECTION_OVERVIEW, "Overview", { dot: <span className="kc-st kc-ok" /> })}

			<div className="kc-spine-lbl">Close processes</div>
			{DOMAINS.map((d) => {
				const proc = getProcess(data, d.id);
				const ms = proc?.machine_status;
				const st = STATUS[ms] || STATUS.idle;
				const stats = getDomainStats(proc);
				let dot = <span className="kc-st" />;
				let tag = "idle";
				if (["running", "paused"].includes(ms)) { dot = <span className="kc-st kc-run" />; tag = stats.runLabel; }
				else if (ms === "error") { dot = <span className="kc-st kc-fail" />; tag = proc?.blockers ? `${proc.blockers} fail` : "failed"; }
				else if (ms === "done") { dot = <span className="kc-st kc-ok" />; tag = "ok"; }
				else if (proc?.blockers) { dot = <span className="kc-st kc-fail" />; tag = `${proc.blockers}`; }
				return item(d.id, d.label, { dot, tag });
			})}

			<div className="kc-spine-foot">
				<div className="kc-worker">
					<span className={`kc-st ${data?.worker_healthy ? "kc-ok" : "kc-fail"}`} />
					worker · {data?.worker_healthy ? "healthy" : "degraded"}
				</div>
				<button type="button" className="kc-btn kc-btn-ghost kc-btn-sm" onClick={onToggleTheme}>
					{dark ? "☀ Light" : "☾ Dark"}
				</button>
			</div>
		</aside>
	);
}
