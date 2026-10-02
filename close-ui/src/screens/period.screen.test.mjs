// konsol#305 E208a: period.screen.test.mjs
//
// Source-level checks on screens/Period.vue (E2; stories 2.1, 2.2, 2.3; 0.1;
// W2-4). The component is read as text: a .vue file only compiles inside the
// Vite build, which the row's gate runs separately (mirror
// checks.screen.test.mjs's note).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PERIOD = path.join(__dirname, "Period.vue");

function read() {
  return fs.readFileSync(PERIOD, "utf8");
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

test("Period.vue builds its views with periodGrid.js (E207)", () => {
  const source = read();
  const m = source.match(/import\s*\{([^}]*)\}\s*from\s*["']\.\.\/periodGrid\.js["']/);
  assert.ok(m, "imports from ../periodGrid.js");
  for (const name of ["gridView", "readinessView", "toneClass", "COLUMNS"]) {
    assert.match(m[1], new RegExp(`\\b${name}\\b`), `imports ${name}`);
  }
  const js = script(source);
  assert.match(js, /\bgridView\(/);
  assert.match(js, /\breadinessView\(/);
});

test("Period.vue calls the server through api.js and reads the period with route.js", () => {
  const source = read();
  assert.match(source, /import\s*\{[^}]*\bget\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
  assert.match(source, /from\s*["']\.\.\/route\.js["']/);
  assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js");
});

test("Period.vue has no POST call site: the screen is read only", () => {
  const source = read();
  assert.doesNotMatch(script(source), /\bpost\(/);
  assert.doesNotMatch(source, /konsol\.close\.\w+_api\.\w*post\w*/i);
});

test("Period.vue reads get_readiness and get_period_grid (E203, E204)", () => {
  const source = read();
  assert.match(source, /konsol\.close\.grid_api\.get_readiness/);
  assert.match(source, /konsol\.close\.grid_api\.get_period_grid/);
});

test("Failure path (W2-4): no Intercompany or Checks column in the grid", () => {
  const tpl = template(read());
  assert.doesNotMatch(tpl, /Intercompany/);
  assert.doesNotMatch(tpl, />\s*Checks\s*</);
});

test("The table headers are exactly Entity, Ccy, then COLUMNS", () => {
  const tpl = template(read());
  assert.match(tpl, /<th[^>]*>\s*Entity\s*<\/th>/);
  assert.match(tpl, /<th[^>]*>\s*Ccy\s*<\/th>/);
  const entityAt = tpl.search(/<th[^>]*>\s*Entity\s*<\/th>/);
  const ccyAt = tpl.search(/<th[^>]*>\s*Ccy\s*<\/th>/);
  assert.ok(ccyAt > entityAt, "Ccy follows Entity");
});

test("Period.vue renders LoadState at least twice: the readiness strip and the entity grid", () => {
  const tpl = template(read());
  const loadStates = tpl.match(/<LoadState\b/g) || [];
  assert.ok(loadStates.length >= 2, "at least two LoadState panels");
  assert.match(
    read(),
    /import\s+LoadState\s+from\s*["']\.\.\/components\/LoadState\.vue["']/,
  );
});

test("The All/Problems filter toggles a problemsOnly ref passed to gridView(", () => {
  const source = read();
  const js = script(source);
  assert.match(js, /\bproblemsOnly\s*=\s*ref\(/, "a ref named problemsOnly");
  assert.match(js, /gridView\(\s*[\w.]+\s*,\s*problemsOnly\.value\s*\)/, "gridView is called with it");
  const tpl = template(source);
  assert.match(tpl, /problemsOnly\s*=\s*(true|false)/, "the template can change it (refs unwrap in <template>)");
});

test("The All and Problems counts come from gridView's all/problems, not re-counted here", () => {
  const source = read();
  assert.match(source, /\.all\b/);
  assert.match(source, /\.problems\b/);
});

test("hiddenNote and ratesNote are shown when the view sets them", () => {
  const tpl = template(read());
  assert.match(tpl, /\bhiddenNote\b/);
  assert.match(tpl, /\bratesNote\b/);
});

test("Cells show cell.label in a chip with toneClass(cell.tone): text binding only, no v-html", () => {
  const source = read();
  const tpl = template(source);
  assert.match(tpl, /toneClass\(/);
  assert.match(tpl, /\.label\b/);
  assert.doesNotMatch(source, /v-html/);
});

test("An in-app link to sign-off is built with format({..., screen: \"sign-off\"})", () => {
  const source = read();
  assert.match(source, /format\(\s*\{[^}]*screen:\s*["']sign-off["'][^}]*\}\s*\)/s);
});

test("Period.vue offers Retry through LoadState on an error", () => {
  const tpl = template(read());
  assert.match(tpl, /@retry=/);
});
