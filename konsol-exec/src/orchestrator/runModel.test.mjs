import { test } from "node:test";
import assert from "node:assert/strict";

import { normalizeRun, progressPct, orderSteps } from "./runModel.js";

test("normalizeRun maps a Pipeline Run doc + child rows to camelCase", () => {
	const doc = {
		name: "PR-0001",
		status: "Running",
		steps: [
			{
				step_id: "sync",
				step_type: "airbyte",
				status: "Success",
				started_at: "2026-06-28 10:00:00",
				ended_at: "2026-06-28 10:05:00",
				rows: 511789,
				output: "synced",
				error: "",
			},
			{
				step_id: "dbt",
				step_type: "dbt",
				status: "Running",
				started_at: "2026-06-28 10:05:00",
				ended_at: null,
				rows: 0,
				output: "",
				error: "",
			},
		],
	};

	const run = normalizeRun(doc);
	assert.equal(run.name, "PR-0001");
	assert.equal(run.status, "Running");
	assert.equal(run.steps.length, 2);

	const first = run.steps[0];
	assert.equal(first.id, "sync");
	assert.equal(first.type, "airbyte");
	assert.equal(first.status, "Success");
	assert.equal(first.startedAt, "2026-06-28 10:00:00");
	assert.equal(first.endedAt, "2026-06-28 10:05:00");
	assert.equal(first.rows, 511789);
	assert.equal(first.output, "synced");
	assert.equal(first.error, "");

	const second = run.steps[1];
	assert.equal(second.id, "dbt");
	assert.equal(second.endedAt, null);
});

test("normalizeRun tolerates missing steps, missing fields, and null doc", () => {
	const noSteps = normalizeRun({ name: "PR-0002", status: "Pending" });
	assert.equal(noSteps.name, "PR-0002");
	assert.deepEqual(noSteps.steps, []);

	const sparse = normalizeRun({ steps: [{ step_id: "x" }] });
	assert.equal(sparse.name, null);
	assert.equal(sparse.status, null);
	assert.equal(sparse.steps[0].id, "x");
	assert.equal(sparse.steps[0].type, null);
	assert.equal(sparse.steps[0].status, null);
	assert.equal(sparse.steps[0].startedAt, null);
	assert.equal(sparse.steps[0].rows, 0);
	assert.equal(sparse.steps[0].output, "");
	assert.equal(sparse.steps[0].error, "");

	const empty = normalizeRun(null);
	assert.equal(empty.name, null);
	assert.equal(empty.status, null);
	assert.deepEqual(empty.steps, []);
});

test("progressPct = terminal-success / total as 0-100", () => {
	const steps = [
		{ status: "Success" },
		{ status: "Completed" },
		{ status: "Running" },
		{ status: "Pending" },
	];
	assert.equal(progressPct(steps), 50);

	assert.equal(progressPct([{ status: "Success" }, { status: "Success" }]), 100);
	assert.equal(progressPct([]), 0);
	assert.equal(progressPct(undefined), 0);
	assert.equal(progressPct([{ status: "Failed" }, { status: "Cancelled" }]), 0);
});

test("orderSteps returns stable input order without mutating input", () => {
	const steps = [{ id: "a" }, { id: "b" }, { id: "c" }];
	const out = orderSteps(steps);
	assert.deepEqual(out.map((s) => s.id), ["a", "b", "c"]);
	// no mutation
	assert.notEqual(out, steps);
	assert.deepEqual(steps.map((s) => s.id), ["a", "b", "c"]);

	assert.deepEqual(orderSteps(undefined), []);
});
