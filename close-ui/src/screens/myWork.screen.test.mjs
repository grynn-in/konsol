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

// A20: a period-less screen item (E6-P10) routes to the URL's current
// period, never an invented one. MyWork.vue must hand itemRoute its own
// `current` (computed from the route, B18) so a period-less item can
// resolve; a null route (no current) renders the title with no anchor.
test("A20: MyWork passes current to itemRoute, so a period-less item routes to the URL's period", () => {
  const s = script(read());
  assert.match(s, /itemRoute\(\s*item\s*,\s*current(?:\.value)?\s*\)/, "itemRoute is called with (item, current)");
});

test("A20: a null route (no current) renders the item's title with no anchor, never a thrown error", () => {
  const s = script(read());
  assert.match(s, /routeOf\(item\)\.none|r\s*==\s*null|r\s*===\s*null/,
    "routeOf must recognise itemRoute returning null and not read a property off it");
  const t = template(read());
  assert.match(t, /routeOf\(item\)\.none/, "the template has a branch for the no-route case, rendering no link");
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

test("each item shows its badge (U7: badgeFor, not a raw item.period.code/Setup choice), owner and one action", () => {
  const s = script(read());
  const t = template(read());
  assert.match(s, /import\s*\{[^}]*\bbadgeFor\b[^}]*\}\s*from\s*["']\.\.\/myWork\.js["']/,
    "badgeFor comes from myWork.js, not a copy in the screen");
  assert.match(t, /badgeFor\(item\)\.theme/);
  assert.match(t, /badgeFor\(item\)\.label/);
  assert.match(t, /item\.owner/);
  assert.match(t, /itemRoute\(item\)|routeOf\(item\)/);
});

// U7 (review-w3.md): the screen must not decide "Setup" vs. the kind's
// badge itself — that was the bug (every period-less item got "Setup",
// including an approvals queue and a sent-back draft). The decision lives
// in myWork.js's badgeFor, which the screen only calls.
test("U7: MyWork does not hardcode the Setup/period choice itself", () => {
  const t = template(read());
  assert.doesNotMatch(t, /v-if="item\.period"/, "the period/gap choice is not re-implemented in the template");
  assert.doesNotMatch(t, /theme="orange"\s+variant="subtle"\s+label="Setup"/, "Setup is not hardcoded in the template");
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

// --- Y64 (stories 1.5, 1.2): the reminded line under a TB item's title --------

test("(Y64) MyWork shows myWork.js's remindedLine under the title, in the user's zone", () => {
  const source = read();
  const s = script(source);
  assert.match(s, /import\s*\{[^}]*\bremindedLine\b[^}]*\}\s*from\s*["']\.\.\/myWork\.js["']/);
  assert.match(s, /import\s*\{[^}]*\buserTimeZone\b[^}]*\}\s*from\s*["']\.\.\/timefmt\.js["']/);
  assert.match(s, /remindedLine\(\s*item\s*,\s*today\s*,\s*timeZone\s*\)/, "the injected today and the user's zone");
  const t = template(source);
  const title = t.indexOf("{{ item.title }}");
  const line = t.search(/v-if="remindedOf\(item\)"/);
  assert.ok(title >= 0 && line > title, "the reminded line sits under the title");
});

test("(Y64) failure path: the screen formats no reminder text itself and has no Remind button", () => {
  const source = read();
  assert.doesNotMatch(source, /Reminded|×| reminded`|of \$\{/, "the text comes from myWork.js/remind.js only");
  assert.doesNotMatch(source, /remind_api|REMIND/, "My work shows the line; Remind lives on the TB list and the IC panel");
});

test("(Y64) failure path: a reminded value the screen cannot read is the screen's error, not a crash", () => {
  const s = script(read());
  const grouped = s.slice(s.indexOf("const grouped = computed("), s.indexOf("const groups = computed("));
  assert.match(grouped, /remindedLine\(/, "lines are built inside grouped's try, so a throw becomes loadError");
});
