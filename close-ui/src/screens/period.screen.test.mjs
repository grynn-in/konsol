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
import { gridView } from "../periodGrid.js";

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
  assert.match(js, /gridView\(\s*[\w.]+\s*,\s*problemsOnly\.value\s*,/, "gridView is called with it");
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

test("R01l: hiddenNote and ratesNote render outside the grid LoadState's default slot", () => {
  const tpl = template(read());
  const opens = [...tpl.matchAll(/<LoadState\b/g)];
  assert.ok(opens.length >= 2, "at least two LoadState panels (readiness strip, entity grid)");
  // The grid LoadState is the second one (the readiness strip is the first).
  const gridOpenAt = opens[1].index;
  const gridCloseAt = tpl.indexOf("</LoadState>", gridOpenAt);
  assert.ok(gridCloseAt > gridOpenAt, "the grid LoadState has a matching closing tag");
  const hiddenAt = tpl.indexOf("hiddenNote");
  const ratesAt = tpl.indexOf("ratesNote");
  assert.ok(hiddenAt >= 0, "hiddenNote appears in the template");
  assert.ok(ratesAt >= 0, "ratesNote appears in the template");
  assert.ok(
    hiddenAt < gridOpenAt || hiddenAt > gridCloseAt,
    "hiddenNote must not sit inside the grid LoadState's default slot (an empty grid never renders it there)",
  );
  assert.ok(
    ratesAt < gridOpenAt || ratesAt > gridCloseAt,
    "ratesNote must not sit inside the grid LoadState's default slot (an empty grid never renders it there)",
  );
});

test("R01l: gridView keeps hiddenNote set when rows is empty (pure view model)", () => {
  const payload = {
    period: { fiscal_year: 2026, fiscal_period: 7, code: "FY2026 P07" },
    rows: [],
    counts: { rows: 0, problems: 0, hidden: 5 },
    rates_error: null,
  };
  const view = gridView(payload, false, new Date("2026-07-15T12:00:00Z"), "Europe/London");
  assert.equal(view.rows.length, 0);
  assert.equal(view.hiddenNote, "5 entities outside your scope are not shown");
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

// --- konsol#305 review-w5: the header names the year (gridView.title) --------

test("the header reads gridView's title, never the bare payload period.code", () => {
  const src = fs.readFileSync(PERIOD, "utf8");
  assert.doesNotMatch(src, /period\.code/);
  assert.match(src, /<h1[^>]*>\{\{\s*title\s*\}\}<\/h1>/);
});

// --- konsol#305 Y63: the reminded text in the Trial balance cell -----------

test("Y63: the TB cell shows row.tbReminded under its status chip, v-if gated, text binding", () => {
  const tpl = template(read());
  const chipAt = tpl.search(/\{\{\s*row\.tb\.label\s*\}\}/);
  assert.ok(chipAt >= 0, "the TB chip renders row.tb.label");
  const rateAt = tpl.search(/\{\{\s*row\.rate\.label\s*\}\}/);
  const m = tpl.match(/<div\s+v-if="row\.tbReminded"[^>]*>\s*\{\{\s*row\.tbReminded\s*\}\}\s*<\/div>/);
  assert.ok(m, "a v-if=\"row.tbReminded\" line renders the text");
  const at = tpl.indexOf(m[0]);
  assert.ok(at > chipAt && at < rateAt, "the text sits in the TB cell, after its chip, before the rate cell");
  assert.equal((tpl.match(/row\.tbReminded/g) || []).length, 2, "one gated line, nothing else");
});

test("Y63: gridView gets a now taken at load and the user's time zone (timefmt.js)", () => {
  const js = script(read());
  assert.match(js, /import\s*\{[^}]*\buserTimeZone\b[^}]*\}\s*from\s*["']\.\.\/timefmt\.js["']/);
  assert.match(js, /const timeZone = userTimeZone\(\)/);
  assert.match(js, /gridView\(\s*grid\.payload\s*,\s*problemsOnly\.value\s*,\s*grid\.now\s*,\s*timeZone\s*\)/);
  assert.match(js, /grid\.now = new Date\(\)/);
});

test("Y63 failure path: with no time zone the grid is an error naming why, never a guessed zone", () => {
  const js = script(read());
  assert.match(js, /if \(!timeZone\)/);
  assert.match(js, /no time zone/);
});

test("Y63 (C-R1): no Remind button on the grid: no REMIND, no remind POST, no Remind label", () => {
  const source = read();
  assert.doesNotMatch(source, /\bREMIND\b/);
  assert.doesNotMatch(source, /remind_api/);
  assert.doesNotMatch(source, /remindBody/);
  assert.doesNotMatch(template(source), />\s*Remind\b/);
  assert.doesNotMatch(script(source), /\bpost\(/);
});
