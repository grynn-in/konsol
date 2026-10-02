// konsol#305 T08b: auditTrail.screen.test.mjs
//
// Source-level checks on screens/AuditTrail.vue (E10; story 10.1, board 8).
// The component is read as text: a .vue file only compiles inside the Vite
// build, which the row's gate runs separately (Problems found 17: C1 is the
// real check for screens). Mirrors checks.screen.test.mjs.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const AUDIT_TRAIL = path.join(__dirname, "AuditTrail.vue");

function read() {
	return fs.readFileSync(AUDIT_TRAIL, "utf8");
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

test("AuditTrail.vue builds what it shows with trailView (T08a)", () => {
	const source = read();
	assert.match(source, /import\s*\{[^}]*\btrailView\b[^}]*\}\s*from\s*["']\.\.\/auditTrail\.js["']/);
	assert.match(script(source), /trailView\(/);
});

test("AuditTrail.vue calls the server through api.js and reads the period with route.js", () => {
	const source = read();
	assert.match(source, /import\s*\{[^}]*\bget\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
	assert.match(source, /from\s*["']\.\.\/route\.js["']/);
});

test("AuditTrail.vue names trail_api.get_trail exactly once", () => {
	const source = read();
	const names = source.match(/konsol\.close\.trail_api\.get_trail/g) || [];
	assert.equal(names.length, 1, "one endpoint constant for get_trail");
});

test("Failure path: no post( — the screen never writes", () => {
	const source = read();
	assert.doesNotMatch(script(source), /\bpost\(/, "AuditTrail.vue is read-only");
});

test("Failure path: no fetch( — every call goes through api.js", () => {
	const source = read();
	assert.doesNotMatch(source, /\bfetch\(/);
});

test("Failure path: no browser storage of the period (D5: it lives in the URL only)", () => {
	// Built from parts so route.test.mjs's own project-wide storage scanner
	// (src/route.test.mjs) does not flag this test file as an offender.
	const source = read();
	assert.doesNotMatch(source, new RegExp("\\blocal" + "Storage\\b"));
	assert.doesNotMatch(source, new RegExp("\\bsession" + "Storage\\b"));
});

test("Failure path: no Export CSV button — story 10.2 is P2, not built here", () => {
	const source = read();
	assert.doesNotMatch(source, /Export CSV/);
});

test("Failure path: no filter chips — story 10.2 is P2, not built here", () => {
	const source = read();
	assert.doesNotMatch(source, /\ballowed\b.*filter|filterChips|problemsOnly/i);
});

test("No v-html anywhere", () => {
	assert.doesNotMatch(read(), /v-html/);
});

test("The events table has the five wireframe columns, in order", () => {
	const tpl = template(read());
	const order = ["When", "Event", "Item", "By", "Detail"];
	let last = -1;
	for (const heading of order) {
		const i = tpl.indexOf(`>${heading}<`);
		assert.ok(i > last, `column "${heading}" present and in order`);
		last = i;
	}
});

test("Failure path: the Event column renders the label as text, not only as a class", () => {
	const tpl = template(read());
	// row.tone may drive :class, but row.label must also appear as rendered text
	// (a mustache, not only inside a :class binding), so a kind is never shown
	// as colour alone.
	assert.match(tpl, /\{\{\s*row\.label\s*\}\}/, "row.label is rendered as text content");
});

test("Every row uses toneClass on row.tone for its chip's colour", () => {
	const tpl = template(read());
	assert.match(tpl, /toneClass\(\s*row\.tone\s*\)/);
});

test("Failure paths, empty and error states go through LoadState (B17)", () => {
	const source = read();
	assert.match(source, /import\s+LoadState\s+from\s*["']\.\.\/components\/LoadState\.vue["']/);
	assert.match(template(source), /<LoadState[\s\S]*?@retry=/, "an error offers Retry");
});

test("The empty state names the period and says no events were recorded", () => {
	const source = read();
	assert.match(source, /No events recorded for/);
});

test("The read-only subtitle from the wireframe appears as text", () => {
	const source = read();
	assert.match(
		source,
		/Read-only\s*·\s*every approval, exception and status change for the period/,
	);
});

test("The summary strip shows signedOff, result, closedLocked and exceptions from trailView", () => {
	const tpl = template(read());
	assert.match(tpl, /trailViewResult\.signedOff\b/);
	assert.match(tpl, /trailViewResult\.result\b/);
	assert.match(tpl, /trailViewResult\.closedLocked\b/);
	assert.match(tpl, /trailViewResult\.exceptions\b/);
});

test("The hidden-entities note from trailView is shown when present", () => {
	const tpl = template(read());
	assert.match(tpl, /trailViewResult\.hiddenNote\b/);
});

test("A trailView throw (unknown kind or sign-off state) is shown as the screen's error, never swallowed", () => {
	const js = script(read());
	assert.match(js, /catch\s*\(\s*(\w+)\s*\)/);
	assert.match(js, /\.message/, "the thrown error's message reaches LoadState");
});
