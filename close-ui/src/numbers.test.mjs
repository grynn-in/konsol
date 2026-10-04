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
import { createRouter, createMemoryHistory } from "vue-router";
import {
	statementView,
	tabRows,
	isDrillable,
	amountText,
	drillView,
	commentaryBody,
	canComment,
	commentaryByText,
	beforeAfter,
} from "./numbers.js";

const FIXTURE_PATH = fileURLToPath(
	new URL("../../konsol/tests/fixtures/close_statement_payload.json", import.meta.url),
);
const DRILL_FIXTURE_PATH = fileURLToPath(
	new URL("../../konsol/tests/fixtures/close_drill_payload.json", import.meta.url),
);
const JOURNAL_ITEM_FIXTURE_PATH = fileURLToPath(
	new URL("../../konsol/tests/fixtures/close_approvals_journal_item.json", import.meta.url),
);
const ROUTER_JS_PATH = fileURLToPath(new URL("./router.js", import.meta.url));

function golden() {
	return JSON.parse(readFileSync(FIXTURE_PATH, "utf8"));
}

function goldenDrill() {
	return JSON.parse(readFileSync(DRILL_FIXTURE_PATH, "utf8"));
}

// U1 (W4 review): a router built from router.js's OWN base and route
// pattern, read out of its source rather than copied by hand, so a change
// to either one breaks this test instead of leaving it silently stale.
// `createCloseRouter` itself can't run under plain `node --test`: it calls
// `createWebHistory` (needs `window.history`, absent in Node) and
// `import.meta.glob` (a Vite build-time macro) — router.test.mjs keeps the
// same boundary for `landingPath`. `createMemoryHistory` needs neither, and
// resolves through the identical route table vue-router would use at
// runtime.
function realRouteTable() {
	const source = readFileSync(ROUTER_JS_PATH, "utf8");
	const baseMatch = source.match(/createWebHistory\(\s*(["'])([^"']+)\1\s*\)/);
	const pathMatch = source.match(/\{\s*path:\s*(["'])([^"']+)\1,\s*component:\s*ScreenLoader/);
	assert.ok(baseMatch, "router.js: expected createWebHistory(\"...\") with a literal base");
	assert.ok(pathMatch, "router.js: expected a { path: \"...\", component: ScreenLoader } route");
	return createRouter({
		history: createMemoryHistory(baseMatch[2]),
		routes: [{ path: pathMatch[2], component: {} }],
	});
}

// W41's golden journal item (test_close_approvals_api.py, built from the REAL
// journal_model.statement_effect, never a JS port). Its two `effect.headings`
// entries are PL heading "4" (net_debit -500) and BS heading "2" (net_debit
// 500) — both present in the golden statement payload's chart (4100/2100,
// W41's fixture note).
function goldenJournalItem() {
	return JSON.parse(readFileSync(JOURNAL_ITEM_FIXTURE_PATH, "utf8"));
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

// --- drillView (U42) ---------------------------------------------------------
//
// Exercises drillView against statement_api.get_drill's real payload shape
// (N52: konsol/close/statement_api.py, konsol/close/drill_model.py). The "ok"
// case loads the golden fixture (close_drill_payload.json, N52's own host
// test commit); every other case is a mutation of it, never a hand-built
// payload.

test("drillView: entity rows carry entity codes, the CTA row has none", () => {
	const view = drillView(goldenDrill());
	const entityRows = view.rows.filter((r) => r.layer === "entity");
	assert.deepEqual(entityRows.map((r) => r.entity), ["ZZA", "ZZB", "ZZC"]);

	const cta = view.rows.find((r) => r.layer === "cta");
	assert.equal(cta.entity, null);
	assert.equal(cta.label, "Currency translation (CTA)");
});

test("drillView: an entity row's amount, accounts and tb source link", () => {
	const view = drillView(goldenDrill());
	const zza = view.rows.find((r) => r.layer === "entity" && r.entity === "ZZA");
	assert.equal(zza.amount, "300.00");
	assert.deepEqual(zza.accounts, [{ mainAccount: "1110", accountName: "Cash", amount: "300.00" }]);
	// U1 (W4 review): no `/close` prefix — the router's history base IS
	// `/close` (router.js's createWebHistory("/close")), so a route's own
	// path never repeats it. See "resolves through the real route table"
	// below, which proves this against router.js itself, not just a pinned
	// string.
	assert.equal(zza.link, "/2025/7/trial-balances");
});

test("drillView: an IC elimination row has no source link and no entity text", () => {
	const view = drillView(goldenDrill());
	const ic = view.rows.find((r) => r.layer === "ic_elimination");
	assert.equal(ic.entity, null);
	assert.equal(ic.label, "Intercompany eliminations");
	assert.equal(ic.link, null);
});

test("drillView: a top-side row's journals link to Adjustments, in the payload's own period", () => {
	const view = drillView(goldenDrill());
	const topside = view.rows.find((r) => r.layer === "topside");
	assert.equal(topside.entity, null);
	assert.equal(topside.label, "Top-side journals");
	assert.equal(topside.link, "/2025/7/adjustments"); // U1 (W4 review): no `/close` prefix
	assert.deepEqual(topside.journals, [
		{
			journalId: "J-1",
			description: "Reclass intercompany loan",
			amount: "40.00",
			postedBy: "alice@example.com",
			approvedBy: "bob@example.com",
		},
	]);
	assert.equal(topside.journalsBasis, "posted in this period (the heading's amount is cumulative)");
});

test("drillView: every drill source link resolves through router.js's REAL route table (U1)", () => {
	const router = realRouteTable();
	const view = drillView(goldenDrill());
	const links = view.rows.map((r) => r.link).filter((link) => link != null);

	assert.ok(links.length > 0, "fixture should produce at least one link to check");
	for (const link of links) {
		const resolved = router.resolve(link);
		assert.ok(resolved.matched.length > 0, `${link} matched 0 routes`);
		// The resolved href carries the router's own /close base exactly
		// once — this is what a broken /close/... link in the source
		// string would double up (U1's bug: matched: 0, href
		// /close/close/2025/7/trial-balances).
		assert.equal(resolved.href, `/close${link}`);
	}
});

test("drillView: dimensionsNote and heading/section are passed through", () => {
	const view = drillView(goldenDrill());
	assert.equal(view.heading, "1");
	assert.equal(view.headingName, "ASSETS");
	assert.equal(view.section, "Balance Sheet");
	assert.equal(view.dimensionsNote, "Not broken down by dimension yet (konsolidat#245).");
	assert.equal(view.state, null);
});

test("drillView: unknown layer labels are shown as sent, not rewritten", () => {
	const payload = goldenDrill();
	payload.drill.rows.push({
		layer: "some_future_adjustment_type",
		label: "some_future_adjustment_type",
		entity: null,
		amount: 12.34,
		accounts: [],
		source: null,
	});
	const view = drillView(payload);
	const row = view.rows.find((r) => r.layer === "some_future_adjustment_type");
	assert.equal(row.label, "some_future_adjustment_type");
	assert.equal(row.amount, "12.34");
});

test("failure path: a drill state other than ok shows the server message and no rows", () => {
	const payload = goldenDrill();
	payload.state = "error";
	payload.message = "OSError (timeout)";
	payload.drill = null;
	const view = drillView(payload);
	assert.deepEqual(view.state, { kind: "error", message: "OSError (timeout)" });
	assert.deepEqual(view.rows, []);
});

test("failure path: an unknown drill state throws", () => {
	const payload = goldenDrill();
	payload.state = "mystery";
	assert.throws(() => drillView(payload), /mystery/);
});

test("failure path: a missing top-level key throws, naming it", () => {
	for (const key of ["period", "consolidation_group", "heading", "state", "message", "drill"]) {
		const payload = goldenDrill();
		delete payload[key];
		assert.throws(
			() => drillView(payload),
			new RegExp(`Numbers payload has no ${key}\\.`),
			`expected a throw for missing ${key}`,
		);
	}
});

test("failure path: an unknown drill source kind throws", () => {
	const payload = goldenDrill();
	payload.drill.rows[0].source.kind = "mystery_source";
	assert.throws(() => drillView(payload), /mystery_source/);
});

// --- commentaryBody, canComment, commentaryByText (U42) -----------------------

test("commentaryBody: exactly the six M44 parameters, from the view and the given heading/text", () => {
	const body = commentaryBody(goldenDrill(), "1", "Strong quarter.", null);
	assert.deepEqual(body, {
		fiscal_year: 2025,
		fiscal_period: 7,
		consolidation_group: "G1",
		heading: "1",
		text: "Strong quarter.",
		modified: null,
	});
});

test("failure path: a body built from a draft carrying owner, docstatus, name has exactly the six keys", () => {
	const draft = {
		owner: "zz-analyst@example.com",
		docstatus: 0,
		name: "SC-G1-2025-7-1",
		modified: "2025-08-01 09:00:00.000000",
	};
	const body = commentaryBody(goldenDrill(), "1", "text", draft);
	assert.deepEqual(Object.keys(body).sort(), [
		"consolidation_group",
		"fiscal_period",
		"fiscal_year",
		"heading",
		"modified",
		"text",
	]);
	assert.equal(body.modified, "2025-08-01 09:00:00.000000");
});

test("commentaryBody: blank/whitespace text is carried through unchanged (a clear, not refused)", () => {
	const body = commentaryBody(goldenDrill(), "1", "   ", null);
	assert.equal(body.text, "   ");
});

test("canComment: true only when can_comment is exactly true", () => {
	const payload = golden();
	assert.equal(canComment(payload), true);

	payload.can_comment = false;
	assert.equal(canComment(payload), false);
});

test("failure path: canComment throws when can_comment is missing, never silently false", () => {
	const payload = golden();
	delete payload.can_comment;
	assert.throws(() => canComment(payload), /can_comment/);
});

test("commentaryByText: '<by> · <formatted time>', mirroring the statement row's own commentary text", () => {
	const entry = { text: "Strong quarter.", by: "Zz Analyst", at: "2025-08-01T10:00:00+01:00" };
	const text = commentaryByText(entry, NOW, TZ);
	assert.equal(text, "Zz Analyst · 10:00");
});

// --- beforeAfter (W42) -------------------------------------------------------
//
// W42 was blocked until N54 added each heading line's own display-sign
// multiplier (`line.sign`) to statement_model's payload. beforeAfter reads
// that sign straight off the matched heading row of the real statement
// payload riding along on `view.statement` — it never re-derives the
// Debit/Credit -> multiplier mapping and never hardcodes a heading code.
// change = sign * net_debit (the 4 Oct amendment to #305-W4-2 2a-ii; the
// pre-amendment "-net_debit for P&L, net_debit for BS" formula is stale).

test("beforeAfter: BS liability heading — the amended per-heading sign turns a debit paydown into a reduction", () => {
	const view = statementView(golden(), NOW, TZ);
	const item = goldenJournalItem();
	const rows = beforeAfter(item.effect, view);

	const liability = rows.find((r) => r.heading === "2");
	// golden statement: heading "2" LIABILITIES, current 803.70, sign -1
	// (Credit-normal). net_debit +500 (a paydown) -> change = -1 * 500 =
	// -500 -> after 303.70, never the stale formula's 1303.70.
	assert.deepEqual(liability, {
		section: "Balance Sheet",
		heading: "2",
		headingName: "Liabilities",
		before: "803.70",
		change: "(500.00)",
		after: "303.70",
	});
});

test("beforeAfter: P&L heading — reuses the section-wide -1 flip", () => {
	const view = statementView(golden(), NOW, TZ);
	const item = goldenJournalItem();
	const rows = beforeAfter(item.effect, view);

	const pl = rows.find((r) => r.heading === "4" && r.section === "Profit and Loss");
	// golden statement: PL heading "4", current 241.43, sign -1. net_debit
	// -500 -> change = -1 * -500 = 500 -> after 741.43.
	assert.deepEqual(pl, {
		section: "Profit and Loss",
		heading: "4",
		headingName: "Revenue",
		before: "241.43",
		change: "500.00",
		after: "741.43",
	});
});

test("failure path: a heading in the effect absent from the statement shows 'not in the statement', after null", () => {
	const view = statementView(golden(), NOW, TZ);
	const item = goldenJournalItem();
	item.effect.headings[0].heading = "99";
	item.effect.headings[0].heading_name = "Mystery";

	const rows = beforeAfter(item.effect, view);
	const missing = rows.find((r) => r.heading === "99");
	assert.deepEqual(missing, {
		section: "Profit and Loss",
		heading: "99",
		headingName: "Mystery",
		before: "not in the statement",
		change: null,
		after: null,
	});
});

test("failure path: a view in a non-ok state throws", () => {
	const payload = golden();
	payload.state = "error";
	payload.message = "OSError (timeout)";
	payload.statement = null;
	const view = statementView(payload, NOW, TZ);
	const item = goldenJournalItem();
	assert.throws(() => beforeAfter(item.effect, view), /state/);
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
