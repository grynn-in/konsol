export const STATUS = {
	idle: { label: "Idle", color: "var(--ink5)", bg: "var(--card2)" },
	running: { label: "Running", color: "var(--amber)", bg: "var(--amberS)" },
	paused: { label: "Paused", color: "var(--amber)", bg: "var(--amberS)" },
	done: { label: "Completed", color: "var(--green)", bg: "var(--greenS)" },
	error: { label: "Failed", color: "var(--red)", bg: "var(--redS)" },
	cancelled: { label: "Cancelled", color: "var(--ink5)", bg: "var(--card2)" },
};

export const SETUP = {
	configured: { label: "Configured", color: "var(--green)", bg: "var(--greenS)", glyph: "✓" },
	missing: { label: "Missing", color: "var(--red)", bg: "var(--redS)", glyph: "✗" },
	stale: { label: "Stale", color: "var(--amber)", bg: "var(--amberS)", glyph: "!" },
	blocked: { label: "Blocked", color: "var(--red)", bg: "var(--redS)", glyph: "⊛" },
};

export const SECTION_OVERVIEW = "overview";

/** Close processes — each has its own sub-nav space. */
export const DOMAINS = [
	{
		id: "budgeting",
		label: "Budget",
		processName: "Budgeting",
		num: "01",
		accent: "#b5611f",
		desc: "Layered budget submission, board lock, and publish.",
	},
	{
		id: "forecasting",
		label: "Forecast",
		processName: "Forecasting",
		num: "02",
		accent: "#0e8f84",
		desc: "Refresh actuals, run allocations, publish forecast scenarios.",
	},
	{
		id: "consolidation",
		label: "Consolidation",
		processName: "Consolidation",
		num: "03",
		accent: "#2f7d4f",
		desc: "Run the group consolidation build — extract → seed → silver → gold.",
	},
	{
		id: "assertions",
		label: "Assertions",
		processName: "Assertions",
		num: "04",
		accent: "#0e8f84",
		desc: "Run the close assertion suite (dbt tests) and sign-off.",
	},
];

/** Primary nav: Overview + each domain. */
export const PRIMARY_NAV = [
	{ id: SECTION_OVERVIEW, label: "Overview" },
	...DOMAINS.map((d) => ({ id: d.id, label: d.label })),
];

/** Sub-nav shown only under Budget / Forecast / Consolidation. */
export const DOMAIN_SUBVIEWS = [
	["setup", "Setup & readiness"],
	["monitor", "Live monitor"],
	["execute", "Execute"],
	["history", "History"],
];

export const LAYER_STATE_LABEL = {
	pending: "Not started",
	draft: "Draft",
	submitted: "Submitted",
	approved: "Approved · locked",
};