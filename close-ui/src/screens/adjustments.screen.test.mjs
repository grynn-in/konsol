// konsol#305 A17: adjustments.screen.test.mjs
//
// Source-level checks on screens/Adjustments.vue (E6; stories 6.1, 6.2;
// W2-10). The component is read as text: a .vue file only compiles inside
// the Vite build, which the row's gate runs separately (mirrors
// rates.screen.test.mjs / intercompany.screen.test.mjs).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ADJUSTMENTS = path.join(__dirname, "Adjustments.vue");

function read() {
	return fs.readFileSync(ADJUSTMENTS, "utf8");
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

test("Red: Adjustments.vue is missing", () => {
	assert.ok(fs.existsSync(ADJUSTMENTS), "close-ui/src/screens/Adjustments.vue must exist");
});

test("Adjustments.vue builds what it shows with journalsView/effectView (A13)", () => {
	const source = read();
	assert.match(source, /import\s*\{[^}]*\bjournalsView\b[^}]*\}\s*from\s*["']\.\.\/adjustments\.js["']/);
	assert.match(source, /import\s*\{[^}]*\beffectView\b[^}]*\}\s*from\s*["']\.\.\/adjustments\.js["']/);
	assert.match(script(source), /journalsView\(/);
});

test("Calls the server through api.js and reads the period with route.js; no fetch (D5; route.test.mjs scans the whole tree for browser storage)", () => {
	const source = read();
	assert.match(source, /import\s*\{[^}]*\bget\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
	assert.match(source, /from\s*["']\.\.\/route\.js["']/);
	assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js");
});

test("Names konsol.close.journal_api.get_journals", () => {
	const source = read();
	assert.match(source, /konsol\.close\.journal_api\.get_journals/);
});

test("Failure path — read-only: no post( anywhere in the script", () => {
	const js = script(read());
	assert.doesNotMatch(js, /\bpost\(/, "A17 has no write controls; A18 adds them");
});

test("Failure path — no Partner column, no Evidence/attach, no 'Still in force', no 'stays until reversed' text", () => {
	const tpl = template(read());
	assert.doesNotMatch(tpl, /Partner/);
	assert.doesNotMatch(tpl, /attach/i);
	assert.doesNotMatch(tpl, /Evidence/);
	assert.doesNotMatch(tpl, /Still in force/);
	assert.doesNotMatch(tpl, /Stays until reversed/i);
});

test("The effect panel renders through effectView; 'no heading' is never a literal fallback in the template", () => {
	const source = read();
	const tpl = template(source);
	assert.match(tpl, /selectedEffect/, "the effect panel reads the effectView result");
	assert.doesNotMatch(tpl, /no heading/i, "the label text comes from effectView, never a template literal");
	assert.match(script(source), /effectView\(/);
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

test("Reads the period from the URL via route.js parse, D5", () => {
	const js = script(read());
	assert.match(js, /\bparse\(/);
	assert.match(js, /useRoute\(/);
});

test("A seq guard exists so a stale response is dropped (mirrors Rates.vue/Intercompany.vue)", () => {
	const js = script(read());
	assert.match(js, /let\s+seq\s*=\s*0/);
	assert.match(js, /\+\+seq\b|seq\s*\+\+/);
});

test("E6-P15: the no-workflow note is shown once, gated on workflowInstalled being false, with the A07 sentence verbatim", () => {
	const source = read();
	const SENTENCE =
		"The journal workflow is not installed on this site (migrate installs it); the Close Lead approves a draft directly from Approvals.";
	assert.match(source, new RegExp(SENTENCE.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), "the sentence appears verbatim");
	const occurrences = (template(source).match(/workflowInstalled/g) || []).length;
	assert.ok(occurrences >= 1, "the note is gated on workflowInstalled");
	// Shown once: exactly one element in the template carries the sentence.
	const tpl = template(source);
	const noteOccurrences = (tpl.match(/NO_WORKFLOW_NOTE/g) || []).length;
	assert.equal(noteOccurrences, 1, "the note is rendered exactly once");
});
