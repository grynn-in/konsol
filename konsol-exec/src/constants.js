/**
 * Vocabulary and state maps for the close-first control plane.
 *
 * Two rules govern this file, both of them fixes for review findings:
 *
 *  U1 — Colour means STATUS and nothing else. There is no layer palette any
 *       more. Stage identity is carried by position and a numeral, which is
 *       what a numbered sequence should use, and which leaves the saturated
 *       hues free to mean running / done / failed unambiguously.
 *
 *  U2 — Stage LABELS name what the step accomplishes, not which dbt folder it
 *       writes to. Stage IDS are unchanged: `params.from_stage` / `to_stage`
 *       are sent verbatim to konsol.orchestrator.api.start_run, so renaming an
 *       id would silently break the build range. Labels are presentation, ids
 *       are contract.
 */

/** Run/process state → frappe-ui Badge theme + a glyph, so state never depends
 *  on hue alone (U5). Badge themes are the library's: gray/blue/green/orange/red. */
export const STATUS = {
	idle:      { label: "Not started", theme: "gray",   glyph: "circle" },
	running:   { label: "Running",     theme: "blue",   glyph: "loader" },
	paused:    { label: "Paused",      theme: "orange", glyph: "pause" },
	done:      { label: "Completed",   theme: "green",  glyph: "check" },
	error:     { label: "Failed",      theme: "red",    glyph: "x" },
	cancelled: { label: "Cancelled",   theme: "gray",   glyph: "slash" },

	// Gate steps (readiness, sign-off) have no run, so "Failed" would be a lie —
	// nothing ran. They report whether the gate is satisfied instead.
	incomplete: { label: "Incomplete",  theme: "orange", glyph: "alert-triangle" },
	ready:      { label: "Ready",       theme: "green",  glyph: "check" },
	waiting:    { label: "Waiting",     theme: "gray",   glyph: "clock" },
};

/** Setup-checklist row state. Same rule: glyph carries the meaning, colour agrees. */
export const SETUP = {
	configured: { label: "Configured", theme: "green",  glyph: "check" },
	missing:    { label: "Missing",    theme: "red",    glyph: "x" },
	stale:      { label: "Stale",      theme: "orange", glyph: "alert-triangle" },
	blocked:    { label: "Blocked",    theme: "red",    glyph: "slash" },
};

/** orchestrator/status.js tones → frappe-ui Badge themes. status.js is covered
 *  by its own tests and stays untouched, so the mapping lives here instead. */
export const TONE_THEME = {
	green: "green",
	red: "red",
	blue: "blue",
	amber: "orange",
	gray: "gray",
};

/**
 * The four close processes.
 *
 * `verb`   the action its Execute plane performs — never a generic "Execute".
 * `stages` the step sequence the rail renders. `id` is the backend contract;
 *          `label` is what the operator reads.
 */
export const DOMAINS = [
	{
		id: "budgeting",
		label: "Budget",
		processName: "Budgeting",
		verb: "Publish cycle",
		desc: "Collect layered submissions, lock the board version, and publish it to the budget fact.",
		stages: [
			{ id: "collect", label: "Collect" },
			{ id: "lock", label: "Lock" },
			{ id: "publish", label: "Publish" },
		],
	},
	{
		id: "forecasting",
		label: "Forecast",
		processName: "Forecasting",
		verb: "Refresh & publish",
		desc: "Pull the latest actuals, run allocations, and publish the forecast scenarios.",
		stages: [
			{ id: "refresh", label: "Refresh actuals" },
			{ id: "allocate", label: "Allocate" },
			{ id: "publish", label: "Publish" },
		],
	},
	{
		id: "consolidation",
		label: "Consolidation",
		processName: "Consolidation",
		verb: "Build",
		desc: "Build the group result from source ledgers through to the consolidated statements.",
		// ids unchanged (backend contract); labels renamed off the medallion metals (U2)
		stages: [
			{ id: "extract", label: "Extract" },
			{ id: "bronze", label: "Validate" },
			{ id: "silver", label: "Translate" },
			{ id: "gold", label: "Report" },
			{ id: "consolidate", label: "Consolidate" },
		],
	},
	{
		id: "assertions",
		label: "Close assertions",
		processName: "Assertions",
		verb: "Run tests",
		desc: "Compile the test graph, run every close assertion, and record the result.",
		stages: [
			{ id: "compile", label: "Compile" },
			{ id: "test", label: "Test" },
			{ id: "signoff", label: "Sign-off" },
		],
	},
];

/**
 * The close checklist — the app's spine (U8).
 *
 * `kind: "process"` steps map onto a DOMAIN and carry its runs.
 * `kind: "gate"` steps are derived from the snapshot rather than run directly:
 *   readiness aggregates every process's prerequisites; signoff needs Fiscal
 *   Period to carry a status, which does not exist yet (architecture finding
 *   F6) — until it does the step reports itself as unavailable rather than
 *   inventing a state.
 */
export const CLOSE_STEPS = [
	{ id: "readiness", kind: "gate", label: "Readiness", blurb: "Configuration every process depends on." },
	{ id: "budgeting", kind: "process", label: "Budget", blurb: "Lock and publish the board version." },
	{ id: "forecasting", kind: "process", label: "Forecast", blurb: "Refresh actuals and re-forecast." },
	{ id: "consolidation", kind: "process", label: "Consolidation", blurb: "Build the group result." },
	{ id: "assertions", kind: "process", label: "Close assertions", blurb: "Prove the numbers tie." },
	{ id: "signoff", kind: "gate", label: "Sign off period", blurb: "Lock the period against further change." },
];

/** Tabs inside a step. Kept from the previous app — this part of the structure
 *  tested well and only its location changed. */
export const STEP_TABS = [
	{ id: "setup", label: "Setup" },
	{ id: "execute", label: "Execute" },
	{ id: "monitor", label: "Monitor" },
	{ id: "history", label: "History" },
];

export const LAYER_STATE_LABEL = {
	pending: "Not started",
	draft: "Draft",
	submitted: "Submitted",
	approved: "Approved · locked",
};
