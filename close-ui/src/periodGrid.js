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
import { dueDateText } from "./dueDate.js";

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

// D61 (stories 2.4, 2.2): the deadlines strip above the grid. The steps and
// their order are deadline_model.STEPS; the labels are the strip's words.
export const DEADLINE_STEPS = ["tb", "ic", "journals", "signoff"];
const STEP_LABELS = { tb: "TB", ic: "IC", journals: "Journals", signoff: "Sign-off" };
// The server flag that marks each step Overdue in the strip (D61, D61b).
// TB has none here: it is marked per cell.
const STEP_OVERDUE_KEYS = { ic: "ic_overdue", journals: "journals_overdue", signoff: "signoff_overdue" };
// R52m (review S4, R52e): the server sends these flags null, with a sentence
// in the matching *_error key, when it could not read them. Sign-off has none.
const STEP_ERROR_KEYS = { ic: "ic_overdue_error", journals: "journals_overdue_error" };

function isSentence(value) {
	return typeof value === "string" && value.trim() !== "";
}

function requireKey(obj, key, where) {
	if (obj == null || !Object.prototype.hasOwnProperty.call(obj, key)) {
		throw new Error(`${where} has no ${key} entry (D57 always sends it).`);
	}
	return obj[key];
}

/**
 * `deadlineStrip(payload)` -> `[{step, label, text, overdue, error}]`, one
 * per DEADLINE_STEPS step, in that order, from `payload.deadlines` (D57: each
 * step `{due, past, text}`). A declared step reads "<Label> due <date>" in
 * dueDate.js's wording ("TB due Fri 3 Oct 2025", R52m/U1); an undeclared one
 * (`due` null) reads "<Label>: <text>", the server's "No due date declared",
 * never a guessed date. A `due` that is not an ISO date throws, and so does
 * an undeclared step whose `text` is not a non-blank string.
 *
 * `overdue` is only ever a server flag, never derived from `past` (one
 * source of truth): Sign-off reads `payload.signoff_overdue`, IC reads
 * `payload.ic_overdue` and Journals reads `payload.journals_overdue` (D61b,
 * #305-Q5-1: D57b decides them). TB overdue is shown per cell
 * (`tbOverdue`), so the TB item is not marked.
 *
 * R52m (S4): `ic_overdue_error` and `journals_overdue_error` are required.
 * When the server could not read a flag it sends it null with a sentence in
 * the error key; that step then carries `overdue: null` and `error: <the
 * sentence>`, which the screen shows in place of the chip, and the rest of
 * the grid still renders. Every other step's `error` is null.
 *
 * A missing `deadlines` key, a missing or unknown step, a step missing
 * `due` or `text`, a missing or non-boolean `signoff_overdue`, a missing
 * `ic_overdue`/`journals_overdue` or its `*_error` key, a null flag with no
 * error sentence, a non-boolean flag, or a true/false flag that also carries
 * an error throws.
 */
export function deadlineStrip(payload) {
	const deadlines = requireKey(payload, "deadlines", "The grid payload");
	if (deadlines == null || typeof deadlines !== "object") {
		throw new Error("The grid payload's deadlines entry is not an object.");
	}
	for (const step of Object.keys(deadlines)) {
		if (!DEADLINE_STEPS.includes(step)) {
			throw new Error(`unknown deadline step: ${step}`);
		}
	}
	const stepOverdue = { tb: false };
	const stepError = { tb: null, signoff: null };
	for (const [step, key] of Object.entries(STEP_OVERDUE_KEYS)) {
		const flag = requireKey(payload, key, "The grid payload");
		const errorKey = STEP_ERROR_KEYS[step];
		const error = errorKey ? requireKey(payload, errorKey, "The grid payload") : null;
		if (errorKey && flag === null) {
			if (!isSentence(error)) {
				throw new Error(`The grid payload's ${key} is null but ${errorKey} gives no reason: ${error}`);
			}
			stepOverdue[step] = null;
			stepError[step] = error;
			continue;
		}
		if (typeof flag !== "boolean") {
			throw new Error(`The grid payload's ${key} is not true or false: ${flag}`);
		}
		if (errorKey && error !== null) {
			throw new Error(`The grid payload's ${key} is ${flag} but ${errorKey} is set: ${error}`);
		}
		stepOverdue[step] = flag;
		stepError[step] = null;
	}
	return DEADLINE_STEPS.map((step) => {
		const entry = requireKey(deadlines, step, "The grid payload's deadlines");
		const label = STEP_LABELS[step];
		const due = requireKey(entry, "due", `The ${step} deadline`);
		const text = requireKey(entry, "text", `The ${step} deadline`);
		if (due === null && !isSentence(text)) {
			throw new Error(`The ${step} deadline has no due date and no text: ${text}`);
		}
		return {
			step,
			label,
			text: due === null ? `${label}: ${text}` : `${label} due ${dueDateText(due, "deadlineStrip")}`,
			overdue: stepOverdue[step],
			error: stepError[step],
		};
	});
}

// D61: a TB cell's `overdue` is the server's flag (D57 sets it on a Missing
// cell past the TB date); a missing key throws, never reads as not overdue.
function tbOverdue(row) {
	const overdue = requireKey(row.tb, "overdue", `${row.entity}'s Trial balance cell`);
	if (typeof overdue !== "boolean") {
		throw new Error(`${row.entity}'s Trial balance cell overdue is not true or false: ${overdue}`);
	}
	return overdue;
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
 *
 * D61: the view also carries `deadlines` (deadlineStrip) and each row
 * `tbOverdue`, the TB cell's server `overdue` flag.
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
	const deadlines = deadlineStrip(payload);
	const code = periodName(payload.period.fiscal_year, payload.period.fiscal_period);
	const counts = payload.counts || {};
	const rows = (payload.rows || [])
		.filter((row) => !problemsOnly || row.problem)
		.map(validateRow)
		.map((row) => ({ ...row, tbReminded: tbReminded(row, now, timeZone), tbOverdue: tbOverdue(row) }));
	const hiddenNote =
		counts.hidden > 0 ? `${counts.hidden} entities outside your scope are not shown` : null;
	const ratesNote = payload.rates_error ? `Rates cannot be checked: ${payload.rates_error}` : null;
	const empty = problemsOnly ? `No problems in ${code}` : `No entities in scope for ${code}`;
	return {
		title: `Period ${code}`,
		rows,
		deadlines,
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
