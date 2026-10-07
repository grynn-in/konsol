// konsol#305 E207: periodGrid.js
//
// Pure view model for Period.vue (E208a): turns grid_api.get_period_grid's
// and grid_api.get_readiness's payloads into what the screen renders — the
// filtered rows, the All/Problems counts, tone validation and the readiness
// lines. There is no IC column and no Checks column (W2-4).
//
// Payload shapes, read from konsol/close/grid_api.py, period_grid_model.py
// and readiness_model.py in this worktree (E203, E204, E201b), not the
// row's sketch — see the iteration log for where they differ:
// - `get_period_grid(fy, fp)` -> {period: {fiscal_year, fiscal_period, code,
//   status, start_date}, rows: [{entity, name, currency, in_scope, problem,
//   ownership: {tone, label}, tb: {tone, label}, rate: {tone, label}}],
//   counts: {rows, problems, hidden}, rates_error}. `counts` has no `all`
//   key; the row's total count is `counts.rows`. The header and the
//   empty-state text name the period by `periodName(fiscal_year,
//   fiscal_period)`, never the bare `payload.period.code` ("P07" live).
// - `get_readiness(fy, fp)` -> `{ready, total, items}` directly (no wrapper
//   key): `items` is `readiness_model.ITEMS`-ordered, each
//   `{code, state: "ok"|"blocked"|"unknown", label, detail, entities, hidden}`.

import { periodName } from "./periodName.js";
import { remindedText } from "./remind.js";

export const COLUMNS = ["Ownership", "Trial balance", "Closing rate"];
export const TONES = ["ok", "blocking", "none"];

// The classes TrialBalances.vue's STATUS_TONE uses for Received / Missing /
// Not expected this period (B19).
const TONE_CLASSES = {
	ok: "bg-surface-green-1 text-ink-green-3",
	blocking: "bg-surface-red-1 text-ink-red-3",
	none: "bg-surface-gray-1 text-ink-gray-6",
};

export function toneClass(tone) {
	if (!Object.prototype.hasOwnProperty.call(TONE_CLASSES, tone)) {
		throw new Error(`unknown tone: ${tone}`);
	}
	return TONE_CLASSES[tone];
}

// Validates a row's three cell tones (throwing on any unknown one) without
// changing the row's shape: the class itself is looked up by the caller via
// `toneClass` at render time.
function validateRow(row) {
	toneClass(row.ownership.tone);
	toneClass(row.tb.tone);
	toneClass(row.rate.tone);
	return row;
}

// Y63 (stories 1.5, 2.2): the reminded text of a row's Trial balance cell.
// Y57 always sends `tb.reminders` (null when none was sent), so a missing key
// throws. Only a Missing cell shows the text; there is no Remind button on
// the grid (C-R1: Remind lives on the TB list and the IC panel).
function tbReminded(row, now, timeZone) {
	if (!("reminders" in row.tb)) {
		throw new Error(`gridView: ${row.entity}'s Trial balance cell has no reminders entry (Y57 always sends it, null when none).`);
	}
	return row.tb.label === "Missing" ? remindedText(row.tb.reminders, now, timeZone) : null;
}

/**
 * `gridView(payload, problemsOnly, now, timeZone)` -> `{rows, all, problems, hiddenNote,
 * ratesNote, empty}` for Period.vue's entity grid.
 *
 * `rows` are `payload.rows` filtered to `row.problem` when `problemsOnly`,
 * kept in server order (never re-sorted, as myWork.js does). `all` and
 * `problems` are read from `payload.counts`, never recomputed from `rows`.
 * Every row's three cell tones are validated eagerly (an unknown tone
 * throws), so a bad tone from the server surfaces as an error rather than
 * rendering silently.
 *
 * Y63: each row gains `tbReminded`, remind.js's "Reminded N× · last <time>
 * by <name>" for a Missing TB cell's `reminders` entry (null otherwise),
 * formatted in `timeZone` relative to `now`; both are required, as in
 * tbTable.entityRows.
 */
export function gridView(payload, problemsOnly, now, timeZone) {
	if (!timeZone) {
		throw new Error("gridView requires a time zone");
	}
	if (!(now instanceof Date) || Number.isNaN(now.getTime())) {
		throw new Error("gridView requires a valid `now`");
	}
	//: review-w5: the live period code is "P07" alone; the header and the
	//: empty text name the year (periodName).
	const code = periodName(payload.period.fiscal_year, payload.period.fiscal_period);
	const counts = payload.counts || {};
	const rows = (payload.rows || [])
		.filter((row) => !problemsOnly || row.problem)
		.map(validateRow)
		.map((row) => ({ ...row, tbReminded: tbReminded(row, now, timeZone) }));
	const hiddenNote =
		counts.hidden > 0 ? `${counts.hidden} entities outside your scope are not shown` : null;
	const ratesNote = payload.rates_error ? `Rates cannot be checked: ${payload.rates_error}` : null;
	const empty = problemsOnly ? `No problems in ${code}` : `No entities in scope for ${code}`;
	return {
		title: `Period ${code}`,
		rows,
		all: counts.rows,
		problems: counts.problems,
		hiddenNote,
		ratesNote,
		empty,
	};
}

const GLYPHS = { ok: "✓", blocked: "✕", unknown: "?" };

// E207b: never print more than this many entity codes in the readiness
// strip (found on the live demo walk, 2 Oct — a configuration item printed
// 306 codes).
const MAX_VISIBLE_ENTITIES = 5;

// The visible entity codes plus, when the server hid some, the count it
// hid — never the hidden codes themselves (readiness_model.py: a hidden
// entity's code never appears anywhere in the result). When the item names
// more than MAX_VISIBLE_ENTITIES codes, only the first MAX_VISIBLE_ENTITIES
// are printed, followed by "and N more" for the rest.
//
// R01m: when no entities are visible (`item.entities` is empty), the server
// already folded the hidden count into `detail` itself (readiness_model's
// `_hidden_detail`: "N entities you cannot see") — there is nothing left to
// list, so this returns null rather than a bare "and N outside your scope"
// that would double-count alongside that detail text. The suffix is only
// ever appended here when there is a visible list to attach it to.
function entitiesText(item) {
	if (!item.entities || !item.entities.length) {
		return null;
	}
	const visible = item.entities.slice(0, MAX_VISIBLE_ENTITIES);
	let codes = visible.join(", ");
	if (item.entities.length > MAX_VISIBLE_ENTITIES) {
		codes += ` and ${item.entities.length - MAX_VISIBLE_ENTITIES} more`;
	}
	if (item.hidden) {
		codes += ` and ${item.hidden} outside your scope`;
	}
	return codes;
}

/**
 * `readinessView(payload)` -> `{title, items: [{glyph, text, entities}]}`
 * for the readiness strip. `payload` is `get_readiness`'s return value
 * directly. An unknown item state throws. `text` is the item's label and
 * detail together ("Checks: Green"), so the strip never shows a bare detail
 * with no indication of which check it belongs to (R01m).
 */
export function readinessView(payload) {
	const items = (payload.items || []).map((item) => {
		if (!Object.prototype.hasOwnProperty.call(GLYPHS, item.state)) {
			throw new Error(`unknown state: ${item.state}`);
		}
		return {
			glyph: GLYPHS[item.state],
			text: `${item.label}: ${item.detail}`,
			entities: entitiesText(item),
		};
	});
	return {
		title: `Ready to close · ${payload.ready} of ${payload.total}`,
		items,
	};
}
