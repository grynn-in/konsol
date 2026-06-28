// Pure run view-model for the konsol-exec orchestration plane.
//
// Maps a Pipeline Run doc (+ its `steps` child rows) into a stable,
// camelCase shape the SPA/timeline can render, and derives progress.
//
// Pure ESM — no React, no fetch, no frappe. Data in / data out.

import { isTerminal } from "./status.js";

/**
 * Map one Pipeline Run `steps` child row into camelCase, tolerating
 * missing fields.
 * @param {object} row child-table row carrying snake_case fields.
 */
function normalizeStep(row = {}) {
	const r = row || {};
	return {
		id: r.step_id ?? null,
		type: r.step_type ?? null,
		status: r.status ?? null,
		startedAt: r.started_at ?? null,
		endedAt: r.ended_at ?? null,
		rows: r.rows ?? 0,
		output: r.output ?? "",
		error: r.error ?? "",
	};
}

/**
 * Normalize a Pipeline Run doc + its child rows.
 * @param {object|null} doc Pipeline Run doc with optional `steps` array.
 * @returns {{name:(string|null), status:(string|null), steps:object[]}}
 */
export function normalizeRun(doc) {
	const d = doc || {};
	const steps = Array.isArray(d.steps) ? d.steps : [];
	return {
		name: d.name ?? null,
		status: d.status ?? null,
		steps: steps.map(normalizeStep),
	};
}

/**
 * Percentage (0–100) of steps that have settled successfully.
 * Counts terminal + Success/Completed steps over total.
 * @param {object[]} steps step objects with a `status` field.
 * @returns {number} 0 when empty/missing.
 */
export function progressPct(steps) {
	const list = Array.isArray(steps) ? steps : [];
	if (list.length === 0) return 0;
	const done = list.filter(
		(s) => isTerminal(s && s.status) && (s.status === "Success" || s.status === "Completed"),
	).length;
	return (done / list.length) * 100;
}

/**
 * Return steps in stable input order without mutating the input.
 * @param {object[]} steps
 * @returns {object[]} a shallow copy in the same order.
 */
export function orderSteps(steps) {
	return Array.isArray(steps) ? steps.slice() : [];
}
