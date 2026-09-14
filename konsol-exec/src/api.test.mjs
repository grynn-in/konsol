import { test } from "node:test";
import assert from "node:assert/strict";

// api.js reads window.csrf_token and calls fetch when a request is made.
globalThis.window = globalThis.window || {};
const calls = [];
globalThis.fetch = async (url, init) => {
	calls.push({ url, body: JSON.parse(init.body) });
	return { ok: true, status: 200, json: async () => ({ message: { status: "Open" } }) };
};

const { setPeriodStatus } = await import("./api.js");

test("setPeriodStatus sends the reason when one is given", async () => {
	calls.length = 0;
	await setPeriodStatus(2026, 9, "Open", "late accrual");
	assert.equal(calls.length, 1);
	assert.equal(calls[0].url, "/api/method/konsol.control_api.set_period_status");
	assert.deepEqual(calls[0].body, {
		fiscal_year: "2026",
		fiscal_period: "9",
		status: "Open",
		reason: "late accrual",
	});
});

test("setPeriodStatus sends no reason when none is given", async () => {
	calls.length = 0;
	await setPeriodStatus("2026", "9", "Closed");
	assert.deepEqual(calls[0].body, { fiscal_year: "2026", fiscal_period: "9", status: "Closed" });
});
