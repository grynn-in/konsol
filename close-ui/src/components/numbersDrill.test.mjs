// konsol#305 U45: numbersDrill.test.mjs
//
// Exercises the drill panel (E8; story 8.2, three clicks or fewer; D2-5):
// clicking a statement heading row opens NumbersDrill.vue, which loads
// get_drill (N52) and shows drillView's rows (U42) — a row expands to its
// accounts, and the source link opens the TB screen or Adjustments for
// that period. NumbersDrill.vue is read as text, like Numbers.vue's own
// screen test (numbers.screen.test.mjs): a .vue file only compiles inside
// the Vite build, which the row's gate runs separately.
//
// U41's facts: residual, not-in-chart, no-heading and "of which" rows are
// never clickable — the click is bound only where isDrillable(row). That
// wiring lives in Numbers.vue (the statement table), so this file checks
// both files.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { statementView, tabRows, isDrillable, drillView } from "../numbers.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DRILL_VUE = path.join(__dirname, "NumbersDrill.vue");
const NUMBERS_VUE = path.join(__dirname, "..", "screens", "Numbers.vue");

const STATEMENT_FIXTURE_PATH = fileURLToPath(
	new URL("../../../konsol/tests/fixtures/close_statement_payload.json", import.meta.url),
);
const DRILL_FIXTURE_PATH = fileURLToPath(
	new URL("../../../konsol/tests/fixtures/close_drill_payload.json", import.meta.url),
);

function goldenStatement() {
	return JSON.parse(fs.readFileSync(STATEMENT_FIXTURE_PATH, "utf8"));
}

function goldenDrill() {
	return JSON.parse(fs.readFileSync(DRILL_FIXTURE_PATH, "utf8"));
}

function readDrillVue() {
	return fs.readFileSync(DRILL_VUE, "utf8");
}

function readNumbersVue() {
	return fs.readFileSync(NUMBERS_VUE, "utf8");
}

function script(source) {
	const matches = [...source.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)];
	assert.ok(matches.length, "the component has a <script>");
	return matches.map((m) => m[1]).join("\n");
}

function template(source) {
	const start = source.indexOf("<template>");
	const end = source.lastIndexOf("</template>");
	assert.ok(start >= 0 && end > start, "the component has a <template>");
	return source.slice(start, end);
}

const NOW = new Date("2025-08-01T18:00:00Z");
const TZ = "Europe/London";

test("Red: NumbersDrill.vue is missing", () => {
	assert.ok(fs.existsSync(DRILL_VUE), "close-ui/src/components/NumbersDrill.vue must exist");
});

// --- pure: isDrillable over the golden view is exactly the heading rows ----

test("pure: tabRows(view, s).filter(isDrillable) over the golden view is exactly the heading rows, Profit and Loss", () => {
	const view = statementView(goldenStatement(), NOW, TZ);
	const drillable = tabRows(view, "Profit and Loss").filter(isDrillable);
	assert.deepEqual(drillable.map((r) => r.kind), ["heading"]);
});

test("pure: tabRows(view, s).filter(isDrillable) over the golden view is exactly the heading rows, Balance Sheet — no residual, no-heading, not-in-chart or 'of which' row", () => {
	const view = statementView(goldenStatement(), NOW, TZ);
	const all = tabRows(view, "Balance Sheet");
	const drillable = all.filter(isDrillable);
	assert.deepEqual(drillable.map((r) => r.kind), ["heading", "heading", "heading"]);
	// the rows isDrillable must exclude are present in this tab, and excluded
	assert.ok(all.some((r) => r.kind === "cta"), "the fixture carries a cta ('of which') row");
	assert.ok(all.some((r) => r.kind === "current_year_result"), "the fixture carries a current_year_result ('of which') row");
	assert.ok(all.some((r) => r.kind === "residual"), "the fixture carries a residual row");
	for (const row of drillable) {
		assert.notEqual(row.kind, "residual");
		assert.notEqual(row.kind, "no_heading");
		assert.notEqual(row.kind, "not_in_chart");
		assert.notEqual(row.kind, "cta");
		assert.notEqual(row.kind, "current_year_result");
	}
});

// --- pure: the panel renders drillView(goldenDrill()).rows ------------------

test("pure: drillView(goldenDrill()) is what the panel has to render — server order, entity codes on entity rows, none on the CTA/top-side rows", () => {
	const view = drillView(goldenDrill());
	assert.equal(view.rows.length, 6);
	assert.equal(view.headingName, "ASSETS");
	assert.equal(view.dimensionsNote, "Not broken down by dimension yet (konsolidat#245).");
	assert.deepEqual(
		view.rows.map((r) => r.entity),
		["ZZA", "ZZB", "ZZC", null, null, null],
	);
});

// --- NumbersDrill.vue: wiring ------------------------------------------------

test("NumbersDrill.vue builds its view with drillView (U42); no second derivation off the raw payload", () => {
	const source = readDrillVue();
	assert.match(source, /import\s*\{[^}]*\bdrillView\b[^}]*\}\s*from\s*["']\.\.\/numbers\.js["']/);
	const tpl = template(source);
	assert.match(tpl, /\bview\.rows\b/, "the row loop reads view.rows (drillView's own shape)");
});

test("Calls the server through api.js; no fetch, no browser storage of any kind", () => {
	const source = readDrillVue();
	assert.match(source, /import\s*\{[^}]*\bget\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
	assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js");
});

test("Names konsol.close.statement_api.get_drill exactly once; no post( — the panel only reads", () => {
	const source = readDrillVue();
	const names = source.match(/konsol\.close\.statement_api\.get_drill/g) || [];
	assert.equal(names.length, 1, "one endpoint constant for get_drill");
	const js = script(source);
	const gets = js.match(/\bget\(\s*GET_DRILL\b/g) || [];
	assert.equal(gets.length, 1, "exactly one get(GET_DRILL call site");
	assert.doesNotMatch(js, /\bpost\(/, "U45's panel is read-only: no post( anywhere");
});

test("A seq guard exists, so a fast second click never shows the first heading's rows", () => {
	const js = script(readDrillVue());
	assert.match(js, /let\s+seq\s*=\s*0/);
	assert.match(js, /\+\+seq\b|seq\s*\+\+/);
	// the fetch is keyed off the heading prop changing, not fired once at mount
	assert.match(js, /watch\(\s*\(\)\s*=>\s*props\.heading/);
});

test("The panel shows dimensionsNote", () => {
	const tpl = template(readDrillVue());
	assert.match(tpl, /view\.dimensionsNote/);
});

test("A non-ok drill state shows the server's message, never a guessed breakdown next to it", () => {
	const tpl = template(readDrillVue());
	assert.match(tpl, /v-if="view\.state"/);
	assert.match(tpl, /view\.state\.message/);
});

// --- NumbersDrill.vue: a row expands to its accounts ------------------------

test("A row with accounts can expand to show them; the expanded set is local component state, never stored", () => {
	const source = readDrillVue();
	const js = script(source);
	assert.match(js, /\bexpanded\b/, "an 'expanded' tracking variable exists");
	const tpl = template(source);
	assert.match(tpl, /row\.accounts/);
	assert.match(tpl, /account\.mainAccount|account\.accountName/);
});

// --- NumbersDrill.vue: the source link --------------------------------------

test("A row's source link opens through RouterLink at row.link, guarded so a null link renders no link", () => {
	const source = readDrillVue();
	assert.match(source, /RouterLink/);
	const tpl = template(source);
	assert.match(tpl, /<RouterLink\b[^>]*v-if="row\.link"[^>]*:to="row\.link"/);
});

test("A top-side row's journals and journalsBasis are shown", () => {
	const tpl = template(readDrillVue());
	assert.match(tpl, /row\.journals/);
	assert.match(tpl, /row\.journalsBasis/);
});

test("The panel can be closed, emitting close", () => {
	const source = readDrillVue();
	assert.match(source, /defineEmits\(\s*\[\s*["']close["']\s*\]\s*\)/);
	const tpl = template(source);
	assert.match(tpl, /emit\(\s*["']close["']\s*\)/);
});

// --- Numbers.vue: the click is bound only under isDrillable(row) -----------

test("Numbers.vue opens NumbersDrill for the clicked heading, closing clears the selection (local state, never stored)", () => {
	const source = readNumbersVue();
	assert.match(source, /import\s+NumbersDrill\s+from\s*["']\.\.\/components\/NumbersDrill\.vue["']/);
	const tpl = template(source);
	const tag = tpl.match(/<NumbersDrill\b[\s\S]*?\/?>/);
	assert.ok(tag, "NumbersDrill is used in the template");
	assert.match(tag[0], /v-if="selectedHeading"/);
	assert.match(tag[0], /:heading="selectedHeading"/);
	assert.match(tag[0], /@close="selectedHeading\s*=\s*null"/);
});

test("failure path: the drill click handler is bound only under isDrillable(row) — no unconditional @click on a row", () => {
	const tpl = template(readNumbersVue());
	const rowTags = tpl.match(/<tr[^>]*>/g) || [];
	const withClick = rowTags.filter((t) => /@click=/.test(t));
	assert.ok(withClick.length > 0, "at least one row carries a click binding");
	for (const tag of withClick) {
		assert.match(tag, /@click="isDrillable\(row\)\s*\?/, "every @click on a row is guarded by isDrillable(row)");
	}
});

test("Numbers.vue imports isDrillable from numbers.js to guard the click (U41)", () => {
	const source = readNumbersVue();
	assert.match(source, /import\s*\{[^}]*\bisDrillable\b[^}]*\}\s*from\s*["']\.\.\/numbers\.js["']/);
});

test("period change clears the open drill panel, same as the group choice (D5: nothing survives a period change)", () => {
	const js = script(readNumbersVue());
	assert.match(js, /selectedHeading\.value\s*=\s*null/);
});
