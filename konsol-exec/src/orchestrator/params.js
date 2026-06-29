// Pure run-params builder for the konsol-exec orchestration plane.
//
// Mirrors the old Desk page `collect_params()`: turn a flat launch-form
// object into the `{ definition, params }` shape the backend
// `konsol.orchestrator.api.start_run(definition, params)` expects.
//
// Pure ESM — no React, no fetch, no frappe. Data in / data out.

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
