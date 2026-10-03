// konsol#305 A19: approvals.screen.test.mjs
//
// Source-level checks on screens/Approvals.vue (E6; stories 6.2, 6.3, 6.4;
// R2, R5; D2-8). The component is read as text, as rates.screen.test.mjs /
// adjustments.screen.test.mjs do: a .vue file only compiles inside the Vite
// build, which the row's gate runs apart.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APPROVALS = path.join(__dirname, "Approvals.vue");

function read() {
	return fs.readFileSync(APPROVALS, "utf8");
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

test("Red: Approvals.vue is missing", () => {
	assert.ok(fs.existsSync(APPROVALS), "close-ui/src/screens/Approvals.vue must exist");
});

test("Approvals.vue builds what it shows and sends with approvals.js (A14)", () => {
	const source = read();
	const imp = source.match(/import\s*\{([^}]*)\}\s*from\s*["']\.\.\/approvals\.js["']/);
	assert.ok(imp, "imports from ../approvals.js");
	for (const name of ["queueView", "approveBody", "rejectBody"]) {
		assert.match(imp[1], new RegExp(`\\b${name}\\b`), `imports ${name}`);
	}
	const js = script(source);
	assert.match(js, /queueView\(/);
	assert.match(js, /approveBody\(/);
	assert.match(js, /rejectBody\(/);
});

test("Calls the server through api.js; no fetch (CSRF on POST goes through api.js)", () => {
	const source = read();
	assert.match(source, /import\s*\{[^}]*\bget\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
	assert.match(source, /import\s*\{[^}]*\bpost\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
	assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js");
});

test("No v-html anywhere: server sentences are plain text", () => {
	assert.doesNotMatch(read(), /v-html/);
});

test("Names get_queue, approval_api.approve and approval_api.reject", () => {
	const js = script(read());
	assert.match(js, /GET_QUEUE\s*=\s*["']konsol\.close\.approvals_api\.get_queue["']/);
	assert.match(js, /APPROVE\s*=\s*["']konsol\.close\.approval_api\.approve["']/);
	assert.match(js, /REJECT\s*=\s*["']konsol\.close\.approval_api\.reject["']/);
});

test("Exactly one post(APPROVE and exactly one post(REJECT call site, and no other post(", () => {
	const js = script(read());
	assert.equal((js.match(/\bpost\(\s*APPROVE\b/g) || []).length, 1, "one approve call site");
	assert.equal((js.match(/\bpost\(\s*REJECT\b/g) || []).length, 1, "one reject call site");
	assert.equal((js.match(/\bpost\(/g) || []).length, 2, "no third POST");
});

test("The single approve call lives in approve(item), only after approveBody returns no error", () => {
	const js = script(read());
	const m = js.match(/async function approve\(item\)\s*\{([\s\S]*?)\n\}/);
	assert.ok(m, "a function approve(item)");
	assert.match(m[1], /approveBody\(/);
	const errIdx = m[1].search(/built\.error/);
	const postIdx = m[1].search(/post\(\s*APPROVE\b/);
	assert.ok(errIdx >= 0 && postIdx >= 0 && errIdx < postIdx, "the error check happens before the post");
});

test("Failure path: a blank reject reason is refused by rejectBody before any request, and shown", () => {
	const js = script(read());
	const m = js.match(/async function reject\(item\)\s*\{([\s\S]*?)\n\}/);
	assert.ok(m, "a function reject(item)");
	assert.match(m[1], /rejectBody\(/);
	const errIdx = m[1].search(/built\.error/);
	const postIdx = m[1].search(/post\(\s*REJECT\b/);
	assert.ok(errIdx >= 0 && postIdx >= 0 && errIdx < postIdx, "the error check happens before any post(REJECT");
});

test("Failure path, R2: the Approve and Reject controls are v-if-gated on approve.kind being button/reason, and on inline", () => {
	const source = read();
	const js = script(source);
	const m = js.match(/function canAct\(item\)\s*\{([\s\S]*?)\n\}/);
	assert.ok(m, "canAct(item) is defined");
	assert.match(m[1], /item\.inline/, "checks inline");
	assert.match(m[1], /item\.approve\.kind\s*===\s*["']button["']/, "checks the button kind");
	assert.match(m[1], /item\.approve\.kind\s*===\s*["']reason["']/, "checks the reason kind");

	const tpl = template(source);
	const idx = tpl.indexOf('v-else-if="canAct(item)"');
	assert.ok(idx >= 0, "a template branch gates the controls on canAct(item)");
	const end = tpl.indexOf("<p v-else", idx);
	assert.ok(end > idx, "a v-else fallback follows the gated branch");
	const block = tpl.slice(idx, end);
	assert.match(block, />\s*Approve\s*</, "an Approve control inside the gate");
	assert.match(block, /Reject with reason/, "a Reject control inside the gate");
});

test("Failure path, BC/BD: a deskLink item renders an anchor and no Approve", () => {
	const tpl = template(read());
	const start = tpl.indexOf('<template v-if="item.deskLink">');
	assert.ok(start >= 0, "a template branch guards on item.deskLink");
	const end = tpl.indexOf("</template>", start);
	assert.ok(end > start);
	const block = tpl.slice(start, end);
	assert.match(block, /<a\b[^>]*:href="item\.deskLink"/, "renders an anchor from item.deskLink");
	assert.doesNotMatch(block, /Approve/, "no Approve control in the Desk-only branch");
});

test("Failure path: a sent-back item renders no Approve or Reject control", () => {
	const tpl = template(read());
	const liIdx = tpl.indexOf('v-for="item in view.sentBack"');
	assert.ok(liIdx >= 0, "renders the sent-back list");
	const block = tpl.slice(liIdx);
	assert.doesNotMatch(block, /@click="(approve|startReject|reject)\(/, "no action control in the sent-back list");
	assert.doesNotMatch(block, /canAct\(/, "no approve/reject gate reused in the sent-back list");
});

test("No fetch, and no Before/After columns (wave 4)", () => {
	const source = read();
	assert.doesNotMatch(template(source), /Before|After/, "Before/After columns are wave 4");
});

test("Self-approval policy line is shown only when the caller is an approver", () => {
	const tpl = template(read());
	assert.match(tpl, /v-if="view\s*&&\s*view\.canApprove\s*&&\s*view\.selfApproval"/);
	assert.match(tpl, /Self-approval policy/);
});
