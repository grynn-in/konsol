// konsol#305 L01b: signedOffBar.test.mjs
//
// AppShell's "You are viewing a signed-off period" bar (showProvisionalBar)
// must show only when the viewed period is actually signed off — never
// merely because it differs from the Viewer's provisional period (L01:
// Viewers saw the bar on FY2025 P07 and P12, both Open and unsigned;
// showProvisionalBar tested only `period != provisional`).
import { test } from "node:test";
import assert from "node:assert/strict";
import { isSignedOffPeriod } from "./signedOffBar.js";

test("an Open, unsigned period is not signed off", () => {
  assert.equal(isSignedOffPeriod({ status: "Open", is_signed: false }), false);
});

test("a signed-off period (is_signed true) counts, whatever its status", () => {
  assert.equal(isSignedOffPeriod({ status: "Open", is_signed: true }), true);
});

test("a Closed period counts even when is_signed is false", () => {
  assert.equal(isSignedOffPeriod({ status: "Closed", is_signed: false }), true);
});

test("a Locked period counts even when is_signed is false", () => {
  assert.equal(isSignedOffPeriod({ status: "Locked", is_signed: false }), true);
});

test("no selected period (null/undefined) is never signed off", () => {
  assert.equal(isSignedOffPeriod(null), false);
  assert.equal(isSignedOffPeriod(undefined), false);
});
