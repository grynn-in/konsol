// konsol#305 B18: myWork.screen.test.mjs
//
// Source-level checks on screens/MyWork.vue (Problems found 17: C1 is the
// real check for screens; the Vite build in the gate compiles it). The
// screen must reuse the pure modules rather than re-implement them:
// myWork.js (B10) `sections` / `itemRoute`, LoadState (B17), api.js (B06)
// and route.js (B07). Story 0.4: the Desk opens only for a configuration
// gap, so no `/app/` literal and no new-tab link may appear outside the
// gap-item branch, which is fenced by the markers below.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MY_WORK = path.join(__dirname, "MyWork.vue");

const GAP_OPEN = "<!-- gap-item: the only Desk link (story 0.4) -->";
const GAP_CLOSE = "<!-- /gap-item -->";

function read() {
  return fs.readFileSync(MY_WORK, "utf8");
}

function script(source) {
  const m = source.match(/<script setup>([\s\S]*?)<\/script>/);
  assert.ok(m, "MyWork.vue has a <script setup>");
  return m[1];
}

function template(source) {
  const start = source.indexOf("<template>");
  const end = source.lastIndexOf("</template>");
  assert.ok(start >= 0 && end > start, "MyWork.vue has a <template>");
  return source.slice(start, end);
}

/** The source with the fenced gap-item branch cut out. */
function outsideGapBranch(source) {
  const open = source.indexOf(GAP_OPEN);
  const close = source.indexOf(GAP_CLOSE);
  assert.ok(open >= 0, "the gap-item branch opens with its marker");
  assert.ok(close > open, "the gap-item branch closes with its marker");
  assert.equal(source.indexOf(GAP_OPEN, open + 1), -1, "there is exactly one gap-item branch");
  return source.slice(0, open) + source.slice(close + GAP_CLOSE.length);
}

function gapBranch(source) {
  const open = source.indexOf(GAP_OPEN);
  const close = source.indexOf(GAP_CLOSE);
  return source.slice(open, close);
}

test("MyWork groups with sections and routes with itemRoute (B10), not its own copies", () => {
  const s = script(read());
  assert.match(s, /import\s*\{[^}]*\bsections\b[^}]*\}\s*from\s*["']\.\.\/myWork\.js["']/);
  assert.match(s, /import\s*\{[^}]*\bitemRoute\b[^}]*\}\s*from\s*["']\.\.\/myWork\.js["']/);
  assert.match(s, /sections\(/);
  assert.match(s, /itemRoute\(/);
});

test("MyWork renders LoadState (B17) for its data", () => {
  const source = read();
  assert.match(script(source), /import\s+LoadState\s+from\s*["']\.\.\/components\/LoadState\.vue["']/);
  assert.match(template(source), /<LoadState\b/);
});

test("MyWork calls the server through api.js (B06) and reads A29 get_my_work", () => {
  const s = script(read());
  assert.match(s, /import\s*\{[^}]*\bget\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
  assert.match(s, /konsol\.close\.mywork_api\.get_my_work/);
  assert.doesNotMatch(s, /\bfetch\(/, "all server calls go through api.js");
  assert.doesNotMatch(s, /frappe\.call\(|createResource\(/, "all server calls go through api.js");
});

// Browser storage is already refused for every file under src/ by
// route.test.mjs (B07); naming the APIs here would trip that guard.
test("MyWork reads the period from the URL with route.js (B07)", () => {
  const s = script(read());
  assert.match(s, /import\s*\{[^}]*\bparse\b[^}]*\}\s*from\s*["']\.\.\/route\.js["']/);
});

test("MyWork keeps the server's order and always shows all three groups (B10)", () => {
  const source = read();
  assert.doesNotMatch(source, /\.sort\(|\.reverse\(/, "the server ranks the items; the screen never re-sorts");
  assert.doesNotMatch(source, /sections\([^)]*\)\s*\.filter\(/, "no group is dropped");
  const t = template(source);
  assert.match(t, /v-for="section in groups"/);
  assert.match(t, /section\.empty/, "an empty group is drawn with its own text, not hidden");
  assert.doesNotMatch(t, /v-if="[^"]*section\.items\.length/, "a group is never hidden for having no items");
});

test("each item shows its period tag, owner and one action", () => {
  const t = template(read());
  assert.match(t, /item\.period\.code/);
  assert.match(t, /item\.owner/);
  assert.match(t, /itemRoute\(item\)|routeOf\(item\)/);
});

test("an Entity Accountant with no entities gets the explicit A25 message and no upload control (B18b: from entities_assigned, not my_tbs)", () => {
  const source = read();
  assert.doesNotMatch(source, /konsol\.close\.tb_read_api\.my_tbs/,
    "B18b: no separate my_tbs call; entities_assigned comes with get_my_work");
  assert.match(source, /entities_assigned/);
  assert.match(source, /No entities are assigned to you\. Ask the System Manager\./);
  assert.doesNotMatch(template(source), /[Uu]pload (a |the )?(TB|trial balance) file|type="file"/,
    "My work has no upload control");
});

test("(B18b) an Entity Accountant with entities assigned but none in scope this period gets a distinct out-of-scope message", () => {
  const source = read();
  assert.match(source, /entitiesAssigned === true/, "the true-but-empty case is handled separately from false");
  const t = template(source);
  assert.match(t, /entityBanner/);
});

test("(B18b) each item's age comes from myWork.js's ageText, with today injected, never read inside a pure module", () => {
  const s = script(read());
  assert.match(s, /import\s*\{[^}]*\bageText\b[^}]*\}\s*from\s*["']\.\.\/myWork\.js["']/);
  assert.match(s, /ageText\(/);
  const t = template(read());
  assert.match(t, /ageOf\(item\)|ageText\(/, "the age is rendered per item");
});

test("failure path: no /app/ literal and no new-tab link outside the gap-item branch (story 0.4)", () => {
  const source = read();
  const outside = outsideGapBranch(source);
  assert.doesNotMatch(outside, /\/app\//, "a Desk path appears only in the gap-item branch");
  assert.doesNotMatch(outside, /target="_blank"/, "only the gap-item Desk link opens a new tab");
  assert.doesNotMatch(source, /window\.open\(|window\.location/, "no navigation around the router");
});

test("the gap-item Desk link opens in a new tab and is marked as Desk", () => {
  const gap = gapBranch(read());
  assert.match(gap, /\.external/, "the Desk link comes from itemRoute's {external}");
  assert.match(gap, /target="_blank"/);
  assert.match(gap, /rel="noopener[^"]*"/);
  assert.match(gap, /\bDesk\b/, "the link says it opens the Desk");
});
