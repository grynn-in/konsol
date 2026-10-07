// konsol#305 R52l (review U3): dueDate.test.mjs
//
// The one calendar-day wording of the close app is pinned to literal strings.
// Every expected value below is written out by hand; none is computed with
// dueDateText itself, so a wording change (ICU's "Sept" for "Sep", say) turns
// these tests red instead of moving the expectation along with it.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { dueDateText } from "./dueDate.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

test("Sep is 'Sep', never ICU's 'Sept': 2025-09-30 is 'Tue 30 Sep 2025'", () => {
  assert.equal(dueDateText("2025-09-30", "t"), "Tue 30 Sep 2025");
});

test("one literal wording per month, all twelve", () => {
  const cases = [
    ["2025-01-11", "Sat 11 Jan 2025"],
    ["2025-02-12", "Wed 12 Feb 2025"],
    ["2025-03-13", "Thu 13 Mar 2025"],
    ["2025-04-14", "Mon 14 Apr 2025"],
    ["2025-05-15", "Thu 15 May 2025"],
    ["2025-06-16", "Mon 16 Jun 2025"],
    ["2025-07-17", "Thu 17 Jul 2025"],
    ["2025-08-18", "Mon 18 Aug 2025"],
    ["2025-09-19", "Fri 19 Sep 2025"],
    ["2025-10-20", "Mon 20 Oct 2025"],
    ["2025-11-21", "Fri 21 Nov 2025"],
    ["2025-12-31", "Wed 31 Dec 2025"],
  ];
  for (const [iso, text] of cases) {
    assert.equal(dueDateText(iso, "t"), text, iso);
  }
});

test("the day has no leading zero, and Sunday is 'Sun'", () => {
  assert.equal(dueDateText("2025-10-05", "t"), "Sun 5 Oct 2025");
  assert.equal(dueDateText("2025-10-01", "t"), "Wed 1 Oct 2025");
});

test("a leap day is a real date: 2024-02-29 is 'Thu 29 Feb 2024'", () => {
  assert.equal(dueDateText("2024-02-29", "t"), "Thu 29 Feb 2024");
});

test("Failure path: a value that is not an ISO date throws, naming the caller", () => {
  for (const bad of ["30/09/2025", "2025-9-30", "2025-09-30T00:00:00Z", "", null, undefined, 20250930]) {
    assert.throws(() => dueDateText(bad, "caller"), /caller: the due date is not an ISO date/, String(bad));
  }
});

test("Failure path: an impossible calendar date throws", () => {
  for (const bad of ["2025-02-29", "2025-13-01", "2025-00-10", "2025-04-31", "2025-01-00"]) {
    assert.throws(() => dueDateText(bad, "caller"), /caller: the deadline's due is not a real date/, bad);
  }
});

test("dueDate.js builds the wording from fixed tables: no Intl", () => {
  const source = fs.readFileSync(path.join(__dirname, "dueDate.js"), "utf8");
  const code = source.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
  assert.doesNotMatch(code, /\bIntl\b/);
  assert.doesNotMatch(code, /toLocale\w*\(/);
});

test("No test file under src/ builds an expected value with dueDateText", () => {
  for (const rel of ["rates.test.mjs", path.join("sections", "ownershipChange.section.test.mjs")]) {
    const source = fs.readFileSync(path.join(__dirname, rel), "utf8");
    assert.doesNotMatch(source, /dueDateText\s*\(/, `${rel} calls dueDateText`);
  }
});
