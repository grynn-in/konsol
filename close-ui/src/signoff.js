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
// U47 (M46): `commentary[]`, per-group heading commentary coverage
// (`consolidation_group`, `headings`, `with_commentary`, `missing[]`) — a
// missing key throws (M46 always sends it); the section never changes
// `action`.
// #305-W5-2 (8.4): `commentary_required` — the headings above the declared
// threshold with no commentary (their own section; the action's Amber comes
// from the server's `action`, never from this module).
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
import { amountText } from "./numbers.js";

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

/**
 * C23: shows the server's intercompany acknowledgement sentence
 * (`acknowledgements.intercompany`, set by signoff_model.summary, C21) as
 * the first row, so the Close Lead sees why a Green run asks for an
 * acknowledgement. The text is relayed verbatim — this module invents no
 * wording of its own. A missing/null value (not configured, not applicable,
 * or an older payload) adds no row.
 */
function acknowledgementsSection(ack) {
	const names = (ack && ack.names) || [];
	const total = ack ? ack.total : null;
	const unlisted = ack ? ack.unlisted : null;
	const intercompany = ack ? ack.intercompany : null;
	// #305-W5-2 (story 8.4): the server's commentary sentence, verbatim, after IC.
	const commentary = ack ? ack.commentary : null;
	const rows = [];
	for (const sentence of [intercompany, commentary]) {
		if (typeof sentence === "string" && sentence.length > 0) {
			rows.push(sentence);
		}
	}
	rows.push(...names.map((name) => `Acknowledged: ${name}`));
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
 * U47: the sign-off summary's commentary coverage (M46's `commentary` key,
 * story 9.1): per consolidation group, how many statement headings have
 * commentary for the period and which do not. Purely informational — it
 * never changes `action` or any gate, so a group with every heading
 * commented reads the same as one missing all of them except for its text.
 * A missing `commentary` key (an older payload) throws: M46 always sends
 * it, so there is no default to guess. R41j (U8): `signoff_api.py`'s
 * `_commentary` always returns a list (never null — `commentary_model.
 * missing_commentary` starts `result = []` and only appends) and
 * `result.update({... "commentary": _commentary(key) ...})` sets it
 * unconditionally on every call, so a `null` is as much a broken contract
 * as `undefined` — both throw here, naming the key, instead of a bare
 * `null` silently reading as an empty section.
 */
function commentarySection(list) {
	if (list === null || list === undefined) {
		throw new Error("Sign-off summary has no commentary.");
	}
	const rows = list.map((g) => {
		const missing = g.missing && g.missing.length ? ` · missing: ${g.missing.join(", ")}` : "";
		return `${g.consolidation_group}: ${g.with_commentary} of ${g.headings} headings commented${missing}`;
	});
	return section(rows);
}

const COMMENTARY_REQUIRED_STATES = ["undeclared", "checked", "unknown"];

function thresholdText(threshold) {
	const parts = [];
	if (threshold.amount !== null && threshold.amount !== undefined) {
		parts.push(amountText(threshold.amount));
	}
	if (threshold.percent !== null && threshold.percent !== undefined) {
		parts.push(`${threshold.percent}%`);
	}
	if (parts.length === 2) {
		if (threshold.combine === "Either is exceeded") return `${parts[0]} or ${parts[1]}`;
		if (threshold.combine === "Both are exceeded") return `${parts[0]} and ${parts[1]}`;
		throw new Error(`Unknown commentary threshold rule: ${threshold.combine}`);
	}
	if (parts.length === 1) return parts[0];
	throw new Error("The commentary threshold has neither an amount nor a percentage.");
}

/**
 * #305-W5-2 (story 8.4): `commentary_required` (signoff_api, the
 * commentary_model.requirement line) — the headings above the declared
 * Close Settings threshold that have no commentary, apart from the
 * informational `commentary` list. Undeclared or unknown shows the server's
 * own message (never a count); checked shows the threshold, then one row per
 * required heading, and a group with no comparison says why it was not
 * compared. A missing/null key or an unknown state throws (always sent).
 */
function commentaryRequiredSection(line) {
	if (line === null || line === undefined) {
		throw new Error("Sign-off summary has no commentary_required.");
	}
	if (!COMMENTARY_REQUIRED_STATES.includes(line.state)) {
		throw new Error(`Unknown commentary_required state: ${line.state}`);
	}
	if (line.state !== "checked") {
		return section(messageLines(line.message));
	}
	const rows = [
		"Threshold: a heading needs commentary when its variance against the previous period " +
			`is above ${thresholdText(line.threshold)}`,
	];
	for (const group of line.groups || []) {
		if (group.state === "not_comparable") {
			rows.push(`${group.consolidation_group}: not compared — ${group.message}`);
		}
		for (const r of group.required || []) {
			const pct = r.percent === null || r.percent === undefined
				? ""
				: ` (${r.percent}%)`;
			const base = r.percent === null || r.percent === undefined
				? " (no percentage on a zero base)"
				: "";
			rows.push(
				`${group.consolidation_group} · ${r.heading_name}: moved ${amountText(r.variance)}${pct} ` +
					`from ${amountText(r.comparison)}${base} — no commentary`,
			);
		}
	}
	if (line.required_missing === 0) {
		rows.push("Every heading above the threshold has commentary");
	}
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
		commentaryRequired: commentaryRequiredSection(summary.commentary_required),
		commentary: commentarySection(summary.commentary),
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

/**
 * #305-W5-1 (story 9.4, #157): what the Reject dialog does when the sign-off
 * machine moves from `prev` to `state`; `error` is the machine's
 * context.error after the move.
 * - "refused": the server refused the reject; the dialog stays open with the
 *   typed reason and shows the message. Either rejecting → signed, or
 *   (konsol#305 U3) rejecting → loading WITH a message: A58's stale-run
 *   refusal, which reloads the summary. The reason is kept while it
 *   reloads; nothing is re-sent, so the Close Lead clicks Reject again
 *   against the new summary's run (the button is disabled until the
 *   machine is back in `signed` and takes REJECT).
 * - "keep": still signed (including a reload after a stale refusal landing
 *   back in `signed`), or the reject is in flight.
 * - "reset": anything else (an accepted reject reloads with no message, a
 *   refresh from `signed`, a reload that lands outside `signed` so Reject is
 *   no longer offered, a close, a load failure); the dialog closes and its
 *   text goes.
 */
export function rejectDialogAfter(prev, state, error) {
	if (prev === "rejecting" && state === "signed") return "refused";
	if (prev === "rejecting" && state === "loading") return error ? "refused" : "reset";
	if (state === "signed" || state === "rejecting") return "keep";
	return "reset";
}

/** The Reject dialog when it is closed: nothing typed, nothing refused. */
export const REJECT_DIALOG_CLOSED = Object.freeze({ open: false, reason: "", refused: Object.freeze([]) });

/**
 * konsol#305 U3/U10: the Reject dialog's whole state ({open, reason,
 * refused}) after `event`; always a new object. SignOffReject.vue applies it
 * to every change, so no path changes the dialog any other way.
 * - OPEN: opens it, keeping nothing from before (a close already reset it).
 * - TYPE {text}: the reason as typed.
 * - SENT: the confirm was clicked; the last refusal is cleared.
 * - CLOSE: Cancel, Esc, the overlay or the dialog's own close (U10): back to
 *   REJECT_DIALOG_CLOSED, so reopening never shows an old reason or refusal.
 * - MACHINE {prev, state, error}: the sign-off machine moved
 *   (rejectDialogAfter decides).
 */
export function rejectDialogNext(dialog, event) {
	switch (event.type) {
		case "OPEN":
			return { open: true, reason: dialog.reason, refused: [...dialog.refused] };
		case "TYPE":
			return { ...dialog, refused: [...dialog.refused], reason: String(event.text ?? "") };
		case "SENT":
			return { ...dialog, refused: [] };
		case "CLOSE":
			return { open: false, reason: "", refused: [] };
		case "MACHINE": {
			const step = rejectDialogAfter(event.prev, event.state, event.error);
			if (step === "refused") return { ...dialog, refused: messageLines(event.error) };
			if (step === "reset") return { open: false, reason: "", refused: [] };
			return { ...dialog, refused: [...dialog.refused] };
		}
		default:
			throw new Error(`Reject dialog: unknown event ${event.type}`);
	}
}
