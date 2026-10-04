// konsol#305 U44: numbers.screen.test.mjs
//
// Source-level checks on screens/Numbers.vue (E8; story 8.1; W4-2/W4-3/
// W4-4 as rendered by numbers.js's statementView, U41). The component is
// read as text: a .vue file only compiles inside the Vite build, which the
// row's gate runs separately (mirrors intercompany.screen.test.mjs).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { statementView, tabRows } from "../numbers.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const NUMBERS_VUE = path.join(__dirname, "Numbers.vue");

const FIXTURE_PATH = fileURLToPath(
	new URL("../../../konsol/tests/fixtures/close_statement_payload.json", import.meta.url),
);

function golden() {
	return JSON.parse(fs.readFileSync(FIXTURE_PATH, "utf8"));
}

function read() {
	return fs.readFileSync(NUMBERS_VUE, "utf8");
}

function script(source) {
	const m = source.match(/<script[^>]*>([\s\S]*?)<\/script>/);
	assert.ok(m, "the component has a <script>");
	return m[1];
}

function template(source) {
	const start = source.indexOf("<template>");
	const end = source.lastIndexOf("</template>");
	assert.ok(start >= 0 && end > start, "the component has a <template>");
	return source.slice(start, end);
}

/**
 * From the `<` of an opening `tagName` tag at `openIdx`, finds the matching
 * closing tag by counting nested opens/closes of the same tag name (a tiny
 * balanced scan — the templates here nest plainly, with no self-closing
 * `<div/>`). Returns `{start, end}` spanning the whole element, end
 * exclusive of the closing tag's `>`.
 */
function blockFor(tpl, openIdx, tagName) {
	const openRe = new RegExp(`<${tagName}(?=[\\s>])`, "g");
	const closeRe = new RegExp(`</${tagName}>`, "g");
	const tagEnd = tpl.indexOf(">", openIdx) + 1;
	assert.ok(tagEnd > 0, `the opening <${tagName}> tag closes`);
	let depth = 1;
	let pos = tagEnd;
	while (depth > 0) {
		openRe.lastIndex = pos;
		closeRe.lastIndex = pos;
		const nextOpen = openRe.exec(tpl);
		const nextClose = closeRe.exec(tpl);
		assert.ok(nextClose, `a matching </${tagName}> exists`);
		if (nextOpen && nextOpen.index < nextClose.index) {
			depth++;
			pos = nextOpen.index + nextOpen[0].length;
		} else {
			depth--;
			pos = nextClose.index + nextClose[0].length;
			if (depth === 0) return { start: openIdx, end: pos };
		}
	}
}

/** The span of the nearest `<tagName ...>` element whose own opening tag
 * (attributes included) matches `pattern`. */
function blockMatching(tpl, tagName, pattern) {
	const re = new RegExp(`<${tagName}(?:(?!>)[\\s\\S])*?>`, "g");
	let m;
	while ((m = re.exec(tpl))) {
		if (pattern.test(m[0])) {
			return blockFor(tpl, m.index, tagName);
		}
	}
	return null;
}

test("Red: Numbers.vue is missing", () => {
	assert.ok(fs.existsSync(NUMBERS_VUE), "close-ui/src/screens/Numbers.vue must exist");
});

// --- pure: the real producer's output, fed through U41's own helpers -------

test("pure: tabRows over the golden statement gives the rows in server order, residual last on the Balance Sheet tab", () => {
	const view = statementView(golden(), new Date("2025-08-01T18:00:00Z"), "Europe/London");

	const pl = tabRows(view, "Profit and Loss");
	assert.deepEqual(pl.map((r) => r.kind), ["heading", "net_result"]);

	const bs = tabRows(view, "Balance Sheet");
	assert.deepEqual(
		bs.map((r) => r.kind),
		["heading", "heading", "heading", "cta", "current_year_result", "residual"],
	);
	assert.equal(bs[bs.length - 1].kind, "residual", "the residual row is last");
});

// --- wiring ------------------------------------------------------------------

test("Numbers.vue builds what it shows with statementView/tabRows/isDrillable (U41)", () => {
	const source = read();
	assert.match(source, /import\s*\{[^}]*\bstatementView\b[^}]*\}\s*from\s*["']\.\.\/numbers\.js["']/);
	assert.match(source, /import\s*\{[^}]*\btabRows\b[^}]*\}\s*from\s*["']\.\.\/numbers\.js["']/);
	assert.match(source, /import\s*\{[^}]*\bisDrillable\b[^}]*\}\s*from\s*["']\.\.\/numbers\.js["']/);
	assert.match(script(source), /statementView\(/);
});

test("The row loop is v-for over tabRows(view, tab.section) — no second derivation off view.tabs[i].rows", () => {
	const tpl = template(read());
	assert.match(tpl, /v-for="\(row,\s*i\)\s+in\s+tabRows\(\s*view\s*,\s*tab\.section\s*\)"/);
	assert.doesNotMatch(tpl, /\btab\.rows\b/, "the template never reads a tab's rows directly");
});

test("Calls the server through api.js and reads the period with route.js; no fetch, no browser storage (D5; route.test.mjs scans the whole tree for that)", () => {
	const source = read();
	assert.match(source, /import\s*\{[^}]*\bget\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
	assert.match(source, /from\s*["']\.\.\/route\.js["']/);
	assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js");
});

test("Names konsol.close.statement_api.get_statement exactly once; no post( — the screen is read-only", () => {
	const source = read();
	const names = source.match(/konsol\.close\.statement_api\.get_statement/g) || [];
	assert.equal(names.length, 1, "one endpoint constant for get_statement");
	const js = script(source);
	const gets = js.match(/\bget\(\s*GET_STATEMENT\b/g) || [];
	assert.equal(gets.length, 1, "exactly one get(GET_STATEMENT call site");
	assert.doesNotMatch(js, /\bpost\(/, "U44 is read-only: no post( anywhere");
	assert.doesNotMatch(source, /konsol\.close\.[a-z_]+\.save_|konsol\.close\.[a-z_]+\.send_/);
});

test("Failure path — no literal Budget/Gross profit/EBITDA: no subtotal and no budget column (D2-6, W4-3)", () => {
	const tpl = template(read());
	assert.doesNotMatch(tpl, /Budget|Gross profit|EBITDA/);
});

test("The error path renders through LoadState, not an empty table: the table markup sits inside LoadState's own default slot", () => {
	const source = read();
	assert.match(source, /import\s+LoadState\s+from\s*["']\.\.\/components\/LoadState\.vue["']/);
	const tpl = template(source);
	const loadStateBlock = blockMatching(tpl, "LoadState", /<LoadState\b/);
	assert.ok(loadStateBlock, "a <LoadState> element wraps the body");
	const inner = tpl.slice(loadStateBlock.start, loadStateBlock.end);
	assert.match(inner, /tabRows\(/, "the row loop sits inside <LoadState>");
	const outside = tpl.slice(0, loadStateBlock.start) + tpl.slice(loadStateBlock.end);
	assert.doesNotMatch(outside, /tabRows\(/, "no row loop sits outside <LoadState>");
	assert.match(source, /:error="loadError"/);
	assert.match(source, /@retry="load"/);
});

test("The table only renders when view.state is absent — the non-ok business states (choose_group/no_chart/not_built/error) show no table", () => {
	const tpl = template(read());
	const elseBlock = blockMatching(tpl, "template", /v-else\b/);
	assert.ok(elseBlock, "a v-else template follows the view.state branch");
	const inner = tpl.slice(elseBlock.start, elseBlock.end);
	assert.match(inner, /tabRows\(/, "the table sits in the v-else branch, not beside view.state");
});

test("choose_group: the group select is gated on view.state.kind === 'choose_group', local state only, never stored", () => {
	const tpl = template(read());
	const block = blockMatching(tpl, "div", /v-if="view\.state\.kind === 'choose_group'"/);
	assert.ok(block, "a div's v-if tests view.state.kind === 'choose_group'");
	const inner = tpl.slice(block.start, block.end);
	assert.match(inner, /<select\b/, "the group select sits inside the choose_group-gated element");
	assert.match(inner, /v-for="g in view\.groupChoice"/);
	// Browser persistence is checked tree-wide by route.test.mjs (D5); no
	// copy of that literal check belongs here.
});

test("The declared-accounts gap sentence (view.gapText) offers a Desk link to Close Settings, story 0.4", () => {
	const source = read();
	const block = blockMatching(template(source), "div", /v-if="view\s*&&\s*view\.gapText"/);
	assert.ok(block, "a div's v-if tests view && view.gapText");
	const tpl = template(source);
	const inner = tpl.slice(block.start, block.end);
	assert.match(inner, /view\.gapText/);
	assert.match(inner, /href="\/app\/close-settings"/);
	assert.match(inner, /Open Close Settings/);
});

// U5 "Related": once a group is resolved, the choice must not be a one-way
// door — a second switcher, gated on view.groupChoice alone (not on being
// in the choose_group state), lets the user pick a different one at any
// time there is more than one group to pick from.
test("U5: a group switcher is also offered outside the choose_group state, gated on view.groupChoice alone", () => {
	const tpl = template(read());
	const blocks = [];
	const re = /<div(?:(?!>)[\s\S])*?>/g;
	let m;
	while ((m = re.exec(tpl))) {
		if (/view\.groupChoice/.test(m[0]) && !/view\.state\.kind === 'choose_group'/.test(m[0])) {
			blocks.push(blockFor(tpl, m.index, "div"));
		}
	}
	assert.ok(blocks.length >= 1, "a div's v-if reads view.groupChoice without also requiring the choose_group state");
	const inner = tpl.slice(blocks[0].start, blocks[0].end);
	assert.match(inner, /<select\b/, "the switcher offers a select");
	assert.match(inner, /v-for="g in view\.groupChoice"/);
	assert.match(inner, /chooseGroup\(/, "it calls the same chooseGroup as the choose_group banner's own select");
});

test("setup_gap: the state banner shows the server's message and offers Open Close Settings (S5, R41d/R41i)", () => {
	const tpl = template(read());
	const block = blockMatching(tpl, "div", /v-if="view\.state\.kind === 'setup_gap'"/);
	assert.ok(block, "a div's v-if tests view.state.kind === 'setup_gap'");
	const inner = tpl.slice(block.start, block.end);
	assert.match(inner, /href="\/app\/close-settings"/);
	assert.match(inner, /Open Close Settings/);
	// The message itself is the shared <p>{{ view.state.message }}</p> above
	// every state banner, not a second, duplicated literal.
	const stateBannerBlock = blockMatching(tpl, "div", /v-if="view\.state"/);
	assert.ok(stateBannerBlock, "the outer state banner div exists");
	const bannerInner = tpl.slice(stateBannerBlock.start, stateBannerBlock.end);
	assert.match(bannerInner, /\{\{\s*view\.state\.message\s*\}\}/);
});

test("U6: comparisonNote is rendered above the tables, not just computed", () => {
	const tpl = template(read());
	const pos = tpl.search(/view\.comparisonNote/);
	assert.ok(pos >= 0, "the template reads view.comparisonNote somewhere");
	const tabsLoopPos = tpl.search(/v-for="tab in view\.tabs"/);
	assert.ok(tabsLoopPos > 0, "the tabs loop exists");
	assert.ok(pos < tabsLoopPos, "comparisonNote is rendered above (before) the tables");
	assert.match(tpl.slice(Math.max(0, pos - 80), pos), /<p\b/, "it is rendered as visible text, not just read");
});

test("A seq guard exists so a stale response is dropped (mirrors Rates.vue/Intercompany.vue)", () => {
	const js = script(read());
	assert.match(js, /let\s+seq\s*=\s*0/);
	assert.match(js, /\+\+seq\b|seq\s*\+\+/);
});

test("Reads the period from the URL via route.js parse, D5 (nothing kept in the browser, no last-viewed memory)", () => {
	const js = script(read());
	assert.match(js, /\bparse\(/);
	assert.match(js, /useRoute\(/);
});

test("statementView errors are never swallowed: caught, recorded, and shown — never rendered as if ok", () => {
	const js = script(read());
	assert.match(js, /catch\s*\(\s*e\s*\)\s*\{[\s\S]*?viewError\.value\s*=\s*e\.message/);
});
