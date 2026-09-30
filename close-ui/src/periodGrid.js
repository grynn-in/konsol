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
//   key; the row's total count is `counts.rows`. The period code for the
//   empty-state text is `payload.period.code`, not a top-level `code`.
// - `get_readiness(fy, fp)` -> `{ready, total, items}` directly (no wrapper
//   key): `items` is `readiness_model.ITEMS`-ordered, each
//   `{code, state: "ok"|"blocked"|"unknown", label, detail, entities, hidden}`.

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

/**
 * `gridView(payload, problemsOnly)` -> `{rows, all, problems, hiddenNote,
 * ratesNote, empty}` for Period.vue's entity grid.
 *
 * `rows` are `payload.rows` filtered to `row.problem` when `problemsOnly`,
 * kept in server order (never re-sorted, as myWork.js does). `all` and
 * `problems` are read from `payload.counts`, never recomputed from `rows`.
 * Every row's three cell tones are validated eagerly (an unknown tone
 * throws), so a bad tone from the server surfaces as an error rather than
 * rendering silently.
 */
export function gridView(payload, problemsOnly) {
	const code = payload.period && payload.period.code;
	const counts = payload.counts || {};
	const rows = (payload.rows || [])
		.filter((row) => !problemsOnly || row.problem)
		.map(validateRow);
	const hiddenNote =
		counts.hidden > 0 ? `${counts.hidden} entities outside your scope are not shown` : null;
	const ratesNote = payload.rates_error ? `Rates cannot be checked: ${payload.rates_error}` : null;
	const empty = problemsOnly ? `No problems in ${code}` : `No entities in scope for ${code}`;
	return {
		rows,
		all: counts.rows,
		problems: counts.problems,
		hiddenNote,
		ratesNote,
		empty,
	};
}

const GLYPHS = { ok: "✓", blocked: "✕", unknown: "?" };

// The visible entity codes plus, when the server hid some, the count it
// hid — never the hidden codes themselves (readiness_model.py: a hidden
// entity's code never appears anywhere in the result).
function entitiesText(item) {
	const parts = [];
	if (item.entities && item.entities.length) {
		parts.push(item.entities.join(", "));
	}
	if (item.hidden) {
		parts.push(`and ${item.hidden} outside your scope`);
	}
	return parts.length ? parts.join(" ") : null;
}

/**
 * `readinessView(payload)` -> `{title, items: [{glyph, text, entities}]}`
 * for the readiness strip. `payload` is `get_readiness`'s return value
 * directly. An unknown item state throws.
 */
export function readinessView(payload) {
	const items = (payload.items || []).map((item) => {
		if (!Object.prototype.hasOwnProperty.call(GLYPHS, item.state)) {
			throw new Error(`unknown state: ${item.state}`);
		}
		return {
			glyph: GLYPHS[item.state],
			text: item.detail,
			entities: entitiesText(item),
		};
	});
	return {
		title: `Ready to close · ${payload.ready} of ${payload.total}`,
		items,
	};
}
