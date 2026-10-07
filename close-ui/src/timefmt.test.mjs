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

test("timefmt exports parseZoned, formatTime, userTimeZone and ageText", async () => {
  const m = await load();
  for (const name of ["parseZoned", "formatTime", "userTimeZone", "ageText"]) {
    assert.equal(typeof m[name], "function", `${name} is exported`);
  }
});

// --- F03: ageText — the one age rule for Approvals and My work -------------
//
// konsol#305 F03 (live: the same waiting item showed "16 days" on one
// screen and "17 days" on the other). Age is the whole-calendar-day
// difference in `timeZone`, never a floor of raw elapsed milliseconds —
// that drifts by a day depending on what time of day `now` lands on.

test("ageText: whole calendar days in the zone, not a floor of elapsed hours", async () => {
  const { ageText } = await load();
  // 23:50 on the 13th in London is still "the 13th" there. Flooring the raw
  // elapsed milliseconds between this instant and 07:00 on the 29th gives
  // only 15 full 24h periods; the calendar dates are 16 days apart.
  assert.equal(
    ageText("2026-09-13T23:50:00+01:00", new Date("2026-09-29T07:00:00Z"), "Europe/London"),
    "16 days",
  );
});

test("ageText: a bare YYYY-MM-DD date is read as a calendar date, no zone conversion", async () => {
  const { ageText } = await load();
  assert.equal(ageText("2026-09-13", new Date("2026-09-29T12:00:00Z"), "Europe/London"), "16 days");
});

test("ageText: today, 1 day, null and no negative age", async () => {
  const { ageText } = await load();
  assert.equal(ageText(null, new Date("2026-09-25T12:00:00Z"), "UTC"), null);
  assert.equal(ageText(undefined, new Date("2026-09-25T12:00:00Z"), "UTC"), null);
  assert.equal(ageText("2026-09-25T00:30:00Z", new Date("2026-09-25T23:00:00Z"), "UTC"), "today");
  assert.equal(ageText("2026-09-24T23:50:00Z", new Date("2026-09-25T00:10:00Z"), "UTC"), "1 day");
  // a value later than now's calendar date (clock skew, or a not-yet-ended
  // period) renders nothing — never a negative count.
  assert.equal(ageText("2026-09-26T00:00:00Z", new Date("2026-09-25T00:00:00Z"), "UTC"), null);
  assert.equal(ageText("2026-10-01", new Date("2026-09-25T12:00:00Z"), "UTC"), null);
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

test("formatTime: another day shows '20 Sep, 10:42' (day-first, #305-R52-3-1)", async () => {
  const { formatTime } = await load();
  assert.equal(formatTime(new Date("2026-09-20T09:42:00Z"), NOW, TZ), "20 Sep, 10:42");
});

// konsol#305 R53d (decision #305-R52-3-1, Deepak Pai 7 Oct): instants are
// day-first everywhere, like the due dates ("Tue 7 Oct 2025"). Same year as
// `now` (judged in the zone): "6 Oct, 10:00"; another year: "6 Oct 2024, 10:00".
test("formatTime (R53d): day-first across every month of the same year", async () => {
  const { formatTime } = await load();
  const now = new Date("2026-12-31T12:00:00Z");
  const expected = [
    "6 Jan, 10:00", "6 Feb, 10:00", "6 Mar, 10:00", "6 Apr, 10:00", "6 May, 10:00", "6 Jun, 10:00",
    "6 Jul, 10:00", "6 Aug, 10:00", "6 Sep, 10:00", "6 Oct, 10:00", "6 Nov, 10:00", "6 Dec, 10:00",
  ];
  for (let m = 0; m < 12; m += 1) {
    const date = new Date(Date.UTC(2026, m, 6, 10, 0));
    assert.equal(formatTime(date, now, "UTC"), expected[m], `month ${m + 1}`);
  }
});

test("formatTime (R53d): another year carries the year — '6 Oct 2024, 10:00'", async () => {
  const { formatTime } = await load();
  const now = new Date("2026-10-07T12:00:00Z");
  assert.equal(formatTime(new Date("2024-10-06T10:00:00Z"), now, "UTC"), "6 Oct 2024, 10:00");
  assert.equal(formatTime(new Date("2025-12-31T23:59:00Z"), now, "UTC"), "31 Dec 2025, 23:59");
  // a later year (clock skew) is still another year, never guessed away
  assert.equal(formatTime(new Date("2027-01-02T08:05:00Z"), now, "UTC"), "2 Jan 2027, 08:05");
});

test("formatTime (R53d): same year, earlier day — '6 Oct, 10:00'; today — '10:00'", async () => {
  const { formatTime } = await load();
  const now = new Date("2026-10-07T12:00:00Z");
  assert.equal(formatTime(new Date("2026-10-06T10:00:00Z"), now, "UTC"), "6 Oct, 10:00");
  assert.equal(formatTime(new Date("2026-10-07T10:00:00Z"), now, "UTC"), "10:00");
  assert.equal(formatTime(new Date("2026-01-01T00:00:00Z"), now, "UTC"), "1 Jan, 00:00");
});

test("formatTime (R53d): the day, month and year are judged in the zone, not UTC", async () => {
  const { formatTime } = await load();
  const now = new Date("2026-06-15T12:00:00Z");
  // 31 Dec 2025 20:00 UTC is 1 Jan 2026 01:30 in Kolkata: same year as now there.
  assert.equal(formatTime(new Date("2025-12-31T20:00:00Z"), now, "Asia/Kolkata"), "1 Jan, 01:30");
  // ... and still 31 Dec 2025 in UTC: another year.
  assert.equal(formatTime(new Date("2025-12-31T20:00:00Z"), now, "UTC"), "31 Dec 2025, 20:00");
  // 1 Mar 2026 03:00 UTC is 28 Feb 2026 22:00 in New York.
  assert.equal(formatTime(new Date("2026-03-01T03:00:00Z"), now, "America/New_York"), "28 Feb, 22:00");
});

test("formatTime (R53d): month names come from dueDate.js's fixed table, never Intl", () => {
  const src = fs.readFileSync(TIMEFMT, "utf8");
  assert.doesNotMatch(src, /month:\s*["'](short|long|narrow)["']/, "no Intl month name");
  assert.doesNotMatch(src, /toLocale/, "no toLocale*String for names");
  assert.match(src, /import\s*\{[^}]*\bMONTHS\b[^}]*\}\s*from\s*["']\.\/dueDate\.js["']/, "MONTHS imported from dueDate.js");
  assert.doesNotMatch(src, /["']Jan["']/, "no second month table in timefmt.js");
});

test("dueDate.js exports the one MONTHS table", async () => {
  const { MONTHS } = await import("./dueDate.js");
  assert.deepEqual(MONTHS, ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]);
});

test("formatTime: the calendar day is judged in the given zone, not UTC", async () => {
  const { formatTime } = await load();
  // 23:30 UTC on the 24th is 00:30 on the 25th in London: today.
  assert.equal(formatTime(new Date("2026-09-24T23:30:00Z"), NOW, TZ), "00:30");
  // The same instant in New York is the 24th, 19:30: not today.
  assert.equal(formatTime(new Date("2026-09-24T23:30:00Z"), NOW, "America/New_York"), "24 Sep, 19:30");
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

// --- F03: approvals.js and myWork.js both go through timefmt.js's ageText --
//
// Neither screen may keep its own copy of the day-math (that is how they
// drifted apart: approvals.js floored elapsed milliseconds, myWork.js
// diffed calendar dates). Both now import the one function from here.

test("approvals.js imports ageText from timefmt.js", () => {
  const source = fs.readFileSync(path.join(SRC_DIR, "approvals.js"), "utf8");
  assert.match(source, /import\s*\{[^}]*\bageText\b[^}]*\}\s*from\s*["']\.\/timefmt\.js["']/);
});

test("myWork.js imports ageText from timefmt.js", () => {
  const source = fs.readFileSync(path.join(SRC_DIR, "myWork.js"), "utf8");
  assert.match(source, /import\s*\{[^}]*\bageText\b[^}]*\}\s*from\s*["']\.\/timefmt\.js["']/);
});
