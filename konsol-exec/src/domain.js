import { DOMAINS, SECTION_OVERVIEW } from "./constants";

export function isDomainSection(section) {
	return section && section !== SECTION_OVERVIEW;
}

export function getDomainMeta(domainId) {
	return DOMAINS.find((d) => d.id === domainId) || DOMAINS[0];
}

export function getProcess(data, domainId) {
	return data?.processes?.[domainId] || null;
}

export function getDomainReminders(data, domainId) {
	return (data?.reminders || []).filter((r) => r.process_id === domainId);
}

export function getDomainRuns(data, domainId) {
	return data?.runs?.[domainId] || [];
}

export function getDomainStats(proc) {
	if (!proc) {
		return { readiness: "—", status: "idle", blockers: 0, runLabel: "No run" };
	}
	const run = proc.run || {};
	const st = proc.machine_status || "idle";
	const done = run.step_done || 0;
	const total = run.step_total || 0;
	let runLabel = "No active run";
	if (["running", "paused"].includes(st)) {
		runLabel = `${done}/${total} steps`;
	} else if (st === "done") {
		runLabel = "Last run completed";
	} else if (st === "error") {
		runLabel = "Last run failed";
	}
	return {
		readiness: `${proc.ready_count}/${proc.total_count}`,
		status: st,
		blockers: proc.blockers || 0,
		runLabel,
	};
}

/**
 * Compute per-stage rail state for a process, from its snapshot `proc`.
 * Returns the domain's stages tagged with state: "done" | "now" | "fail" | "idle".
 *
 * The snapshot carries a coarse `run.step_done`/`step_total`; we map that onto
 * the domain's declared stage list so the rail (overview mini-rail + the big
 * LayerRail) always reflects how far the last/active build got. When a run
 * failed we mark the stage it stalled on.
 */
export function railStages(meta, proc) {
	const stages = (meta && meta.stages) || [];
	const run = (proc && proc.run) || {};
	const st = (proc && proc.machine_status) || "idle";
	const total = run.step_total || stages.length || 1;
	const done = Math.max(0, Math.min(run.step_done || 0, stages.length));
	// index of the stage currently in motion (or the one that failed)
	const cursor = Math.min(done, stages.length - 1);

	return stages.map((stage, i) => {
		let state = "idle";
		if (i < done) state = "done";
		else if (i === cursor && st === "running") state = "now";
		else if (i === cursor && st === "error") state = "fail";
		else if (st === "done") state = "done";
		return { ...stage, state };
	});
}

const ROLE_LABELS = {
	primary: "Primary",
	execution: "Execution",
	upstream: "Upstream",
	trigger: "Trigger",
	context: "Context",
};

export function roleLabel(role) {
	return ROLE_LABELS[role] || role;
}