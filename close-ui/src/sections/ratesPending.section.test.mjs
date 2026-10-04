// konsol#305 E410: ratesPending.section.test.mjs
//
// Source-level checks on sections/RatesPending.vue (the "Historical equity
// rates" tab: every HER and OP draft awaiting approval, with the preparer,
// the created date and an approve control; stories 4.3, E4-P8; R2, R5). A
// .vue file only compiles inside the Vite build, which the row's gate runs
// apart (mirrors signOffPeriodActions.section.test.mjs / rates.screen.test.mjs).
//
// The section is presentational: it takes `pendingView(payload)` (rates.js,
// E407) as a prop and emits `approve(doctype, name, reason)`. It never calls
// api.js itself — Rates.vue owns the one approve() call site (E409) and the
// one get_pending load (E410).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SECTION = path.join(__dirname, "RatesPending.vue");

function read() {
  return fs.readFileSync(SECTION, "utf8");
}

function script(source) {
  const m = source.match(/<script[^>]*>([\s\S]*?)<\/script>/);
  assert.ok(m, "the section has a <script>");
  return m[1];
}

function template(source) {
  const start = source.indexOf("<template>");
  const end = source.lastIndexOf("</template>");
  assert.ok(start >= 0 && end > start, "the section has a <template>");
  return source.slice(start, end);
}

test("the section file exists", () => {
  assert.ok(fs.existsSync(SECTION), "sections/RatesPending.vue exists");
});

test("It never posts: no api.js import, no post( or get( call; it emits approve(doctype, name, reason)", () => {
  const source = read();
  assert.doesNotMatch(source, /from\s*["']\.\.\/api\.js["']/, "no api.js import");
  const js = script(source);
  assert.doesNotMatch(js, /\bpost\(/, "the section never posts");
  assert.doesNotMatch(js, /\bget\(/, "the section never fetches");
  assert.match(js, /defineEmits\(\s*\[\s*["']approve["']\s*\]\s*\)/, "it emits approve");
  assert.match(js, /emit\(\s*["']approve["']\s*,\s*item\.doctype\s*,\s*item\.name\s*,/, "approve(doctype, name, reason)");
});

test("Takes pendingView(payload) as a prop (E407 shape: items, counts)", () => {
  const js = script(read());
  assert.match(js, /defineProps\(/);
  assert.match(js, /\bview\b/, "a view prop");
  const tpl = template(read());
  assert.match(tpl, /view\.items\b/, "iterates view.items");
});

test("Failure path: refused and none render no button — the v-if tests the approve kind", () => {
  const js = script(read());
  const fn = js.match(/function canApprove\(([^)]*)\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(fn, "canApprove(item) decides from the approve kind");
  assert.match(fn[2], /["']button["']/);
  assert.match(fn[2], /["']reason["']/);
  assert.doesNotMatch(fn[2], /["']refused["']|["']none["']/, "refused and none never approve");
  const tpl = template(read());
  assert.match(tpl, /v-if="[^"]*\bcanApprove\(\s*item\s*\)/, "the approve control is gated on canApprove(item)");
  assert.match(tpl, /<Button\b/, "a Button exists for the approvable kinds");
});

test("`refused` shows the server's sentence; `none` says the Close Lead approves (R2)", () => {
  const tpl = template(read());
  assert.match(tpl, /v-else-if="[^"]*\.approve\.kind\s*===\s*'refused'[^"]*"/);
  assert.match(tpl, /item\.approve\.message\b/, "the refusal is the server's sentence");
  assert.match(tpl, /The Close Lead approves \(R2\)/);
});

test("A `reason` approve opens an inline reason input before emitting approve", () => {
  const tpl = template(read());
  assert.match(tpl, /v-if="[^"]*\.kind\s*===\s*'reason'[^"]*"/, "the reason input shows only for kind reason");
});

test("Each item shows the preparer, the created date and its kind — OP drafts marked 'Ownership period'", () => {
  const tpl = template(read());
  assert.match(tpl, /item\.preparer\b/);
  assert.match(tpl, /item\.created\b/);
  assert.match(tpl, /item\.title\b/);
  assert.match(tpl, /item\.detail\b/);
  assert.match(script(read()), /["']Ownership Period["']/, "the OP doctype name is checked");
  assert.match(script(read()), /["']Ownership period["']/, "OP drafts are labelled 'Ownership period'");
});

test("L01d: the created time is formatted through timefmt (rates.js pendingCreatedText), never shown as the server's raw ISO string", () => {
  const source = read();
  assert.match(
    source,
    /import\s*\{[^}]*\bpendingCreatedText\b[^}]*\}\s*from\s*["']\.\.\/rates\.js["']/,
    "imports pendingCreatedText from rates.js",
  );
  assert.match(
    source,
    /import\s*\{[^}]*\buserTimeZone\b[^}]*\}\s*from\s*["']\.\.\/timefmt\.js["']/,
    "imports userTimeZone from timefmt.js (same zone lookup as the TB screen, B29)",
  );
  const js = script(source);
  assert.match(js, /\bpendingCreatedText\(/, "calls pendingCreatedText");
  const tpl = template(source);
  // The raw field is still the argument (not re-derived), but it must be
  // wrapped by a call, not interpolated bare: `{{ item.created }}` alone
  // would be the live defect again (the raw ISO string).
  assert.doesNotMatch(tpl, /\{\{\s*item\.created\s*\}\}/, "created is never interpolated raw");
  assert.match(tpl, /\(\s*item\.created\s*\)/, "item.created is passed through a formatting call");
});

test("L01f: pendingCreatedText is called with no systemZone/naiveInZone fallback, and a naive created throws that is caught and shown as this item's own error text", () => {
  const js = script(read());
  assert.doesNotMatch(js, /naiveInZone/, "the naive-time fallback helper is gone from this section too");
  assert.doesNotMatch(js, /systemZone/i, "no system-zone parameter is threaded through any more");
  // createdText must call pendingCreatedText with exactly (created, now, timeZone) --
  // three arguments, not the old four-argument naive-fallback shape.
  const call = js.match(/pendingCreatedText\(([^)]*)\)/);
  assert.ok(call, "calls pendingCreatedText");
  const args = call[1].split(",").map((a) => a.trim());
  assert.equal(args.length, 3, `pendingCreatedText is called with 3 arguments, got: ${call[1]}`);
  // The call is wrapped so one item's unparsable created shows its own
  // error text in place of a converted time, never crashing the whole list.
  assert.match(js, /try\s*\{[\s\S]*pendingCreatedText\([\s\S]*?\}\s*catch\s*\(e\)\s*\{[\s\S]*return\s+e\.message/,
    "pendingCreatedText is wrapped in try/catch, returning e.message as the visible error text");
});

test("#305-R01p: the empty state is built by the pure pendingEmptyMessage(view) helper, not a hardcoded string", () => {
  const source = read();
  assert.match(
    source,
    /import\s*\{[^}]*\bpendingEmptyMessage\b[^}]*\}\s*from\s*["']\.\.\/rates\.js["']/,
    "imports pendingEmptyMessage from rates.js",
  );
  const js = script(source);
  assert.match(js, /\bpendingEmptyMessage\(/, "calls pendingEmptyMessage");
  const tpl = template(source);
  assert.doesNotMatch(
    tpl,
    /No historical equity rates or ownership periods are awaiting approval\./,
    "the empty text is no longer hardcoded in the template",
  );
});

test("No v-html, no browser dialogs, no browser storage", () => {
  const source = read();
  assert.doesNotMatch(source, /v-html/);
  assert.doesNotMatch(source, /window\.(prompt|confirm|alert)|\bprompt\(|\bconfirm\(|\balert\(/);
  for (const store of ["local" + "Storage", "session" + "Storage", "indexed" + "DB"]) {
    assert.ok(!source.includes(store), `no ${store}`);
  }
});
