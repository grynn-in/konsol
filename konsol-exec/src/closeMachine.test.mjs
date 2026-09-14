/**
 * #189 PR2 row 70e (review finding 4b): the period list always came from
 * `launch_options` for the newest fiscal year, whatever year a directly
 * opened step page showed — so `defaultPeriod`/`accountingPeriods` reflected
 * the wrong year's declared calendar. `launch_options` takes a `fiscal_year`
 * argument; the close flow must ask for the year it is showing.
 *
 * #189 PR2 row 70j (re-review finding 1, from 70e): that full-plane fetch
 * (snapshot + launch_options) must run only when the shown period actually
 * changes (SET_PERIOD). The poll tick, the Refresh button, and the refresh
 * after Start all re-read the SAME period's close state — they must not
 * re-ask launch_options every 2s, and a launch_options fetch that fails must
 * keep the previously loaded options rather than blanking them to null.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createActor, fromPromise, fromCallback } from "xstate";
import { closeMachine, loadPlane } from "./machines/closeMachine.js";

const flush = () => new Promise((r) => setTimeout(r, 0));

function start() {
	const calls = { plane: [], snapshot: [] };
	const machine = closeMachine.provide({
		actors: {
			fetchPlane: fromPromise(async ({ input }) => {
				calls.plane.push(input?.period ?? null);
				const period = input?.period || { year: "2026", period: "9" };
				return {
					data: { worker_healthy: true, processes: { demo: { machine_status: "running" } } },
					options: { fiscal_years: [period.year], fiscal_periods: [] },
					period,
				};
			}),
			fetchSnapshot: fromPromise(async ({ input }) => {
				calls.snapshot.push(input?.period ?? null);
				return { data: { worker_healthy: true, processes: {} }, period: input?.period ?? null };
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
	assert.deepEqual(calls.snapshot, [], "SET_PERIOD must not go through the snapshot-only refresh");
	actor.stop();
});

test("REFRESH re-fetches only the snapshot, never launch_options", async () => {
	const { actor, calls } = start();
	await flush(); await flush();

	actor.send({ type: "REFRESH" });
	await flush(); await flush();

	assert.deepEqual(calls.plane, [null], "REFRESH must not call fetchPlane/getLaunchOptions again");
	assert.deepEqual(calls.snapshot, [{ year: "2026", period: "9" }], "REFRESH used the snapshot-only actor");
	actor.stop();
});

test("the poll tick re-fetches only the snapshot, never launch_options", async () => {
	const { actor, calls } = start();
	await flush(); await flush();
	assert.ok(actor.getSnapshot().context.data.processes.demo, "first load left an active run, so polling is armed");

	actor.send({ type: "POLL_TICK" });
	await flush(); await flush();

	assert.deepEqual(calls.plane, [null], "the poll tick must not call fetchPlane/getLaunchOptions again");
	assert.deepEqual(calls.snapshot, [{ year: "2026", period: "9" }], "the poll tick used the snapshot-only actor");
	actor.stop();
});

test("a completed Start refreshes only the snapshot, never launch_options", async () => {
	const calls = { plane: [], snapshot: [] };
	const machine = closeMachine.provide({
		actors: {
			fetchPlane: fromPromise(async ({ input }) => {
				calls.plane.push(input?.period ?? null);
				const period = input?.period || { year: "2026", period: "9" };
				return { data: { worker_healthy: true, processes: {} }, options: { fiscal_years: [period.year], fiscal_periods: [] }, period };
			}),
			fetchSnapshot: fromPromise(async ({ input }) => {
				calls.snapshot.push(input?.period ?? null);
				return { data: { worker_healthy: true, processes: {} }, period: input?.period ?? null };
			}),
			startProcessActor: fromPromise(async () => ({ name: "run-1" })),
			pollTicker: fromCallback(() => () => {}),
		},
	});
	const actor = createActor(machine).start();
	await flush(); await flush();

	actor.send({ type: "START_PROCESS", processId: "p1" });
	await flush(); await flush();

	assert.deepEqual(calls.plane, [null], "a completed Start must not call fetchPlane/getLaunchOptions again");
	assert.deepEqual(calls.snapshot, [{ year: "2026", period: "9" }], "the post-Start refresh used the snapshot-only actor");
	actor.stop();
});

// Unit-level: `loadPlane` (what SET_PERIOD's full reload invokes) must itself
// pass the shown year through to `getLaunchOptions`, not always ask for the
// newest, and must fall back to the previously loaded options — never null —
// when the launch_options request itself fails.
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

test("loadPlane keeps the previous options when the launch_options fetch fails for the SAME year", async () => {
	const savedFetch = globalThis.fetch;
	globalThis.fetch = async (url, init) => {
		if (url.includes("launch_options")) {
			throw new Error("network down");
		}
		return savedFetch(url, init);
	};
	const previousOptions = { fiscal_years: ["2025"], fiscal_periods: [{ value: "7" }] };
	try {
		const result = await loadPlane({ year: "2025", period: "7" }, previousOptions, "2025");
		assert.deepEqual(result.options, previousOptions, "a failed refetch for the same year keeps the previous options, not null");
	} finally {
		globalThis.fetch = savedFetch;
	}
});

// #189 PR2 row 70p (re-review 2 finding 1, from 70j): the previous-options
// fallback must not carry a DIFFERENT fiscal year's options over onto the
// newly requested year (e.g. showing FY2025 P7 labelled with FY2026's
// periods) just because a fetch happened to fail. Only a same-year retry may
// reuse what was already loaded.
test("loadPlane discards previous options from a DIFFERENT fiscal year when the refetch fails", async () => {
	const savedFetch = globalThis.fetch;
	globalThis.fetch = async (url, init) => {
		if (url.includes("launch_options")) {
			throw new Error("network down");
		}
		return savedFetch(url, init);
	};
	const previousOptions = { fiscal_years: ["2024"], fiscal_periods: [{ value: "7" }] };
	try {
		const result = await loadPlane({ year: "2025", period: "7" }, previousOptions, "2024");
		assert.equal(result.options, null, "a failed refetch for a different year must not reuse the old year's options");
	} finally {
		globalThis.fetch = savedFetch;
	}
});
