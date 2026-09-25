// konsol#305 B13: checks.test.mjs
//
// Exercises checksView against A26's real payload shape (parallel-run
// override, 25 Sep 2026): `{latest, staleness, staleness_note, as_of,
// results_run, domains, failures, warnings, can_run}`, each cause
// `{assertion, title, status, rows_failed, severity, message, description,
// description_missing}`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { checksView } from "./checks.js";

function cause(overrides = {}) {
  return {
    assertion: "assert_fx_balances",
    title: "Fx balances",
    status: "Fail",
    rows_failed: 3,
    severity: "error",
    message: "3 rows out of balance",
    description: null,
    description_missing: true,
    ...overrides,
  };
}

function domain(overrides = {}) {
  return {
    domain: "FX",
    count: 0,
    warn_count: 0,
    failures: [],
    warnings: [],
    ...overrides,
  };
}

function payload(overrides = {}) {
  return {
    latest: { name: "AR-0001", status: "Completed", completed_at: "2026-09-25T09:00:00+00:00" },
    staleness: "current",
    staleness_note: null,
    as_of: "2026-09-25T08:00:00+00:00",
    results_run: "AR-0001",
    domains: [],
    failures: 0,
    warnings: 0,
    can_run: false,
    ...overrides,
  };
}

test("stale: banner tells the Analyst to run checks again", () => {
  const view = checksView(payload({ staleness: "stale" }));
  assert.equal(view.banner, "Checks are older than the numbers — run them again");
});

test("running: banner says checks are running", () => {
  const view = checksView(payload({
    staleness: "running",
    latest: { name: "AR-0002", status: "Running", completed_at: null },
    results_run: "AR-0002",
  }));
  assert.equal(view.banner, "Checks running…");
});

test("not_run: banner says no checks ran for the period", () => {
  const view = checksView(payload({ staleness: "not_run", latest: null, results_run: null }));
  assert.equal(view.banner, "No checks run for this period");
});

test("current: no banner is needed", () => {
  const view = checksView(payload({ staleness: "current" }));
  assert.equal(view.banner, null);
});

test("results from an earlier run: the banner names it (results_run differs from latest.name)", () => {
  const view = checksView(payload({
    staleness: "running",
    latest: { name: "AR-0003", status: "Running", completed_at: null },
    results_run: "AR-0002",
  }));
  assert.equal(view.banner, "Checks running… These results are from an earlier run.");
});

test("results from an earlier run with an otherwise-empty banner: the note stands alone", () => {
  // Defensive: latest_close_run should not diverge from latest while current,
  // but the module never assumes that — it compares the two names it is given.
  const view = checksView(payload({
    staleness: "current",
    latest: { name: "AR-0003", status: "Completed", completed_at: "2026-09-25T09:00:00+00:00" },
    results_run: "AR-0002",
  }));
  assert.equal(view.banner, "These results are from an earlier run.");
});

test("cause text comes from the description when one is declared", () => {
  const d = domain({
    failures: [cause({ description: "Fx balances must net to zero.", description_missing: false })],
  });
  const view = checksView(payload({ domains: [d] }));
  assert.equal(view.domains[0].causes[0].text, "Fx balances must net to zero.");
});

test("cause text is the declared-missing text when none is declared (Problems 4)", () => {
  const d = domain({
    failures: [cause({ assertion: "assert_fx_balances", description: null, description_missing: true })],
  });
  const view = checksView(payload({ domains: [d] }));
  assert.equal(view.domains[0].causes[0].text, "No description declared for assert_fx_balances");
});

test("a domain's causes hold its failures and warnings together, under the domain's name and count", () => {
  const d = domain({
    domain: "FX",
    failures: [cause({ assertion: "assert_a", title: "A", status: "Fail" })],
    warnings: [cause({ assertion: "assert_b", title: "B", status: "Warn" })],
  });
  const view = checksView(payload({ domains: [d] }));
  assert.equal(view.domains[0].name, "FX");
  assert.equal(view.domains[0].count, 2);
  assert.deepEqual(view.domains[0].causes.map((c) => c.title), ["A", "B"]);
});

test("a cause's rows comes from rows_failed", () => {
  const d = domain({ failures: [cause({ rows_failed: 7 })] });
  const view = checksView(payload({ domains: [d] }));
  assert.equal(view.domains[0].causes[0].rows, 7);
});

test("canRun passes through can_run", () => {
  assert.equal(checksView(payload({ can_run: true })).canRun, true);
  assert.equal(checksView(payload({ can_run: false })).canRun, false);
});

test("no domains: an empty list, not an error", () => {
  const view = checksView(payload({ staleness: "not_run", latest: null, results_run: null, domains: [] }));
  assert.deepEqual(view.domains, []);
});

test("failure path: an unknown staleness throws, never falls through as current", () => {
  assert.throws(
    () => checksView(payload({ staleness: "weird" })),
    /Unknown staleness state: weird/,
  );
});
