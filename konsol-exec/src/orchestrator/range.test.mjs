import { test } from "node:test";
import assert from "node:assert/strict";
import { withStageRange, describeStageRange, isFullRange } from "./params.js";

const STAGES = [
	{ id: "extract", label: "Extract" },
	{ id: "bronze", label: "Validate" },
	{ id: "silver", label: "Translate" },
	{ id: "gold", label: "Report" },
	{ id: "consolidate", label: "Consolidate" },
];

test("withStageRange sends stage IDS, not labels — that is the backend contract", () => {
	const p = withStageRange({ fiscal_year: "2026" }, STAGES, 2, 4);
	assert.equal(p.from_stage, "silver");
	assert.equal(p.to_stage, "consolidate");
	assert.equal(p.fiscal_year, "2026", "existing params survive");
});

test("withStageRange normalises a reversed range", () => {
	const p = withStageRange({}, STAGES, 4, 1);
	assert.equal(p.from_stage, "bronze", "lower index becomes from_stage");
	assert.equal(p.to_stage, "consolidate", "higher index becomes to_stage");
});

test("withStageRange clamps out-of-bounds indices", () => {
	const p = withStageRange({}, STAGES, -3, 99);
	assert.equal(p.from_stage, "extract");
	assert.equal(p.to_stage, "consolidate");
});

test("withStageRange leaves params alone when a process has no stages", () => {
	const p = withStageRange({ scope: "SGP" }, [], 0, 0);
	assert.deepEqual(p, { scope: "SGP" });
});

test("withStageRange does not mutate the params it is given", () => {
	const original = { scope: "SGP" };
	withStageRange(original, STAGES, 0, 1);
	assert.deepEqual(original, { scope: "SGP" });
});

test("describeStageRange names a single stage without a range", () => {
	assert.equal(describeStageRange(STAGES, 2, 2), "Rebuilds Translate only.");
});

test("describeStageRange counts and names the ends of a span", () => {
	assert.equal(
		describeStageRange(STAGES, 1, 3),
		"Rebuilds 3 stages, Validate through Report."
	);
});

test("isFullRange is true only when every stage is covered", () => {
	assert.equal(isFullRange(STAGES, 0, 4), true);
	assert.equal(isFullRange(STAGES, 0, 3), false);
	assert.equal(isFullRange(STAGES, 1, 4), false);
});
