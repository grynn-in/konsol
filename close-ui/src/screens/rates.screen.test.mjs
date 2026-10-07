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

test("Exactly one post(APPROVE and exactly one post(SAVE_RATE call site, and no other post(", () => {
  const js = script(read());
  assert.equal((js.match(/\bpost\(\s*APPROVE\b/g) || []).length, 1, "one approve call site");
  assert.equal((js.match(/\bpost\(\s*SAVE_RATE\b/g) || []).length, 1, "one save call site");
  assert.equal((js.match(/\bpost\(/g) || []).length, 2, "no third POST");
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
// only when the server says can_record, fed get_ownership's `change` choices (O63).

test("O60: the Ownership tab mounts OwnershipChange only when can_record, with get_ownership's change", () => {
  const source = read();
  assert.match(source, /import OwnershipChange from ["']\.\.\/sections\/OwnershipChange\.vue["']/);
  const tpl = template(source);
  const ownershipTab = tpl.slice(tpl.indexOf('aria-label="Ownership"'));
  const tags = tagsWith(ownershipTab, "<OwnershipChange");
  assert.equal(tags.length, 1, "one OwnershipChange");
  assert.match(tags[0], /v-if="[^"]*\bownershipViewData\.canRecord\b[^"]*"/, "v-if on can_record");
  assert.match(tags[0], /:change="ownership\.payload\.change"/, "fed the server's change choices");
  assert.match(tags[0], /@saved="[^"]*\bloadPending\(/, "a saved draft reloads the pending tab");
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
