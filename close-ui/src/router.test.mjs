// konsol#305 B16: router.test.mjs
//
// `landingPath` is the only part of router.js safe to import under plain
// `node --test`: everything else in the file either calls `createWebHistory`
// (needs `window.history`, absent in Node) or `import.meta.glob` (a Vite
// build-time macro, not a runtime function outside a Vite build). Both are
// deferred inside `createCloseRouter`, which this test never calls — mirrors
// route.test.mjs (B07) keeping vue-router itself out of the pure test.
//
// D5: `context.landing.period` null names the reason instead of guessing a
// period — an undeclared first close, or a Viewer who has signed nothing.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { landingPath } from "./router.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROUTER_JS = path.join(__dirname, "router.js");

test("landingPath for a normal landing lands on My work", () => {
  const context = {
    landing: { period: [2025, 9], rule: "current", reason: null },
  };
  assert.equal(landingPath(context), "/close/2025/9/my-work");
});

test("landingPath for a Viewer's latest signed period", () => {
  const context = {
    landing: {
      period: [2025, 6],
      rule: "latest_signed",
      reason: null,
      provisional: { period: [2025, 9], rule: "current", reason: null },
    },
  };
  assert.equal(landingPath(context), "/close/2025/6/my-work");
});

test("failure path: an undeclared first close names the reason, never a guessed period", () => {
  const context = {
    landing: { period: null, rule: "first_close_undeclared", reason: "first_close_undeclared" },
  };
  assert.equal(landingPath(context), null); // B16b: stay on /close; the shell shows the reason
});

test("failure path: a Viewer who has signed nothing names the reason (stays on /close)", () => {
  const reason =
    "No period has been signed off yet. Switch to the provisional period to see the close in progress.";
  const context = {
    landing: {
      period: null,
      rule: null,
      reason,
      provisional: { period: [2025, 9], rule: "current", reason: null },
    },
  };
  // B16b: no period → stay on /close; the shell shows the reason and the provisional switch.
  assert.equal(landingPath(context), null);
});

test("router.js resolves screens through import.meta.glob(\"./screens/*.vue\")", () => {
  const source = fs.readFileSync(ROUTER_JS, "utf8");
  assert.match(source, /import\.meta\.glob\(\s*["']\.\/screens\/\*\.vue["']\s*\)/);
});
