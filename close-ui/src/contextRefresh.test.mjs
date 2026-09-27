// konsol#305 B31: contextRefresh.test.mjs
//
// The header's "Checks: <status>" comes from the period context (A15
// get_context), which the shell loads once per period. C1 run 2: after a run
// turned Amber the header still said "Checks: Not run" until a reload. The
// rule: when the Checks screen sees the latest run reach a terminal state, or
// a new run appear, it asks the shell to reload the context — once. A poll
// that changes nothing asks for nothing.
import { test } from "node:test";
import assert from "node:assert/strict";
import { contextReloadNeeded, CONTEXT_RELOAD, IN_FLIGHT, TERMINAL } from "./contextRefresh.js";

const run = (name, status) => ({ name, status, completed_at: null });

test("the run states are the Assertion Run options, split into in flight and terminal", () => {
  assert.deepEqual([...IN_FLIGHT], ["Queued", "Running"]);
  assert.deepEqual([...TERMINAL], ["Green", "Amber", "Red", "Error"]);
});

test("the first observation of a period never asks for a reload (the context was just loaded)", () => {
  assert.equal(contextReloadNeeded(undefined, null), false);
  assert.equal(contextReloadNeeded(undefined, run("AR-1", "Amber")), false);
  assert.equal(contextReloadNeeded(undefined, run("AR-1", "Running")), false);
});

test("every terminal transition of the same run asks for a reload", () => {
  for (const from of IN_FLIGHT) {
    for (const to of TERMINAL) {
      assert.equal(contextReloadNeeded(run("AR-1", from), run("AR-1", to)), true, `${from} -> ${to}`);
    }
  }
});

test("a reload is asked for once: the next poll of the same terminal run asks for nothing", () => {
  // Replay the screen's poll sequence and count the requests.
  const polls = [run("AR-1", "Queued"), run("AR-1", "Running"), run("AR-1", "Running"), run("AR-1", "Amber"), run("AR-1", "Amber"), run("AR-1", "Amber")];
  let last;
  let requests = 0;
  for (const next of polls) {
    if (contextReloadNeeded(last, next)) requests++;
    last = next;
  }
  assert.equal(requests, 1);
});

test("non-terminal polls ask for nothing", () => {
  assert.equal(contextReloadNeeded(run("AR-1", "Queued"), run("AR-1", "Queued")), false);
  assert.equal(contextReloadNeeded(run("AR-1", "Queued"), run("AR-1", "Running")), false);
  assert.equal(contextReloadNeeded(run("AR-1", "Running"), run("AR-1", "Running")), false);
  assert.equal(contextReloadNeeded(null, null), false);
  for (const s of TERMINAL) {
    assert.equal(contextReloadNeeded(run("AR-1", s), run("AR-1", s)), false, `${s} -> ${s}`);
  }
});

test("a new run starting asks for a reload (the header shows it is running)", () => {
  assert.equal(contextReloadNeeded(null, run("AR-1", "Queued")), true, "first run of the period");
  assert.equal(contextReloadNeeded(run("AR-1", "Amber"), run("AR-2", "Queued")), true, "a rerun");
  assert.equal(contextReloadNeeded(run("AR-1", "Amber"), run("AR-2", "Green")), true, "a rerun finished between polls");
});

test("failure path: an unknown run status is refused, never treated as terminal or in flight", () => {
  assert.throws(() => contextReloadNeeded(run("AR-1", "Running"), run("AR-1", "Done")), /Unknown check run status: Done/);
  assert.throws(() => contextReloadNeeded(undefined, run("AR-1", "")), /Unknown check run status/);
});

test("the provide/inject key is a named string the shell and the screen share", () => {
  assert.equal(typeof CONTEXT_RELOAD, "string");
  assert.match(CONTEXT_RELOAD, /context/i);
});
