import { DOMAINS, CLOSE_STEPS, STATUS } from "./constants.js";

/* ── existing helpers, unchanged in behaviour ──────────────────────────── */

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
	if (["running", "paused"].includes(st)) runLabel = `${done}/${total} steps`;
	else if (st === "done") runLabel = "Last run completed";
	else if (st === "error") runLabel = "Last run failed";
	return {
		readiness: `${proc.ready_count}/${proc.total_count}`,
		status: st,
		blockers: proc.blockers || 0,
		runLabel,
	};
}

/**
 * Per-stage rail state for a process. Unchanged from the previous app except
 * that it no longer carries a metal — colour is status-only now (U1).
 * Returns stages tagged "done" | "now" | "fail" | "idle".
 */
export function railStages(meta, proc) {
	const stages = (meta && meta.stages) || [];
	const run = (proc && proc.run) || {};
	const st = (proc && proc.machine_status) || "idle";
	const done = Math.max(0, Math.min(run.step_done || 0, stages.length));
	const cursor = Math.min(done, stages.length - 1);

	return stages.map((stage, i) => {
		let state = "idle";
		if (i < done) state = "done";
		else if (i === cursor && st === "running") state = "now";
		else if (i === cursor && st === "error") state = "fail";
		else if (st === "done") state = "done";
		return { ...stage, state, n: i + 1 };
	});
}

/* ── close-first model (U8) ────────────────────────────────────────────── */

/**
 * Aggregate every process's prerequisites into one readiness picture.
 * Rows are de-duplicated by doctype — the same prerequisite (EPM Settings,
 * Fiscal Period…) appears under several processes and should be one line, not
 * four. Worst status wins, so a row that is missing anywhere reads as missing.
 */
const SEVERITY = { configured: 0, stale: 1, blocked: 2, missing: 3 };

export function readinessRows(data) {
	const byDoctype = new Map();
	for (const meta of DOMAINS) {
		const proc = getProcess(data, meta.id);
		for (const row of proc?.prerequisites || []) {
			const seen = byDoctype.get(row.doctype);
			if (!seen || SEVERITY[row.status] > SEVERITY[seen.status]) {
				byDoctype.set(row.doctype, { ...row, processes: [...(seen?.processes || []), meta.id] });
			} else {
				seen.processes.push(meta.id);
			}
		}
	}
	return [...byDoctype.values()];
}

export function readinessSummary(data) {
	const rows = readinessRows(data);
	const ok = rows.filter((r) => r.status === "configured").length;
	return { ok, total: rows.length, rows, blocking: rows.length - ok };
}

/**
 * One checklist row per close step, resolved against the snapshot.
 *
 * `available: false` marks a step the backend cannot answer yet — rendered as
 * such rather than guessed at.
 */
export function closeSteps(data) {
	const readiness = readinessSummary(data);

	return CLOSE_STEPS.map((step, i) => {
		const n = i + 1;

		if (step.id === "readiness") {
			return {
				...step,
				n,
				available: true,
				state: readiness.blocking ? "incomplete" : "ready",
				detail: readiness.blocking
					? `${readiness.blocking} of ${readiness.total} still to configure`
					: `All ${readiness.total} configured`,
				blockers: readiness.blocking,
			};
		}

		if (step.id === "signoff") {
			const status = data?.period?.status;
			const assertionsPassed = getProcess(data, "assertions")?.machine_status === "done";
			return {
				...step,
				n,
				// A period with no Period Status record has never been closed,
				// so it is Open — the step is available either way.
				available: Boolean(data?.period?.fiscal_period),
				state: status && status !== "Open" ? "done" : "waiting",
				detail: status && status !== "Open"
					? `Period is ${status.toLowerCase()}`
					: assertionsPassed
						? "Assertions passed — ready to close"
						: "Waiting on assertions",
				blockers: 0,
			};
		}

		const proc = getProcess(data, step.id);
		const stats = getDomainStats(proc);
		return {
			...step,
			n,
			available: Boolean(proc),
			state: proc?.machine_status || "idle",
			detail: proc
				? proc.blockers
					? `${proc.blockers} blocker${proc.blockers === 1 ? "" : "s"}`
					: stats.runLabel
				: "Not reported",
			blockers: proc?.blockers || 0,
			runnable: proc?.runnable,
			readiness: stats.readiness,
		};
	});
}

/**
 * The one action a step offers, chosen by its state. Carried over from the
 * previous Overview cards — this decision tested well and is kept verbatim.
 */
export function primaryAction(step) {
	if (!step.available) return null;
	if (step.id === "readiness") {
		return step.blockers
			? { label: "Finish configuration", tab: null, theme: "orange" }
			: { label: "Review", tab: null, theme: "gray" };
	}
	if (step.id === "signoff") return null;
	if (["running", "paused"].includes(step.state)) return { label: "Open live monitor", tab: "monitor", theme: "blue" };
	if (step.state === "error") return { label: "Review failures", tab: "monitor", theme: "red" };
	if (!step.runnable) return { label: "Finish setup", tab: "setup", theme: "orange" };
	const meta = getDomainMeta(step.id);
	return { label: meta.verb, tab: "execute", theme: "gray" };
}

/** Role a related document plays in a run, for the drill-down. */
const ROLE_LABELS = {
	trigger: "Triggered by",
	approval: "Approved via",
	scope: "Scope",
	definition: "Definition",
	output: "Produced",
	source: "Source",
};

export function roleLabel(role) {
	return ROLE_LABELS[role] || role;
}

export function statusMeta(state) {
	return STATUS[state] || STATUS.idle;
}
