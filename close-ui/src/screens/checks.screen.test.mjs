// konsol#305 B22: checks.screen.test.mjs
//
// Source-level checks on screens/Checks.vue (E7; stories 7.1-7.4). The
// component is read as text: a .vue file only compiles inside the Vite build,
// which the row's gate runs separately (Problems found 17: C1 is the real
// check for screens).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CHECKS = path.join(__dirname, "Checks.vue");

function read() {
  return fs.readFileSync(CHECKS, "utf8");
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

test("Checks.vue builds what it shows with checksView (B13)", () => {
  const source = read();
  assert.match(source, /import\s*\{[^}]*\bchecksView\b[^}]*\}\s*from\s*["']\.\.\/checks\.js["']/);
  assert.match(script(source), /checksView\(/);
});

test("Checks.vue calls the server through api.js and reads the period with route.js", () => {
  const source = read();
  assert.match(source, /import\s*\{[^}]*\bget\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
  assert.match(source, /import\s*\{[^}]*\bpost\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
  assert.match(source, /from\s*["']\.\.\/route\.js["']/);
  assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js");
});

test("Checks.vue reads A26 get_checks and has exactly one run_checks call site", () => {
  const source = read();
  assert.match(source, /konsol\.close\.checks_api\.get_checks/);
  const runs = source.match(/konsol\.close\.checks_api\.run_checks/g) || [];
  assert.equal(runs.length, 1, "one endpoint constant for run_checks");
  const posts = script(source).match(/\bpost\(/g) || [];
  assert.equal(posts.length, 1, "exactly one post( call site: one Run button (#298 stage 1)");
});

test("There is exactly one Run button, rendered only when canRun", () => {
  const tpl = template(read());
  const runButtons = tpl.match(/@click="runChecks"/g) || [];
  assert.equal(runButtons.length, 1, "one Run button");
  const i = tpl.indexOf('@click="runChecks"');
  const tagStart = tpl.lastIndexOf("<", i);
  const tag = tpl.slice(tagStart, tpl.indexOf(">", i) + 1);
  assert.match(tag, /v-if="[^"]*\bcanRun\b[^"]*"/, "the Run button is gated by canRun");
});

test("The Run button is disabled while a run is in progress", () => {
  const tpl = template(read());
  const i = tpl.indexOf('@click="runChecks"');
  const tagStart = tpl.lastIndexOf("<", i);
  const tag = tpl.slice(tagStart, tpl.indexOf(">", i) + 1);
  assert.match(tag, /:disabled="[^"]*\b(inProgress|running)\b[^"]*"/, "disabled while a run is in progress");
});

test("It polls get_checks while a run is running, and stops when unmounted", () => {
  const src = script(read());
  assert.match(src, /setInterval\(|setTimeout\(/, "polls on a timer");
  assert.match(src, /["']running["']/, "polling keys on the running state");
  assert.match(src, /onBeforeUnmount\(/, "clears the timer when the screen goes");
  assert.match(src, /clearInterval\(|clearTimeout\(/);
});

test("Failure path: the server's refusal (e.g. already in progress) is shown as the server wrote it", () => {
  const source = read();
  const src = script(source);
  // The caught Error's message is kept verbatim (api.js already extracts the
  // server's own text); nothing replaces it with a generic line.
  assert.match(src, /catch\s*\(\s*(\w+)\s*\)\s*\{[\s\S]*?\1\.message/, "keeps e.message");
  assert.doesNotMatch(source, /Something went wrong/i, "no generic replacement text");
  assert.match(source, /messageLines\(/, "split on <br> into plain lines, never HTML");
  assert.match(template(source), /role="alert"/, "the refusal is announced");
});

test("Failure path: loading, empty and error states go through LoadState (B17)", () => {
  const source = read();
  assert.match(source, /import\s+LoadState\s+from\s*["']\.\.\/components\/LoadState\.vue["']/);
  assert.match(template(source), /<LoadState[\s\S]*?@retry=/, "an error offers Retry");
});

test("A missing description shows the P4 text through checksView, never invented text", () => {
  const source = read();
  assert.match(template(source), /cause\.text/, "the cause text comes from checksView");
  assert.doesNotMatch(source, /description_missing/, "the screen does not re-decide the missing-description text");
});

test("Results from an earlier run are labelled: the banner comes from checksView", () => {
  const tpl = template(read());
  assert.match(tpl, /\.banner\b/);
});

test("No v-html anywhere", () => {
  assert.doesNotMatch(read(), /v-html/);
});

test("B13b: a warning looks different from a failure — the cause's label renders as text, not colour alone", () => {
  const tpl = template(read());
  assert.match(tpl, /cause\.label\b/, "the Fail/Error/Warn label from checksView renders");
});

// --- konsol#305 B31: the header's Checks status follows the run -----------

test("Checks.vue asks the shell to reload the context with contextReloadNeeded (B31)", () => {
  const source = read();
  const js = script(source);
  assert.match(
    source,
    /import\s*\{[^}]*\bcontextReloadNeeded\b[^}]*\bCONTEXT_RELOAD\b[^}]*\}\s*from\s*["']\.\.\/contextRefresh\.js["']|import\s*\{[^}]*\bCONTEXT_RELOAD\b[^}]*\bcontextReloadNeeded\b[^}]*\}\s*from\s*["']\.\.\/contextRefresh\.js["']/,
  );
  assert.match(js, /\binject\(\s*CONTEXT_RELOAD\s*\)/, "the reload comes from the shell, with no silent default");
  assert.match(js, /if\s*\(\s*contextReloadNeeded\([^)]*\)\s*\)\s*reloadContext\(\)/, "the rule decides; the screen calls");
});

test("Checks.vue forgets the last run it saw when the period changes, and adds no polling of its own", () => {
  const js = script(read());
  const watchAt = js.indexOf("watch(");
  assert.ok(watchAt >= 0);
  assert.match(js.slice(watchAt), /lastLatest\s*=\s*undefined/, "a new period is a first observation");
  assert.equal((js.match(/setTimeout\(/g) || []).length, 1, "only the existing running poll");
  assert.doesNotMatch(js, /setInterval\(/);
});
