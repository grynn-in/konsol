// konsol#305 B19: trialBalances.screen.test.mjs
//
// Source-level checks on screens/TrialBalances.vue (my entities for the
// period, stories 3.1 and 3.7). The component is read as text: a .vue file
// only compiles inside the Vite build, which the row's gate runs separately
// (Problems found 17: C1 is the real check for screens).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCREEN = path.join(__dirname, "TrialBalances.vue");

function read() {
  return fs.readFileSync(SCREEN, "utf8");
}

function template(source) {
  const start = source.indexOf("<template>");
  const end = source.lastIndexOf("</template>");
  assert.ok(start >= 0 && end > start, "the screen has a <template>");
  return source.slice(start, end);
}

function script(source) {
  const match = source.match(/<script setup>([\s\S]*?)<\/script>/);
  assert.ok(match, "the screen has a <script setup>");
  return match[1];
}

test("it reads A25 my_tbs through api.js (B06), not its own fetch", () => {
  const source = read();
  assert.match(source, /import\s*\{[^}]*\bget\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
  assert.match(source, /konsol\.close\.tb_read_api\.my_tbs/);
  assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js");
});

test("the period comes from the URL through route.js (B07), never remembered", () => {
  const source = read();
  assert.match(source, /import\s*\{[^}]*\bparse\b[^}]*\}\s*from\s*["']\.\.\/route\.js["']/);
  assert.match(source, /useRoute\(/);
  // D5 "nothing is remembered" is enforced for every file by route.test.mjs (B07).
});

test("it builds the rows with entityRows (B12), not its own mapping", () => {
  const source = read();
  assert.match(source, /import\s*\{[^}]*\bentityRows\b[^}]*\}\s*from\s*["']\.\.\/tbTable\.js["']/);
  assert.match(script(source), /entityRows\(/);
});

test("it shows the on-behalf label verbatim (R4), and 'not recorded' when there is none", () => {
  const source = read();
  const tpl = template(source);
  assert.match(tpl, /on_behalf_label/);
  assert.match(source, /not recorded/);
  // Verbatim: the label is never rebuilt from owner/entity in the screen.
  assert.doesNotMatch(source, /`by \$\{/, "the screen never rebuilds the label");
});

test("it renders LoadState (B17) for loading, empty and error", () => {
  const source = read();
  assert.match(source, /import\s+LoadState\s+from\s*["']\.\.\/components\/LoadState\.vue["']/);
  const tpl = template(source);
  assert.match(tpl, /<LoadState\b/);
  assert.match(tpl, /@retry=/, "an error offers the Retry");
});

test("an Entity Accountant with no entities gets the explicit message (A25 note)", () => {
  const source = read();
  assert.match(source, /No entities are assigned to you\. Ask the System Manager\./);
  assert.match(source, /entity_accountant/, "the message is for the Entity Accountant");
  assert.match(source, /konsol\.close\.period_api\.get_context/, "the persona comes from A15");
});

test("the screen handles all six A25 statuses via KNOWN_STATUSES, not its own list", () => {
  const source = read();
  // Styling per status may be declared, but every status must be present so
  // none renders unstyled or blank.
  for (const status of [
    "Missing",
    "Frequency not declared",
    "Quarter not declared",
    "Received",
    "Exception declared",
    "Not expected this period",
  ]) {
    assert.ok(source.includes(`"${status}"`), `status "${status}" is handled`);
  }
});

test("a null count reads 'unknown', never 0 or blank", () => {
  const source = read();
  assert.match(source, /["']unknown["']/);
  assert.match(source, /==\s*null/);
});

test("selecting an entity opens its detail area (filled by B20 and B21)", () => {
  const tpl = template(read());
  assert.match(tpl, /@click=/);
  assert.match(tpl, /selected/);
  assert.match(tpl, /data-detail|aria-label="Detail/);
});

test("failure path: no row-edit control (D1)", () => {
  const source = read();
  const tpl = template(source);
  assert.doesNotMatch(tpl, /contenteditable/i, "no editable cells");
  assert.doesNotMatch(tpl, /<input\b[^>]*v-model/i, "no input bound to a value");
  assert.doesNotMatch(tpl, /<(FormControl|TextInput|Input)\b[^>]*v-model/i, "no frappe-ui input bound to a value");
  assert.doesNotMatch(tpl, /v-model=["'][^"']*(amount|debit|credit|balance)/i, "no input bound to a row amount");
});

test("failure path: no v-html, window.open or bare Loading… text", () => {
  const source = read();
  assert.doesNotMatch(source, /v-html/);
  assert.doesNotMatch(source, /window\.open\(|window\.prompt\(/);
  assert.doesNotMatch(template(source), />\s*Loading…?\s*</);
});

// --- B18b: `?entity=` pre-selects the detail area -------------------------

test("(B18b) a ?entity= in the URL opens that entity's detail area", () => {
  const source = read();
  assert.match(source, /route\.query\.entity/, "the query is read from the URL, not remembered client state");
  assert.match(source, /selectedCode\.value\s*=\s*match\.entity/);
});

test("(B18b) failure path: an unknown ?entity= is ignored with a note, never guessed", () => {
  const source = read();
  assert.match(source, /entityNote/);
  const t = template(source);
  assert.match(t, /entityNote/, "the note is rendered");
  // Never silently falls back to the first row or any other row when the
  // requested entity is not found.
  assert.doesNotMatch(source, /rows\[0\]|rows\.find\([^)]*\)\s*\|\|\s*rows\[/,
    "an unknown ?entity= is never replaced by a guessed row");
});
