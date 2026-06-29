/**
 * Status model for orchestrator runs/steps.
 * Backend vocab: Pending/Running/Success/Failed/Skipped/Cancelled.
 * The realtime layer also uses Queued/Completed, so we tolerate those.
 * Pure ESM — no React, no fetch.
 */

const TONES = {
	Success: "green",
	Completed: "green",
	Failed: "red",
	Cancelled: "red",
	Running: "blue",
	Queued: "amber",
	Pending: "gray",
	Skipped: "gray",
};

const TERMINAL = new Set(["Completed", "Failed", "Cancelled", "Success"]);

/**
 * @param {string} status
 * @returns {"green"|"red"|"blue"|"amber"|"gray"}
 */
export function statusTone(status) {
	return TONES[status] || "gray";
}

/**
 * @param {string} status
 * @returns {boolean} true when the run/step has settled.
 */
export function isTerminal(status) {
	return TERMINAL.has(status);
}

/**
 * @param {string} status
 * @returns {boolean} true while actively executing.
 */
export function isRunning(status) {
	return status === "Running";
}
