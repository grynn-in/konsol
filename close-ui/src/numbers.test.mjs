// konsol#305 U41: numbers.test.mjs
//
// Exercises numbers.js against statement_api.get_statement's real payload
// shape (N51: konsol/close/statement_api.py, konsol/close/statement_model.py).
// The "ok" cases load the golden fixture statement_api's own host test
// commits (W4-E19) and build every other case as a mutation of it, never a
// hand-built payload.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
	statementView,
	tabRows,
	isDrillable,
	amountText,
} from "./numbers.js";

const FIXTURE_PATH = fileURLToPath(
	new URL("../../konsol/tests/fixtures/close_statement_payload.json", import.meta.url),
);

function golden() {
	return JSON.parse(readFileSync(FIXTURE_PATH, "utf8"));
}

// The fixture's one commentary entry is at 2025-08-01T10:00:00+01:00, which
// is 10:00 local time in Europe/London in August (BST). `now` is later the
// same London calendar day, so formatTime's "today" branch applies.
const NOW = new Date("2025-08-01T18:00:00Z");
const TZ = "Europe/London";

// --- header, label, legend --------------------------------------------------

test("header names the period, group and currency", () => {
	const view = statementView(golden(), NOW, TZ);
	assert.equal(view.header, "Numbers · FY2025P07 · G1 · USD");
});

test("label follows the sign-off run, not the statement: provisional on the golden payload", () => {
	const view = statementView(golden(), NOW, TZ);
	assert.deepEqual(view.label, { text: "Provisional", tone: "warn" });
});

test("label: signed and resign_needed", () => {
	const signed = golden();
	signed.signoff.state = "signed";
	assert.deepEqual(statementView(signed, NOW, TZ).label, { text: "Signed", tone: "ok" });

	const resign = golden();
	resign.signoff.state = "resign_needed";
	assert.deepEqual(statementView(resign, NOW, TZ).label, {
		text: "Re-sign needed — numbers changed after signing",
		tone: "block",
	});
});

test("failure path: an unknown sign-off state throws", () => {
	const payload = golden();
	payload.signoff.state = "mystery";
	assert.throws(() => statementView(payload, NOW, TZ), /mystery/);
});

test("legend is the AMENDED 4 Oct wording, shown on an ok payload", () => {
	const view = statementView(golden(), NOW, TZ);
	assert.equal(
		view.legend,
		"Profit and loss: income positive, costs in brackets. Balance sheet: assets, liabilities and equity positive.",
	);
	assert.equal(view.legend, golden().statement.legend);
});

// --- state, groupChoice, gapText, notIncluded -------------------------------

test("state is null and no group choice when the payload is ok", () => {
	const view = statementView(golden(), NOW, TZ);
	assert.equal(view.state, null);
	assert.equal(view.groupChoice, null);
	assert.equal(view.gapText, null);
});

test("choose_group: a fixed title plus the groups to pick from", () => {
	const payload = golden();
	payload.state = "choose_group";
	payload.message = "Choose a consolidation group.";
	payload.consolidation_group = null;
	payload.reporting_currency = null;
	payload.statement = null;
	const view = statementView(payload, NOW, TZ);
	assert.deepEqual(view.state, { kind: "choose_group", message: "Choose a consolidation group" });
	assert.deepEqual(view.groupChoice, ["G1"]);
	assert.deepEqual(view.tabs, []);
});

test("no_chart/not_built/error show the server's own message, never tabs", () => {
	for (const [state, message] of [
		["no_chart", "Publish the group chart (Main Account) first."],
		["not_built", "ChRelationNotBuilt"],
		["error", "OSError (timeout)"],
	]) {
		const payload = golden();
		payload.state = state;
		payload.message = message;
		payload.statement = null;
		const view = statementView(payload, NOW, TZ);
		assert.deepEqual(view.state, { kind: state, message });
		assert.deepEqual(view.tabs, []);
		assert.equal(view.comparisonNote, null);
	}
});

test("gapText carries the declared-accounts setup gap's message", () => {
	const payload = golden();
	payload.gap = { code: "statement_accounts_undeclared", message: "Declare the CTA account…" };
	const view = statementView(payload, NOW, TZ);
	assert.equal(view.gapText, "Declare the CTA account…");
});

test("notIncluded: singular, with no hidden suffix on the golden payload", () => {
	const view = statementView(golden(), NOW, TZ);
	assert.equal(view.notIncluded, "1 entity in scope is not in these numbers: ZZB");
});

test("notIncluded: plural, and the hidden-count suffix", () => {
	const payload = golden();
	payload.not_included = { count: 2, entities: ["CA_OVIVO", "US_OVIVO"], hidden: 3 };
	const view = statementView(payload, NOW, TZ);
	assert.equal(
		view.notIncluded,
		"2 entities in scope are not in these numbers: CA_OVIVO, US_OVIVO, and 3 outside your scope",
	);
});

test("notIncluded: null when nothing is missing", () => {
	const payload = golden();
	payload.not_included = { count: 0, entities: [], hidden: 0 };
	assert.equal(statementView(payload, NOW, TZ).notIncluded, null);
});

// --- tabs: Profit and Loss --------------------------------------------------

test("P&L tab: columns include Year to date; two rows, heading row carries commentary", () => {
	const view = statementView(golden(), NOW, TZ);
	const pl = view.tabs.find((t) => t.section === "Profit and Loss");
	assert.deepEqual(pl.columns, ["This period", "Comparison", "Variance", "Year to date"]);
	assert.deepEqual(tabRows(view, "Profit and Loss"), [
		{
			kind: "heading",
			heading: "4",
			label: "COST OF SALES",
			current: "241.43",
			comparison: "not loaded",
			variance: "not loaded",
			ytd: "241.43",
			commentary: { text: "Strong quarter.", byText: "Zz Analyst · 10:00" },
			commentable: true,
		},
		{
			kind: "net_result",
			label: "Net result",
			current: "241.43",
			comparison: "not loaded",
			variance: "not loaded",
			ytd: "241.43",
		},
	]);
});

test("comparisonNote is carried from the statement, and drives the not-loaded cells", () => {
	const view = statementView(golden(), NOW, TZ);
	assert.equal(view.comparisonNote, "No rows in the warehouse for FY2025 P06");
});

test("isDrillable: only heading rows", () => {
	const view = statementView(golden(), NOW, TZ);
	const rows = tabRows(view, "Profit and Loss");
	assert.equal(isDrillable(rows[0]), true); // heading
	assert.equal(isDrillable(rows[1]), false); // net_result
});

// --- tabs: Balance Sheet -----------------------------------------------------

test("BS tab: no Year to date column; includes render as indented of-which rows; residual is ok at 0.00", () => {
	const view = statementView(golden(), NOW, TZ);
	const bs = view.tabs.find((t) => t.section === "Balance Sheet");
	assert.deepEqual(bs.columns, ["This period", "Comparison", "Variance"]);

	const rows = tabRows(view, "Balance Sheet");
	assert.deepEqual(
		rows.map((r) => r.kind),
		["heading", "heading", "heading", "cta", "current_year_result", "residual"],
	);
	assert.equal(rows.every((r) => !("ytd" in r)), true);

	const equity = rows[2];
	assert.equal(equity.heading, "3");
	assert.equal(equity.current, "931.40");
	assert.equal(isDrillable(equity), true);

	const ctaRow = rows[3];
	assert.equal(ctaRow.indent, true);
	assert.equal(ctaRow.label, "of which currency translation (CTA)");
	assert.equal(ctaRow.current, "5.07");
	assert.equal(ctaRow.comparison, "not loaded");
	assert.equal(isDrillable(ctaRow), false);

	const resultRow = rows[4];
	assert.equal(resultRow.current, "241.43");
	assert.equal(isDrillable(resultRow), false);

	const residual = rows[5];
	assert.equal(residual.tone, "ok");
	assert.equal(residual.current, "0.00");
	assert.equal("explained" in residual, false);
	assert.equal(isDrillable(residual), false);
});

test("failure path: a non-zero residual is block tone and shows both not-placed lines", () => {
	const payload = golden();
	const bsSection = payload.statement.sections.find((s) => s.section === "Balance Sheet");
	const residualLine = bsSection.lines.find((l) => l.kind === "residual");
	residualLine.current = 246.5;
	residualLine.explained = [
		{ label: "CTA not placed (declare the CTA account)", amount: -5.07 },
		{ label: "Current-year result not placed (declare the current-year result account)", amount: -241.43 },
	];
	residualLine.unexplained = 0;

	const view = statementView(payload, NOW, TZ);
	const residual = tabRows(view, "Balance Sheet").find((r) => r.kind === "residual");
	assert.equal(residual.tone, "block");
	assert.equal(residual.current, "246.50");
	assert.deepEqual(residual.explained, [
		{ label: "CTA not placed (declare the CTA account)", amount: "(5.07)" },
		{
			label: "Current-year result not placed (declare the current-year result account)",
			amount: "(241.43)",
		},
	]);
	assert.equal(residual.unexplained, "0.00");
});

// --- failure paths that must throw -----------------------------------------

test("failure path: a missing top-level key throws, naming it", () => {
	const payload = golden();
	delete payload.statement;
	assert.throws(() => statementView(payload, NOW, TZ), /Numbers payload has no statement\./);
});

test("failure path: every required top-level key is checked", () => {
	for (const key of [
		"period",
		"groups",
		"consolidation_group",
		"group_note",
		"reporting_currency",
		"state",
		"message",
		"signoff",
		"gap",
		"not_included",
		"commentary",
		"can_comment",
	]) {
		const payload = golden();
		delete payload[key];
		assert.throws(
			() => statementView(payload, NOW, TZ),
			new RegExp(`Numbers payload has no ${key}\\.`),
			`expected a throw for missing ${key}`,
		);
	}
});

test("failure path: a null current cell throws rather than showing 0.00", () => {
	const payload = golden();
	const pl = payload.statement.sections.find((s) => s.section === "Profit and Loss");
	pl.lines.find((l) => l.kind === "heading").current = null;
	assert.throws(() => statementView(payload, NOW, TZ), /missing amount/);
});

test("failure path: an unknown line kind throws", () => {
	const payload = golden();
	const pl = payload.statement.sections.find((s) => s.section === "Profit and Loss");
	pl.lines.push({ kind: "mystery_kind", current: 1, comparison: null, variance: null });
	assert.throws(() => statementView(payload, NOW, TZ), /mystery_kind/);
});

test("failure path: an unknown includes kind throws", () => {
	const payload = golden();
	const bs = payload.statement.sections.find((s) => s.section === "Balance Sheet");
	const equity = bs.lines.find((l) => l.heading === "3");
	equity.includes.push({ kind: "mystery", label: "of which mystery", current: 1, comparison: null });
	assert.throws(() => statementView(payload, NOW, TZ), /mystery/);
});

test("failure path: an unknown statement section throws", () => {
	const payload = golden();
	payload.statement.sections.push({ section: "Cash Flow", lines: [] });
	assert.throws(() => statementView(payload, NOW, TZ), /Cash Flow/);
});

test("tabRows: an unknown section throws", () => {
	const view = statementView(golden(), NOW, TZ);
	assert.throws(() => tabRows(view, "Cash Flow"), /Cash Flow/);
});

// --- amountText --------------------------------------------------------------

test("amountText: 2dp, thousands grouping, negatives in brackets", () => {
	assert.equal(amountText(1234.5), "1,234.50");
	assert.equal(amountText(-1234.5), "(1,234.50)");
	assert.equal(amountText(0), "0.00");
	assert.equal(amountText(-0.0), "0.00");
});

test("amountText: null/undefined throws, never renders 0.00", () => {
	assert.throws(() => amountText(null), /missing amount/);
	assert.throws(() => amountText(undefined), /missing amount/);
});

// --- module hygiene ----------------------------------------------------------

// route.test.mjs (B07) already scans every file under close-ui/src for the
// browser-storage literal, this test file included — no copy of that check
// (and no copy of the literal itself) belongs here.
test("the source imports no vue, frappe or xstate", () => {
	const path = fileURLToPath(new URL("./numbers.js", import.meta.url));
	const source = readFileSync(path, "utf8");
	for (const term of ["vue", "frappe", "xstate"]) {
		assert.ok(!source.includes(`"${term}`) && !source.includes(`'${term}`), `unexpected import of ${term}`);
	}
});
