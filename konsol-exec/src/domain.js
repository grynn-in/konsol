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