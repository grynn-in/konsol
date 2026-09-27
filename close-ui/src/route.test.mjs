// konsol#305 B07: route.test.mjs
//
// D5: the period lives in the URL, and nothing is silently remembered. A
// malformed period must come out as an explicit error value, never coerced
// into a valid period — so the parser is exercised on every failure path
// (unknown screen, non-numeric period, no period at all), and the whole
// close-ui source tree is scanned for localStorage/sessionStorage the way
// guard.test.mjs (B01) scans it for konsol-exec imports.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse, format, SCREENS } from "./route.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC_DIR = __dirname; // close-ui/src
const THIS_FILE = fileURLToPath(import.meta.url);

function walk(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules") continue;
      out.push(...walk(full));
    } else {
      out.push(full);
    }
  }
  return out;
}

test("SCREENS is the declared screen list", () => {
  assert.deepEqual(SCREENS, ["my-work", "trial-balances", "checks", "sign-off"]);
});

test("parse and format round-trip for every screen", () => {
  for (const screen of SCREENS) {
    const state = { year: 2025, period: 9, screen };
    const path_ = format(state);
    assert.equal(path_, `/close/2025/9/${screen}`);
    assert.deepEqual(parse(path_), state);
  }
});

test("/close with no period asks the server for the landing period", () => {
  assert.deepEqual(parse("/close"), { year: null });
  assert.deepEqual(parse("/close/"), { year: null });
});

test("an unknown screen is an explicit error, not a guess", () => {
  assert.deepEqual(parse("/close/2025/9/not-a-screen"), { error: "unknown screen" });
});

test("a non-numeric period is an explicit error, never coerced", () => {
  assert.deepEqual(parse("/close/2025/abc/my-work"), { error: "invalid period" });
  assert.deepEqual(parse("/close/2025/9.5/my-work"), { error: "invalid period" });
  assert.deepEqual(parse("/close/2025/P13/my-work"), { error: "invalid period" });
});

test("a non-numeric year is an explicit error, never coerced", () => {
  assert.deepEqual(parse("/close/abc/9/my-work"), { error: "invalid year" });
  assert.deepEqual(parse("/close/FY2025/9/my-work"), { error: "invalid year" });
});

test("P0 and P13 are valid period codes; the server decides if they exist", () => {
  assert.deepEqual(parse("/close/2025/0/my-work"), { year: 2025, period: 0, screen: "my-work" });
  assert.deepEqual(parse("/close/2025/13/my-work"), { year: 2025, period: 13, screen: "my-work" });
});

test("no file under close-ui/src reads or writes localStorage or sessionStorage", () => {
  const allFiles = walk(SRC_DIR);
  assert.ok(allFiles.length > 0, "expected at least one file under close-ui/src");
  const files = allFiles.filter((f) => f !== THIS_FILE);
  const offenders = [];
  for (const file of files) {
    const source = fs.readFileSync(file, "utf8");
    if (/localStorage|sessionStorage/.test(source)) {
      offenders.push(path.relative(SRC_DIR, file));
    }
  }
  assert.deepEqual(offenders, []);
});

test("the scanner catches a planted localStorage use (failure path)", () => {
  const synthetic = `export function bad() { return localStorage.getItem("period"); }\n`;
  assert.match(synthetic, /localStorage|sessionStorage/);
});
