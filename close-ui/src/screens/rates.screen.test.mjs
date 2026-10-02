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

test("No browser storage (D5): the period lives in the URL only", () => {
  const source = read();
  assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB|document\.cookie/);
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
  for (const field of ["statusLabel", "previousValue", "previousLabel", "deltaText", "flag", "preparer"]) {
    assert.match(tpl, new RegExp(`\\.${field}\\b`), `renders ${field}`);
  }
});
