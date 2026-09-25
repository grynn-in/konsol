// konsol#305 B17: shell.test.mjs
//
// Source-level checks on the shell: AppShell (header + nav + router-view,
// and the /close landing states B16b left to it), HeaderBar (period picker,
// period state, other open periods, freshness) and LoadState (the designed
// loading / empty / error states). The components are read as text: they
// are .vue files that only compile inside the Vite build, which the row's
// gate runs separately. Problems found 17: C1 is the real check for screens.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const COMPONENTS = path.join(__dirname, "components");
const APP_SHELL = path.join(COMPONENTS, "AppShell.vue");
const HEADER_BAR = path.join(COMPONENTS, "HeaderBar.vue");
const LOAD_STATE = path.join(COMPONENTS, "LoadState.vue");

function read(p) {
  return fs.readFileSync(p, "utf8");
}

function template(source) {
  const start = source.indexOf("<template>");
  const end = source.lastIndexOf("</template>");
  assert.ok(start >= 0 && end > start, "the component has a <template>");
  return source.slice(start, end);
}

// --- AppShell ------------------------------------------------------------

test("AppShell builds the nav with navFor (B08) and the freshness text with freshnessView (B09)", () => {
  const source = read(APP_SHELL);
  assert.match(source, /import\s*\{[^}]*\bnavFor\b[^}]*\}\s*from\s*["']\.\.\/nav\.js["']/);
  assert.match(source, /import\s*\{[^}]*\bfreshnessView\b[^}]*\}\s*from\s*["']\.\.\/freshness\.js["']/);
  assert.match(source, /navFor\(/);
  assert.match(source, /freshnessView\(/);
});

test("AppShell uses route.js (B07) and api.js (B06), not its own copies", () => {
  const source = read(APP_SHELL);
  assert.match(source, /from\s*["']\.\.\/route\.js["']/);
  assert.match(source, /import\s*\{[^}]*\bget\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
  assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js");
});

test("AppShell reads A15 get_context, A16 get_freshness and A29 get_my_work", () => {
  const source = read(APP_SHELL);
  assert.match(source, /konsol\.close\.period_api\.get_context/);
  assert.match(source, /konsol\.close\.freshness_api\.get_freshness/);
  assert.match(source, /konsol\.close\.mywork_api\.get_my_work/);
});

test("AppShell polls freshness every 60 s, only while the tab is visible", () => {
  const source = read(APP_SHELL);
  assert.match(source, /60[_]?000/);
  assert.match(source, /visibilitychange/);
  assert.match(source, /visibilityState/);
  assert.match(source, /clearInterval|clearTimeout/, "the poll is stopped, not leaked");
});

test("AppShell takes the time zone from timefmt.js (B29) and never invents one", () => {
  const source = read(APP_SHELL);
  // B29: the lookup (Frappe's boot, else the browser) lives in timefmt.js,
  // whose test checks it. Here: imported, not copied. Comments are stripped
  // so a mention in prose cannot satisfy the check.
  const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
  assert.match(code, /import\s*\{[^}]*\buserTimeZone\b[^}]*\}\s*from\s*["']\.\.\/timefmt\.js["']/);
  assert.doesNotMatch(code, /function\s+userTimeZone\b|\buserTimeZone\s*=/, "no local copy");
  assert.doesNotMatch(code, /resolvedOptions\(\)/, "no local copy of the lookup");
  assert.doesNotMatch(source, /["'](UTC|Etc\/[A-Za-z]+|Europe\/[A-Za-z]+|America\/[A-Za-z]+|Asia\/[A-Za-z]+)["']/,
    "no hard-coded zone");
});

test("AppShell renders the router view and the <HeaderBar>", () => {
  const tpl = template(read(APP_SHELL));
  assert.match(tpl, /<router-view|<RouterView/);
  assert.match(tpl, /<HeaderBar\b/);
});

test("AppShell renders landingState.reason and landingState.error from router.js (B16b)", () => {
  const source = read(APP_SHELL);
  assert.match(source, /import\s*\{[^}]*\blandingState\b[^}]*\}\s*from\s*["']\.\.\/router\.js["']/);
  const tpl = template(source);
  assert.match(tpl, /landingState\.reason/);
  assert.match(tpl, /landingState\.error/);
});

test("AppShell offers the Viewer's switch to the provisional period", () => {
  const source = read(APP_SHELL);
  assert.match(source, /landing\.provisional|provisional\.period/);
  assert.match(template(source), /provisional/i);
});

test("AppShell shows a null nav count as unknown, never as 0", () => {
  const tpl = template(read(APP_SHELL));
  assert.match(tpl, /count\s*(==|===)\s*null/);
  assert.match(tpl, /unknown/i);
});

test("AppShell renders LoadState for its loading and error states", () => {
  const source = read(APP_SHELL);
  assert.match(source, /import\s+LoadState\s+from\s*["']\.\/LoadState\.vue["']/);
  assert.match(template(source), /<LoadState\b/);
});

test("AppShell has a ⌘K stub", () => {
  const source = read(APP_SHELL);
  assert.match(source, /metaKey|ctrlKey/);
  assert.match(template(source), /⌘K/);
});

// --- HeaderBar -----------------------------------------------------------

test("HeaderBar lists other_open", () => {
  const tpl = template(read(HEADER_BAR));
  assert.match(tpl, /v-for="[^"]*\bin\b[^"]*other_open/);
});

test("HeaderBar shows the period's Open/Closed/Locked status and its sign-off state", () => {
  const tpl = template(read(HEADER_BAR));
  assert.match(tpl, /\.status\b/);
  assert.match(tpl, /\.signoff\b/);
});

test("HeaderBar changes the period through the URL only (route.js format)", () => {
  const source = read(HEADER_BAR);
  assert.match(source, /import\s*\{[^}]*\bformat\b[^}]*\}\s*from\s*["']\.\.\/route\.js["']/);
  assert.match(source, /router\.push\(/);
  // Built from parts: route.test.mjs (B07) scans every src file for the literal names.
  assert.doesNotMatch(source, new RegExp(["local", "session"].map((w) => w + "Storage").join("|")));
});

test("HeaderBar shows the freshness text and its detail", () => {
  const tpl = template(read(HEADER_BAR));
  assert.match(tpl, /freshness\.text/);
  assert.match(tpl, /freshness\.detail/);
});

// --- LoadState -----------------------------------------------------------

test("LoadState has loading, empty and error slots", () => {
  const tpl = template(read(LOAD_STATE));
  assert.match(tpl, /<slot\s+name="loading"/);
  assert.match(tpl, /<slot\s+name="empty"/);
  assert.match(tpl, /<slot\s+name="error"/);
});

test("LoadState's error slot shows the message and a Retry", () => {
  const tpl = template(read(LOAD_STATE));
  const at = tpl.indexOf('<slot name="error"');
  const slot = tpl.slice(at, tpl.indexOf("</slot>", at));
  assert.match(slot, /\{\{[^}]*error[^}]*\}\}/);
  assert.match(slot, /Retry/);
  assert.match(slot, /emit\(\s*["']retry["']\s*\)|\$emit\(\s*["']retry["']\s*\)/);
});

// --- Failure path: forbidden patterns in every .vue ----------------------

const FORBIDDEN = [
  { name: "window.open(", re: /window\.open\(/ },
  { name: "window.prompt(", re: /window\.prompt\(/ },
  // #298 stage 1: a bare "Loading…" with nothing saying what is loading.
  { name: "bare Loading…", re: />\s*Loading(…|\.\.\.)\s*</ },
  { name: "bare Loading… string", re: /["'`]Loading(…|\.\.\.)["'`]/ },
];

function offences(text) {
  return FORBIDDEN.filter((f) => f.re.test(text)).map((f) => f.name);
}

function vueFiles(dir) {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...vueFiles(p));
    else if (p.endsWith(".vue")) out.push(p);
  }
  return out;
}

test("failure path: the scanner flags each forbidden pattern", () => {
  assert.deepEqual(offences(`<a @click="window.open('/app/x')">`), ["window.open("]);
  assert.deepEqual(offences(`const n = window.prompt("Name");`), ["window.prompt("]);
  assert.deepEqual(offences(`<div>Loading…</div>`), ["bare Loading…"]);
  assert.deepEqual(offences(`<p> Loading... </p>`), ["bare Loading…"]);
  assert.deepEqual(offences(`label: "Loading…",`), ["bare Loading… string"]);
  assert.deepEqual(offences(`<p>Loading the period FY2025 P07</p>`), []);
});

test("failure path: no .vue file in src/ uses window.open(, window.prompt( or a bare Loading…", () => {
  const files = vueFiles(__dirname);
  assert.ok(files.includes(APP_SHELL) && files.includes(HEADER_BAR) && files.includes(LOAD_STATE),
    "the three shell components exist");
  const bad = files
    .map((p) => [path.relative(__dirname, p), offences(read(p))])
    .filter(([, found]) => found.length);
  assert.deepEqual(bad, []);
});

// --- found on live (25 Sep): states that read as wrong facts ---------------

test("live: other_open is 'unknown', not 'none', while the first close period is undeclared", () => {
  // A04 other_open returns [] when first close is undeclared; "none" would
  // state a fact nobody computed.
  const tpl = template(read(HEADER_BAR));
  assert.match(read(HEADER_BAR), /first_close_undeclared/);
  assert.match(tpl, /unknown until the first close period is declared/);
});

test("live: a bad address still loads who I am, so the nav never waits forever", () => {
  // On /close/abc/1/my-work the nav showed "Fetching your screens" for good:
  // the context was never requested for a malformed address.
  const source = read(APP_SHELL);
  assert.doesNotMatch(source, /if \(p\.error\) return;/);
});

test("live: a refused call (no close role) tells the user whom to ask", () => {
  const source = read(APP_SHELL);
  assert.match(source, /PermissionError/);
  assert.match(template(source), /Ask the System Manager/);
});
