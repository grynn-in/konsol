// konsol#305 B21: tbCompare.section.test.mjs
//
// Source-level checks on sections/TbCompare.vue (view by account against the
// previous period; story 3.4) and on its mount in screens/TrialBalances.vue.
// A .vue file only compiles inside the Vite build, which the row's gate runs
// separately (Problems found 17: C1 is the real check for screens).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SECTION = path.join(__dirname, "TbCompare.vue");
const SCREEN = path.join(__dirname, "..", "screens", "TrialBalances.vue");

function read(file = SECTION) {
  return fs.readFileSync(file, "utf8");
}

function template(source) {
  const start = source.indexOf("<template>");
  const end = source.lastIndexOf("</template>");
  assert.ok(start >= 0 && end > start, "the file has a <template>");
  return source.slice(start, end);
}

function script(source) {
  const match = source.match(/<script setup>([\s\S]*?)<\/script>/);
  assert.ok(match, "the file has a <script setup>");
  return match[1];
}

test("it reads A28 tb_compare through api.js (B06), not its own fetch", () => {
  const source = read();
  assert.match(source, /import\s*\{[^}]*\bget\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
  assert.match(source, /konsol\.close\.tb_read_api\.tb_compare/);
  assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js");
});

test("it builds the table with compareRows (B12), not its own mapping", () => {
  const source = read();
  assert.match(script(source), /import\s*\{[^}]*\bcompareRows\b[^}]*\}\s*from\s*["']\.\.\/tbTable\.js["']/);
  assert.match(script(source), /compareRows\(/);
});

test("basis_note and previous_note are shown, passed through verbatim", () => {
  const source = read();
  const tpl = template(source);
  assert.match(tpl, /basis_note/);
  assert.match(tpl, /previous_note/);
  // Verbatim: the section never composes its own sentence for either note.
  assert.doesNotMatch(source, /not comparable`|no trial balance for \$\{/i);
});

test("the previous period's code is shown as the server sent it (a cross-year code already carries its year)", () => {
  const tpl = template(read());
  assert.match(tpl, /previous_code/);
});

test("IC partner rows carry their partner and the is_ic flag", () => {
  const tpl = template(read());
  assert.match(tpl, /\.partner\b/);
  assert.match(tpl, /is_ic/);
});

test("it renders LoadState (B17) for loading and error, with a Retry", () => {
  const source = read();
  assert.match(source, /import\s+LoadState\s+from\s*["']\.\.\/components\/LoadState\.vue["']/);
  const tpl = template(source);
  assert.match(tpl, /<LoadState\b/);
  assert.match(tpl, /@retry=/, "an error offers the Retry");
});

test("A28's refusal 'No submitted trial balance…' is shown as a state, not the red error box", () => {
  const source = read();
  const js = script(source);
  // The section recognises the refusal text and routes it away from the
  // error state (LoadState's red alert), showing it as the server wrote it.
  assert.match(js, /No submitted trial balance/, "the refusal text is matched");
  assert.doesNotMatch(js.slice(js.indexOf("No submitted trial balance") - 200, js.indexOf("No submitted trial balance")), /status\s*=\s*["']error["']/, "matching the refusal does not fall into the error branch first");
  const tpl = template(source);
  assert.match(tpl, /empty-text|emptyText/, "the refusal is shown through LoadState's empty state, not error");
});

test("failure path: read-only (D1); no editable cell, no input bound to a row value", () => {
  const source = read();
  const tpl = template(source);
  assert.doesNotMatch(tpl, /contenteditable/i, "no editable cells");
  assert.doesNotMatch(tpl, /<input\b/i, "no input at all: this view has nothing to type into");
  assert.doesNotMatch(tpl, /v-model/i, "no v-model anywhere");
});

test("failure path: no v-html, no window.open, no bare Loading… text", () => {
  const source = read();
  assert.doesNotMatch(source, /v-html/);
  assert.doesNotMatch(source, /window\.open\(|window\.prompt\(/);
  assert.doesNotMatch(template(source), />\s*Loading…?\s*</);
});

test("TrialBalances.vue mounts the section in the detail area, alongside TbUpload, for a received TB", () => {
  const source = read(SCREEN);
  assert.match(script(source), /import\s+TbCompare\s+from\s*["']\.\.\/sections\/TbCompare\.vue["']/);
  const tpl = template(source);
  const detail = tpl.slice(tpl.indexOf("data-detail"));
  assert.match(detail, /<TbCompare\b/, "mounted inside the detail area");
  assert.match(detail, /<TbUpload\b/, "B20's upload section is still mounted (this row must not break it)");
  const tag = detail.match(/<TbCompare\b[^>]*>/)[0];
  assert.match(tag, /v-if="[^"]*Received[^"]*"/, "shown only for a received trial balance");
  assert.match(tag, /:key=/, "a new entity or period gets a fresh load");
});
