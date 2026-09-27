// konsol#305 B29: timefmt.test.mjs
//
// One time formatter and one zone lookup for the whole app. B27 had copied
// parseZoned/formatTime into tbTable.js and userTimeZone() into
// TrialBalances.vue; this row keeps a single definition in timefmt.js and
// a source scan holds it there.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC_DIR = __dirname;
const TIMEFMT = path.join(SRC_DIR, "timefmt.js");

const TZ = "Europe/London"; // UTC+1 in September (BST)
const NOW = new Date("2026-09-25T12:00:00Z");

async function load() {
  return import("./timefmt.js");
}

test("timefmt exports parseZoned, formatTime and userTimeZone", async () => {
  const m = await load();
  for (const name of ["parseZoned", "formatTime", "userTimeZone"]) {
    assert.equal(typeof m[name], "function", `${name} is exported`);
  }
});

test("parseZoned accepts Z and +hh:mm / +hhmm offsets", async () => {
  const { parseZoned } = await load();
  assert.equal(parseZoned("2026-09-25T09:42:00Z").toISOString(), "2026-09-25T09:42:00.000Z");
  assert.equal(parseZoned("2026-09-25T11:42:00+02:00").toISOString(), "2026-09-25T09:42:00.000Z");
  assert.equal(parseZoned("2026-09-25T04:42:00-0500").toISOString(), "2026-09-25T09:42:00.000Z");
});

test("parseZoned refuses a zone-less or non-string timestamp (B09b)", async () => {
  const { parseZoned } = await load();
  for (const bad of ["2026-09-25 09:42:00", "2026-09-25T09:42:00", "", null, undefined, 1727257320000]) {
    assert.throws(() => parseZoned(bad), /Timestamp has no time zone/, `refuses ${String(bad)}`);
  }
});

test("formatTime: today shows the time only, in the given zone", async () => {
  const { formatTime } = await load();
  assert.equal(formatTime(new Date("2026-09-25T09:42:00Z"), NOW, TZ), "10:42");
});

test("formatTime: another day shows 'Sep 20, 10:42'", async () => {
  const { formatTime } = await load();
  assert.equal(formatTime(new Date("2026-09-20T09:42:00Z"), NOW, TZ), "Sep 20, 10:42");
});

test("formatTime: the calendar day is judged in the given zone, not UTC", async () => {
  const { formatTime } = await load();
  // 23:30 UTC on the 24th is 00:30 on the 25th in London: today.
  assert.equal(formatTime(new Date("2026-09-24T23:30:00Z"), NOW, TZ), "00:30");
  // The same instant in New York is the 24th, 19:30: not today.
  assert.equal(formatTime(new Date("2026-09-24T23:30:00Z"), NOW, "America/New_York"), "Sep 24, 19:30");
});

function withWindow(win, fn) {
  const had = Object.prototype.hasOwnProperty.call(globalThis, "window");
  const prev = globalThis.window;
  globalThis.window = win;
  try {
    return fn();
  } finally {
    if (had) globalThis.window = prev;
    else delete globalThis.window;
  }
}

test("userTimeZone: Frappe's boot time_zone.user wins", async () => {
  const { userTimeZone } = await load();
  const tz = withWindow({ frappe: { boot: { time_zone: { user: "Asia/Kolkata" } } } }, () => userTimeZone());
  assert.equal(tz, "Asia/Kolkata");
});

test("userTimeZone: without a boot zone, the browser's resolved zone", async () => {
  const { userTimeZone } = await load();
  const browser = Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  assert.equal(withWindow({ frappe: { boot: {} } }, () => userTimeZone()), browser);
  assert.equal(withWindow(undefined, () => userTimeZone()), browser);
});

test("userTimeZone: null when neither the boot nor the browser says (never a default)", async () => {
  const { userTimeZone } = await load();
  const original = Intl.DateTimeFormat;
  Intl.DateTimeFormat = function () {
    return { resolvedOptions: () => ({ timeZone: undefined }) };
  };
  try {
    assert.equal(withWindow({ frappe: { boot: {} } }, () => userTimeZone()), null);
  } finally {
    Intl.DateTimeFormat = original;
  }
  Intl.DateTimeFormat = function () {
    throw new Error("no Intl");
  };
  try {
    assert.equal(withWindow(undefined, () => userTimeZone()), null);
  } finally {
    Intl.DateTimeFormat = original;
  }
});

test("timefmt.js reads boot time_zone.user and resolvedOptions().timeZone, and hard-codes no zone", () => {
  const source = fs.readFileSync(TIMEFMT, "utf8");
  assert.match(source, /time_zone\s*&&\s*boot\.time_zone\.user|time_zone\.user/);
  assert.match(source, /resolvedOptions\(\)\.timeZone/);
  assert.doesNotMatch(source, /["'](UTC|Etc\/[A-Za-z]+|Europe\/[A-Za-z]+|America\/[A-Za-z]+|Asia\/[A-Za-z]+)["']/,
    "no hard-coded zone");
});

// --- one definition only -------------------------------------------------

function walk(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules") continue;
      out.push(...walk(full));
    } else if (/\.(js|mjs|vue)$/.test(entry.name) && !entry.name.endsWith(".test.mjs")) {
      out.push(full);
    }
  }
  return out;
}

const ZONED_REGEX_SOURCE = "(Z|[+-]\\d{2}:?\\d{2})";
const DEFINITIONS = [
  ["the zoned-time regex", (s) => s.includes(ZONED_REGEX_SOURCE)],
  ["a userTimeZone definition", (s) => /function\s+userTimeZone\b|\buserTimeZone\s*=/.test(s)],
  ["a parseZoned definition", (s) => /function\s+parseZoned\b|\bparseZoned\s*=/.test(s)],
  ["a formatTime definition", (s) => /function\s+formatTime\b|\bformatTime\s*=/.test(s)],
];

test("the scan sees the real regex text (guards the scan itself)", () => {
  // If the scan's pattern drifted from the regex's source text, the
  // one-definition test below would pass on nothing.
  assert.ok(fs.readFileSync(TIMEFMT, "utf8").includes(ZONED_REGEX_SOURCE),
    "timefmt.js holds the zoned-time regex");
});

for (const [what, found] of DEFINITIONS) {
  test(`${what} exists ONLY in timefmt.js`, () => {
    const files = walk(SRC_DIR).filter((f) => found(fs.readFileSync(f, "utf8")));
    const rel = files.map((f) => path.relative(SRC_DIR, f)).sort();
    assert.deepEqual(rel, ["timefmt.js"]);
  });
}
