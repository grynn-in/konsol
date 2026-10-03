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

test("Intercompany.vue calls the server through api.js and reads the period with route.js; no fetch/localStorage", () => {
	const source = read();
	assert.match(source, /import\s*\{[^}]*\bget\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
	assert.match(source, /import\s*\{[^}]*\bpost\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
	assert.match(source, /from\s*["']\.\.\/route\.js["']/);
	assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js");
	assert.doesNotMatch(source, /\blocalStorage\b/, "D5: no localStorage");
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
	const i = tpl.indexOf("sendBack(");
	assert.ok(i >= 0, "a sendBack( call site exists in the template");
	// Walk outward to the nearest enclosing v-if on an ancestor tag.
	const before = tpl.slice(0, i);
	const vIfs = [...before.matchAll(/v-if="([^"]*)"/g)];
	assert.ok(vIfs.length > 0, "an enclosing v-if exists");
	const nearest = vIfs[vIfs.length - 1][1];
	assert.match(nearest, /\bcanSendBack\b/, "tests canSendBack");
	assert.match(nearest, /\bcan_send_back\b/, "tests the pair's can_send_back");
	// No unconditional send-back control: canSendBack alone must gate render
	// of the button area — check there is no other sendBack( site outside
	// a v-if containing both terms.
	const allSendBackCalls = [...tpl.matchAll(/sendBack\(/g)];
	assert.equal(allSendBackCalls.length, 1, "exactly one sendBack( call site in the template");
});

test("Failure path — not configured/not applicable/not built/error shows no tiles or table: both sit behind the same v-if on the checked state (no new branch)", () => {
	const tpl = template(read());
	const tilesAt = tpl.search(/\bchips\b/);
	assert.ok(tilesAt >= 0, "tiles reference view.chips");
	const tableAt = tpl.search(/\bgroups\b/);
	assert.ok(tableAt >= 0, "the pairs table reads view.groups");
	// Both the tiles block and the groups block must be inside a v-if that
	// mentions "checked" (the one state that reads the warehouse) — a
	// single check, reused for not_configured, not_applicable, not_built
	// and error alike (W3-7 amendment: no new branch per state).
	for (const marker of ["chips", "groups"]) {
		const idx = tpl.indexOf(marker);
		const before = tpl.slice(0, idx);
		const vIfs = [...before.matchAll(/v-if="([^"]*)"/g)];
		assert.ok(vIfs.length > 0, `an enclosing v-if exists before ${marker}`);
		const nearest = vIfs[vIfs.length - 1][1];
		assert.match(nearest, /checked/, `${marker} is gated on the checked state`);
	}
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

test("Reads the period from the URL via route.js parse, D5 (no localStorage, no last-viewed memory)", () => {
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
