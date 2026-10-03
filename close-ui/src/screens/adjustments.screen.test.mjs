// konsol#305 A17/A18: adjustments.screen.test.mjs
//
// Source-level checks on screens/Adjustments.vue (E6; stories 6.1, 6.2;
// W2-10; R2). The component is read as text: a .vue file only compiles
// inside the Vite build, which the row's gate runs separately (mirrors
// rates.screen.test.mjs / intercompany.screen.test.mjs). A17's tests above
// cover the read-only list and panel; A18 adds the draft editor, save and
// send tests below.
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
