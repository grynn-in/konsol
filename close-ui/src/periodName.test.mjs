// konsol#305 review-w5 U1 (remaining places): periodName.test.mjs — the one
// "FY2025 P07" format every screen uses. The live `period_code` is "P07"
// alone, ambiguous across a year boundary, so a label is built from the
// fiscal year and period, never from the code.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { periodName } from "./periodName.js";

test("periodName: the fiscal year and the two-digit period", () => {
	assert.equal(periodName(2025, 7), "FY2025 P07");
	assert.equal(periodName(2024, 12), "FY2024 P12");
	assert.equal(periodName(2025, 0), "FY2025 P00");
});

test("periodName: failure path — a missing or non-integer part throws, never 'FYundefined'", () => {
	assert.throws(() => periodName(undefined, 7), /fiscal year/);
	assert.throws(() => periodName(2025, null), /fiscal period/);
	assert.throws(() => periodName("2025", 7), /fiscal year/);
	assert.throws(() => periodName(2025, 7.5), /fiscal period/);
});

test("periodName is the only copy of the format in close-ui/src (no screen builds its own)", () => {
	const root = path.dirname(fileURLToPath(import.meta.url));
	const offenders = [];
	const walk = (dir) => {
		for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
			const full = path.join(dir, entry.name);
			if (entry.isDirectory()) walk(full);
			else if (/\.(js|vue)$/.test(entry.name) && entry.name !== "periodName.js") {
				const src = fs.readFileSync(full, "utf8");
				if (/padStart\(2,\s*"0"\)/.test(src)) offenders.push(path.relative(root, full));
			}
		}
	};
	walk(root);
	assert.deepEqual(offenders, []);
});
