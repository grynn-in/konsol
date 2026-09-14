/**
 * #189 PR2 row 70e (review finding 4b): the period list always came from
 * `launch_options` for the newest fiscal year, whatever year a directly
 * opened step page showed — so `defaultPeriod`/`accountingPeriods` reflected
 * the wrong year's declared calendar. `launch_options` takes a `fiscal_year`
 * argument; the close flow must ask for the year it is showing.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createActor, fromPromise, fromCallback } from "xstate";
import { closeMachine } from "./machines/closeMachine.js";

const flush = () => new Promise((r) => setTimeout(r, 0));

function start() {
	const calls = { plane: [] };
	const machine = closeMachine.provide({
		actors: {
			fetchPlane: fromPromise(async ({ input }) => {
				calls.plane.push(input?.period ?? null);
				const period = input?.period || { year: "2026", period: "9" };
				return { data: { worker_healthy: true }, options: { fiscal_years: [period.year], fiscal_periods: [] }, period };
			}),
			pollTicker: fromCallback(() => () => {}),
		},
	});
	return { actor: createActor(machine).start(), calls };
}

test("SET_PERIOD re-fetches the plane for the newly shown year, not the last one loaded", async () => {
	const { actor, calls } = start();
	await flush(); await flush();
	assert.ok(actor.getSnapshot().matches("ready"), "loads to ready first");
	assert.deepEqual(calls.plane, [null], "the first load carries no known period yet");

	actor.send({ type: "SET_PERIOD", year: "2025", period: "7" });
	await flush(); await flush();

	assert.deepEqual(
		calls.plane[calls.plane.length - 1],
		{ year: "2025", period: "7" },
		"SET_PERIOD must re-invoke the plane fetch (which asks launch_options) for the shown year"
	);
	actor.stop();
});

// Unit-level: `loadPlane` (what the flow above invokes) must itself pass the
// shown year through to `getLaunchOptions`, not always ask for the newest.
globalThis.window = globalThis.window || {};
const httpCalls = [];
globalThis.fetch = async (url, init) => {
	const body = init?.body ? JSON.parse(init.body) : null;
	httpCalls.push({ url, body });
	if (url.includes("launch_options")) {
		return {
			ok: true,
			status: 200,
			json: async () => ({ message: { fiscal_years: ["2026", "2025"], fiscal_periods: [] } }),
		};
	}
	return { ok: true, status: 200, json: async () => ({ message: { period: {}, worker_healthy: true } }) };
};

const { loadPlane } = await import("./machines/closeMachine.js");

test("loadPlane asks launch_options for the shown year, not the newest declared one", async () => {
	httpCalls.length = 0;
	await loadPlane({ year: "2025", period: "7" });
	const launchCall = httpCalls.find((c) => c.url.includes("launch_options"));
	assert.ok(launchCall, "launch_options was requested");
	assert.deepEqual(launchCall.body, { fiscal_year: "2025" });
});

test("loadPlane asks for the newest year when no period is known yet", async () => {
	httpCalls.length = 0;
	await loadPlane(null);
	const launchCall = httpCalls.find((c) => c.url.includes("launch_options"));
	assert.ok(launchCall, "launch_options was requested");
	assert.deepEqual(launchCall.body, {});
});
