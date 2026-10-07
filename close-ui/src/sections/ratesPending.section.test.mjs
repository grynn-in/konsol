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
  assert.match(js, /defineEmits\(\s*\[\s*["']approve["']\s*,\s*["']edit["']\s*\]\s*\)/, "it emits approve and (O67) edit");
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

// ---------------------------------------------------------------------------
// konsol#305 O61 (wireframe-4.2.md section 3, confirmed by Deepak Pai 7 Oct):
// a pending Ownership Period item carrying `ownership_effect` (R52q) shows the read-only
// EFFECT IF APPROVED panel above Approve; an item with `ownership_effect: null` (a Desk
// "Record ownership" draft, O57) shows "Drafted in Desk: effect not
// previewed." and never an empty panel. Fed the REAL O57 golden payload
// through the REAL pendingView + ownershipEffectView (rates.js, O59).
// ---------------------------------------------------------------------------
import { pendingView, ownershipEffectView } from "../rates.js";
import * as ratesModule from "../rates.js";

const PENDING = JSON.parse(
  fs.readFileSync(new URL("../../../konsol/tests/fixtures/close_rates_pending_payload.json", import.meta.url), "utf8"),
);

/** R52o: builds the section's own `effectFor(item)` from its <script>
 * source, with the real rates.js `opEffectView` injected — the function under
 * test is the one the component runs, not a copy. The section keeps no
 * DESK_DRAFT or opEffect copy of its own (review U6). */
function loadOpEffect() {
  const js = script(read());
  assert.doesNotMatch(js, /const DESK_DRAFT\b/, "R52o: the Desk sentence lives in rates.js only");
  assert.doesNotMatch(js, /function opEffect\(/, "R52o: no local opEffect copy");
  const fn = js.match(/function effectFor\(item\)\s*\{[\s\S]*?\n\}\n/);
  assert.ok(fn, "the section declares function effectFor(item)");
  return new Function("opEffectView", `${fn[0]}return effectFor;`)(ratesModule.opEffectView);
}

function itemNamed(name) {
  const item = pendingView(PENDING).items.find((i) => i.name === name);
  assert.ok(item, `the golden payload has ${name}`);
  return item;
}

test("O61: the golden OP draft with effect gives the wireframe's EFFECT IF APPROVED rows", () => {
  const opEffect = loadOpEffect();
  const item = itemNamed("OP-ZZ5B1-2025-10-01");
  const panel = opEffect(item);
  const real = ownershipEffectView(item.ownership_effect);
  assert.equal(panel.desk, undefined);
  assert.equal(panel.error, undefined);
  assert.deepEqual(
    panel.view.rows.map((r) => [r.label, r.before, r.after]),
    [
      ["Ownership", "100 %", "80 %"],
      ["Method", "full", "full"],
    ],
    "Ownership and Method, before → after (section 3 shows no Covers row: it shows Ends)",
  );
  assert.equal(panel.view.currentEnds, real.currentEnds);
  assert.equal(panel.view.endsLine, real.endsLine, "O65: Ends names the predecessor");
  assert.match(panel.view.endsLine, /^OP-ZZ5B1-1 on /);
  assert.equal(panel.view.periods, "FY2025 P10 onward (open-ended)");
  assert.deepEqual(panel.view.resign, real.resign);
  assert.match(panel.view.resign[0], /^FY2025 P11 \(signed .+ by Zz Lead\)$/, "O65: who signed it and when");
  assert.equal(panel.view.resignNone, null);
  assert.equal(panel.view.notShown, "Goodwill, NCI and results are not previewed; they change at the next build.");
});

test("O61 failure path: the golden Desk draft (ownership_effect null) gives the sentence, never empty columns", () => {
  const opEffect = loadOpEffect();
  const panel = opEffect(itemNamed("OP-ZZ5B2-2025-10-01"));
  assert.deepEqual(panel, { desk: "Drafted in Desk: effect not previewed." });
});

test("O61 failure path: an HER item has no panel; an OP item whose effect is broken shows the thrown sentence, not a guessed panel", () => {
  const opEffect = loadOpEffect();
  assert.equal(opEffect(itemNamed("HER-ZZ5B1-1")), null);
  const op = itemNamed("OP-ZZ5B1-2025-10-01");
  const { resign, ...noResign } = op.ownership_effect;
  const broken = opEffect({ ...op, ownership_effect: noResign });
  assert.equal(broken.view, undefined);
  assert.match(broken.error, /resign/);
  const missing = { ...op, effect: op.ownership_effect };
  delete missing.ownership_effect;
  assert.match(
    opEffect(missing).error,
    /OP-ZZ5B1-2025-10-01 has no ownership_effect$/,
    "an OP item with no ownership_effect key (R52q: the old `effect` key included) is a server regression, shown as such",
  );
});

test("R52o failure path (S2 consumer): an OP item carrying the server's ownership_effect_error shows that sentence in place of the panel", () => {
  const opEffect = loadOpEffect();
  const sentence =
    "The pending ownership change OP-ZZ5B3-2025-10-01 cannot be shown: OP-ZZ5B3-2025-10-01 supersedes " +
    "OP-ZZ5B3-1, which is not an approved Ownership Period. Correct or delete the draft in Desk.";
  for (const name of ["OP-ZZ5B1-2025-10-01", "OP-ZZ5B2-2025-10-01"]) {
    const broken = { ...itemNamed(name), ownership_effect: null, ownership_effect_error: sentence };
    assert.deepEqual(opEffect(broken), { error: sentence }, name);
  }
});

test("R52o (U9): the Edit button is labelled with the draft's name", () => {
  const tpl = template(read());
  const at = tpl.indexOf('v-if="canEdit(item)"');
  assert.ok(at > 0);
  const tag = tpl.slice(tpl.lastIndexOf("<", at), tpl.indexOf(">", at));
  assert.ok(tag.includes(':aria-label="`Edit ${item.name}`"'), tag);
});

test("O61: the template renders the panel above Approve, read-only, with the Desk sentence and the error branch", () => {
  const source = read();
  assert.match(
    source,
    /import\s*\{[^}]*\bopEffectView\b[^}]*\}\s*from\s*["']\.\.\/rates\.js["']/,
    "R52o: imports opEffectView from rates.js",
  );
  const tpl = template(source);
  assert.match(tpl, /EFFECT IF APPROVED/);
  const panelAt = tpl.indexOf("EFFECT IF APPROVED");
  const approveAt = tpl.indexOf("<Button");
  assert.ok(panelAt >= 0 && approveAt > panelAt, "the effect panel comes before the Approve button");
  for (const field of ["row.before", "row.after", "endsLine", "periods", "resign", "resignNone", "notShown"]) {
    assert.ok(tpl.includes(field), `the panel shows ${field}`);
  }
  assert.doesNotMatch(tpl, /The current period, on/, "O65: Ends names the predecessor, not 'The current period'");
  assert.match(tpl, /\.desk\b/, "the Desk-draft sentence is rendered");
  assert.match(tpl, /\.error\b/, "a broken effect's sentence is rendered");
  assert.match(tpl, /Re-sign Needed/);
  const controlAt = tpl.indexOf('v-if="canApprove(item)"');
  assert.ok(controlAt > panelAt, "the approve control follows the panel");
  const panel = tpl.slice(panelAt, controlAt);
  assert.doesNotMatch(panel, /<input|<select|<textarea|<Button|@click/, "the panel is read-only");
});

test("O61: Approve stays the existing approve emit — one approve emit site; O67 adds only the edit emit", () => {
  const js = script(read());
  assert.equal((js.match(/emit\(\s*["']approve["']/g) || []).length, 1, "one approve emit call");
  assert.equal((js.match(/emit\(\s*["']edit["']/g) || []).length, 1, "one edit emit call");
  assert.equal((js.match(/emit\(/g) || []).length, 2, "no other emit");
  assert.match(js, /defineEmits\(\s*\[\s*["']approve["']\s*,\s*["']edit["']\s*\]\s*\)/);
});

// ---------------------------------------------------------------------------
// konsol#305 O67 (wireframe-4.2.md section 1, "The Analyst can edit it until
// it is approved", confirmed by Deepak Pai 7 Oct): an Ownership Period draft
// whose server `edit` (O69) is not null offers "Edit", which emits
// edit(name) for Rates.vue to load into the Change ownership form. The
// decision is the server's: no title or detail is parsed, and a Desk draft
// (edit null) or an HER offers no Edit. Fed the REAL O69 golden through the
// REAL pendingView.
// ---------------------------------------------------------------------------

function loadEditFns() {
  const js = script(read());
  const can = js.match(/\nfunction canEdit\(item\)\s*\{[\s\S]*?\n\}\n/);
  assert.ok(can, "the section declares function canEdit(item)");
  const edit = js.match(/\nfunction edit\(item\)\s*\{[\s\S]*?\n\}\n/);
  assert.ok(edit, "the section declares function edit(item)");
  const emitted = [];
  const emit = (...args) => emitted.push(args);
  const fns = new Function("emit", `${can[0]}${edit[0]}return { canEdit, edit };`)(emit);
  return { ...fns, emitted };
}

test("O67: the golden OP draft with a server edit offers Edit; the Desk draft and the HER do not", () => {
  const { canEdit } = loadEditFns();
  assert.equal(canEdit(itemNamed("OP-ZZ5B1-2025-10-01")), true);
  assert.equal(canEdit(itemNamed("OP-ZZ5B2-2025-10-01")), false, "Drafted in Desk: edit null, no Edit");
  assert.equal(canEdit(itemNamed("HER-ZZ5B1-1")), false);
});

test("O67 failure path: an OP item whose edit is null offers no Edit whatever its title says", () => {
  const { canEdit } = loadEditFns();
  const op = itemNamed("OP-ZZ5B1-2025-10-01");
  assert.equal(canEdit({ ...op, edit: null }), false);
  assert.equal(canEdit({ ...op, doctype: "Historical Equity Rate" }), false);
});

test("O67: Edit emits edit(name) only; the template gates it on canEdit(item) and puts it beside Approve", () => {
  const { edit, emitted } = loadEditFns();
  edit(itemNamed("OP-ZZ5B1-2025-10-01"));
  assert.deepEqual(emitted, [["edit", "OP-ZZ5B1-2025-10-01"]]);
  const tpl = template(read());
  const at = tpl.indexOf('v-if="canEdit(item)"');
  assert.ok(at > 0, "the Edit control is gated on canEdit(item)");
  assert.ok(at > tpl.indexOf('v-if="canApprove(item)"'), "Edit sits in the actions column, after the read-only panel");
  const tag = tpl.slice(tpl.lastIndexOf("<", at), tpl.indexOf(">", at));
  assert.match(tag, /@click="edit\(item\)"/);
  const js = script(read());
  assert.doesNotMatch(js, /item\.title\.|item\.detail\.|split\(/, "no title or detail parsing");
});

