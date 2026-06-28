import { test } from "node:test";
import assert from "node:assert/strict";

import { statusTone, isTerminal, isRunning } from "./status.js";

test("statusTone maps every status to the right tone", () => {
	assert.equal(statusTone("Success"), "green");
	assert.equal(statusTone("Completed"), "green");
	assert.equal(statusTone("Failed"), "red");
	assert.equal(statusTone("Cancelled"), "red");
	assert.equal(statusTone("Running"), "blue");
	assert.equal(statusTone("Queued"), "amber");
	assert.equal(statusTone("Pending"), "gray");
	assert.equal(statusTone("Skipped"), "gray");
});

test("statusTone defaults to gray for unknown/missing", () => {
	assert.equal(statusTone("Whatever"), "gray");
	assert.equal(statusTone(""), "gray");
	assert.equal(statusTone(undefined), "gray");
	assert.equal(statusTone(null), "gray");
});

test("isTerminal is true only for terminal statuses", () => {
	for (const s of ["Completed", "Failed", "Cancelled", "Success"]) {
		assert.equal(isTerminal(s), true, `${s} should be terminal`);
	}
	for (const s of ["Pending", "Running", "Queued", "Skipped", "Whatever", undefined]) {
		assert.equal(isTerminal(s), false, `${s} should not be terminal`);
	}
});

test("isRunning is true only for Running", () => {
	assert.equal(isRunning("Running"), true);
	for (const s of ["Pending", "Queued", "Success", "Failed", "Cancelled", "Skipped", undefined]) {
		assert.equal(isRunning(s), false, `${s} should not be running`);
	}
});
