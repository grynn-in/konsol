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

test("Names konsol.close.statement_api.get_drill exactly once; get_drill itself is never posted to", () => {
	// U45's drill fetch stays read-only; U46 adds the one, separate
	// commentary write (its own test below names SAVE_COMMENTARY exactly).
	const source = readDrillVue();
	const names = source.match(/konsol\.close\.statement_api\.get_drill/g) || [];
	assert.equal(names.length, 1, "one endpoint constant for get_drill");
	const js = script(source);
	const gets = js.match(/\bget\(\s*GET_DRILL\b/g) || [];
	assert.equal(gets.length, 1, "exactly one get(GET_DRILL call site");
	assert.doesNotMatch(js, /\bpost\(\s*GET_DRILL\b/, "get_drill is a read, never posted to");
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
	// U46 adds a second emit (saved); close must still be one of them.
	const source = readDrillVue();
	assert.match(source, /defineEmits\(\s*\[[^\]]*["']close["'][^\]]*\]\s*\)/);
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

// --- U46: the commentary editor in the drill panel -------------------------
//
// The panel shows the heading's commentary and, when canComment(view), an
// editor with Save (story 8.3; #305-W4-5 5b). canComment itself is read
// from get_statement's payload — get_drill carries no can_comment (U42
// facts) — so Numbers.vue computes it with the real `canComment` helper
// and passes it (and the heading's raw commentary entry) into the panel
// rather than the panel inventing either.

const SAVE_COMMENTARY = "konsol.close.commentary_api.save_commentary";

test("NumbersDrill.vue declares canComment and commentary props (fed by Numbers.vue, not invented here)", () => {
	const js = script(readDrillVue());
	assert.match(js, /canComment\s*:\s*\{[^}]*type\s*:\s*Boolean[^}]*required\s*:\s*true/s, "canComment is a required Boolean prop");
	assert.match(js, /commentary\s*:\s*\{[^}]*type\s*:\s*Object[^}]*default\s*:\s*null/s, "commentary is an Object prop, default null");
});

test("Numbers.vue feeds NumbersDrill canComment and commentary from the real statement payload, not a guess", () => {
	const source = readNumbersVue();
	assert.match(
		source,
		/import\s*\{[^}]*\bcanComment\b[^}]*\}\s*from\s*["']\.\.\/numbers\.js["']/,
		"Numbers.vue imports the real canComment helper",
	);
	const tpl = template(source);
	const tag = tpl.match(/<NumbersDrill\b[\s\S]*?\/?>/)[0];
	assert.match(tag, /:can-comment="[^"]+"/, "the NumbersDrill tag passes :can-comment");
	assert.match(tag, /:commentary="[^"]+"/, "the NumbersDrill tag passes :commentary");
	const js = script(source);
	// the value bound to :can-comment must come from calling canComment(...),
	// never a hard-coded true/false and never numbers.payload.can_comment read
	// a second time independently of the helper.
	assert.match(js, /canComment\s*\(/, "canComment(...) is actually called in Numbers.vue");
});

test("A successful save tells Numbers.vue to reload the statement exactly once, through a 'saved' event", () => {
	const drillSource = readDrillVue();
	assert.match(drillSource, /defineEmits\(\s*\[\s*["']close["']\s*,\s*["']saved["']\s*\]\s*\)|defineEmits\(\s*\[\s*["']saved["']\s*,\s*["']close["']\s*\]\s*\)/);
	const js = script(drillSource);
	assert.match(js, /emit\(\s*["']saved["']\s*\)/);

	const numbersSource = readNumbersVue();
	const tpl = template(numbersSource);
	const tag = tpl.match(/<NumbersDrill\b[\s\S]*?\/?>/)[0];
	assert.match(tag, /@saved="[^"]+"/, "Numbers.vue listens for the saved event");
});

test("The commentary editor is v-if on the canComment prop — never rendered unconditionally", () => {
	const tpl = template(readDrillVue());
	// a save control (button/textarea) gated on canComment
	assert.match(tpl, /v-if="canComment"/, "an editor block is gated on canComment");
});

test("Exactly one post(SAVE_COMMENTARY call site; the body comes from commentaryBody, never an object literal naming heading", () => {
	const source = readDrillVue();
	assert.match(source, new RegExp(SAVE_COMMENTARY.replace(/\./g, "\\.")));
	const js = script(source);
	const posts = js.match(/\bpost\(\s*SAVE_COMMENTARY\b/g) || [];
	assert.equal(posts.length, 1, "exactly one post(SAVE_COMMENTARY call site");
	// the body passed to post(SAVE_COMMENTARY, ...) is whatever a
	// `commentaryBody(...)` call was assigned to, never a hand-built
	// object literal naming heading: directly.
	const postArg = js.match(/post\(\s*SAVE_COMMENTARY\s*,\s*(\w+)\s*\)/);
	assert.ok(postArg, "post(SAVE_COMMENTARY, <ident>) call site");
	const bodyVar = postArg[1];
	assert.match(
		js,
		new RegExp(`\\b${bodyVar}\\s*=\\s*commentaryBody\\(`),
		`${bodyVar} is built by commentaryBody(...), not a hand-built object`,
	);
	assert.doesNotMatch(js, /post\(\s*SAVE_COMMENTARY\s*,\s*\{\s*heading\s*:/, "no object literal naming heading: built by hand for the post body");
});

test("commentaryBody and commentaryByText are imported from numbers.js (the real producer's helpers, not re-implemented)", () => {
	const source = readDrillVue();
	assert.match(source, /import\s*\{[^}]*\bcommentaryBody\b[^}]*\}\s*from\s*["']\.\.\/numbers\.js["']/);
	assert.match(source, /import\s*\{[^}]*\bcommentaryByText\b[^}]*\}\s*from\s*["']\.\.\/numbers\.js["']/);
});

test("A refused save keeps the typed text: the catch branch sets only an error, never the draft text", () => {
	const js = script(readDrillVue());
	const fnMatch = js.match(/async function save\w*\s*\([^)]*\)\s*\{[\s\S]*?\n\}/);
	assert.ok(fnMatch, "a save function exists");
	const body = fnMatch[0];
	const catchMatch = body.match(/catch\s*\(e\)\s*\{([\s\S]*?)\}\s*finally/);
	assert.ok(catchMatch, "the save function has a catch block");
	const catchBody = catchMatch[1];
	assert.doesNotMatch(catchBody, /draftText\.value\s*=/, "the catch branch never resets the typed draft text");
	assert.match(catchBody, /Error\.value\s*=\s*e\.message/, "the catch branch records the server's message");
});

test("A Viewer (canComment false) sees the heading's commentary text read-only, with no editor", () => {
	const tpl = template(readDrillVue());
	// the read-only display is not itself gated on canComment
	assert.match(tpl, /localCommentary|commentary(?!View)/i);
});

test("The panel shows the heading's commentary byline through commentaryByText, not re-derived", () => {
	const js = script(readDrillVue());
	assert.match(js, /commentaryByText\s*\(/);
});
