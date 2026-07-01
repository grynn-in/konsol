import * as React from "react";
import { DOMAINS, STATUS } from "../constants";
import { getDomainStats, getProcess, railStages } from "../domain";
import { LayerRail } from "./LayerRail";

/**
 * Control-plane overview. One tile row (active / attention / done today) and one
 * card per process. Each card is deliberately spare: a monogram, one live-state
 * chip, an inline mini-rail showing how far the last build got, and ONE primary
 * verb that names what it launches. Colour means state and layer — nothing else.
 */
export function GlobalOverview({ data, onOpen }) {
	const active = data?.stats?.active ?? 0;
	const errors = data?.stats?.errors ?? 0;
	const doneToday = data?.stats?.done_today ?? 0;

	const cards = DOMAINS.map((meta) => {
		const proc = getProcess(data, meta.id);
		if (!proc) return null;
		const ms = proc.machine_status;
		const st = STATUS[ms] || STATUS.idle;
		const stats = getDomainStats(proc);
		const stages = railStages(meta, proc);

		// one primary action, chosen by state
		let primary = { label: `${meta.verb} →`, sub: "execute" };
		if (["running", "paused"].includes(ms)) primary = { label: "Open live monitor →", sub: "monitor" };
		else if (ms === "error") primary = { label: "Review failures →", sub: "monitor" };
		else if (!proc.runnable) primary = { label: "Finish setup →", sub: "setup" };

		const chipCls =
			ms === "running" || ms === "paused" ? "kc-s-run" :
			ms === "error" ? "kc-s-fail" :
			ms === "done" ? "kc-s-ok" : "kc-s-idle";

		return (
			<article className="kc-pcard" key={meta.id}>
				<div className="kc-pcard-top">
					<span className="kc-mono-badge kc-mono">{meta.mono}</span>
					<div className="kc-pcard-id">
						<h3>{meta.label}</h3>
						<div className="kc-pcard-verb kc-mono">
							runs · {meta.stages.map((s) => s.label).join(" → ")}
						</div>
					</div>
					<span className={`kc-state-chip ${chipCls}`}>
						<span className="kc-dot" />
						{st.label}{ms === "running" && stats.runLabel ? ` · ${stats.runLabel}` : ""}
					</span>
				</div>

				<LayerRail stages={stages} variant="mini" />

				<div className="kc-pcard-line">
					<span>Readiness <span className="kc-mono">{stats.readiness}</span></span>
					<span className="kc-mono">
						{proc.blockers ? `${proc.blockers} blocker${proc.blockers === 1 ? "" : "s"}` : stats.runLabel}
					</span>
				</div>

				<div className="kc-pcard-act">
					<button
						type="button"
						className="kc-btn kc-btn-primary kc-wide"
						onClick={() => onOpen(meta.id, primary.sub)}
					>
						{primary.label}
					</button>
					{primary.sub !== "setup" ? (
						<button type="button" className="kc-btn kc-btn-ghost" onClick={() => onOpen(meta.id, "setup")}>
							Open
						</button>
					) : null}
				</div>
			</article>
		);
	});

	return (
		<div className="kc-global-overview">
			<p className="kc-eyebrow">Control plane</p>
			<h1 className="kc-h1">What's moving right now</h1>
			<p className="kc-lede">
				Every close process, its readiness, and where its last build got to — one glance before you launch anything.
			</p>

			<div className="kc-tiles">
				<div className="kc-tile">
					<div className="kc-tile-k">Active runs</div>
					<div className="kc-tile-v">{active}<small>in progress</small></div>
				</div>
				<div className={`kc-tile ${errors ? "kc-alert" : ""}`}>
					<div className="kc-tile-k">Needs attention</div>
					<div className="kc-tile-v">{errors}<small>failed</small></div>
				</div>
				<div className="kc-tile">
					<div className="kc-tile-k">Completed today</div>
					<div className="kc-tile-v">{doneToday}<small>runs</small></div>
				</div>
			</div>

			<p className="kc-eyebrow">Processes</p>
			<div className="kc-cards">{cards}</div>
		</div>
	);
}
