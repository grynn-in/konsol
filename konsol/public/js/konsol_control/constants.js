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

export const PROCESS_IDS = ["budgeting", "forecasting", "consolidation"];

export const LAYER_STATE_LABEL = {
	pending: "Not started",
	draft: "Draft",
	submitted: "Submitted",
	approved: "Approved · locked",
};