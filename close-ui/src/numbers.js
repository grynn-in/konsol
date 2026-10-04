// konsol#305 U41: numbers.js (part 1 of 2 — W42 adds the drill/commentary
// helpers in the same file, U42)
//
// Turns statement_api.get_statement's payload (N51:
// konsol/close/statement_api.py, konsol/close/statement_model.py) into
// everything the Numbers screen shows (story 8.1). The screen re-derives
// nothing: every label, tone, column set and cell text is decided here, so
// the component only walks `view.tabs` and binds `tabRows`/`isDrillable`.
// Takes no vue/frappe/xstate import; the period comes from the URL
// (route.js), not from here or from browser storage.
//
// No silent fallbacks (wave-4 rule):
// - a missing top-level payload key throws, naming it;
// - an unknown `signoff.state`, payload `state`, statement line `kind` or
//   `includes` entry `kind` throws, naming it — never shown as if it were
//   a known one;
// - `amountText` throws on `null`/`undefined`: a missing amount is never
//   0.00. The one declared exception is a comparison/variance cell when
//   the statement's `comparison_note` is set (no comparison period
//   available) — those read "not loaded", never a number and never blank.
//
// Amounts: two decimals with thousands grouping; a negative amount renders
// in brackets, "(1,234.50)" (mirrors tbTable.js's/adjustments.js's
// `AMOUNT_FORMAT`, defined again here since those files are outside this
// row's files).

import { formatTime, parseZoned } from "./timefmt.js";

const PL = "Profit and Loss";
const BS = "Balance Sheet";

// #305-W4-2 2a-ii, AMENDED 4 Oct — the legend text the decision gives
// verbatim. Shown on every state (it explains a display convention, not a
// fact about this payload), so it is this module's own constant rather
// than read from `payload.statement.legend`, which is absent on every
// non-ok state.
export const LEGEND =
	"Profit and loss: income positive, costs in brackets. " +
	"Balance sheet: assets, liabilities and equity positive.";

const COLUMNS = {
	[PL]: ["This period", "Comparison", "Variance", "Year to date"],
	[BS]: ["This period", "Comparison", "Variance"],
};

const SIGNOFF_LABEL = {
	signed: { text: "Signed", tone: "ok" },
	provisional: { text: "Provisional", tone: "warn" },
	resign_needed: { text: "Re-sign needed — numbers changed after signing", tone: "block" },
};

// statement_api.py's own non-ok states (W4-E8, module docstring): choose_group
// gets a fixed title (the groups to pick from are `groupChoice`, not this
// text); the other three show the server's own message verbatim.
const SERVER_MESSAGE_STATES = new Set(["no_chart", "not_built", "error"]);

const CHOOSE_GROUP_TITLE = "Choose a consolidation group";

const REQUIRED_KEYS = [
	"period",
	"groups",
	"consolidation_group",
	"group_note",
	"reporting_currency",
	"state",
	"message",
	"signoff",
	"statement",
	"gap",
	"not_included",
	"commentary",
	"can_comment",
];

const AMOUNT_FORMAT = new Intl.NumberFormat("en", {
	minimumFractionDigits: 2,
	maximumFractionDigits: 2,
});

/** A number -> "1,234.50" / "(1,234.50)" for negative. `null`/`undefined`
 * throws: a missing amount is never shown as 0.00 (the caller must decide
 * first whether this cell is "not loaded" instead of calling this). */
export function amountText(value) {
	if (value === null || value === undefined) {
		throw new Error("Numbers: missing amount — never shown as 0.00");
	}
	const formatted = AMOUNT_FORMAT.format(Math.abs(value));
	return value < 0 ? `(${formatted})` : formatted;
}

/** A comparison/variance cell: "not loaded" when the statement declared no
 * comparison period AND the value is null — never a guessed number, and
 * never confused with a genuine null (which still throws). Every other
 * cell (`current`, `ytd`, and this one when there is no `comparisonNote`)
 * goes through `amountText` unchanged. */
function comparisonCell(value, comparisonNote) {
	if (comparisonNote && (value === null || value === undefined)) {
		return "not loaded";
	}
	return amountText(value);
}

function header(payload) {
	const parts = [payload.period.code, payload.consolidation_group, payload.reporting_currency].filter(
		(part) => part !== null && part !== undefined && part !== "",
	);
	return ["Numbers", ...parts].join(" · ");
}

function buildLabel(signoff) {
	const found = SIGNOFF_LABEL[signoff.state];
	if (!found) {
		throw new Error(`Numbers: unknown sign-off state: ${signoff.state}`);
	}
	return found;
}

function buildState(payload) {
	if (payload.state === "ok") {
		return null;
	}
	if (payload.state === "choose_group") {
		return { kind: "choose_group", message: CHOOSE_GROUP_TITLE };
	}
	if (SERVER_MESSAGE_STATES.has(payload.state)) {
		return { kind: payload.state, message: payload.message };
	}
	throw new Error(`Numbers: unknown payload state: ${payload.state}`);
}

/** W4-E10's "entities not included" chip text. `null` when nothing is
 * missing (count 0) — never an empty sentence. */
function notIncludedText(notIncluded) {
	if (!notIncluded || !notIncluded.count) {
		return null;
	}
	const noun = notIncluded.count === 1 ? "entity" : "entities";
	const verb = notIncluded.count === 1 ? "is" : "are";
	const names = notIncluded.entities && notIncluded.entities.length ? `: ${notIncluded.entities.join(", ")}` : "";
	let text = `${notIncluded.count} ${noun} in scope ${verb} not in these numbers${names}`;
	if (notIncluded.hidden) {
		text += `, and ${notIncluded.hidden} outside your scope`;
	}
	return text;
}

const INCLUDE_KINDS = new Set(["cta", "current_year_result"]);

/** A heading line's `includes` entry (N49: the CTA/current-year-result
 * sub-amount placed under its BS heading) -> an indented "of which" row.
 * Never drillable (`isDrillable` only knows `kind: "heading"`). An
 * `includes` kind this module does not know throws, naming it. */
function includeRow(entry, comparisonNote) {
	if (!INCLUDE_KINDS.has(entry.kind)) {
		throw new Error(`Numbers: unknown includes kind: ${entry.kind}`);
	}
	return {
		kind: entry.kind,
		label: entry.label,
		indent: true,
		current: amountText(entry.current),
		comparison: comparisonCell(entry.comparison, comparisonNote),
	};
}

/** `commentary` is `statement_api`'s `{heading_code: {text, by, at, ...}}`
 * (N51 `_commentary`). `null` when the heading has none. */
function commentaryFor(commentary, headingCode, now, timeZone) {
	const entry = commentary[headingCode];
	if (!entry) {
		return null;
	}
	return {
		text: entry.text,
		byText: `${entry.by} · ${formatTime(parseZoned(entry.at), now, timeZone)}`,
	};
}

/** One `statement_model.statement` line -> one or more display rows (a
 * heading line also yields its `includes` rows, indented, right after it).
 * Unknown line kinds throw, naming the kind — a statement line is never
 * shown blank or guessed at. */
function rowsForLine(line, commentary, comparisonNote, now, timeZone) {
	switch (line.kind) {
		case "heading": {
			const row = {
				kind: "heading",
				heading: line.heading,
				label: line.heading_name,
				current: amountText(line.current),
				comparison: comparisonCell(line.comparison, comparisonNote),
				variance: comparisonCell(line.variance, comparisonNote),
				commentary: commentaryFor(commentary, line.heading, now, timeZone),
				commentable: true,
			};
			if ("ytd" in line) {
				row.ytd = amountText(line.ytd);
			}
			const rows = [row];
			for (const entry of line.includes || []) {
				rows.push(includeRow(entry, comparisonNote));
			}
			return rows;
		}
		case "no_heading":
		case "net_result": {
			const row = {
				kind: line.kind,
				label: line.label,
				current: amountText(line.current),
				comparison: comparisonCell(line.comparison, comparisonNote),
				variance: comparisonCell(line.variance, comparisonNote),
			};
			if ("ytd" in line) {
				row.ytd = amountText(line.ytd);
			}
			return [row];
		}
		case "not_in_chart": {
			return [
				{
					kind: "not_in_chart",
					label: line.label,
					codes: line.codes,
					current: amountText(line.current),
					comparison: comparisonCell(line.comparison, comparisonNote),
					variance: comparisonCell(line.variance, comparisonNote),
				},
			];
		}
		case "residual": {
			const tone = line.current === 0 ? "ok" : "block";
			const row = {
				kind: "residual",
				label: line.label,
				tone,
				current: amountText(line.current),
			};
			if (tone === "block") {
				row.explained = (line.explained || []).map((item) => ({
					label: item.label,
					amount: amountText(item.amount),
				}));
				row.unexplained = amountText(line.unexplained);
			}
			return [row];
		}
		default:
			throw new Error(`Numbers: unknown statement line kind: ${line.kind}`);
	}
}

function buildTabs(statement, commentary, now, timeZone) {
	const comparisonNote = statement.periods.comparison_note;
	return statement.sections.map((section) => {
		const columns = COLUMNS[section.section];
		if (!columns) {
			throw new Error(`Numbers: unknown statement section: ${section.section}`);
		}
		const rows = [];
		for (const line of section.lines) {
			rows.push(...rowsForLine(line, commentary, comparisonNote, now, timeZone));
		}
		return { section: section.section, columns, rows };
	});
}

/**
 * `statement_api.get_statement`'s payload -> everything the Numbers screen
 * shows: `{header, label, legend, state, groupChoice, notIncluded, gapText,
 * tabs, comparisonNote}` (U41 facts). `now`/`timeZone` are required for the
 * per-heading commentary's `byText` (mirrors timefmt.js's convention: no
 * reading of the machine's clock in here).
 *
 * Throws `"Numbers payload has no <key>."` on any missing top-level key —
 * no silent default for a key the producer did not send.
 */
export function statementView(payload, now, timeZone) {
	for (const key of REQUIRED_KEYS) {
		if (!Object.prototype.hasOwnProperty.call(payload, key)) {
			throw new Error(`Numbers payload has no ${key}.`);
		}
	}

	const label = buildLabel(payload.signoff);
	const state = buildState(payload);
	const isOk = payload.state === "ok";

	const tabs = isOk ? buildTabs(payload.statement, payload.commentary || {}, now, timeZone) : [];
	const comparisonNote = isOk ? payload.statement.periods.comparison_note : null;

	return {
		header: header(payload),
		label,
		legend: LEGEND,
		state,
		groupChoice: payload.state === "choose_group" ? (payload.groups || []).map((g) => g.consolidation_group) : null,
		notIncluded: notIncludedText(payload.not_included),
		gapText: payload.gap ? payload.gap.message : null,
		tabs,
		comparisonNote,
	};
}

/** The rows of one tab, in server order. An unknown section throws, naming
 * it — the screen never silently shows an empty table for a typo'd
 * section name. */
export function tabRows(view, section) {
	const tab = (view.tabs || []).find((t) => t.section === section);
	if (!tab) {
		throw new Error(`Numbers: view has no tab for section: ${section}`);
	}
	return tab.rows;
}

/** True only for a chart heading row (N52/W4-E15: a heading is the only
 * row the drill screen can open). Every other kind — the net result, an
 * "of which" sub-row, "not in the group chart", the unmatched residual —
 * is never drillable. */
export function isDrillable(row) {
	return row.kind === "heading";
}
