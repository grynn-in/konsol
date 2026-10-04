// konsol#305 A17/A18/W43: adjustments.screen.test.mjs
//
// Source-level checks on screens/Adjustments.vue (E6; stories 6.1, 6.2;
// W2-10; R2; W43 adds Before/After, #305-W3-4, W4-E18). The component is
// read as text: a .vue file only compiles inside the Vite build, which the
// row's gate runs separately (mirrors rates.screen.test.mjs /
// intercompany.screen.test.mjs / numbers.screen.test.mjs). A17's tests above
// cover the read-only list and panel; A18 adds the draft editor, save and
// send tests; W43 adds the Before/After tests at the bottom.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { beforeAfter, statementView } from "../numbers.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ADJUSTMENTS = path.join(__dirname, "Adjustments.vue");

// W43: the same golden fixtures W42/N54 committed (W4-E19) — the real
// statement_model.statement() payload and the real approvals_model journal
// item built from the real journal_model.statement_effect. Loaded the same
// way numbers.screen.test.mjs loads the statement fixture for Numbers.vue.
const STATEMENT_FIXTURE_PATH = fileURLToPath(
	new URL("../../../konsol/tests/fixtures/close_statement_payload.json", import.meta.url),
);
const JOURNAL_ITEM_FIXTURE_PATH = fileURLToPath(
	new URL("../../../konsol/tests/fixtures/close_approvals_journal_item.json", import.meta.url),
);
const NOW = new Date("2025-08-01T18:00:00Z");
const TZ = "Europe/London";

function goldenStatement() {
	return JSON.parse(fs.readFileSync(STATEMENT_FIXTURE_PATH, "utf8"));
}

function goldenJournalItem() {
	return JSON.parse(fs.readFileSync(JOURNAL_ITEM_FIXTURE_PATH, "utf8"));
}

function read() {
	return fs.readFileSync(ADJUSTMENTS, "utf8");
}

/** The opening tag that contains `tagName` starting at or after `openIdx`
 * -> the whole balanced element (mirrors numbers.screen.test.mjs's
 * blockFor/blockMatching — duplicated here since Adjustments.vue's own test
 * file is outside that row's files). */
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
 * matches `pattern` (mirrors numbers.screen.test.mjs). */
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

/** The opening tag that contains the given index (mirrors rates.screen.test.mjs). */
function tagAt(tpl, i) {
	const tagStart = tpl.lastIndexOf("<", i);
	return tpl.slice(tagStart, tpl.indexOf(">", i) + 1);
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

test("A18: exactly one post(SAVE_JOURNAL and exactly one post(SEND call site, and no other post(", () => {
	const source = read();
	const js = script(source);
	assert.match(source, /SAVE_JOURNAL\s*=\s*["']konsol\.close\.journal_api\.save_journal["']/);
	assert.match(source, /SEND\s*=\s*["']konsol\.close\.journal_api\.send_for_approval["']/);
	assert.equal((js.match(/\bpost\(\s*SAVE_JOURNAL\b/g) || []).length, 1, "one save call site");
	assert.equal((js.match(/\bpost\(\s*SEND\b/g) || []).length, 1, "one send call site");
	assert.equal((js.match(/\bpost\(/g) || []).length, 2, "no third POST");
});

test("A18 amended 3 Oct (U4): 'New' is gated on canOpenNew (canDraft AND canEditPeriod — can_draft alone ignores period status)", () => {
	const source = read();
	const js = script(source);
	const tpl = template(source);
	const newPos = tpl.indexOf(">New<");
	assert.ok(newPos >= 0, "a New control exists");
	assert.match(tagAt(tpl, newPos), /v-if="[^"]*\bnewAllowed\b[^"]*"/, "New is gated on the newAllowed computed");
	assert.match(js, /import\s*\{[^}]*\bcanOpenNew\b[^}]*\}\s*from\s*["']\.\.\/adjustments\.js["']/);
	assert.match(js, /newAllowed\s*=\s*computed\(\s*\(\)\s*=>\s*canOpenNew\(/, "newAllowed computed wraps canOpenNew(view)");
});

test("A18 amended 3 Oct (U3, U4): 'Send for approval' is gated on canSendDraft (canSend, canEditPeriod, a saved name, and no unsaved change)", () => {
	const source = read();
	const js = script(source);
	const tpl = template(source);
	const sendPos = tpl.indexOf(">Send for approval<");
	assert.ok(sendPos >= 0, "a Send for approval control exists");
	assert.match(tagAt(tpl, sendPos), /v-if="[^"]*\bsendAllowed\b[^"]*"/, "Send for approval is gated on the sendAllowed computed");
	assert.match(js, /import\s*\{[^}]*\bcanSendDraft\b[^}]*\}\s*from\s*["']\.\.\/adjustments\.js["']/);
	assert.match(js, /sendAllowed\s*=\s*computed\(\s*\(\)\s*=>\s*canSendDraft\(/, "sendAllowed computed wraps canSendDraft(view, draft, editorDirty)");
});

test("A18 amended 3 Oct (U4): 'Save draft' is gated on canSaveDraft (no invalid line AND canEditPeriod)", () => {
	const source = read();
	const js = script(source);
	const tpl = template(source);
	assert.match(js, /import\s*\{[^}]*\bcanSaveDraft\b[^}]*\}\s*from\s*["']\.\.\/adjustments\.js["']/);
	assert.match(js, /saveAllowed\s*=\s*computed\(\s*\(\)\s*=>\s*canSaveDraft\(/, "saveAllowed computed wraps canSaveDraft(view, totals)");
	const savePos = tpl.indexOf(">Save draft<");
	assert.ok(savePos >= 0, "a Save draft control exists");
	assert.match(tagAt(tpl, savePos), /:disabled="[^"]*\bsaveAllowed\b[^"]*"/, "Save draft's disabled state reads saveAllowed");
});

test("A18 amended 3 Oct (U3): the editor snapshots every field after a save, and Send/the effect note gate on draftDirty against that snapshot, not on lines alone", () => {
	const js = script(read());
	assert.match(js, /import\s*\{[^}]*\bsnapshotDraft\b[^}]*\}\s*from\s*["']\.\.\/adjustments\.js["']/);
	assert.match(js, /import\s*\{[^}]*\bdraftDirty\b[^}]*\}\s*from\s*["']\.\.\/adjustments\.js["']/);
	const saveFn = js.match(/async function saveDraft\(\)\s*\{([\s\S]*?)\n\}/);
	assert.ok(saveFn, "saveDraft() exists");
	assert.match(saveFn[1], /savedSnapshot\.value\s*=\s*snapshotDraft\(/, "saveDraft() takes a fresh snapshot once the save succeeds");
	const openEditFn = js.match(/function openEdit\([\s\S]*?\n\}/);
	assert.ok(openEditFn, "openEdit() exists");
	assert.match(openEditFn[0], /savedSnapshot\.value\s*=\s*snapshotDraft\(/, "openEdit() treats the loaded draft as the last-saved snapshot too");
	assert.match(js, /editorDirty\s*=\s*computed\([\s\S]*?draftDirty\(/, "editorDirty is computed from draftDirty(savedSnapshot, draft)");
});

test("A18 amended 3 Oct (U11): saveDraft() ignores its result if the period changed while the save was in flight", () => {
	const js = script(read());
	const saveFn = js.match(/async function saveDraft\(\)\s*\{([\s\S]*?)\n\}/);
	assert.ok(saveFn, "saveDraft() exists");
	const body = saveFn[1];
	const awaitIdx = body.search(/await\s+post\(\s*SAVE_JOURNAL\b/);
	assert.ok(awaitIdx >= 0, "saveDraft() awaits the save POST");
	const afterAwait = body.slice(awaitIdx);
	assert.match(afterAwait, /\breturn\b/, "a guard after the await can bail out before using the result");
	assert.doesNotMatch(afterAwait.slice(0, afterAwait.search(/\breturn\b/)), /draft\.name\s*=/, "the result is checked against the current period before draft.name is set");
});

test("A18: the request bodies (saveDraft, sendForApproval) carry no forged status/docstatus/approved_by/workflow_state key literal; the save body comes only from saveJournalBody", () => {
	const source = read();
	const js = script(source);
	// Scoped to the two functions that build a request body: the wider script
	// also carries the unrelated local loading-state object `{status:
	// "loading", ...}` (A17's `journals`/`rates.js` pattern), which is not a
	// server request body and is not "the payload" either.
	const saveFn = js.match(/async function saveDraft\(\)\s*\{([\s\S]*?)\n\}/);
	const sendFn = js.match(/async function sendForApproval\(\)\s*\{([\s\S]*?)\n\}/);
	assert.ok(saveFn && sendFn, "saveDraft() and sendForApproval() exist");
	for (const literal of ["docstatus", "status", "approved_by", "workflow_state"]) {
		const re = new RegExp(`\\b${literal}\\s*:`);
		assert.doesNotMatch(saveFn[1], re, `no ${literal}: key literal in saveDraft()`);
		assert.doesNotMatch(sendFn[1], re, `no ${literal}: key literal in sendForApproval()`);
	}
	assert.match(js, /import\s*\{[^}]*\bsaveJournalBody\b[^}]*\}\s*from\s*["']\.\.\/adjustments\.js["']/);
	assert.match(js, /saveJournalBody\(/);
	assert.match(js, /post\(\s*SAVE_JOURNAL\s*,\s*\w+\s*\)/, "the save POST carries a body built elsewhere, not a literal");
	assert.match(js, /post\(\s*SEND\s*,\s*\{\s*name\s*:\s*draft\.name\s*\}\s*\)/, "send carries only the journal's name");
});

test("A18: no 'Stays until reversed' duration option; the choices come from durationOptions (A13)", () => {
	const source = read();
	assert.match(source, /import\s*\{[^}]*\bdurationOptions\b[^}]*\}\s*from\s*["']\.\.\/adjustments\.js["']/);
	assert.match(script(source), /durationOptions\(/);
	assert.doesNotMatch(source, /stays until reversed/i);
});

test("A18: the running Dr/Cr total and Balanced come from draftTotals (A13)", () => {
	const source = read();
	assert.match(source, /import\s*\{[^}]*\bdraftTotals\b[^}]*\}\s*from\s*["']\.\.\/adjustments\.js["']/);
	assert.match(script(source), /draftTotals\(/);
});

test("A17 amended 3 Oct (U6): the list's totals column renders journalsView's formatted totalsText, never the raw total_debit/total_credit", () => {
	const tpl = template(read());
	assert.match(tpl, /\bjournal\.totalsText\b/, "the list reads the formatted totalsText");
	assert.doesNotMatch(tpl, /\bjournal\.total_debit\b/, "no raw total_debit in the template");
	assert.doesNotMatch(tpl, /\bjournal\.total_credit\b/, "no raw total_credit in the template");
});

test("A18 amended 3 Oct: Save is disabled while any line's amount fails to parse; the check runs, and the message names the invalid line numbers, before any save request is sent", () => {
	const source = read();
	const js = script(source);
	const fn = js.match(/async function saveDraft\(\)\s*\{([\s\S]*?)\n\}/);
	assert.ok(fn, "a saveDraft() function");
	const body = fn[1];
	assert.match(body, /\.invalid\.length/, "checks draftTotals(...).invalid before doing anything else");
	const invalidIdx = body.search(/\.invalid\.length/);
	const returnAfter = body.slice(invalidIdx).search(/\breturn\b/);
	assert.ok(returnAfter >= 0 && returnAfter < body.slice(invalidIdx).search(/post\(\s*SAVE_JOURNAL\b/), "returns before any save request when a line is invalid");
	const postIdx = body.search(/post\(\s*SAVE_JOURNAL\b/);
	assert.ok(invalidIdx >= 0 && postIdx >= 0 && invalidIdx < postIdx, "the invalid check runs before the save request");
	const tpl = template(source);
	// Amended 3 Oct (U4): the disabled state now reads `saveAllowed`, which
	// folds the invalid-line check together with `canEditPeriod` — one gate,
	// tested directly as `canSaveDraft` in adjustments.test.mjs, not a
	// template literal repeating the condition.
	assert.match(tpl, /:disabled="[^"]*\bsaveAllowed\b[^"]*"/, "Save draft is disabled while !saveAllowed");
	assert.match(js, /invalidLinesMessage\(/, "a message names the invalid line numbers");
	assert.match(tpl, /invalidLinesMessage\(/, "the message is shown in the template");
});

test("A18: Edit is offered only on an editable journal (A13's editable), and opens the draft editor", () => {
	const source = read();
	assert.match(source, /import\s*\{[^}]*\beditable\b[^}]*\}\s*from\s*["']\.\.\/adjustments\.js["']/);
	assert.match(script(source), /editable\(/);
	const tpl = template(source);
	const editPos = tpl.indexOf(">Edit<");
	assert.ok(editPos >= 0, "an Edit control exists");
	assert.match(tagAt(tpl, editPos), /v-if="[^"]*\bcanEditSelected\b[^"]*"/, "Edit is gated on the editable() result");
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

// --- W43: Before/After in the journal effect panel (#305-W3-4, W4-E18) -----

// pure: the panel's rows must equal beforeAfter(goldenEffect,
// statementView(golden)) exactly — the REAL producers' output (W4-E19),
// never a hand-built row. This pins the same two cases W42 already proved
// in numbers.test.mjs (a BS paydown reducing the liability; a PL heading
// reusing the section-wide -1 flip), as the concrete numbers this screen's
// wiring must reproduce.
test("pure: beforeAfter(goldenEffect, statementView(golden)) — the exact rows the panel must show", () => {
	const view = statementView(goldenStatement(), NOW, TZ);
	const item = goldenJournalItem();
	const rows = beforeAfter(item.effect, view);

	const liability = rows.find((r) => r.heading === "2");
	assert.deepEqual(liability, {
		section: "Balance Sheet",
		heading: "2",
		headingName: "Liabilities",
		before: "803.70",
		change: "(500.00)",
		after: "303.70",
	});

	const pl = rows.find((r) => r.heading === "4" && r.section === "Profit and Loss");
	assert.deepEqual(pl, {
		section: "Profit and Loss",
		heading: "4",
		headingName: "Revenue",
		before: "241.43",
		change: "500.00",
		after: "741.43",
	});
});

test("Red: Adjustments.vue reads no statement — W43 not wired yet", () => {
	const source = read();
	assert.doesNotMatch(
		source,
		/konsol\.close\.statement_api\.get_statement/,
		"this check goes red the moment W43 adds the GET_STATEMENT call",
	);
});

test("W43: Adjustments.vue builds the panel with beforeAfter/statementView from ../numbers.js", () => {
	const source = read();
	assert.match(source, /import\s*\{[^}]*\bbeforeAfter\b[^}]*\}\s*from\s*["']\.\.\/numbers\.js["']/);
	assert.match(source, /import\s*\{[^}]*\bstatementView\b[^}]*\}\s*from\s*["']\.\.\/numbers\.js["']/);
	assert.match(script(source), /beforeAfter\(/);
	assert.match(script(source), /statementView\(/);
});

test("W43: Names konsol.close.statement_api.get_statement exactly once; exactly one get(GET_STATEMENT call site", () => {
	const source = read();
	const names = source.match(/konsol\.close\.statement_api\.get_statement/g) || [];
	assert.equal(names.length, 1, "one endpoint constant for get_statement");
	const js = script(source);
	const gets = js.match(/\bget\(\s*GET_STATEMENT\b/g) || [];
	assert.equal(gets.length, 1, "exactly one get(GET_STATEMENT call site");
});

test("W43: a second seq guard exists for the statement load, so a stale response is dropped (distinct from the journals list's own seq)", () => {
	const js = script(read());
	const seqDecls = js.match(/let\s+(\w*[sS]eq\w*)\s*=\s*0/g) || [];
	assert.ok(seqDecls.length >= 2, "a seq counter for the journals list AND one for the statement load");
	const names = seqDecls.map((d) => d.match(/let\s+(\w+)\s*=\s*0/)[1]);
	assert.ok(new Set(names).size >= 2, "the two seq counters are distinct variables");
});

test("W43: Before/After is rendered only when selectedJournal.docstatus === 0; an approved/reversed journal shows 'Included in the statement'", () => {
	const tpl = template(read());
	const draftBlock = blockMatching(tpl, "template", /v-if="[^"]*selectedJournal\.docstatus\s*===\s*0[^"]*"|v-else-if="[^"]*selectedJournal\.docstatus\s*===\s*0[^"]*"/);
	assert.ok(draftBlock, "a template branch is gated on selectedJournal.docstatus === 0");
	const inner = tpl.slice(draftBlock.start, draftBlock.end);
	assert.match(inner, /beforeAfter|Before|Change|After/i, "the docstatus===0 branch renders the before/after data");
	assert.match(tpl, /Included in the statement/i, "an approved/reversed journal shows this sentence");
});

test("W43: a statement load error is shown as visible text in the panel, never swallowed", () => {
	const js = script(read());
	assert.match(
		js,
		/catch\s*\(\s*e\s*\)\s*\{[\s\S]*?\.(error|message)\s*=\s*e\.message/,
		"the statement GET's catch block records the error for display",
	);
});

test("W43: a non-ok statement state shows its own message, never a blank or zero before/after", () => {
	const tpl = template(read());
	// The non-ok-state message must appear somewhere in the Before/After
	// branch, not just the journals-list LoadState error (which already has
	// its own :error binding tested elsewhere) — grep the whole template for
	// a second, distinct message/error binding feeding the side panel.
	const panelSection = tpl.slice(tpl.indexOf("Journal detail") >= 0 ? tpl.indexOf("role=\"dialog\"") : 0);
	assert.match(
		panelSection,
		/\.(message|text|error)\b/,
		"the side panel reads a message/text/error field for the non-ok statement state",
	);
});
