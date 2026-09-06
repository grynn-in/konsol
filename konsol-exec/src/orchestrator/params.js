// Pure run-params builder for the konsol-exec orchestration plane.
//
// Mirrors the old Desk page `collect_params()`: turn a flat launch-form
// object into the `{ definition, params }` shape the backend
// `konsol.orchestrator.api.start_run(definition, params)` expects.
//
// Pure ESM — framework-free. Data in / data out.

/**
 * @param {object} form launch-form values, e.g.
 *   { fiscal_year, fiscal_period, scope, definition, full_refresh, skip_sync }
 * @returns {{ definition: (string|null), params: object }}
 */
export function buildRunArgs(form = {}) {
	const f = form || {};
	const params = {};

	// Fiscal/scope filters are only sent when the user supplied a value.
	if (f.fiscal_year) params.fiscal_year = f.fiscal_year;
	if (f.fiscal_period) params.fiscal_period = f.fiscal_period;
	if (f.scope) params.scope = f.scope;

	// Checkboxes are always sent as 0/1 ints (backend expects Frappe Checks).
	params.full_refresh = f.full_refresh ? 1 : 0;
	params.skip_sync = f.skip_sync ? 1 : 0;

	return { definition: f.definition || null, params };
}

/**
 * Apply a build range to run params.
 *
 * The range is the highest-stakes input on the launch form — it decides whether
 * a run rebuilds one stage or all of them — so it lives here as a pure function
 * with tests, rather than only inside a component where the only way to check
 * it is to click it.
 *
 * `from`/`to` are indices into `stages` and may arrive in either order; the
 * backend contract is stage IDS, not labels or indices.
 *
 * @param {object} params params object from buildRunArgs
 * @param {{id: string}[]} stages ordered stage list
 * @param {number} from index
 * @param {number} to index
 * @returns {object} a new params object; unchanged when there are no stages
 */
export function withStageRange(params, stages, from, to) {
	const list = Array.isArray(stages) ? stages : [];
	if (!list.length) return { ...params };
	const lo = Math.max(0, Math.min(Number(from) || 0, Number(to) || 0));
	const hi = Math.min(list.length - 1, Math.max(Number(from) || 0, Number(to) || 0));
	return { ...params, from_stage: list[lo].id, to_stage: list[hi].id };
}

/**
 * Plain-English description of what a range will rebuild — the caption the
 * operator reads before committing. Kept next to the logic it describes so the
 * two cannot drift apart.
 */
export function describeStageRange(stages, from, to) {
	const list = Array.isArray(stages) ? stages : [];
	if (!list.length) return "";
	const lo = Math.max(0, Math.min(Number(from) || 0, Number(to) || 0));
	const hi = Math.min(list.length - 1, Math.max(Number(from) || 0, Number(to) || 0));
	if (lo === hi) return `Rebuilds ${list[lo].label} only.`;
	return `Rebuilds ${hi - lo + 1} stages, ${list[lo].label} through ${list[hi].label}.`;
}

/** True when the range covers every stage. */
export function isFullRange(stages, from, to) {
	const list = Array.isArray(stages) ? stages : [];
	if (!list.length) return true;
	const lo = Math.min(Number(from) || 0, Number(to) || 0);
	const hi = Math.max(Number(from) || 0, Number(to) || 0);
	return lo <= 0 && hi >= list.length - 1;
}
