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
import { beforeAfter, statementView } from "../numbers.js";
import { queueView } from "../approvals.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APPROVALS = path.join(__dirname, "Approvals.vue");

// konsol#305 W44: the real golden fixtures (never a hand-built item or
// statement) — W41's journal item (fiscal_year/fiscal_period/
// consolidation_group, the three keys this row reads) and N51/N54's
// statement payload (each heading line's own display `sign`).
const ITEM_FIXTURE = fileURLToPath(
	new URL("../../../konsol/tests/fixtures/close_approvals_journal_item.json", import.meta.url),
);
const STATEMENT_FIXTURE = fileURLToPath(
	new URL("../../../konsol/tests/fixtures/close_statement_payload.json", import.meta.url),
);

function golden(fixturePath) {
	return JSON.parse(fs.readFileSync(fixturePath, "utf8"));
}

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

/**
 * From the `<` of an opening `tagName` tag at `openIdx`, finds the matching
 * closing tag by counting nested opens/closes of the same tag name (mirrors
 * numbers.screen.test.mjs's own helper — the templates here nest plainly,
 * with no self-closing `<div/>`). Returns `{start, end}` spanning the whole
 * element, end exclusive of the closing tag's `>`.
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

test("No raw fetch( — W44 now builds Before/After columns (A19's wave-4 stub above), but every server call still goes through api.js", () => {
	const source = read();
	assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js");
});

test("Self-approval policy line is shown only when the caller is an approver", () => {
	const tpl = template(read());
	assert.match(tpl, /v-if="view\s*&&\s*view\.canApprove\s*&&\s*view\.selfApproval"/);
	assert.match(tpl, /Self-approval policy/);
});

// --- W44: Before/Change/After per heading in the journal detail panel ------

// konsol#305 R41g/U2: the screen never hands beforeAfter the raw fixture
// directly — it selects a row off `queueView`'s own `items`, exactly as
// Approvals.vue's template does (`v-for="item in view.items"`, then
// `selectJournal(item)`). `queueView` has already run the journal's effect
// through `effectView` for display (approvals.js's `baseView`), which drops
// `net_debit`/`heading_name`; the raw effect for `beforeAfter` has to come
// from a field that survives that, which is what `rawEffect` is for. Feeding
// the OLD (view-shaped) `.effect` here reproduces U2's bug: "NaN" and
// `headingName: undefined`, caught by the non-finite guard below.
function queuePayloadFor(item) {
	return {
		waiting: { count: 1, oldest: item.created },
		hidden: 0,
		items: [item],
		sent_back: [],
		can_approve: true,
		self_approval: null,
	};
}

test("pure: Approvals' own path — queueView(...).items[0].rawEffect into beforeAfter — gives the amended per-heading before/change/after, never NaN (N54/W42/U2)", () => {
	const item = golden(ITEM_FIXTURE);
	const statement = golden(STATEMENT_FIXTURE);
	const now = new Date("2025-08-01T18:00:00Z");
	const tz = "Europe/London";
	const queue = queueView(queuePayloadFor(item), now, tz);
	const queueItem = queue.items[0];
	const view = statementView(statement, now, tz);
	const rows = beforeAfter(queueItem.rawEffect, view);
	assert.deepEqual(
		rows.map((r) => [r.heading, r.before, r.change, r.after]),
		[
			["4", "241.43", "500.00", "741.43"],
			["2", "803.70", "(500.00)", "303.70"],
		],
	);
});

test("failure path: feeding beforeAfter the queueView item's display-shaped .effect (U2's bug) throws instead of rendering \"NaN\"", () => {
	const item = golden(ITEM_FIXTURE);
	const statement = golden(STATEMENT_FIXTURE);
	const now = new Date("2025-08-01T18:00:00Z");
	const tz = "Europe/London";
	const queue = queueView(queuePayloadFor(item), now, tz);
	const queueItem = queue.items[0];
	const view = statementView(statement, now, tz);
	assert.throws(() => beforeAfter(queueItem.effect, view), /non-finite amount/);
});

// konsol#305 R41g/U2: beforeAfter is called with the raw effect
// (`rawEffect`, approvals.js), never the display-shaped `.effect`
// (`effectView`'s output, which has no `net_debit`/`heading_name`).
test("beforeAfter is called with selectedItem.value.rawEffect, never .effect (U2)", () => {
	const js = script(read());
	assert.match(js, /beforeAfter\(\s*selectedItem\.value\.rawEffect\s*,/, "beforeAfter reads the raw effect, not the view-shaped one");
	assert.doesNotMatch(
		js.replace(/beforeAfter\(\s*selectedItem\.value\.rawEffect\s*,/g, ""),
		/beforeAfter\(\s*selectedItem\.value\.effect\b/,
		"no remaining call site still passes the display-shaped effect",
	);
});

test("Imports beforeAfter and statementView from numbers.js (W44)", () => {
	const source = read();
	assert.match(source, /import\s*\{[^}]*\bbeforeAfter\b[^}]*\}\s*from\s*["']\.\.\/numbers\.js["']/);
	assert.match(source, /import\s*\{[^}]*\bstatementView\b[^}]*\}\s*from\s*["']\.\.\/numbers\.js["']/);
});

test("Names konsol.close.statement_api.get_statement exactly once; exactly one get(GET_STATEMENT call site (W44)", () => {
	const source = read();
	const names = source.match(/konsol\.close\.statement_api\.get_statement/g) || [];
	assert.equal(names.length, 1, "one endpoint constant for get_statement");
	const js = script(source);
	const gets = js.match(/\bget\(\s*GET_STATEMENT\b/g) || [];
	assert.equal(gets.length, 1, "exactly one get(GET_STATEMENT call site");
});

test("The statement loads only for a journal item: loadDetailStatement is called from selectJournal, after its doctype !== JOURNAL return guard", () => {
	const js = script(read());
	const selectFn = js.match(/function selectJournal\(item\)\s*\{([\s\S]*?)\n\}/);
	assert.ok(selectFn, "selectJournal(item) is defined");
	const guardIdx = selectFn[1].search(/item\.doctype\s*!==\s*JOURNAL/);
	assert.ok(guardIdx >= 0, "selectJournal still refuses a non-journal item first");
	const loaderCallIdx = selectFn[1].search(/loadDetailStatement\(/);
	assert.ok(loaderCallIdx > guardIdx, "the statement loader is only reached past the JOURNAL guard, so BC/BD items never load one");
	const loaderFn = js.match(/async function loadDetailStatement\(item\)\s*\{([\s\S]*?)\n\}/);
	assert.ok(loaderFn, "loadDetailStatement(item) is defined");
	assert.match(loaderFn[1], /get\(\s*GET_STATEMENT\b/, "loadDetailStatement is the one get(GET_STATEMENT call site");
	assert.match(
		loaderFn[1],
		/fiscal_year\s*:\s*item\.fiscal_year[\s\S]*?fiscal_period\s*:\s*item\.fiscal_period[\s\S]*?consolidation_group\s*:\s*item\.consolidation_group/,
		"reads the item's own period/group (W41's keys), not the screen's",
	);
});

test("Stale-guarded: a sequence counter distinct from the queue's own seq drops a late response", () => {
	const js = script(read());
	assert.match(js, /let\s+detailSeq\s*=\s*0/);
	const loaderFn = js.match(/async function loadDetailStatement\(item\)\s*\{([\s\S]*?)\n\}/);
	assert.ok(loaderFn, "loadDetailStatement(item) is defined");
	assert.match(loaderFn[1], /\+\+detailSeq\b/, "a fresh call claims a new sequence number");
	assert.match(loaderFn[1], /mine\s*!==\s*detailSeq/, "a stale response is dropped before it is applied");
});

test("Before/Change/After columns render for the selected journal's effect", () => {
	const tpl = template(read());
	assert.match(tpl, /Before/);
	assert.match(tpl, /Change/);
	assert.match(tpl, /After/);
});

test("Failure path: a non-ok or thrown statement shows its own message text, in a branch separate from the Before/After table — never blank or zero columns", () => {
	const source = read();
	const js = script(source);
	assert.match(js, /detailBeforeAfter/, "a computed combines the fetched statement with beforeAfter/statementView");
	const tpl = template(source);
	const errBlock = blockMatching(tpl, "p", /detailBeforeAfter\.status === 'error'/);
	assert.ok(errBlock, "an error branch renders detailBeforeAfter.message");
	const inner = tpl.slice(errBlock.start, errBlock.end);
	assert.match(inner, /detailBeforeAfter\.message/);
	const tableBlock = blockMatching(tpl, "table", /<table\b/);
	assert.ok(tableBlock, "a <table> renders the Before/Change/After rows, separate from the error paragraph");
	assert.ok(
		errBlock.end <= tableBlock.start || tableBlock.end <= errBlock.start,
		"the error message and the table are sibling branches, never shown together",
	);
});
