// konsol#305 B15: signoff.js
//
// Pure view model for the sign-off screen (E9; story 9.1). Turns A21's
// `signoff_model.summary()` output into the sections the screen renders.
// Takes no frappe/vue import.
//
// Parallel-run override (25 Sep 2026): the input is A21's summary shape:
// `action`, `label`, `gates{config_gaps, order, completeness, messages}`,
// `checks`, `acknowledgements{names, total, unlisted}`,
// `on_behalf{labels, unknown}`, `exceptions[]`, `covers`, `previous[]`.
// Unknown (null) counts are shown as "unknown", never 0 — a missing count
// means the caller never read it, not that it is zero. Server messages may
// contain a literal "<br>" (A17: `signoff_gate.assert_can_sign` joins its
// problems with "<br>"); this module splits them into separate lines and
// returns plain text lines: the screen renders them with a text binding (never v-html), which escapes them once (B15b)
// and never needs `v-html`. An unknown `action` throws.

const KNOWN_ACTIONS = [
	"signed",
	"blocked",
	"run_checks",
	"rerun",
	"wait",
	"sign",
	"acknowledge",
	"override",
];

const NONE = "None";




/**
 * Splits a server message on a literal "<br>" (any of `<br>`, `<br/>`,
 * `<br />`, case-insensitive), trims and drops empty lines, and
 * Plain text, not escaped (B15b). A message with no "<br>" comes back as one line.
 * `null`/`undefined` comes back as `[]` (nothing to show).
 */
export function messageLines(text) {
	if (text === null || text === undefined) {
		return [];
	}
	return String(text)
		.split(/<br\s*\/?>/gi)
		.map((line) => line.trim())
		.filter((line) => line.length > 0)
		;
}

function unknownOr(value) {
	return value === null || value === undefined ? "unknown" : value;
}

/**
 * B28: how many rows a section shows before "and N more" (C1 run 1: 328 TB
 * exceptions buried the page). A display limit, not policy: `rows` always
 * carries the full list, and the screen's Show all toggle reveals it.
 */
export const SECTION_LIMIT = 10;

/**
 * Every section shares this shape: `rows` is the full list, or exactly
 * `["None"]` when there is nothing to show; `shown` is its first
 * SECTION_LIMIT rows; `hidden` counts the rest and `moreText` says
 * "and N more" (null when nothing is hidden).
 */
function section(rows) {
	const list = rows || [];
	const all = list.length ? list : [NONE];
	const hidden = Math.max(0, all.length - SECTION_LIMIT);
	return {
		rows: all,
		empty: list.length === 0,
		shown: all.slice(0, SECTION_LIMIT),
		hidden,
		moreText: hidden ? `and ${hidden} more` : null,
	};
}

function assertKnownAction(action) {
	if (!KNOWN_ACTIONS.includes(action)) {
		throw new Error(`Unknown sign-off action: ${action}`);
	}
}

/** Configuration gaps first, then the order gate, then completeness (mirrors signoff_model._gate_messages). */
function gatesSection(gates) {
	const rows = [];
	for (const gap of (gates && gates.config_gaps) || []) {
		rows.push(...messageLines(gap.message));
	}
	if (gates && gates.order) {
		rows.push(...messageLines(gates.order.message));
	}
	if (gates && gates.completeness) {
		rows.push(...messageLines(gates.completeness.message));
	}
	return section(rows);
}

function checksSection(checks) {
	if (!checks || checks.run === null || checks.run === undefined) {
		return section([]);
	}
	return section([
		`Run: ${checks.run}`,
		`Status: ${unknownOr(checks.status)}`,
		`Sign-off status: ${unknownOr(checks.signoff_status)}`,
		`Failed: ${unknownOr(checks.failed)}`,
		`Errored: ${unknownOr(checks.errored)}`,
	]);
}

function acknowledgementsSection(ack) {
	const names = (ack && ack.names) || [];
	const total = ack ? ack.total : null;
	const unlisted = ack ? ack.unlisted : null;
	const rows = names.map((name) => `Acknowledged: ${name}`);
	if (names.length || total !== null && total !== undefined) {
		rows.push(`Warned in total: ${unknownOr(total)}`);
		rows.push(`Not listed above: ${unknownOr(unlisted)}`);
	}
	return section(rows);
}

function onBehalfSection(onBehalf) {
	const labels = (onBehalf && onBehalf.labels) || [];
	const unknown = (onBehalf && onBehalf.unknown) || [];
	return section([...labels, ...unknown]);
}

function exceptionsSection(exceptions) {
	const rows = (exceptions || []).map(
		(e) => `${e.entity}: ${e.reason} (declared by ${e.declared_by})`,
	);
	return section(rows);
}

function coversSection(covers) {
	return section((covers || []));
}

function previousSection(previous) {
	const rows = (previous || []).map((p) => `${p.code}: ${p.status}, ${p.signoff}`);
	return section(rows);
}

/**
 * A21's `summary()` output → `{action, label, gates, checks, acknowledgements,
 * onBehalf, exceptions, covers, previous}`. Every section is
 * `{rows, empty, shown, hidden, moreText}`; an empty section's `rows` is
 * exactly `["None"]`.
 * Throws on an unknown `action`.
 */
export function summaryView(summary) {
	assertKnownAction(summary.action);
	return {
		action: summary.action,
		label: summary.label,
		gates: gatesSection(summary.gates),
		checks: checksSection(summary.checks),
		acknowledgements: acknowledgementsSection(summary.acknowledgements),
		onBehalf: onBehalfSection(summary.on_behalf),
		exceptions: exceptionsSection(summary.exceptions),
		covers: coversSection(summary.covers),
		previous: previousSection(summary.previous),
	};
}
