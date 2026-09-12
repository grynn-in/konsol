import { test } from "node:test";
import assert from "node:assert/strict";
import { summarize, rowStatus, visibleRows, loadLabel, progress, periodText, money } from "./uploads.js";

const report = [
	{ entity: "AMDE", fiscal_year: 2025, fiscal_period: 12, rows: 10, ok: true, errors: [] },
	{ entity: "AMDE", fiscal_year: 2024, fiscal_period: 12, rows: 8, ok: false, errors: ["Debits do not equal credits", "Unknown 9999"] },
	{ entity: "AMUS", fiscal_year: 2025, fiscal_period: 12, rows: 12, ok: true, errors: [], loaded: "TBS-00007" },
	{ entity: "AMHQ", fiscal_year: 2025, fiscal_period: 12, rows: 5, ok: true, errors: [], load_error: "Period closed" },
];

test("summary counts entity-periods, entities, lines and outcomes", () => {
	// the loaded AMUS row is done, not ready; AMHQ failed to load and is ready to retry
	assert.deepEqual(summarize(report), { groups: 4, ready: 2, problems: 1, lines: 35, entities: 3, loaded: 1, failed: 1 });
	assert.equal(summarize(null).groups, 0);
});

test("each row says ready, problem, loaded or failed", () => {
	assert.deepEqual(rowStatus(report[0]), { state: "ready", label: "Ready", note: "" });
	assert.equal(rowStatus(report[1]).note, "Debits do not equal credits · Unknown 9999");
	assert.equal(rowStatus(report[2]).label, "Loaded");
	assert.equal(rowStatus(report[3]).state, "error");
});

test("problems-only keeps refused and failed rows", () => {
	assert.deepEqual(visibleRows(report, true).map((r) => r.entity), ["AMDE", "AMHQ"]);
	assert.equal(visibleRows(report, false).length, 4);
});

test("the load button says what it will do", () => {
	assert.equal(loadLabel({ ready: 3, problems: 1 }), "Load 3 trial balances, skip 1");
	assert.equal(loadLabel({ ready: 1, problems: 0 }), "Load 1 trial balance");
	assert.equal(loadLabel({ ready: 0, problems: 4 }), null);
});

test("progress and formatting", () => {
	assert.equal(progress({ valid_count: 4, loaded_count: 1, failed_count: 1 }), 50);
	assert.equal(progress({ valid_count: 0 }), 0);
	assert.equal(periodText(report[0]), "FY2025 P12");
	assert.equal(money(1234.5), "1,234.50");
});
