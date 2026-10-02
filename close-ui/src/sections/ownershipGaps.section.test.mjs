// konsol#305 E411: ownershipGaps.section.test.mjs
//
// Source-level checks on sections/OwnershipGaps.vue (the "Ownership" tab:
// entities with a submitted trial balance but no ownership covering the
// period's start, each with a "Record ownership" link to the Desk form the
// SERVER builds; stories #305-W2-3, #305-W2-2) and on screens/Rates.vue's
// wiring of it. A .vue file only compiles inside the Vite build, which the
// row's gate runs apart (mirrors ratesPending.section.test.mjs /
// rates.screen.test.mjs).
//
// The section is presentational: it takes `ownershipView(payload)`
// (rates.js, E407) as its `view` prop, plus the raw `out_of_scope` entity
// names as a separate `outOfScope` prop (ownershipView collapses that list
// to a count only; Rates.vue, which already holds the raw payload, passes
// the names through unchanged so the "N out of scope" count can expand).
// It never calls api.js, and it never builds a Desk URL itself: the link's
// `href` is exactly the server's own `desk` field.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SECTION = path.join(__dirname, "OwnershipGaps.vue");
const RATES = path.join(__dirname, "..", "screens", "Rates.vue");

function read(file) {
  return fs.readFileSync(file, "utf8");
}

function script(source) {
  const m = source.match(/<script[^>]*>([\s\S]*?)<\/script>/);
  assert.ok(m, "the file has a <script>");
  return m[1];
}

function template(source) {
  const start = source.indexOf("<template>");
  const end = source.lastIndexOf("</template>");
  assert.ok(start >= 0 && end > start, "the file has a <template>");
  return source.slice(start, end);
}

/** The opening tag that contains the given index. */
function tagAt(tpl, i) {
  const tagStart = tpl.lastIndexOf("<", i);
  return tpl.slice(tagStart, tpl.indexOf(">", i) + 1);
}

test("the section file exists", () => {
  assert.ok(fs.existsSync(SECTION), "sections/OwnershipGaps.vue exists");
});

test("It never posts or fetches, and never builds a Desk URL itself: no api.js import, no post(/get(, no literal ownership-period path", () => {
  const source = read(SECTION);
  assert.doesNotMatch(source, /from\s*["']\.\.\/api\.js["']/, "no api.js import");
  const js = script(source);
  assert.doesNotMatch(js, /\bpost\(/, "the section never posts");
  assert.doesNotMatch(js, /\bget\(/, "the section never fetches");
  assert.doesNotMatch(source, /\/app\/ownership-period/, "the server owns the Desk path, not the section");
});

test("Takes ownershipView(payload) as a view prop, plus the raw out-of-scope names as a separate prop", () => {
  const js = script(read(SECTION));
  assert.match(js, /defineProps\(/);
  assert.match(js, /\bview\b/, "a view prop");
  assert.match(js, /\boutOfScope\b/, "an outOfScope prop (raw names; ownershipView only gives a count)");
});

test("The Record ownership link's href binds to the item's own desk field", () => {
  const tpl = template(read(SECTION));
  assert.match(tpl, /:href="\s*item\.desk\s*"/, "href is bound to item.desk, not built here");
});

test("Failure path: the Record ownership link only renders when canRecord", () => {
  const tpl = template(read(SECTION));
  const i = tpl.indexOf(':href="item.desk"');
  assert.ok(i >= 0, "the Record ownership link exists");
  const tag = tagAt(tpl, i);
  assert.match(tag, /v-if="[^"]*\bview\.canRecord\b[^"]*"/, "the link is gated on view.canRecord");
});

test("The link opens Desk in a new tab, safely, and says so", () => {
  const tpl = template(read(SECTION));
  const i = tpl.indexOf(':href="item.desk"');
  const tag = tagAt(tpl, i);
  assert.match(tag, /target="_blank"/, "opens in a new tab");
  assert.match(tag, /rel="noopener"/, "noopener");
  const linkEnd = tpl.indexOf("</a>", i);
  assert.match(tpl.slice(i, linkEnd + 4), /opens Desk/i, "the link text says it opens Desk");
});

test("Each blocking entity shows its name and the server's sentence", () => {
  const tpl = template(read(SECTION));
  assert.match(tpl, /\bview\.blocking\b/, "iterates view.blocking");
  assert.match(tpl, /item\.entity\b/);
  assert.match(tpl, /item\.message\b/);
});

test("Failure path: no blocking entities shows an empty state, not a blank panel", () => {
  const tpl = template(read(SECTION));
  assert.match(tpl, /v-if="[^"]*!\s*view(\.blocking\.length|\s*\|\|\s*!\s*view\.blocking\.length)[^"]*"|v-if="[^"]*\bview\.blocking\.length\s*===\s*0[^"]*"|v-if="\s*!view\s*\|\|\s*!view\.blocking\.length\s*"/);
});

test("Out-of-scope entities show a count that expands to the names, and the in-scope count", () => {
  const tpl = template(read(SECTION));
  assert.match(tpl, /\bview\.outOfScopeCount\b/, "shows the out-of-scope count");
  assert.match(tpl, /<details/, "expands via <details>/<summary>");
  assert.match(tpl, /<summary/);
  assert.match(tpl, /\boutOfScope\b/, "the expanded list iterates the outOfScope prop (the names)");
  assert.match(tpl, /\bview\.inScopeCount\b/, "shows the in-scope count");
});

test("A hiddenCount > 0 shows the same entity-scope note the other screens use", () => {
  const tpl = template(read(SECTION));
  assert.match(tpl, /view\.hiddenCount\s*>\s*0/, "gated on hiddenCount > 0");
  assert.match(tpl, /outside your scope are not shown/);
});

test("#305-R01q: the empty-state sentence comes from ownershipEmptyMessage(view) in rates.js, not a hardcoded string, so a hidden blocking gap is never read as 'no gaps'", () => {
  const source = read(SECTION);
  assert.match(
    source,
    /import\s*\{[^}]*\bownershipEmptyMessage\b[^}]*\}\s*from\s*["']\.\.\/rates\.js["']/,
    "imports ownershipEmptyMessage from rates.js",
  );
  const tpl = template(source);
  assert.doesNotMatch(tpl, /No ownership gaps for this period\./, "the sentence is not hardcoded in the template");
});

test("No v-html, no browser dialogs, no browser storage", () => {
  const source = read(SECTION);
  assert.doesNotMatch(source, /v-html/);
  assert.doesNotMatch(source, /window\.(prompt|confirm|alert)|\bprompt\(|\bconfirm\(|\balert\(/);
  for (const store of ["local" + "Storage", "session" + "Storage", "indexed" + "DB"]) {
    assert.ok(!source.includes(store), `no ${store}`);
  }
});

// -- Rates.vue wiring --

test("Rates.vue names get_ownership and imports OwnershipGaps and ownershipView", () => {
  const js = script(read(RATES));
  assert.match(js, /GET_OWNERSHIP\s*=\s*["']konsol\.close\.rates_api\.get_ownership["']/);
  const source = read(RATES);
  assert.match(
    source,
    /import\s+OwnershipGaps\s+from\s*["']\.\.\/sections\/OwnershipGaps\.vue["']/,
    "imports the section",
  );
  assert.match(source, /import\s*\{[^}]*\bownershipView\b[^}]*\}\s*from\s*["']\.\.\/rates\.js["']/, "imports ownershipView");
});

test("Rates.vue mounts OwnershipGaps inside the Ownership tabpanel only", () => {
  const tpl = template(read(RATES));
  const start = tpl.indexOf('aria-label="Ownership"');
  assert.ok(start >= 0, "the Ownership tabpanel exists");
  const end = tpl.indexOf("</section>", start);
  assert.ok(end > start, "the Ownership tabpanel closes");
  assert.match(tpl.slice(start, end), /<OwnershipGaps\b/, "OwnershipGaps renders inside the Ownership tabpanel");
  // It must not render inside the other two tabpanels.
  const groupStart = tpl.indexOf('aria-label="Group rates"');
  const groupEnd = tpl.indexOf("</section>", groupStart);
  assert.doesNotMatch(tpl.slice(groupStart, groupEnd), /<OwnershipGaps\b/);
  const herStart = tpl.indexOf('aria-label="Historical equity rates"');
  const herEnd = tpl.indexOf("</section>", herStart);
  assert.doesNotMatch(tpl.slice(herStart, herEnd), /<OwnershipGaps\b/);
});

test("Rates.vue's Ownership tab label carries the blocking count (N gap(s)), or plain Ownership when there are none", () => {
  const source = read(RATES);
  assert.match(source, /Ownership.*gap\(s\)/);
});

test("get_ownership is loaded through api.js's get(), reloaded on period change, with a seq guard", () => {
  const js = script(read(RATES));
  assert.match(js, /get\(\s*GET_OWNERSHIP\s*,/, "fetched through get()");
  assert.match(js, /ownershipSeq/, "a seq guard for stale ownership responses");
});
