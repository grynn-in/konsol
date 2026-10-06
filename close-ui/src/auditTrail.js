// konsol#305 T08a: auditTrail.js
//
// Pure view model for the Audit trail board (board 8; story 10.1). Turns
// trail_api.get_trail's payload into the summary strip and the event rows
// AuditTrail.vue (T08b) renders. Every event kind has a text label and a
// tone (never colour alone); an unknown kind throws rather than rendering
// blank. Mirrors checks.js:1-60 (header comment, constant maps, a thrown
// error on an unknown state) and periodGrid.js's hiddenNote. No vue,
// frappe or xstate import.
//
// Payload shape, read from konsol/close/trail_api.py and
// konsol/close/trail_model.py in this worktree (T07a, T07b, amended 2 Oct
// by #305-W2-8 and #305-W2-9), not the row's sketch -- see the iteration
// log for where this differs from the sketch:
// - `get_trail(fiscal_year, fiscal_period)` -> `{period: {fiscal_year,
//   fiscal_period, code, status}, summary, events, hidden}`.
// - `summary` (trail_model.summary, with datetimes made ISO by
//   trail_api._iso_summary) is `{signoff, closed, locked, counts}`.
//   `signoff` is `{"state": "none"}`, or `{state: "signed", by, at,
//   result, run_status, reason, warnings}`, or `{state: "voided" | "rejected", by, at,
//   reason}`. `closed`/`locked` are `{by, at}` or null. `trail_api`
//   (T07c) adds `by_name` and `by_missing` next to `by` on `signoff`,
//   `closed` and `locked`, resolved from the same single User lookup
//   `_event_out` uses for `actor_name` -- so `by` is the raw actor id but
//   is never shown raw once a name exists (T08d's `summaryBy`): the
//   "signed" line shows `by_name`, or "<id> (user deleted)" when
//   `by_missing`.
//   `counts` is `{approvals, self_approvals, rejections, on_behalf_uploads,
//   acknowledgements, overrides, reopenings, recovered,
//   reasons_not_recorded, cancellations}`.
// - `events[]`, newest first, each `{name, kind, entity,
//   reference_doctype, reference_name, actor, actor_name, actor_missing,
//   actor_persona, at, reason, detail, source}`. `at` is a zoned ISO
//   string. `detail` is the parsed object (or null).
// - `hidden` is the count of entity-scoped events the caller's scope
//   dropped (#305-W2-9); it is always present, 0 when nothing was hidden.
// - Story 10.2: `total` is the scoped event count before the filters;
//   `filters` echoes the applied filters (`{kinds, actors, entities}` as
//   lists, [] = none; `date_from`/`date_to` ISO or null); `options` holds
//   the scoped choices (`kinds`, `entities` with GROUP_LEVEL for
//   group-level events, and `actors` as `{actor, actor_name,
//   actor_missing}`). The server filters; this module only labels the
//   choices and turns a filter state into query params.

import { parseZoned, formatTime } from "./timefmt.js";

// The Close Event kinds whose label and tone do not depend on `detail`.
// `signed_off` and `tb_submitted` are handled separately below -- their
// label depends on `detail.signoff_status` / `detail.on_behalf` -- but
// every kind in close_event_model.KINDS has a label somewhere in this
// file (see auditTrail.test.mjs's parity test against that module).
const KIND_LABEL = {
	approved: { label: "Approved", tone: "ok" },
	self_approved: { label: "Self-approved", tone: "warn" },
	rejected: { label: "Rejected", tone: "block" },
	approval_cancelled: { label: "Approval cancelled", tone: "warn" },
	period_closed: { label: "Closed", tone: "mute" },
	period_locked: { label: "Locked", tone: "mute" },
	period_reopened: { label: "Reopened", tone: "warn" },
	year_closed: { label: "Year closed", tone: "mute" },
	year_locked: { label: "Year locked", tone: "mute" },
	year_reopened: { label: "Year reopened", tone: "warn" },
	signoff_voided: { label: "Sign-off voided", tone: "warn" },
	// #305-W5-1 (story 9.4, #157): the Close Lead sent the signed run back.
	signoff_rejected: { label: "Sign-off rejected", tone: "block" },
	tb_cancelled: { label: "Cancelled", tone: "mute" },
	tb_exception_declared: { label: "No-TB exception", tone: "warn" },
	tb_exception_cancelled: { label: "Exception cancelled", tone: "mute" },
	ic_sent_back: { label: "Sent back", tone: "warn" },
	commentary_saved: { label: "Commentary", tone: "mute" },
};

const SIGNOFF_LABEL = {
	"Signed Off": { label: "Signed off", tone: "ok" },
	Acknowledged: { label: "Acknowledged", tone: "warn" },
	Overridden: { label: "Overridden", tone: "block" },
	unknown: { label: "Signed off (state not recorded)", tone: "warn" },
};

const PERSONA_LABEL = {
	close_lead: "Close Lead",
	group_accountant: "Group Accountant",
	entity_accountant: "Entity Accountant",
	viewer: "Viewer",
};

const TB_KINDS = new Set([
	"tb_submitted", "tb_cancelled", "tb_exception_declared", "tb_exception_cancelled",
]);
const PERIOD_KINDS = new Set(["period_closed", "period_locked", "period_reopened"]);
const YEAR_KINDS = new Set(["year_closed", "year_locked", "year_reopened"]);

/** `{label, tone}` for one event. Throws on an unknown kind, naming it,
 * and on a `signed_off` whose `detail.signoff_status` is not one of the
 * declared states (never shown as "Signed off"). */
function labelTone(event) {
	const detail = event.detail || {};
	if (event.kind === "signed_off") {
		const found = SIGNOFF_LABEL[detail.signoff_status];
		if (!found) {
			throw new Error(`Unknown sign-off status: ${detail.signoff_status}`);
		}
		return found;
	}
	if (event.kind === "tb_submitted") {
		if (detail.on_behalf === "Yes") return { label: "On behalf", tone: "warn" };
		if (detail.on_behalf === "No") return { label: "Submitted", tone: "mute" };
		return { label: "Submitted (on-behalf not recorded)", tone: "mute" };
	}
	const found = KIND_LABEL[event.kind];
	if (!found) {
		throw new Error(`Unknown Close Event kind: ${event.kind}`);
	}
	return found;
}

/** `periodFiscalYear` is `payload.period.fiscal_year` (the server's
 * period), the only fiscal year the payload carries -- `_event_out`
 * (trail_api.py:116-137) sends no `fiscal_year` on the event itself. */
function itemText(event, periodFiscalYear) {
	if (TB_KINDS.has(event.kind)) {
		return `Trial balance · ${event.entity}`;
	}
	if (PERIOD_KINDS.has(event.kind)) {
		return `Period ${(event.detail || {}).period_code}`;
	}
	if (YEAR_KINDS.has(event.kind)) {
		return `FY${periodFiscalYear}`;
	}
	if (event.kind === "ic_sent_back") {
		const { entity_a, account_a, entity_b, account_b } = event.detail || {};
		if (!entity_a || !account_a || !entity_b || !account_b) {
			return "Intercompany pair (not recorded)";
		}
		return `Intercompany · ${entity_a} ${account_a} ↔ ${entity_b} ${account_b}`;
	}
	if (event.kind === "commentary_saved") {
		const { heading, heading_name } = event.detail || {};
		if (!heading || !heading_name) {
			return "Commentary (heading not recorded)";
		}
		return `Commentary · ${heading_name} (${heading})`;
	}
	return `${event.reference_doctype} ${event.reference_name}`;
}

/** `actor_name`, plus ` · <persona label>` when `actor_persona` is set.
 * Throws on a persona not among #298's four job titles. */
function byText(event) {
	const persona = event.actor_persona;
	if (persona == null) {
		return event.actor_name;
	}
	const label = PERSONA_LABEL[persona];
	if (!label) {
		throw new Error(`Unknown persona: ${persona}`);
	}
	return `${event.actor_name} · ${label}`;
}

/** The detail line: a quoted reason, "Prepared by" for an `approved`
 * event, the year-action note, the unrecorded-reason note and the
 * recovered-from-backfill note, joined by " · ". */
function detailText(event) {
	const detail = event.detail || {};
	const parts = [];
	if (event.reason) {
		if (event.kind === "signed_off" && detail.signoff_status === "Acknowledged") {
			parts.push(`Acknowledged: "${event.reason}"`);
		} else {
			parts.push(`Reason: "${event.reason}"`);
		}
	}
	if (event.kind === "approved") {
		parts.push(`Prepared by ${detail.preparer}`);
	}
	if (detail.via === "year") {
		parts.push("by the year action");
	}
	if (detail.reason_not_recorded) {
		parts.push("Reason not recorded");
	}
	if (event.kind === "commentary_saved") {
		// R41j (U8): `commentary_model.event_detail` always sets `"text":
		// text or ""` (konsol/close/commentary_model.py:85-93) — never
		// omits the key, even for a clearing save. So a missing key here is
		// a broken contract, not a clear: only an actual blank string reads
		// as "Commentary cleared".
		if (!("text" in detail)) {
			throw new Error("Audit trail: commentary_saved event has no detail.text.");
		}
		parts.push(detail.text ? `Text: "${detail.text}"` : "Commentary cleared");
	}
	if (event.source === "backfill") {
		parts.push("Recovered from records");
	}
	return parts.join(" · ");
}

function eventRow(event, now, timeZone, periodFiscalYear) {
	const { label, tone } = labelTone(event);
	return {
		name: event.name,
		kind: event.kind,
		label,
		tone,
		item: itemText(event, periodFiscalYear),
		by: byText(event),
		time: formatTime(parseZoned(event.at), now, timeZone),
		detail: detailText(event),
	};
}

/** T08d: the summary's resolved actor text for an entry that carries `by`
 * (trail_api._with_by_name: `signoff`/`closed`/`locked`). `by_name` when
 * the actor was resolved; "<id> (user deleted)" when `by_missing`; the
 * raw `by` unchanged when no name was resolved at all (an older payload
 * with no `by_name`/`by_missing`, so trailView still renders rather than
 * guessing). Never shows the raw login once a name exists. */
function summaryBy(entry) {
	if (entry.by_missing) {
		return `${entry.by} (user deleted)`;
	}
	if (Object.prototype.hasOwnProperty.call(entry, "by_name")) {
		return entry.by_name;
	}
	return entry.by;
}

function signedOffText(signoff, now, timeZone) {
	if (signoff.state === "none") {
		return "Not signed off";
	}
	if (signoff.state === "voided") {
		return `Voided — ${signoff.reason}`;
	}
	if (signoff.state === "rejected") {
		return `Rejected — ${signoff.reason}`;
	}
	if (signoff.state === "signed") {
		return `${summaryBy(signoff)} · ${formatTime(parseZoned(signoff.at), now, timeZone)}`;
	}
	throw new Error(`Unknown sign-off state: ${signoff.state}`);
}

function resultText(signoff) {
	if (signoff.state !== "signed") {
		return null;
	}
	let text = `${signoff.run_status} — ${signoff.result}`;
	if (signoff.warnings) {
		text += ` · ${signoff.warnings}`;
	}
	return text;
}

function closedLockedText(summary, now, timeZone) {
	const parts = [];
	if (summary.closed) {
		parts.push(`Closed ${formatTime(parseZoned(summary.closed.at), now, timeZone)}`);
	}
	if (summary.locked) {
		parts.push(`Locked ${formatTime(parseZoned(summary.locked.at), now, timeZone)}`);
	}
	return parts.length ? parts.join(" · ") : "Open";
}

function exceptionsText(counts) {
	return `${counts.self_approvals} self-approval(s) · ${counts.on_behalf_uploads} on-behalf upload(s) · ${counts.overrides} override(s)`;
}

/** Story 10.2: trail_model.GROUP_LEVEL, the entity choice that selects
 * group-level (blank-entity) events. auditTrail.test.mjs pins it to the
 * Python constant. */
export const GROUP_LEVEL = "(group)";

const DYNAMIC_KIND_LABEL = { signed_off: "Signed off", tb_submitted: "Submitted" };

function kindChoiceLabel(kind) {
	if (DYNAMIC_KIND_LABEL[kind]) return DYNAMIC_KIND_LABEL[kind];
	const found = KIND_LABEL[kind];
	if (!found) {
		throw new Error(`Unknown Close Event kind: ${kind}`);
	}
	return found.label;
}

/** The empty filter state (every field unfiltered). */
export function noFilters() {
	return { kinds: [], actors: [], entities: [], date_from: null, date_to: null };
}

function anyFilter(filters) {
	return Boolean(
		filters && (
			(filters.kinds && filters.kinds.length) ||
			(filters.actors && filters.actors.length) ||
			(filters.entities && filters.entities.length) ||
			filters.date_from || filters.date_to
		),
	);
}

/** `list` with `value` added when absent, removed when present; a new array. */
export function toggled(list, value) {
	return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

/** A filter state as get_trail / export_trail_csv query params: non-empty
 * lists as JSON, dates as given; an empty field is left out. */
export function filterParams(filters) {
	const params = {};
	for (const key of ["kinds", "actors", "entities"]) {
		if (filters[key] && filters[key].length) params[key] = JSON.stringify(filters[key]);
	}
	for (const key of ["date_from", "date_to"]) {
		if (filters[key]) params[key] = filters[key];
	}
	return params;
}

/** The labelled filter choices from `payload.options` (the scoped period's
 * kinds, actors and entities, never a hidden entity's). Throws on a
 * payload with no options and on an unknown kind. */
export function filterChoices(payload) {
	if (!payload || !payload.options) {
		throw new Error("Audit trail payload has no filter options.");
	}
	const { kinds, actors, entities } = payload.options;
	return {
		kinds: kinds.map((kind) => ({ value: kind, label: kindChoiceLabel(kind) })),
		actors: actors.map((a) => ({
			value: a.actor,
			label: a.actor_missing ? `${a.actor} (user deleted)` : a.actor_name,
		})),
		entities: entities.map((code) => ({
			value: code,
			label: code === GROUP_LEVEL ? "Group-level" : code,
		})),
	};
}

/**
 * `trailView(payload, now, timeZone)` -> `{signedOff, result, closedLocked,
 * exceptions, rows, countNote, hiddenNote}` for AuditTrail.vue (T08b).
 *
 * `rows` are `payload.events` in the server's (newest-first) order, never
 * re-sorted. Every event's kind is resolved to a label and a tone eagerly;
 * an unknown kind throws, naming it, so it is never rendered blank.
 * `hiddenNote` is "N events for entities outside your scope are not shown"
 * when `payload.hidden > 0`, else null (mirrors periodGrid.js's
 * `hiddenNote`). `payload.hidden` must be present -- it is never guessed
 * as 0. `countNote` (10.2) is "Showing N of M events" when the payload's
 * echoed filters are not empty, else null; a filtered payload with no
 * `total` throws.
 */
export function trailView(payload, now, timeZone) {
	if (!payload || !payload.summary) {
		throw new Error("Audit trail payload has no summary.");
	}
	if (!Object.prototype.hasOwnProperty.call(payload, "hidden")) {
		throw new Error("Audit trail payload has no hidden count.");
	}
	const { summary, events, period } = payload;
	let countNote = null;
	if (anyFilter(payload.filters)) {
		if (!Object.prototype.hasOwnProperty.call(payload, "total")) {
			throw new Error("Audit trail payload is filtered but has no total.");
		}
		countNote = `Showing ${events.length} of ${payload.total} events`;
	}
	return {
		signedOff: signedOffText(summary.signoff, now, timeZone),
		result: resultText(summary.signoff),
		closedLocked: closedLockedText(summary, now, timeZone),
		exceptions: exceptionsText(summary.counts),
		rows: events.map((event) => eventRow(event, now, timeZone, period.fiscal_year)),
		countNote,
		hiddenNote: payload.hidden > 0
			? `${payload.hidden} events for entities outside your scope are not shown`
			: null,
	};
}

// Exported for the parity test: every kind close_event_model.py declares
// must resolve to a label here (statically, or via SIGNOFF_LABEL /
// tb_submitted's on-behalf switch), or trailView throws.
export const STATIC_KINDS = Object.keys(KIND_LABEL);
export const DYNAMIC_KINDS = ["signed_off", "tb_submitted"];
