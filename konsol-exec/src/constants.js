export const STATUS = {
	idle: { label: "Idle", color: "var(--ink5)", bg: "var(--card2)" },
	running: { label: "Running", color: "var(--run)", bg: "var(--runS)" },
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

/** Layer metals — the accent system IS the medallion architecture.
 *  Each token maps to a CSS var carrying the metal's fill colour. */
export const METAL = {
	steel: "var(--steel)",
	bronze: "var(--bronze)",
	silver: "var(--silver)",
	gold: "var(--gold)",
	platinum: "var(--platinum)",
};

/**
 * Close processes. Each carries:
 *  - `verb`   the action its Execute plane performs (never a generic "Execute")
 *  - `stages` the step sequence the Layer Rail renders, so the rail is
 *             self-describing: a controller sees exactly what a run will do.
 */
export const DOMAINS = [
	{
		id: "budgeting",
		label: "Budget",
		processName: "Budgeting",
		mono: "Bd",
		verb: "Publish cycle",
		desc: "Collect layered submissions, lock the board version, and publish it to the gold budget fact.",
		stages: [
			{ id: "collect", label: "Collect", metal: "bronze", glyph: "◆" },
			{ id: "lock", label: "Lock", metal: "silver", glyph: "▣" },
			{ id: "publish", label: "Publish", metal: "gold", glyph: "⇥" },
		],
	},
	{
		id: "forecasting",
		label: "Forecast",
		processName: "Forecasting",
		mono: "Fc",
		verb: "Refresh & publish",
		desc: "Pull the latest actuals, run allocations, and publish the forecast scenarios.",
		stages: [
			{ id: "refresh", label: "Refresh actuals", metal: "steel", glyph: "⇥" },
			{ id: "allocate", label: "Allocate", metal: "bronze", glyph: "◆" },
			{ id: "publish", label: "Publish", metal: "gold", glyph: "⇥" },
		],
	},
	{
		id: "consolidation",
		label: "Consolidation",
		processName: "Consolidation",
		mono: "Cn",
		verb: "Build",
		desc: "Smelt the group result — extract → bronze → silver → gold → consolidate.",
		stages: [
			{ id: "extract", label: "Extract", metal: "steel", glyph: "⇥" },
			{ id: "bronze", label: "Bronze", metal: "bronze", glyph: "◆" },
			{ id: "silver", label: "Silver", metal: "silver", glyph: "◆" },
			{ id: "gold", label: "Gold", metal: "gold", glyph: "◆" },
			{ id: "consolidate", label: "Consolidate", metal: "platinum", glyph: "▣" },
		],
	},
	{
		id: "assertions",
		label: "Assertions",
		processName: "Assertions",
		mono: "As",
		verb: "Run tests",
		desc: "Compile the dbt test graph, run every close assertion, and sign off the period.",
		stages: [
			{ id: "compile", label: "Compile", metal: "steel", glyph: "◆" },
			{ id: "test", label: "Test", metal: "silver", glyph: "▣" },
			{ id: "signoff", label: "Sign-off", metal: "gold", glyph: "✓" },
		],
	},
];

/** Primary nav: Overview + each domain. */
export const PRIMARY_NAV = [
	{ id: SECTION_OVERVIEW, label: "Overview" },
	...DOMAINS.map((d) => ({ id: d.id, label: d.label })),
];

/**
 * Sub-views under a domain. The "execute" id is stable (routing + tests depend
 * on it); its label is resolved per-domain to the process verb in DomainSubNav.
 */
export const DOMAIN_SUBVIEWS = [
	["setup", "Setup"],
	["execute", "Execute"],
	["monitor", "Monitor"],
	["history", "History"],
];

export const LAYER_STATE_LABEL = {
	pending: "Not started",
	draft: "Draft",
	submitted: "Submitted",
	approved: "Approved · locked",
};
