// konsol#305 E409: rates.screen.test.mjs
//
// Source-level checks on screens/Rates.vue (E4; stories 4.1, 4.3; R2, R5;
// 0.4). The component is read as text, as checks.screen.test.mjs does: a .vue
// file only compiles inside the Vite build, which the row's gate runs apart.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const RATES = path.join(__dirname, "Rates.vue");

function read() {
  return fs.readFileSync(RATES, "utf8");
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

/** The opening tag that contains the given index. */
function tagAt(tpl, i) {
  const tagStart = tpl.lastIndexOf("<", i);
  return tpl.slice(tagStart, tpl.indexOf(">", i) + 1);
}

/** Every opening tag carrying `needle`. */
function tagsWith(tpl, needle) {
  const out = [];
  let i = tpl.indexOf(needle);
  while (i >= 0) {
    out.push(tagAt(tpl, i));
    i = tpl.indexOf(needle, i + needle.length);
  }
  return out;
}

test("Rates.vue builds what it shows and sends with rates.js (E407)", () => {
  const source = read();
  const imp = source.match(/import\s*\{([^}]*)\}\s*from\s*["']\.\.\/rates\.js["']/);
  assert.ok(imp, "imports from ../rates.js");
  for (const name of ["gridView", "saveBody", "approveAction", "approveBody"]) {
    assert.match(imp[1], new RegExp(`\\b${name}\\b`), `imports ${name}`);
  }
  const js = script(source);
  assert.match(js, /gridView\(/);
  assert.match(js, /saveBody\(/);
  assert.match(js, /approveBody\(/);
});

test("Rates.vue calls the server through api.js and reads the period with route.js", () => {
  const source = read();
  assert.match(source, /import\s*\{[^}]*\bget\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
  assert.match(source, /import\s*\{[^}]*\bpost\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
  assert.match(source, /import\s*\{[^}]*\}\s*from\s*["']\.\.\/route\.js["']/);
  assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js (CSRF on POST)");
});

// The Web Storage APIs are guarded for every file under close-ui/src by
// route.test.mjs ("no file under close-ui/src reads or writes ..."), which
// would also flag this file if it named them; this test covers the rest.
test("No other browser storage (D5): the period lives in the URL only", () => {
  const source = read();
  assert.doesNotMatch(source, /indexedDB|document\.cookie|caches\./);
});

test("No v-html anywhere: server sentences are plain text", () => {
  assert.doesNotMatch(read(), /v-html/);
});

test("It names get_rates, save_rate and approval_api.approve", () => {
  const js = script(read());
  assert.match(js, /["']konsol\.close\.rates_api\.get_rates["']/);
  assert.match(js, /["']konsol\.close\.rates_api\.save_rate["']/);
  assert.match(js, /APPROVE\s*=\s*["']konsol\.close\.approval_api\.approve["']/);
});

test("Exactly one post(APPROVE, one post(SAVE_RATE and one post(DELETE_RATE call site, and no other post(", () => {
  const js = script(read());
  assert.equal((js.match(/\bpost\(\s*APPROVE\b/g) || []).length, 1, "one approve call site");
  assert.equal((js.match(/\bpost\(\s*SAVE_RATE\b/g) || []).length, 1, "one save call site");
  assert.equal((js.match(/\bpost\(\s*DELETE_RATE\b/g) || []).length, 1, "one delete call site (FR8)");
  assert.equal((js.match(/\bpost\(/g) || []).length, 3, "no fourth POST");
});

test("The single approve call lives in approve(doctype, name, reason)", () => {
  const js = script(read());
  const m = js.match(/async function approve\(\s*doctype\s*,\s*name\s*,\s*reason\s*\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(m, "a function approve(doctype, name, reason)");
  assert.match(m[1], /\bpost\(\s*APPROVE\b/, "the approve POST is inside approve()");
});

test("Failure path — no Import from file (#305-W2-3)", () => {
  const source = read();
  assert.doesNotMatch(template(source), /Import from file/i);
  assert.doesNotMatch(source, /upload|FileReader/);
  assert.doesNotMatch(source, /type="file"/);
});

test("Failure path — approve by an Analyst or Viewer: every approve control is gated on the approveAction kind", () => {
  const tpl = template(read());
  const buttons = tagsWith(tpl, "Approve");
  // Every element whose click starts an approval sits under a v-if on the kind.
  const clicks = tagsWith(tpl, "@click=\"approveCell(");
  assert.ok(clicks.length >= 1, "an approve control exists");
  for (const tag of clicks) {
    assert.match(
      tag,
      /v-if="[^"]*\bcanApproveNow\(/,
      `approve control is gated on the kind: ${tag}`,
    );
  }
  assert.ok(buttons.length >= 1);
  const js = script(read());
  const fn = js.match(/function canApproveNow\(([^)]*)\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(fn, "canApproveNow(cell) decides from the approveAction kind");
  assert.match(fn[2], /["']button["']/);
  assert.match(fn[2], /["']reason["']/);
  assert.doesNotMatch(fn[2], /["']refused["']|["']none["']/, "refused and none never approve");
});

test("A `reason` approve opens an inline reason input before posting", () => {
  const tpl = template(read());
  assert.match(tpl, /v-if="[^"]*\.kind\s*===\s*'reason'[^"]*"/, "the reason input shows only for kind reason");
});

test("Failure path — `refused` renders the server's message as text, `none` says the Close Lead approves (R2)", () => {
  const tpl = template(read());
  assert.match(tpl, /v-else-if="[^"]*\.kind\s*===\s*'refused'[^"]*"/);
  assert.match(tpl, /\.approve\.message\b/, "the refusal is the server's sentence");
  assert.match(tpl, /The Close Lead approves \(R2\)/);
});

test("Save rates is rendered only when canEnter, and cell inputs only when editable", () => {
  const tpl = template(read());
  const i = tpl.indexOf("Save rates");
  assert.ok(i >= 0, "a Save rates control");
  const btnStart = tpl.lastIndexOf("<Button", i);
  const tag = tpl.slice(btnStart, tpl.indexOf(">", btnStart) + 1);
  assert.match(tag, /v-if="[^"]*\bcanEnter\b[^"]*"/, "Save rates is gated on canEnter");
  const inputs = tagsWith(tpl, "<input");
  assert.ok(inputs.length >= 1, "cells can be edited");
  const js = script(read());
  const fn = js.match(/function editable\(([^)]*)\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(fn, "editable(cell) decides whether a cell takes input");
  assert.match(fn[2], /\bcanEnter\b/, "nothing is editable without canEnter (Viewer, Close Lead off-Open)");
});

test("Request bodies come only from saveBody and approveBody: no forged field literals", () => {
  const js = script(read());
  for (const literal of ["docstatus", "source", "erp_quote", "owner", "source_note"]) {
    assert.doesNotMatch(js, new RegExp(`\\b${literal}\\s*:`), `no ${literal}: key in the script`);
  }
  // Each POST sends the body a rates.js builder returned.
  assert.match(js, /post\(\s*SAVE_RATE\s*,\s*built\.body\s*\)/);
  assert.match(js, /post\(\s*APPROVE\s*,\s*built\.body\s*\)/);
});

test("A refusal is shown as the server wrote it, through messageLines, announced", () => {
  const source = read();
  assert.match(source, /import\s*\{[^}]*\bmessageLines\b[^}]*\}\s*from\s*["']\.\.\/signoff\.js["']/);
  assert.match(script(source), /catch\s*\(\s*(\w+)\s*\)\s*\{[\s\S]*?\1\.message/, "keeps e.message");
  assert.doesNotMatch(source, /Something went wrong/i);
  assert.match(template(source), /role="alert"/);
});

test("Loading, empty and error states go through LoadState, with a seq guard", () => {
  const source = read();
  assert.match(source, /import\s+LoadState\s+from\s*["']\.\.\/components\/LoadState\.vue["']/);
  assert.match(template(source), /<LoadState[\s\S]*?@retry=/);
  const js = script(source);
  assert.match(js, /mine\s*!==\s*seq/, "stale responses are dropped");
});

test("After a successful approve, the shell's context reloads once", () => {
  const source = read();
  const js = script(source);
  assert.match(source, /import\s*\{[^}]*\bCONTEXT_RELOAD\b[^}]*\}\s*from\s*["']\.\.\/contextRefresh\.js["']/);
  assert.match(js, /\binject\(\s*CONTEXT_RELOAD\s*\)/);
  const fn = js.match(/async function approve\(\s*doctype\s*,\s*name\s*,\s*reason\s*\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(fn);
  assert.equal((fn[1].match(/reloadContext\(\)/g) || []).length, 1, "one context reload in approve()");
  assert.equal((js.match(/reloadContext\(\)/g) || []).length, 1, "and nowhere else");
});

test("Unrequired rows render under their own heading, and the banner renders gridView's lines", () => {
  const tpl = template(read());
  assert.match(tpl, /Rates this period does not need/);
  assert.match(tpl, /\.unrequired\b/);
  assert.match(tpl, /\.banner\b/);
  assert.match(tpl, /\.thresholdText\b/);
});

test("Quoted Per options come from the server's quotedPerOptions (E409c): no hand-copied list of quote units", () => {
  const source = read();
  assert.doesNotMatch(
    script(source),
    /\[\s*["']1["']\s*,\s*["']10["']\s*,\s*["']100["']\s*,\s*["']1000["']\s*,\s*["']10000["']\s*\]/,
    "no literal array of quote units",
  );
  const tpl = template(source);
  assert.match(tpl, /v-for="q in (view\.)?quotedPerOptions"/, "the select options iterate view.quotedPerOptions");
});

test("The subtitle names each group currency from the rows' to_currency, never assumed", () => {
  const source = read();
  assert.match(script(source), /\.toCurrency\b/);
  assert.match(source, /group currency/);
  assert.doesNotMatch(source, /["'](GBP|EUR|USD)["']/, "no hard-coded currency");
});

test("The tab bar is final: Group rates, Historical equity rates, Ownership", () => {
  const tpl = template(read());
  assert.match(tpl, /Group rates/);
  assert.match(tpl, /Historical equity rates/);
  assert.match(tpl, /Ownership/);
  assert.match(tpl, /role="tab"/);
});

test("Status, previous rate, delta, flag and preparer come from the cell view", () => {
  const tpl = template(read());
  for (const field of ["statusLabel", "previousValue", "previousLabel", "deltaText", "flagTag", "preparer"]) {
    assert.match(tpl, new RegExp(`\\.${field}\\b`), `renders ${field}`);
  }
});

// -- R01v: a move is judged only where a rate can still be entered -----------

test("R01v goal 1: no bare .flag left in the template — the decision is flagTag/flagMessage from rates.js, not a re-check of the raw server flag", () => {
  const tpl = template(read());
  assert.doesNotMatch(tpl, /\.flag\b(?!(Tag|Message))/, "the template reads flagTag/flagMessage, not the raw cell.flag");
});

test("R01v goal 1: the per-cell warning box is keyed off flagMessage (null for an Approved cell), not the raw flag", () => {
  const js = script(read());
  const fn = js.match(/function rowNotes\(row\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(fn, "rowNotes(row) exists");
  assert.match(fn[1], /\.flagMessage\b/, "rowNotes reads cell.flagMessage, not cell.flag");
});

test("R01v goal 4: the Ownership tab count comes from the pure ownershipGapsCount helper (visible + hidden), not blocking.length alone", () => {
  const source = read();
  const imp = source.match(/import\s*\{([^}]*)\}\s*from\s*["']\.\.\/rates\.js["']/);
  assert.ok(imp, "imports from ../rates.js");
  assert.match(imp[1], /\bownershipGapsCount\b/, "imports ownershipGapsCount");
  const js = script(source);
  assert.match(js, /\bownershipGapsCount\(/, "the tab's gap count is built from ownershipGapsCount, not .blocking.length alone");
});

// -- R01o: change reason, edited by, extra drafts, source (SPA should-fix 6) --

test("a cell shows changeReason, editedBy, extraDraftsText and source (R01o)", () => {
  const tpl = template(read());
  for (const field of ["changeReason", "editedBy", "extraDraftsText", "source"]) {
    assert.match(tpl, new RegExp(`\\.${field}\\b`), `renders ${field}`);
  }
});

// -- E410: pending HER/OP drafts, mounted in the "Historical equity rates" tab --

test("Rates.vue names get_pending, and still has exactly one post(APPROVE call site", () => {
  const js = script(read());
  assert.match(js, /GET_PENDING\s*=\s*["']konsol\.close\.rates_api\.get_pending["']/);
  assert.equal((js.match(/\bpost\(\s*APPROVE\b/g) || []).length, 1, "still one approve call site");
  assert.equal((js.match(/\bpost\(/g) || []).length, 2, "no third POST (get_pending is a GET)");
});

test("Rates.vue mounts RatesPending in the Historical equity rates tab, fed by pendingView", () => {
  const source = read();
  assert.match(
    source,
    /import\s+RatesPending\s+from\s*["']\.\.\/sections\/RatesPending\.vue["']/,
    "imports the section",
  );
  assert.match(source, /import\s*\{[^}]*\bpendingView\b[^}]*\}\s*from\s*["']\.\.\/rates\.js["']/, "imports pendingView");
  const tpl = template(source);
  assert.match(tpl, /<RatesPending\b/, "RatesPending is mounted");
  // It is mounted inside the her tabpanel, not the group or ownership ones.
  const herStart = tpl.indexOf('aria-label="Historical equity rates"');
  const herEnd = tpl.indexOf("</section>", herStart);
  assert.ok(herStart >= 0 && herEnd > herStart, "the her tabpanel exists");
  assert.match(tpl.slice(herStart, herEnd), /<RatesPending\b/, "RatesPending renders inside the her tabpanel");
});

test("The Historical equity rates tab label carries every pending item (HER and OP), via the pure pendingCount helper", () => {
  const source = read();
  assert.match(source, /Historical equity rates.*pending/);
  const js = script(source);
  assert.match(js, /import\s*\{[^}]*\bpendingCount\b[^}]*\}\s*from\s*["']\.\.\/rates\.js["']/, "imports pendingCount");
  assert.match(js, /\bpendingCount\(/, "the tab label is built from pendingCount, not counts['Historical Equity Rate'] alone");
});

test("get_pending is loaded on mount and reloaded after a successful approve", () => {
  const js = script(read());
  assert.match(js, /\bloadPending\s*\(/, "a loadPending function is called");
  const fn = js.match(/async function approve\(\s*doctype\s*,\s*name\s*,\s*reason\s*\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(fn);
  assert.match(fn[1], /loadPending\(/, "approve() reloads the pending list");
});

test("Failure path: an HER or OP approve looks up its mode from the loaded pending payload, not only the grid", () => {
  const js = script(read());
  const fn = js.match(/function actionFor\(([^)]*)\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(fn, "actionFor(doctype, name) exists");
  assert.match(fn[2], /pending\.payload/, "actionFor also searches the pending payload");
});

// -- R01n: approve keeps unsaved edits; a save for an old period never lands on the new one --

test("Rates.vue imports mergeDrafts from ../rates.js and resetDrafts uses it", () => {
  const source = read();
  const imp = source.match(/import\s*\{([^}]*)\}\s*from\s*["']\.\.\/rates\.js["']/);
  assert.ok(imp, "imports from ../rates.js");
  assert.match(imp[1], /\bmergeDrafts\b/, "imports mergeDrafts");
  const js = script(source);
  const fn = js.match(/function resetDrafts\(\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(fn, "resetDrafts() exists");
  assert.match(fn[1], /\bmergeDrafts\(/, "resetDrafts rebuilds each cell through mergeDrafts, not an inline merge");
});

test("Failure path — resetDrafts no longer wipes a dirty, un-refused draft by only keeping cellErrors keys", () => {
  const js = script(read());
  const fn = js.match(/function resetDrafts\(\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(fn);
  // The old bug: every key not in cellErrors was deleted outright before any
  // per-cell decision. Guard against that pattern coming back.
  assert.doesNotMatch(
    fn[1],
    /for\s*\(\s*const k of Object\.keys\(drafts\)\s*\)\s*if\s*\(!keep\.has\(k\)\)\s*delete drafts\[k\]/,
    "drafts are no longer blanket-deleted ahead of the merge decision",
  );
});

test("saveRates captures seq at its start, before any await", () => {
  const js = script(read());
  const fn = js.match(/async function saveRates\(\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(fn, "saveRates() exists");
  const body = fn[1];
  const captureIdx = body.search(/\bmySeq\s*=\s*seq\b/);
  assert.ok(captureIdx >= 0, "captures mySeq = seq");
  const firstAwaitIdx = body.indexOf("await ");
  assert.ok(firstAwaitIdx < 0 || captureIdx < firstAwaitIdx, "seq is captured before the first await");
});

test("saveRates checks mySeq against seq before every cellErrors/reasonOpen write", () => {
  const js = script(read());
  const fn = js.match(/async function saveRates\(\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(fn);
  const body = fn[1];
  const writeRe = /\bcellErrors\[[^\]]+\]\s*=|\breasonOpen\[[^\]]+\]\s*=/g;
  let match;
  let found = 0;
  while ((match = writeRe.exec(body))) {
    found++;
    // A guard either sits on the same line (`if (mySeq === seq) cellErrors[...] = ...;`)
    // or wraps a small block above it (`if (mySeq === seq) {` then the write a
    // couple of lines down); either way it is close by, not anywhere in the function.
    const before = body.slice(Math.max(0, match.index - 160), match.index);
    assert.match(before, /mySeq\s*===\s*seq/, `write is guarded by the seq check nearby: ${match[0]}`);
  }
  assert.ok(found >= 2, "saveRates still writes cellErrors and reasonOpen somewhere");
});

test("saveRates does not reload a period it has already navigated away from", () => {
  const js = script(read());
  const fn = js.match(/async function saveRates\(\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(fn);
  assert.match(fn[1], /mySeq\s*===\s*seq[\s\S]*?loadRates\(/, "the final reload is also guarded by mySeq === seq");
});

// -- O60 (story 4.2; wireframe-4.2.md section 1, confirmed as drawn by Deepak Pai 7 Oct) --
// The Ownership tab mounts the "Change ownership" form (sections/OwnershipChange.vue)
// only when the server says can_change (R53f; #305-R52-4, U10e: was can_record,
// which is the Desk "Record ownership" link's flag), fed get_ownership's
// `change` choices (O63).

test("O60/R53f: the Ownership tab mounts OwnershipChange only when can_change, with get_ownership's change", () => {
  const source = read();
  assert.match(source, /import OwnershipChange from ["']\.\.\/sections\/OwnershipChange\.vue["']/);
  const tpl = template(source);
  const ownershipTab = tpl.slice(tpl.indexOf('aria-label="Ownership"'));
  const tags = tagsWith(ownershipTab, "<OwnershipChange");
  assert.equal(tags.length, 1, "one OwnershipChange");
  assert.match(tags[0], /v-if="[^"]*\bownershipViewData\.canChange\b[^"]*"/, "v-if on can_change");
  assert.doesNotMatch(tags[0], /canRecord/, "never gated on can_record");
  assert.match(tags[0], /:change="ownership\.payload\.change"/, "fed the server's change choices");
  assert.match(tags[0], /@saved="[^"]*\bloadPending\(/, "a saved draft reloads the pending tab");
});

// R53f: the mount gate evaluated on the REAL ownershipView of the golden
// (can_change true) and of the Viewer copy (can_change false, change null).
test("R53f: the form mounts for the golden payload and not for the Viewer payload (the v-if evaluated)", async () => {
  const { ownershipView } = await import("../rates.js");
  const golden = JSON.parse(
    fs.readFileSync(path.join(__dirname, "..", "..", "..", "konsol", "tests", "fixtures", "close_ownership_payload.json"), "utf8"),
  );
  const viewer = { ...golden, can_change: false, change: null };
  const tpl = template(read());
  const tag = tagsWith(tpl.slice(tpl.indexOf('aria-label="Ownership"')), "<OwnershipChange")[0];
  const vIf = tag.match(/v-if="([^"]*)"/)[1];
  const gate = new Function("ownershipViewData", `return Boolean(${vIf});`);
  assert.equal(gate(ownershipView(golden)), true, "the golden (can_change true) mounts the form");
  assert.equal(gate(ownershipView(viewer)), false, "the Viewer (can_change false) sees no form");
  assert.equal(gate(ownershipView({ ...viewer, can_record: true })), false, "can_record alone never mounts it");
  assert.equal(gate(null), false, "nothing mounts while loading");
});

test("O60: the preview and save calls live in OwnershipChange only, never a second call site here", () => {
  const js = script(read());
  assert.doesNotMatch(js, /preview_ownership_change|save_ownership_change/);
});

test("O60 failure path: no input for an acquisition or disposal date or price exists on the screen", () => {
  const tpl = template(read());
  for (const tag of tpl.match(/<(input|select|textarea)\b[^>]*>/g) || []) {
    assert.doesNotMatch(tag, /acquisition|disposal|price/i, tag);
  }
});

// -- O67 (story 4.2; wireframe-4.2.md section 1, "The Analyst can edit it until
// it is approved", confirmed by Deepak Pai 7 Oct) --
// The pending list's Edit (RatesPending, fed the server's `edit`, O69) opens the
// draft in the Change ownership form on the Ownership tab. The editable drafts
// are rates.js ownershipDraftEdits of the pending view: never parsed from a title.

test("O67: RatesPending's edit switches to the Ownership tab and asks the form to open that draft", () => {
  const source = read();
  const js = script(source);
  const fn = js.match(/\nfunction startEdit\(name\)\s*\{[\s\S]*?\n\}\n/);
  assert.ok(fn, "Rates.vue declares function startEdit(name)");
  const openEdit = { value: null };
  const tab = { value: "her" };
  new Function("openEdit", "tab", `${fn[0]}startEdit("OP-ZZ5B1-2025-10-01");`)(openEdit, tab);
  assert.equal(openEdit.value, "OP-ZZ5B1-2025-10-01");
  assert.equal(tab.value, "ownership");
  const tpl = template(source);
  const pendingTags = tagsWith(tpl, "<RatesPending");
  assert.equal(pendingTags.length, 1);
  assert.match(pendingTags[0], /@edit="startEdit"/);
});

test("O67: the form gets the server's editable drafts (ownershipDraftEdits of the pending view) and the draft to open", () => {
  const source = read();
  const js = script(source);
  assert.match(js, /import\s*\{[^}]*\bownershipDraftEdits\b[^}]*\}\s*from\s*["']\.\.\/rates\.js["']/);
  assert.match(js, /const draftEdits = computed\(\(\) =>[\s\S]*?ownershipDraftEdits\(pendingViewData\.value\.items\)/);
  assert.match(js, /const openEdit = ref\(null\);/);
  const tpl = template(source);
  const tags = tagsWith(tpl.slice(tpl.indexOf('aria-label="Ownership"')), "<OwnershipChange");
  assert.equal(tags.length, 1);
  assert.match(tags[0], /:editable="draftEdits"/);
  assert.match(tags[0], /:open-edit="openEdit"/);
  assert.match(tags[0], /@edit-opened="openEdit = null"/, "a draft is opened once, not again on the next mount");
  assert.doesNotMatch(js, /\.title\b[^;]*(split|match)|\.detail\b[^;]*(split|match)/, "no title or detail parsing");
});

// -- F12 (#312-3, #312-3b; Deepak Pai 9 Oct): "Correct" on an approved rate --
// The server's `correct` alone mounts Correct; the form posts through the one
// save call site with correctionBody (supersedes + reason); a refusal is the
// server's sentence; the pending correction (on its grid cell, F11) shows
// old → new and is approved through the one approve(). Fed the REAL F11 golden.

const F12_GOLDEN = JSON.parse(
  fs.readFileSync(path.join(__dirname, "..", "..", "..", "konsol", "tests", "fixtures", "close_rates_correction_payload.json"), "utf8"),
);
const f12CellOf = (row, rateType) => (rateType === "Closing" ? row.closing : row.average);

async function f12Cells() {
  const { gridView } = await import("../rates.js");
  const view = gridView(F12_GOLDEN);
  const out = [];
  for (const row of view.rows) for (const rateType of ["Closing", "Average"]) out.push({ row, rateType });
  return out;
}

test("F12: Rates.vue imports correctionBody and gerApproveAction from ../rates.js", () => {
  const source = read();
  const imp = source.match(/import\s*\{([^}]*)\}\s*from\s*["']\.\.\/rates\.js["']/);
  assert.match(imp[1], /\bcorrectionBody\b/);
  assert.match(imp[1], /\bgerApproveAction\b/);
});

test("F12: Correct renders only where the server's correct is true (the v-if evaluated on the golden)", async () => {
  const tpl = template(read());
  const tags = tagsWith(tpl, '@click="openCorrection(');
  assert.equal(tags.length, 1, "one Correct control");
  const vIf = tags[0].match(/v-if="([^"]*)"/);
  assert.ok(vIf, "Correct is gated");
  assert.doesNotMatch(vIf[1], /status|canEnter|canApprove|approved/, "the screen never decides correct itself");
  const gate = new Function("cellOf", "row", "rateType", `return Boolean(${vIf[1]});`);
  const shown = [];
  for (const { row, rateType } of await f12Cells()) {
    if (gate(f12CellOf, row, rateType)) shown.push(f12CellOf(row, rateType).name);
  }
  assert.deepEqual(shown, ["GER-4"], "only GER-4 (approved, nothing pending)");
});

/** Rates.vue's own saveCorrection, run with the real body builders and a stub send. */
async function runSaveCorrection({ form, send, rateType = "Average", payload = F12_GOLDEN }) {
  const { gridView, correctionBody, correctionEditBody } = await import("../rates.js");
  const js = script(read());
  const fn = js.match(/\nasync function saveCorrection\(row, rateType\)\s*\{[\s\S]*?\n\}\n/);
  assert.ok(fn, "Rates.vue declares async function saveCorrection(row, rateType)");
  const row = gridView(payload).rows[0];
  const correcting = { [`EUR|GBP|${rateType}`]: form };
  const correctionErrors = {};
  const reloads = [];
  const sent = [];
  const run = new Function(
    "cellKey", "correcting", "correctionErrors", "period", "saving", "seq", "correctionBody", "correctionEditBody", "sendSave", "loadRates",
    `${fn[0]}return saveCorrection;`,
  )(
    (r, t) => `${r.fromCurrency}|${r.toCurrency}|${t}`,
    correcting,
    correctionErrors,
    { value: { year: 2025, period: 7 } },
    { value: false },
    1,
    correctionBody,
    correctionEditBody,
    async (built) => {
      sent.push(built.body);
      return send(built);
    },
    async (opts) => reloads.push(opts),
  );
  await run(row, rateType);
  return { sent, correctionErrors, correcting, reloads };
}

test("F12: the correction posts supersedes + reason, then closes its form and reloads the grid", async () => {
  const out = await runSaveCorrection({
    form: { mode: "correct", quote: "0.845", quotedPer: "1", reason: "ECB fixing corrected" },
    send: async () => ({ name: "GER-6", docstatus: 0 }),
  });
  assert.deepEqual(out.sent, [{
    fiscal_year: 2025, fiscal_period: 7, from_currency: "EUR", to_currency: "GBP", rate_type: "Average",
    quote: 0.845, quoted_per: "1", change_reason: "ECB fixing corrected", supersedes: "GER-4",
  }]);
  assert.deepEqual(out.correctionErrors, {});
  assert.equal("EUR|GBP|Average" in out.correcting, false, "the form closes");
  assert.deepEqual(out.reloads, [{ quiet: true }]);
});

test("F12 failure path: the server's refusal is kept verbatim, the form stays open, nothing reloads", async () => {
  const sentence = "Say why this rate replaces GER-4 (Reason for Change).";
  const out = await runSaveCorrection({
    form: { mode: "correct", quote: "0.845", quotedPer: "1", reason: "" },
    send: async () => { throw new Error(sentence); },
  });
  assert.equal(out.sent[0].change_reason, null, "no reason invented");
  assert.deepEqual(out.correctionErrors, { "EUR|GBP|Average": sentence });
  assert.ok(out.correcting["EUR|GBP|Average"], "the form stays open with what was typed");
  assert.deepEqual(out.reloads, []);
});

test("F12: one save call site still — the grid save and the correction both go through sendSave", () => {
  const js = script(read());
  assert.equal((js.match(/\bpost\(\s*SAVE_RATE\b/g) || []).length, 1);
  const send = js.match(/function sendSave\(built\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(send, "function sendSave(built)");
  assert.match(send[1], /post\(\s*SAVE_RATE\s*,\s*built\.body\s*\)/);
  const save = js.match(/async function saveRates\(\)\s*\{([\s\S]*?)\n\}/);
  assert.match(save[1], /sendSave\(built\)/);
  const corr = js.match(/async function saveCorrection\(row, rateType\)\s*\{([\s\S]*?)\n\}/);
  assert.match(corr[1], /correctionBody\(/);
  assert.match(corr[1], /sendSave\(built\)/);
});

test("F12: the pending correction shows its text where the cell carries one (the v-if evaluated on the golden)", async () => {
  const tpl = template(read());
  const i = tpl.indexOf(".correction.text }}");
  assert.ok(i >= 0, "the template shows correction.text");
  const holder = tpl.slice(tpl.lastIndexOf("<div", i), i);
  const vIf = holder.match(/v-if="([^"]*)"/);
  assert.ok(vIf, "the correction block is gated");
  const gate = new Function("cellOf", "row", "rateType", `return Boolean(${vIf[1]});`);
  const shown = [];
  for (const { row, rateType } of await f12Cells()) {
    if (gate(f12CellOf, row, rateType)) shown.push(f12CellOf(row, rateType).correction.text);
  }
  assert.deepEqual(shown, ["0.85 per 1 → 0.86 per 1, replaces GER-1"]);
});

test("F12: the correction is approved through approveCell, gated on its own approve kind", () => {
  const tpl = template(read());
  const tags = tagsWith(tpl, '@click="approveCell(cellOf(row, rateType).correction)"');
  assert.equal(tags.length, 1);
  assert.match(tags[0], /v-if="[^"]*\bcanApproveNow\(cellOf\(row, rateType\)\.correction\)/);
});

test("F12 failure path: actionFor(GER) finds the correction through gerApproveAction, not the approved cell", () => {
  const js = script(read());
  const fn = js.match(/function actionFor\(([^)]*)\)\s*\{([\s\S]*?)\n\}/);
  assert.match(fn[2], /gerApproveAction\(\s*rates\.payload\s*,\s*name\s*\)/);
  assert.match(fn[2], /pending\.payload/, "HER/OP still read the pending payload");
});

test("F12: a correction refusal is shown through lines() under its row", () => {
  const js = script(read());
  const fn = js.match(/function rowNotes\(row\)\s*\{([\s\S]*?)\n\}/);
  assert.match(fn[1], /correctionErrors\[/);
  assert.match(fn[1], /\.correction\b[\s\S]*approveErrors\[/, "an approve refusal of the correction shows too");
});

// -- FR8 (review S1): Edit and Delete of a pending correction, Delete of an
// orphan. The server's flags alone mount the controls (FR2); Edit reuses the
// correction form (mode "edit") and posts correctionEditBody through the one
// save call site; Delete posts delete_rate through one call site; a refusal
// is the server's sentence. Fed the REAL F11 golden (FR2-regenerated).

function fr8Viewer() {
  const payload = structuredClone(F12_GOLDEN);
  payload.rows[0].closing.correction.edit = false;
  payload.rows[0].closing.correction.delete = false;
  return payload;
}

function fr8Orphaned(del = true) {
  const payload = structuredClone(F12_GOLDEN);
  payload.rows[0].closing.orphans = [{ name: "GER-7", supersedes: "GER-0", delete: del }];
  return payload;
}

async function fr8Cells(payload) {
  const { gridView } = await import("../rates.js");
  const out = [];
  for (const row of gridView(payload).rows) for (const rateType of ["Closing", "Average"]) out.push({ row, rateType });
  return out;
}

/** The v-if of the one control whose opening tag carries `needle`, evaluated per cell. */
async function fr8Shown(needle, payload, pick) {
  const tags = tagsWith(template(read()), needle);
  assert.equal(tags.length, 1, `one control with ${needle}`);
  const vIf = tags[0].match(/v-if="([^"]*)"/);
  assert.ok(vIf, `${needle} is gated`);
  assert.doesNotMatch(vIf[1], /status|canEnter|canApprove|approved|correct\b/, "the screen never decides edit/delete itself");
  const gate = new Function("cellOf", "row", "rateType", `return Boolean(${vIf[1]});`);
  const shown = [];
  for (const { row, rateType } of await fr8Cells(payload)) {
    if (gate(f12CellOf, row, rateType)) shown.push(pick(f12CellOf(row, rateType)));
  }
  return shown;
}

test("FR8: Rates.vue imports correctionEditBody and deleteBody and names delete_rate once", () => {
  const source = read();
  const imp = source.match(/import\s*\{([^}]*)\}\s*from\s*["']\.\.\/rates\.js["']/);
  assert.match(imp[1], /\bcorrectionEditBody\b/);
  assert.match(imp[1], /\bdeleteBody\b/);
  const js = script(source);
  assert.match(js, /DELETE_RATE\s*=\s*["']konsol\.close\.rates_api\.delete_rate["']/);
});

test("FR8: Edit correction renders only where the server's correction.edit is true", async () => {
  const pick = (cell) => cell.correction.name;
  assert.deepEqual(await fr8Shown('@click="openCorrectionEdit(', F12_GOLDEN, pick), ["GER-3"]);
  assert.deepEqual(await fr8Shown('@click="openCorrectionEdit(', fr8Viewer(), pick), [], "a Viewer or a Closed period: no Edit");
});

test("FR8: Delete correction renders only where the server's correction.delete is true", async () => {
  const needle = '@click="deleteDraft(row, rateType, cellOf(row, rateType).correction.name)"';
  const pick = (cell) => cell.correction.name;
  assert.deepEqual(await fr8Shown(needle, F12_GOLDEN, pick), ["GER-3"]);
  assert.deepEqual(await fr8Shown(needle, fr8Viewer(), pick), [], "a Viewer or a Closed period: no Delete");
});

test("FR8: each orphan shows its sentence, and Delete only where its delete is true", async () => {
  const tpl = template(read());
  const i = tpl.indexOf("{{ orphan.text }}");
  assert.ok(i >= 0, "the template shows orphan.text");
  const loop = tpl.slice(tpl.lastIndexOf("<", tpl.lastIndexOf('v-for="orphan in cellOf(row, rateType).orphans"', i)), i);
  assert.match(loop, /v-for="orphan in cellOf\(row, rateType\)\.orphans"/, "one line per orphan of the cell");
  const tags = tagsWith(tpl, '@click="deleteDraft(row, rateType, orphan.name)"');
  assert.equal(tags.length, 1, "one orphan Delete control");
  const vIf = tags[0].match(/v-if="([^"]*)"/);
  assert.ok(vIf);
  assert.equal(vIf[1], "orphan.delete", "gated on the server's delete alone");
  const { gridView } = await import("../rates.js");
  const [orphan] = gridView(fr8Orphaned()).rows[0].closing.orphans;
  assert.equal(orphan.text, "GER-7 corrects GER-0, which is no longer the approved rate: it can never be approved.");
  assert.equal(new Function("orphan", `return Boolean(${vIf[1]});`)(orphan), true);
  const [locked] = gridView(fr8Orphaned(false)).rows[0].closing.orphans;
  assert.equal(new Function("orphan", `return Boolean(${vIf[1]});`)(locked), false);
});

test("FR8: Edit opens the correction form pre-filled from the correction (mode edit); Correct opens mode correct", async () => {
  const { gridView } = await import("../rates.js");
  const js = script(read());
  const edit = js.match(/\nfunction openCorrectionEdit\(row, rateType\)\s*\{[\s\S]*?\n\}\n/);
  assert.ok(edit, "Rates.vue declares function openCorrectionEdit(row, rateType)");
  const open = js.match(/\nfunction openCorrection\(row, rateType\)\s*\{[\s\S]*?\n\}\n/);
  const row = gridView(F12_GOLDEN).rows[0];
  const correcting = {};
  const correctionErrors = { "EUR|GBP|Closing": "old refusal" };
  const args = ["cellOf", "cellKey", "correcting", "correctionErrors"];
  const vals = [f12CellOf, (r, t) => `${r.fromCurrency}|${r.toCurrency}|${t}`, correcting, correctionErrors];
  new Function(...args, `${edit[0]}openCorrectionEdit(arguments[4], "Closing");`)(...vals, row);
  assert.deepEqual(correcting["EUR|GBP|Closing"], {
    mode: "edit",
    name: "GER-3",
    quote: "0.86",
    quotedPer: "1",
    reason: "ECB fixing corrected 31 Jul",
  });
  assert.deepEqual(correctionErrors, {});
  new Function(...args, `${open[0]}openCorrection(arguments[4], "Average");`)(...vals, row);
  assert.deepEqual(correcting["EUR|GBP|Average"], { mode: "correct", quote: "0.85", quotedPer: "1", reason: "" });
});

test("FR8: an edit posts correctionEditBody (name + supersedes) through sendSave, then closes and reloads", async () => {
  const out = await runSaveCorrection({
    rateType: "Closing",
    form: { mode: "edit", name: "GER-3", quote: "0.861", quotedPer: "1", reason: "ECB fixing corrected 1 Aug" },
    send: async () => ({ name: "GER-3", docstatus: 0 }),
  });
  assert.deepEqual(out.sent, [{
    fiscal_year: 2025, fiscal_period: 7, from_currency: "EUR", to_currency: "GBP", rate_type: "Closing",
    quote: 0.861, quoted_per: "1", change_reason: "ECB fixing corrected 1 Aug", name: "GER-3", supersedes: "GER-1",
  }]);
  assert.equal("EUR|GBP|Closing" in out.correcting, false);
  assert.deepEqual(out.reloads, [{ quiet: true }]);
});

test("FR8 failure path: an edit refused by the server keeps its sentence and the form", async () => {
  const sentence = "GER-3 is not a draft any more: reload the Rates screen.";
  const out = await runSaveCorrection({
    rateType: "Closing",
    form: { mode: "edit", name: "GER-3", quote: "0.861", quotedPer: "1", reason: "x" },
    send: async () => { throw new Error(sentence); },
  });
  assert.deepEqual(out.correctionErrors, { "EUR|GBP|Closing": sentence });
  assert.ok(out.correcting["EUR|GBP|Closing"]);
  assert.deepEqual(out.reloads, []);
});

/** Rates.vue's own deleteDraft, run with the real deleteBody and a stub post. */
async function runDeleteDraft({ name, send }) {
  const { gridView, deleteBody } = await import("../rates.js");
  const js = script(read());
  const fn = js.match(/\nasync function deleteDraft\(row, rateType, name\)\s*\{[\s\S]*?\n\}\n/);
  assert.ok(fn, "Rates.vue declares async function deleteDraft(row, rateType, name)");
  const row = gridView(F12_GOLDEN).rows[0];
  const correcting = { "EUR|GBP|Closing": { mode: "edit", name: "GER-3", quote: "0.86", quotedPer: "1", reason: "" } };
  const correctionErrors = {};
  const posted = [];
  const reloads = [];
  const run = new Function(
    "cellKey", "correcting", "correctionErrors", "deleting", "seq", "deleteBody", "post", "DELETE_RATE", "loadRates",
    `${fn[0]}return deleteDraft;`,
  )(
    (r, t) => `${r.fromCurrency}|${r.toCurrency}|${t}`,
    correcting,
    correctionErrors,
    { value: null },
    1,
    deleteBody,
    async (method, body) => {
      posted.push([method, body]);
      return send(body);
    },
    "konsol.close.rates_api.delete_rate",
    async (opts) => reloads.push(opts),
  );
  await run(row, "Closing", name);
  return { posted, correctionErrors, correcting, reloads };
}

test("FR8: Delete posts delete_rate with the draft's name, closes its form and reloads", async () => {
  const out = await runDeleteDraft({ name: "GER-3", send: async () => ({ deleted: "GER-3" }) });
  assert.deepEqual(out.posted, [["konsol.close.rates_api.delete_rate", { name: "GER-3" }]]);
  assert.equal("EUR|GBP|Closing" in out.correcting, false);
  assert.deepEqual(out.correctionErrors, {});
  assert.deepEqual(out.reloads, [{ quiet: true }]);
});

test("FR8 failure path: a refused Delete shows the server's sentence under the cell, nothing reloads", async () => {
  const sentence = "2025 P07 is Closed: group rates lock when their period closes.";
  const out = await runDeleteDraft({ name: "GER-7", send: async () => { throw new Error(sentence); } });
  assert.deepEqual(out.correctionErrors, { "EUR|GBP|Closing": sentence });
  assert.deepEqual(out.reloads, []);
});
