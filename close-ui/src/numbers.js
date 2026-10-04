// konsol#305 U41/U42: numbers.js
//
// U41: turns statement_api.get_statement's payload (N51:
// konsol/close/statement_api.py, konsol/close/statement_model.py) into
// everything the Numbers screen shows (story 8.1). The screen re-derives
// nothing: every label, tone, column set and cell text is decided here, so
// the component only walks `view.tabs` and binds `tabRows`/`isDrillable`.
// U42: turns statement_api.get_drill's payload (N52: drill_model.py) into
// the drill panel's view (`drillView`), and builds the one commentary
// request body (`commentaryBody`) and the one comment-permission check
// (`canComment`) the drill panel's editor needs (stories 8.2, 8.3).
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
	if (typeof value !== "number" || !Number.isFinite(value)) {
		throw new Error(`Numbers: non-finite amount (${value}) — never shown as "NaN"`);
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

/** `entry` is one `statement_api._commentary` record (`{name, text, by, at,
 * modified}`, N51/N52) -> "<by> · <formatted time>", the one commentary
 * byline text for both the statement row (via `commentaryFor` below) and
 * the drill panel's own commentary display (U42/U46), which reads the
 * entry straight from the payload rather than through `statementView`. */
export function commentaryByText(entry, now, timeZone) {
	return `${entry.by} · ${formatTime(parseZoned(entry.at), now, timeZone)}`;
}

/** `commentary` is `statement_api`'s `{heading_code: {text, by, at, ...}}`
 * (N51 `_commentary`). `null` when the heading has none — including a
 * CLEARED entry (R41h/U7): a blank save still keeps the record
 * (commentary_model.event_detail records `text: ""` rather than deleting
 * it), but the statement row must not show it as a byline-less comment. The
 * drill panel (NumbersDrill.vue) reads the raw entry straight from the
 * payload for its own display and its save token, independent of this
 * function. */
function commentaryFor(commentary, headingCode, now, timeZone) {
	const entry = commentary[headingCode];
	if (!entry || !entry.text) {
		return null;
	}
	return {
		text: entry.text,
		byText: commentaryByText(entry, now, timeZone),
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
		// The real `statement_model.statement()` payload, unchanged, riding
		// along for `beforeAfter` (W42/N54): it carries each heading line's
		// own `sign` (the exact multiplier `statement()` already applied —
		// `-1` for every P&L heading, the per-heading Debit/Credit result
		// for a BS heading). `null` on a non-ok payload, mirroring `tabs`/
		// `comparisonNote` — never a guess at a statement that wasn't built.
		statement: isOk ? payload.statement : null,
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

// --- U42: statement_api.get_drill's payload -> the drill panel's view ------
//
// `drillView` mirrors `statementView`'s conventions on `get_drill`'s own
// payload (N52: konsol/close/statement_api.py, konsol/close/drill_model.py):
// a missing top-level key throws naming it; an unknown `state` throws; a
// non-ok `state` shows the server's message and no rows (never a guessed
// breakdown). Unlike the statement, a drill row's `layer`/`label` are shown
// exactly as the server sends them (drill_model.py already merged and
// labelled every adjustment_type it knows, and a type it does not recognise
// is still labelled — W4-E6) — this module does not re-map them.

const DRILL_REQUIRED_KEYS = ["period", "consolidation_group", "heading", "state", "message", "drill"];

// get_drill's own non-ok states (statement_api.py:473-476, :485-491): no
// `choose_group`/`no_chart` here — the caller already has a group and a
// chart by the time it can ask for a drill.
const DRILL_SERVER_MESSAGE_STATES = new Set(["not_built", "error"]);

function buildDrillState(payload) {
	if (payload.state === "ok") {
		return null;
	}
	if (DRILL_SERVER_MESSAGE_STATES.has(payload.state)) {
		return { kind: payload.state, message: payload.message };
	}
	throw new Error(`Numbers: unknown drill state: ${payload.state}`);
}

/** D5: the TB screen and the Adjustments screen each carry the period in
 * their own URL, not read from here. `tb`'s source already names its own
 * `fiscal_year`/`fiscal_period` (an entity row and the CTA row may differ
 * from the drill's own period in principle, though not today); `journals`'s
 * source carries none (`{"kind": "journals"}`, drill_model.py:275), so that
 * link uses the drill payload's own period. `null` source (eliminations,
 * equity method, acquisitions/disposals, current-year result: nothing to
 * open) -> no link. An unknown `source.kind` throws, naming it.
 *
 * U1 (W4 review): the router's history base is already `/close`
 * (router.js's `createWebHistory("/close")`), so a route's own path never
 * repeats it — every other RouterLink in the app strips it with
 * `.replace(/^\/close/, "")` (HeaderBar, AppShell, Period, MyWork) after
 * building a full `/close/...` path with `format()`. This link is built
 * directly, so it is built without the prefix in the first place rather
 * than built-then-stripped. */
function drillSourceLink(source, period) {
	if (!source) {
		return null;
	}
	if (source.kind === "tb") {
		return `/${source.fiscal_year}/${source.fiscal_period}/trial-balances`;
	}
	if (source.kind === "journals") {
		return `/${period.fiscal_year}/${period.fiscal_period}/adjustments`;
	}
	throw new Error(`Numbers: unknown drill source kind: ${source.kind}`);
}

/** One `drill_model.drill` row -> one display row. `entity`/`label` are
 * passed through unchanged (D2-5: the CTA, intercompany, top-side,
 * equity-method and acquisition/disposal rows carry `entity: null` and are
 * shown by `label` only; only a genuine per-entity row carries an entity
 * code, so "no entity text" for the others falls out of the server's own
 * shape rather than being decided again here). */
function drillRow(row, period) {
	const view = {
		layer: row.layer,
		entity: row.entity,
		label: row.label,
		amount: amountText(row.amount),
		accounts: (row.accounts || []).map((account) => ({
			mainAccount: account.main_account,
			accountName: account.account_name,
			amount: amountText(account.amount),
		})),
		link: drillSourceLink(row.source, period),
	};
	if (row.journals) {
		view.journals = row.journals.map((journal) => ({
			journalId: journal.journal_id,
			description: journal.description,
			amount: amountText(journal.amount),
			postedBy: journal.posted_by,
			approvedBy: journal.approved_by,
		}));
		view.journalsBasis = row.journals_basis;
	}
	return view;
}

/**
 * `statement_api.get_drill`'s payload -> everything the drill panel shows:
 * `{heading, headingName, section, dimensionsNote, state, rows}` (U42
 * facts). `state` is `null` on an ok payload; otherwise `{kind, message}`
 * and `rows` is `[]` — the panel never shows a guessed breakdown next to an
 * error.
 *
 * Throws `"Numbers payload has no <key>."` on any missing top-level key,
 * mirroring `statementView` — no silent default for a key the producer did
 * not send.
 */
export function drillView(payload) {
	for (const key of DRILL_REQUIRED_KEYS) {
		if (!Object.prototype.hasOwnProperty.call(payload, key)) {
			throw new Error(`Numbers payload has no ${key}.`);
		}
	}

	const state = buildDrillState(payload);
	const isOk = payload.state === "ok";
	const drill = isOk ? payload.drill : null;

	return {
		heading: payload.heading,
		headingName: drill ? drill.heading_name : null,
		section: drill ? drill.section : null,
		dimensionsNote: drill ? drill.dimensions_note : null,
		state,
		rows: isOk ? drill.rows.map((row) => drillRow(row, payload.period)) : [],
	};
}

// --- U42: the commentary request/permission helpers -------------------------

/**
 * `commentaryBody(view, heading, text, modified)` -> exactly the six
 * parameters `commentary_api.save_commentary` names (M44): `{fiscal_year,
 * fiscal_period, consolidation_group, heading, text, modified}`. Nothing
 * else — `view` may be either `get_statement`'s or `get_drill`'s payload
 * (both carry `period`/`consolidation_group`), and `modified` may be the
 * bare token or a whole draft/doc-like object (whatever the caller is
 * holding, which may carry `owner`, `docstatus`, `name`, …): only its
 * `modified` field is read, and nothing else from it ever reaches the
 * request body. `text` is carried through unchanged, including blank or
 * whitespace-only text: that is a clear, not a refusal, and the decision
 * belongs to `commentary_api.save_commentary`'s own `.strip()`, not here.
 */
export function commentaryBody(view, heading, text, modified) {
	let token = null;
	if (modified !== null && modified !== undefined) {
		token = typeof modified === "object" ? (modified.modified ?? null) : modified;
	}
	return {
		fiscal_year: view.period.fiscal_year,
		fiscal_period: view.period.fiscal_period,
		consolidation_group: view.consolidation_group,
		heading,
		text,
		modified: token,
	};
}

// --- W42: before/after per heading for an unapproved journal ---------------
//
// `journal_model.statement_effect`'s own per-heading net-debit amounts
// (`effect.headings`) are not yet in the built statement (the journal is
// docstatus 0). `beforeAfter` shows what each heading would read if this
// journal were approved, without re-building the statement: `before` is the
// statement's own figure for that heading today; `change` reuses the exact
// display-sign multiplier the statement already computed for that heading
// (N54's `line.sign`, read off the matched heading row of the real
// `statement_model.statement()` payload carried on `view.statement` — never
// re-derived from `normal_balance` and never a hardcoded heading code, #305-
// W4-2 2a-ii / drill_model.py's "one sign rule, not two"); `after` is their
// sum. All three are shown in the statement's own display sign, via
// `amountText`.

/** The one heading line (kind `"heading"`) of `statement`'s given section
 * whose own `heading` code matches — `undefined` when the statement has no
 * such section or no matching heading line (the "not in the statement" /
 * "no heading" bucket case). */
function matchingHeadingLine(statement, section, heading) {
	const found = statement.sections.find((s) => s.section === section);
	if (!found) {
		return undefined;
	}
	return found.lines.find((line) => line.kind === "heading" && line.heading === heading);
}

/**
 * `beforeAfter(effect, view)` -> one row per heading of a journal's effect
 * (`effect` is `journal_model.statement_effect`'s output: `{headings: [
 * {section, heading, heading_name, net_debit}]}`), for a journal that is not
 * yet approved (W4-E18; the caller shows "Included in the statement" instead
 * once the journal is approved — this function is never called for one).
 * `view` is this module's own `statementView(payload, now, timeZone)`.
 *
 * Each row is `{section, heading, headingName, before, change, after}`.
 * `before` is the statement's current figure for that heading (P&L this
 * period, BS balance); `change` = `line.sign * net_debit`, reusing the
 * statement's own sign; `after` = `before + change`. A heading the
 * statement does not carry (wrong code, or the journal posts outside the
 * chart) -> `before: "not in the statement"`, `change: null`, `after: null`
 * — shown, never guessed at and never 0.
 *
 * Throws when `view` is not in the `ok` state: there is no built statement
 * to read a sign or a balance from.
 */
export function beforeAfter(effect, view) {
	if (!view.statement) {
		const kind = view.state ? view.state.kind : "unknown";
		throw new Error(`Numbers: cannot show before/after — statement state is "${kind}", not ok.`);
	}

	return effect.headings.map((heading) => {
		const line = matchingHeadingLine(view.statement, heading.section, heading.heading);
		if (!line) {
			return {
				section: heading.section,
				heading: heading.heading,
				headingName: heading.heading_name,
				before: "not in the statement",
				change: null,
				after: null,
			};
		}
		const change = line.sign * heading.net_debit;
		return {
			section: heading.section,
			heading: heading.heading,
			headingName: heading.heading_name,
			before: amountText(line.current),
			change: amountText(change),
			after: amountText(line.current + change),
		};
	});
}

/** `get_statement`'s own `can_comment` (statement_api.py: role and Open
 * period, #305-W4-5 5b) — `true` only when the payload says so exactly.
 * `get_drill`'s payload carries no `can_comment` of its own (N52): the
 * drill panel is shown the statement's payload for this, not its own.
 * A missing `can_comment` key throws rather than silently reading as
 * `false` — the no-policy-default rule applies to a missing permission
 * exactly as it does to a missing statement key. */
export function canComment(payload) {
	if (!Object.prototype.hasOwnProperty.call(payload, "can_comment")) {
		throw new Error("Numbers: payload has no can_comment.");
	}
	return payload.can_comment === true;
}
