// konsol#305 B09: freshness.test.mjs
//
// Exercises every state the freshness payload (A05/A16) can carry, plus the
// two failure paths: a state the model never declared, and a call that
// omits the time zone (the module must never read the machine's clock or
// zone itself — see the parallel-run override on B09).
import { test } from "node:test";
import assert from "node:assert/strict";
import { freshnessView } from "./freshness.js";

const TZ = "Europe/London"; // UTC+1 in September (BST)
const NOW = new Date("2026-09-25T12:00:00Z");

test("fresh: neutral, 'As of <time>' in the given time zone", () => {
  const payload = {
    state: "fresh",
    as_of: "2026-09-25T09:42:00Z", // 10:42 in Europe/London (BST)
    pending: 0,
    changed_since: [],
    last_failed: null,
  };
  const view = freshnessView(payload, NOW, TZ);
  assert.equal(view.tone, "neutral");
  assert.equal(view.text, "As of 10:42");
});

test("pending: blue, '<n> changes pending'", () => {
  const payload = {
    state: "pending",
    as_of: "2026-09-24T09:00:00Z",
    pending: 2,
    changed_since: [],
    last_failed: null,
  };
  const view = freshnessView(payload, NOW, TZ);
  assert.equal(view.tone, "blue");
  assert.equal(view.text, "2 changes pending");
});

test("stale: amber, names what changed since the last build", () => {
  const payload = {
    state: "stale",
    as_of: "2026-09-25T09:00:00Z", // 10:00 Europe/London, same day as `now`
    pending: 0,
    changed_since: ["Trial Balance Submission"],
    last_failed: null,
  };
  const view = freshnessView(payload, NOW, TZ);
  assert.equal(view.tone, "amber");
  assert.equal(
    view.text,
    "Numbers older than changes to Trial Balance Submission",
  );
  assert.equal(view.detail, "As of 10:00");
});

test("stale: a time on an earlier calendar day (in the given zone) carries its date", () => {
  const payload = {
    state: "stale",
    as_of: "2026-09-24T09:00:00Z", // 10:00 Europe/London, the day before `now`
    pending: 0,
    changed_since: ["Trial Balance Submission"],
    last_failed: null,
  };
  const view = freshnessView(payload, NOW, TZ);
  assert.equal(view.detail, "As of Sep 24, 10:00");
});

test("stale: several changed doctypes are listed together", () => {
  const payload = {
    state: "stale",
    as_of: "2026-09-24T09:00:00Z",
    pending: 0,
    changed_since: ["Historical Equity Rate", "Trial Balance Submission"],
    last_failed: null,
  };
  const view = freshnessView(payload, NOW, TZ);
  assert.equal(
    view.text,
    "Numbers older than changes to Historical Equity Rate, Trial Balance Submission",
  );
});

test("failed: red, 'Rebuild failed: <reason>'", () => {
  const payload = {
    state: "failed",
    as_of: "2026-09-20T09:00:00Z",
    pending: 0,
    changed_since: [],
    last_failed: {
      name: "BA-0099",
      at: "2026-09-25T08:00:00Z",
      reason: "dbt exit 2",
    },
  };
  const view = freshnessView(payload, NOW, TZ);
  assert.equal(view.tone, "red");
  assert.equal(view.text, "Rebuild failed: dbt exit 2");
});

test("never_built: red, 'Numbers have never been built'", () => {
  const payload = {
    state: "never_built",
    as_of: null,
    pending: 0,
    changed_since: [],
    last_failed: null,
  };
  const view = freshnessView(payload, NOW, TZ);
  assert.equal(view.tone, "red");
  assert.equal(view.text, "Numbers have never been built");
});

test("failure path: an unknown state renders red and names itself, never fresh", () => {
  const payload = {
    state: "x",
    as_of: null,
    pending: 0,
    changed_since: [],
    last_failed: null,
  };
  const view = freshnessView(payload, NOW, TZ);
  assert.equal(view.tone, "red");
  assert.notEqual(view.tone, "neutral");
  assert.equal(view.text, "Unknown freshness state: x");
});

test("failure path: no time zone is ever invented", () => {
  const payload = {
    state: "fresh",
    as_of: "2026-09-25T09:42:00Z",
    pending: 0,
    changed_since: [],
    last_failed: null,
  };
  assert.throws(() => freshnessView(payload, NOW, undefined), {
    message: /time zone/,
  });
  assert.throws(() => freshnessView(payload, NOW, ""), {
    message: /time zone/,
  });
});

test("a timestamp with no zone is refused, never read in the browser's zone (B09b)", () => {
  // Measured live 25 Sep 2026: A16 sent "2026-09-16T17:47:49.238943", no offset.
  // new Date() would read it in the browser's zone and show the wrong hour.
  const payload = { state: "fresh", as_of: "2026-09-16T17:47:49.238943", pending: 0, changed_since: [], last_failed: null };
  assert.throws(() => freshnessView(payload, new Date("2026-09-25T12:00:00Z"), "Europe/London"), /time zone/i);
});

// konsol#305 B28: "1 changes" reads wrong.
test("B28: pending 1 → '1 change pending' (singular)", () => {
  const view = freshnessView(
    { state: "pending", as_of: null, pending: 1, changed_since: [], last_failed: null },
    NOW,
    TZ,
  );
  assert.equal(view.text, "1 change pending");
});

test("B28: pending 2 → '2 changes pending' (plural)", () => {
  const view = freshnessView(
    { state: "pending", as_of: null, pending: 2, changed_since: [], last_failed: null },
    NOW,
    TZ,
  );
  assert.equal(view.text, "2 changes pending");
});
