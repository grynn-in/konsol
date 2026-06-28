import { test } from "node:test";
import assert from "node:assert/strict";

import { buildRunArgs } from "./params.js";

test("empty form → null definition + checkbox ints only", () => {
	const out = buildRunArgs({});
	assert.deepEqual(out, {
		definition: null,
		params: { full_refresh: 0, skip_sync: 0 },
	});
});

test("full form maps every field", () => {
	const out = buildRunArgs({
		fiscal_year: "2026",
		fiscal_period: "FP-2026-06",
		scope: "GROUP_CORP",
		definition: "Monthly Close",
		full_refresh: true,
		skip_sync: true,
	});
	assert.deepEqual(out, {
		definition: "Monthly Close",
		params: {
			fiscal_year: "2026",
			fiscal_period: "FP-2026-06",
			scope: "GROUP_CORP",
			full_refresh: 1,
			skip_sync: 1,
		},
	});
});

test("checkbox truthiness coerces to 0/1 ints", () => {
	const off = buildRunArgs({ full_refresh: false, skip_sync: 0 });
	assert.equal(off.params.full_refresh, 0);
	assert.equal(off.params.skip_sync, 0);
	const on = buildRunArgs({ full_refresh: 1, skip_sync: "yes" });
	assert.equal(on.params.full_refresh, 1);
	assert.equal(on.params.skip_sync, 1);
});

test("empty-string fiscal/scope fields are omitted", () => {
	const out = buildRunArgs({
		fiscal_year: "",
		fiscal_period: "",
		scope: "",
		definition: "",
	});
	assert.equal("fiscal_year" in out.params, false);
	assert.equal("fiscal_period" in out.params, false);
	assert.equal("scope" in out.params, false);
	assert.equal(out.definition, null);
});

test("definition falls back to null when absent", () => {
	assert.equal(buildRunArgs({ fiscal_year: "2026" }).definition, null);
});
