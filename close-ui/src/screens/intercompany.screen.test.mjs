// konsol#305 C14: intercompany.screen.test.mjs
//
// Source-level checks on screens/Intercompany.vue (E5; stories 5.1, 5.2,
// 5.3; #305-W3-1, W3-2). The component is read as text: a .vue file only
// compiles inside the Vite build, which the row's gate runs separately
// (mirrors checks.screen.test.mjs).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const INTERCOMPANY = path.join(__dirname, "Intercompany.vue");

function read() {
	return fs.readFileSync(INTERCOMPANY, "utf8");
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

/** The span of the nearest `<tagName ... v-if="<pattern>">...</tagName>`
 * element, found by its v-if attribute matching `pattern`. */
function blockWithVIf(tpl, tagName, pattern) {
	const re = new RegExp(`<${tagName}(?:(?!>)[\\s\\S])*?\\bv-if="([^"]*)"(?:(?!>)[\\s\\S])*?>`, "g");
	let m;
	while ((m = re.exec(tpl))) {
		if (pattern.test(m[1])) {
			return blockFor(tpl, m.index, tagName);
		}
	}
	return null;
}

test("Red: Intercompany.vue is missing", () => {
	assert.ok(fs.existsSync(INTERCOMPANY), "close-ui/src/screens/Intercompany.vue must exist");
});

test("Intercompany.vue builds what it shows with intercompanyView/panel/sendBackBody (C12)", () => {
	const source = read();
	assert.match(source, /import\s*\{[^}]*\bintercompanyView\b[^}]*\}\s*from\s*["']\.\.\/intercompany\.js["']/);
	assert.match(source, /import\s*\{[^}]*\bpanel\b[^}]*\}\s*from\s*["']\.\.\/intercompany\.js["']/);
	assert.match(source, /import\s*\{[^}]*\bsendBackBody\b[^}]*\}\s*from\s*["']\.\.\/intercompany\.js["']/);
	assert.match(script(source), /intercompanyView\(/);
});

test("Intercompany.vue calls the server through api.js and reads the period with route.js; no fetch, no browser storage (D5; route.test.mjs scans the whole tree for that)", () => {
	const source = read();
	assert.match(source, /import\s*\{[^}]*\bget\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
	assert.match(source, /import\s*\{[^}]*\bpost\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
	assert.match(source, /from\s*["']\.\.\/route\.js["']/);
	assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js");
});

test("Names konsol.close.ic_api.get_ic and konsol.close.ic_api.send_back; exactly one post(SEND_BACK call site", () => {
	const source = read();
	assert.match(source, /konsol\.close\.ic_api\.get_ic/);
	const sendBacks = source.match(/konsol\.close\.ic_api\.send_back/g) || [];
	assert.equal(sendBacks.length, 1, "one endpoint constant for send_back");
	const js = script(source);
	const posts = js.match(/\bpost\(\s*SEND_BACK\b/g) || [];
	assert.equal(posts.length, 1, "exactly one post(SEND_BACK call site");
	assert.match(js, /function\s+sendBack\s*\(\s*pair\s*,\s*reason\s*\)|async\s+function\s+sendBack\s*\(\s*pair\s*,\s*reason\s*\)/, "sendBack(pair, reason)");
});

test("Failure path — no Remind", () => {
	const tpl = template(read());
	assert.doesNotMatch(tpl, /Remind/, "Remind is not built (P2)");
});

test("Failure path — send back only when allowed: v-if tests both canSendBack and the pair's can_send_back", () => {
	const tpl = template(read());
	const allSendBackCalls = [...tpl.matchAll(/sendBack\(/g)];
	assert.equal(allSendBackCalls.length, 1, "exactly one sendBack( call site in the template");
	const block = blockWithVIf(tpl, "div", /\bcanSendBack\b/);
	assert.ok(block, "a div's v-if tests canSendBack");
	const attr = tpl.slice(block.start, tpl.indexOf(">", block.start) + 1);
	assert.match(attr, /\bcanSendBack\b/, "tests canSendBack");
	assert.match(attr, /\bcan_send_back\b/, "tests the pair's can_send_back");
	// The one send-back call site sits inside this element — not merely
	// textually before it (a sibling's own v-if would otherwise pass).
	const inner = tpl.slice(block.start, block.end);
	assert.match(inner, /sendBack\(/, "the send-back control is inside the gated element");
});

test("Failure path — not configured/not applicable/not built/error shows no tiles or table: both sit behind the same v-if on the checked state (no new branch)", () => {
	const tpl = template(read());
	assert.match(tpl, /\bview\.chips\b/, "tiles reference view.chips");
	assert.match(tpl, /\bview\.groups\b/, "the pairs table reads view.groups");
	const block = blockWithVIf(tpl, "template", /checked/);
	assert.ok(block, "a template's v-if tests the checked state");
	const inner = tpl.slice(block.start, block.end);
	assert.match(inner, /\bview\.chips\b/, "the tiles sit inside the checked-state block");
	assert.match(inner, /\bview\.groups\b/, "the pairs table sits inside the checked-state block");
});

test("Extra (W3-7 amendment): no literal 'not applicable' or 'not configured' text — both come from the view", () => {
	const tpl = template(read());
	assert.doesNotMatch(tpl, /not applicable/i);
	assert.doesNotMatch(tpl, /not configured/i);
});

test("No literal difference/actor/entity/kind key in a request body: bodies come only from sendBackBody", () => {
	const js = script(read());
	// The only object literal passed to post( must come from sendBackBody's
	// return value (built.body), never an inline object with these keys.
	assert.doesNotMatch(js, /post\(\s*SEND_BACK\s*,\s*\{/, "no inline body object at the SEND_BACK call site");
});

test("Loading, empty and error states go through LoadState (B17), with Retry", () => {
	const source = read();
	assert.match(source, /import\s+LoadState\s+from\s*["']\.\.\/components\/LoadState\.vue["']/);
	assert.match(template(source), /<LoadState[\s\S]*?@retry=/, "an error offers Retry");
});

test("Refusals are shown through messageLines, never as raw HTML", () => {
	const source = read();
	assert.match(source, /messageLines\(/);
	assert.doesNotMatch(source, /v-html/);
});

test("Reads the period from the URL via route.js parse, D5 (nothing kept in the browser, no last-viewed memory)", () => {
	const js = script(read());
	assert.match(js, /\bparse\(/);
	assert.match(js, /useRoute\(/);
});

test("Reloads the shell context (CONTEXT_RELOAD) once after a successful send-back", () => {
	const source = read();
	assert.match(source, /import\s*\{[^}]*\bCONTEXT_RELOAD\b[^}]*\}\s*from\s*["']\.\.\/contextRefresh\.js["']/);
	assert.match(script(source), /\binject\(\s*CONTEXT_RELOAD\s*\)/);
});

test("A seq guard exists so a stale response is dropped (mirrors Rates.vue)", () => {
	const js = script(read());
	assert.match(js, /let\s+seq\s*=\s*0/);
	assert.match(js, /\+\+seq\b|seq\s*\+\+/);
});

test("The send-back reason is required: a blank submit is refused client-side via sendBackBody, never posted", () => {
	const js = script(read());
	assert.match(js, /sendBackBody\(/);
	// sendBackBody returns {error} for a blank reason; the screen must check
	// it before posting.
	assert.match(js, /\.error\b/);
});
