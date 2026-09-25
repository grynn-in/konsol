// konsol#305 B15: signoff.test.mjs
//
// Exercises summaryView against A21's real `signoff_model.summary` shape
// (parallel-run override, 25 Sep 2026): `action`, `label`,
// `gates{config_gaps, order, completeness, messages}`, `checks`,
// `acknowledgements{names, total, unlisted}`, `on_behalf{labels, unknown}`,
// `exceptions[]`, `covers`, `previous[]`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { summaryView, messageLines } from "./signoff.js";

function gates(overrides = {}) {
  return {
    config_gaps: [],
    order: null,
    completeness: null,
    messages: [],
    ...overrides,
  };
}

function checks(overrides = {}) {
  return {
    run: null,
    status: null,
    signoff_status: null,
    failed: null,
    errored: null,
    ...overrides,
  };
}

function acknowledgements(overrides = {}) {
  return { names: [], total: null, unlisted: null, ...overrides };
}

function onBehalf(overrides = {}) {
  return { labels: [], unknown: [], ...overrides };
}

function summary(overrides = {}) {
  return {
    action: "run_checks",
    label: "Run the checks",
    gates: gates(),
    checks: checks(),
    acknowledgements: acknowledgements(),
    on_behalf: onBehalf(),
    exceptions: [],
    covers: [],
    previous: [],
    ...overrides,
  };
}

test("every section is rendered from a full summary", () => {
  const view = summaryView(
    summary({
      action: "acknowledge",
      label: "Acknowledge the warnings and sign off",
      gates: gates({
        config_gaps: [{ code: "frequency_undeclared", message: "Set the frequency on ZZA." }],
      }),
      checks: checks({ run: "AR-0001", status: "Amber", signoff_status: "Not Signed Off", failed: 0, errored: 0 }),
      acknowledgements: acknowledgements({ names: ["assert_fx_balances"], total: 3, unlisted: 2 }),
      on_behalf: onBehalf({ labels: ["by alice@example.com for ZZA"], unknown: [] }),
      exceptions: [{ entity: "ZZB", reason: "Entity dormant this period", declared_by: "bob@example.com" }],
      covers: ["ZZC: covers P08–P09"],
      previous: [{ code: "P07", status: "Closed", signoff: "Signed Off" }],
    }),
  );

  assert.equal(view.action, "acknowledge");
  assert.equal(view.label, "Acknowledge the warnings and sign off");
  assert.deepEqual(view.gates.rows, ["Set the frequency on ZZA."]);
  assert.deepEqual(view.checks.rows, [
    "Run: AR-0001",
    "Status: Amber",
    "Sign-off status: Not Signed Off",
    "Failed: 0",
    "Errored: 0",
  ]);
  assert.deepEqual(view.acknowledgements.rows, [
    "Acknowledged: assert_fx_balances",
    "Warned in total: 3",
    "Not listed above: 2",
  ]);
  assert.deepEqual(view.onBehalf.rows, ["by alice@example.com for ZZA"]);
  assert.deepEqual(view.exceptions.rows, ["ZZB: Entity dormant this period (declared by bob@example.com)"]);
  assert.deepEqual(view.covers.rows, ["ZZC: covers P08–P09"]);
  assert.deepEqual(view.previous.rows, ["P07: Closed, Signed Off"]);
});

test("every section is empty and says None on a bare summary", () => {
  const view = summaryView(summary());
  assert.deepEqual(view.gates.rows, ["None"]);
  assert.equal(view.gates.empty, true);
  assert.deepEqual(view.checks.rows, ["None"]);
  assert.equal(view.checks.empty, true);
  assert.deepEqual(view.acknowledgements.rows, ["None"]);
  assert.equal(view.acknowledgements.empty, true);
  assert.deepEqual(view.onBehalf.rows, ["None"]);
  assert.equal(view.onBehalf.empty, true);
  assert.deepEqual(view.exceptions.rows, ["None"]);
  assert.equal(view.exceptions.empty, true);
  assert.deepEqual(view.covers.rows, ["None"]);
  assert.equal(view.covers.empty, true);
  assert.deepEqual(view.previous.rows, ["None"]);
  assert.equal(view.previous.empty, true);
});

test("configuration gaps are listed first, before order, before completeness", () => {
  const view = summaryView(
    summary({
      gates: gates({
        config_gaps: [{ code: "frequency_undeclared", message: "Declare frequencies first." }],
        order: { blocking: "P07", periods: ["P07", "P08"], message: "Sign off P07 first" },
        completeness: { missing: ["ZZA"], message: "No trial balance from ZZA." },
      }),
    }),
  );
  assert.deepEqual(view.gates.rows, [
    "Declare frequencies first.",
    "Sign off P07 first",
    "No trial balance from ZZA.",
  ]);
});

test("a server message joined with <br> is split into separate escaped lines", () => {
  assert.deepEqual(
    messageLines("First problem<br>Second problem"),
    ["First problem", "Second problem"],
  );
  assert.deepEqual(
    messageLines("A<br/>B<br />C"),
    ["A", "B", "C"],
  );
});

test("a message is left as plain text for a text binding to escape", () => {
  assert.deepEqual(messageLines("A & B <script>"), ["A & B <script>"]);
});

test("<br>-joined gate messages are split and escaped inside the gates section", () => {
  const view = summaryView(
    summary({
      gates: gates({
        order: { blocking: "P07", periods: ["P07"], message: "Sign off P07 first<br>Also fix P06" },
      }),
    }),
  );
  assert.deepEqual(view.gates.rows, ["Sign off P07 first", "Also fix P06"]);
});

test("an unknown failed/errored count is shown as unknown, never 0", () => {
  const view = summaryView(
    summary({
      checks: checks({ run: "AR-0002", status: "Running", signoff_status: "Not Signed Off", failed: null, errored: null }),
    }),
  );
  assert.deepEqual(view.checks.rows, [
    "Run: AR-0002",
    "Status: Running",
    "Sign-off status: Not Signed Off",
    "Failed: unknown",
    "Errored: unknown",
  ]);
});

test("an unknown acknowledgement total is shown as unknown, never 0, even with names listed", () => {
  const view = summaryView(
    summary({
      acknowledgements: acknowledgements({ names: ["assert_fx_balances"], total: null, unlisted: null }),
    }),
  );
  assert.deepEqual(view.acknowledgements.rows, [
    "Acknowledged: assert_fx_balances",
    "Warned in total: unknown",
    "Not listed above: unknown",
  ]);
});

test("an on-behalf upload with no tracked flag shows as unknown, not silently dropped", () => {
  const view = summaryView(
    summary({
      on_behalf: onBehalf({
        labels: [],
        unknown: ["ZZA: by carol@example.com; on-behalf not recorded (uploaded before it was tracked)"],
      }),
    }),
  );
  assert.deepEqual(view.onBehalf.rows, [
    "ZZA: by carol@example.com; on-behalf not recorded (uploaded before it was tracked)",
  ]);
  assert.equal(view.onBehalf.empty, false);
});

test("failure path: an unknown action throws", () => {
  assert.throws(
    () => summaryView(summary({ action: "teleport" })),
    /Unknown sign-off action: teleport/,
  );
});

test("B15b: lines stay plain text; the screen's text binding does the escaping", () => {
	// Escaping here AND in Vue's {{ }} would show "&amp;" to the user.
	assert.deepEqual(messageLines("R&D costs<br>P&L <check>"), ["R&D costs", "P&L <check>"]);
});

test("B15b: no screen renders server text as HTML (no v-html anywhere in src)", async () => {
	const { readdir, readFile } = await import("node:fs/promises");
	const { join } = await import("node:path");
	const root = new URL(".", import.meta.url).pathname;
	const bad = [];
	async function walk(dir) {
		for (const e of await readdir(dir, { withFileTypes: true })) {
			const p = join(dir, e.name);
			if (e.isDirectory()) await walk(p);
			else if (p.endsWith(".vue") && (await readFile(p, "utf8")).includes("v-html")) bad.push(p);
		}
	}
	await walk(root);
	assert.deepEqual(bad, []);
});
