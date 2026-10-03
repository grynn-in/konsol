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

import { formatTime, parseZoned } from "./timefmt.js";

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

// konsol#305 C11: the states ic_model (C01, amended W3-7) declares; the
// sign-off summary's `intercompany` carries one of them, or is missing on an
// older payload. Never reads as reconciled when it is not: a missing key, an
// older payload, is shown, never dropped as empty.
const IC_STATES = ["not_configured", "not_applicable", "not_built", "error", "checked"];
const IC_NOT_REPORTED = "Intercompany was not reported by the server — nothing was checked.";

function intercompanyCountsLine(counts) {
	const pairs = counts ? counts.pairs : undefined;
	if (pairs === 0) {
		return "0 intercompany pairs in the last build for this period.";
	}
	return (
		`${unknownOr(pairs)} pairs: ${unknownOr(counts && counts.matched)} matched, ` +
		`${unknownOr(counts && counts.within_tolerance)} within tolerance, ` +
		`${unknownOr(counts && counts.fx_difference)} FX differences, ` +
		`${unknownOr(counts && counts.over_tolerance)} over tolerance`
	);
}

/**
 * C11: the sign-off screen's intercompany section, built from
 * `summary.intercompany` (ic_api.signoff_summary, C10). Never `["None"]` and
 * never reads as reconciled:
 * - missing / null (an older payload) → the "not reported" sentence;
 * - `not_configured` / `not_applicable` / `not_built` / `error` → the
 *   server's message, split on <br> (messageLines);
 * - `checked` → the counts line (or the dedicated 0-pairs sentence), then
 *   open send-backs, then unmatched rows, each only when > 0.
 * An unknown state throws, naming it (mirrors assertKnownAction).
 */
function intercompanySection(ic) {
	if (ic === null || ic === undefined) {
		return section([IC_NOT_REPORTED]);
	}
	if (!IC_STATES.includes(ic.state)) {
		throw new Error(`Unknown intercompany state: ${ic.state}`);
	}
	if (ic.state !== "checked") {
		return section(messageLines(ic.message));
	}
	const rows = [intercompanyCountsLine(ic.counts)];
	if (ic.sent_back_open > 0) {
		rows.push(`${ic.sent_back_open} sent back and still open`);
	}
	if (ic.counts && ic.counts.unmatched > 0) {
		rows.push(`${ic.counts.unmatched} rows without a partner`);
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
 * A21's `summary()` output → `{action, label, gates, checks, intercompany,
 * acknowledgements, onBehalf, exceptions, covers, previous}`. Every section is
 * `{rows, empty, shown, hidden, moreText}`; an empty section's `rows` is
 * exactly `["None"]` — except `intercompany` (C11), which is never `["None"]`
 * and never reads as reconciled: a missing or unrecognised payload still
 * shows a visible sentence.
 * Throws on an unknown `action` or an unknown intercompany state.
 */
export function summaryView(summary) {
	assertKnownAction(summary.action);
	return {
		action: summary.action,
		label: summary.label,
		gates: gatesSection(summary.gates),
		checks: checksSection(summary.checks),
		intercompany: intercompanySection(summary.intercompany),
		acknowledgements: acknowledgementsSection(summary.acknowledgements),
		onBehalf: onBehalfSection(summary.on_behalf),
		exceptions: exceptionsSection(summary.exceptions),
		covers: coversSection(summary.covers),
		previous: previousSection(summary.previous),
	};
}

/**
 * B33: the close's `closed_on` as a time in the user's zone, read like the TB
 * list (B27) through timefmt.js (B29). Missing (null/undefined/"") is
 * "unknown", never blank. A zone-less timestamp is refused (B09b), and so is
 * a missing user zone: neither is guessed from the machine's zone.
 */
export function closedOnText(value, now, timeZone) {
	if (value === null || value === undefined || value === "") {
		return "unknown";
	}
	if (!timeZone) {
		throw new Error("No time zone to show the close time in.");
	}
	return formatTime(parseZoned(value), now, timeZone);
}
