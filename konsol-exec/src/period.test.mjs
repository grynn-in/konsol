import { test } from "node:test";
import assert from "node:assert/strict";
import { formatPeriod, stepPeriod, canStep, yearChoices } from "./period.js";

const OPTIONS = {
	fiscal_years: ["2024", "2025", "2026"],
	fiscal_periods: [
		{ value: "7", label: "Jul" },
		{ value: "8", label: "Aug" },
		{ value: "9", label: "Sep" },
	],
};

test("formatPeriod reads as one thing, not a year and a month", () => {
	assert.equal(formatPeriod({ year: "2026", period: "9" }, OPTIONS), "Sep FY2026");
});

test("formatPeriod falls back to the year when no period is selected", () => {
	assert.equal(formatPeriod({ year: "2026", period: "" }, OPTIONS), "FY2026");
});

test("stepPeriod moves within a year", () => {
	assert.deepEqual(stepPeriod({ year: "2026", period: "8" }, OPTIONS, 1), { year: "2026", period: "9" });
	assert.deepEqual(stepPeriod({ year: "2026", period: "8" }, OPTIONS, -1), { year: "2026", period: "7" });
});

test("stepPeriod rolls forward into the next fiscal year", () => {
	assert.deepEqual(
		stepPeriod({ year: "2025", period: "9" }, OPTIONS, 1),
		{ year: "2026", period: "7" },
		"last period of a year steps to the first period of the next"
	);
});

test("stepPeriod rolls backward into the previous fiscal year", () => {
	assert.deepEqual(
		stepPeriod({ year: "2025", period: "7" }, OPTIONS, -1),
		{ year: "2024", period: "9" },
		"first period of a year steps to the last period of the previous"
	);
});

test("stepPeriod returns null past the ends rather than clamping", () => {
	assert.equal(stepPeriod({ year: "2026", period: "9" }, OPTIONS, 1), null, "no year after the newest");
	assert.equal(stepPeriod({ year: "2024", period: "7" }, OPTIONS, -1), null, "no year before the oldest");
});

test("canStep mirrors stepPeriod so a dead control can be disabled", () => {
	assert.equal(canStep({ year: "2026", period: "8" }, OPTIONS, 1), true);
	assert.equal(canStep({ year: "2026", period: "9" }, OPTIONS, 1), false);
});

test("stepPeriod is safe with empty or unknown input", () => {
	assert.equal(stepPeriod(null, OPTIONS, 1), null);
	assert.equal(stepPeriod({ year: "2026", period: "9" }, {}, 1), null);
	assert.equal(stepPeriod({ year: "1999", period: "9" }, OPTIONS, 1), null, "unknown year");
});

test("yearChoices lists newest first — finance looks backwards", () => {
	assert.deepEqual(yearChoices(OPTIONS), ["2026", "2025", "2024"]);
});
