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

// #189 PR2 row 70w (re-review 3 nit 3, from 70p): `sameYear` compared
// `requestedYear === previousOptionsYear` with `===`, so a number year
// (e.g. from a numeric period argument) against the string year `loadPlane`
// itself records in `optionsYear` never matched, and a failed refetch wrongly
// dropped the previous options even though it was really the same year.
test("loadPlane keeps the previous options when the requested year is a number and the previous options' year is the equal string", async () => {
	const savedFetch = globalThis.fetch;
	globalThis.fetch = async (url, init) => {
		if (url.includes("launch_options")) {
			throw new Error("network down");
		}
		return savedFetch(url, init);
	};
	const previousOptions = { fiscal_years: ["2025"], fiscal_periods: [{ value: "7" }] };
	try {
		const result = await loadPlane({ year: 2025, period: "7" }, previousOptions, "2025");
		assert.deepEqual(result.options, previousOptions, "2025 (number) and \"2025\" (string) must count as the same year");
	} finally {
		globalThis.fetch = savedFetch;
	}
});

// #189 PR2 row 70q (re-review 2 finding 2, from 70j): a rejected SET_PERIOD
// must not land back in `ready` holding the NEW period with the OLD data and
// options and no visible error — it must go to the machine's `failed` state
// (like a rejected first load) so the error shows, and RETRY from there must
// re-run the FULL plane load (options + snapshot) for the new period, not
// just the snapshot.
test("a rejected SET_PERIOD goes to `failed` (not `ready`) with the error, and RETRY reloads the full plane for the new period", async () => {
	let planeCallCount = 0;
	const calls = { plane: [] };
	const machine = closeMachine.provide({
		actors: {
			fetchPlane: fromPromise(async ({ input }) => {
				planeCallCount += 1;
				calls.plane.push(input?.period ?? null);
				if (planeCallCount === 2) {
					throw new Error("boom");
				}
				const period = input?.period || { year: "2026", period: "9" };
				return {
					data: { worker_healthy: true, processes: {} },
					options: { fiscal_years: [period.year], fiscal_periods: [] },
					period,
				};
			}),
			fetchSnapshot: fromPromise(async ({ input }) => ({
				data: { worker_healthy: true, processes: {} },
				period: input?.period ?? null,
			})),
			pollTicker: fromCallback(() => () => {}),
		},
	});
	const actor = createActor(machine).start();
	await flush(); await flush();
	assert.ok(actor.getSnapshot().matches("ready"), "first load succeeds");

	actor.send({ type: "SET_PERIOD", year: "2025", period: "7" });
	await flush(); await flush();

	const failedSnap = actor.getSnapshot();
	assert.ok(failedSnap.matches("failed"), "a rejected period change must land in `failed`, not `ready`");
	assert.ok(failedSnap.context.loadError, "the error must be recorded in context so it can be shown");

	actor.send({ type: "RETRY" });
	await flush(); await flush();

	assert.equal(planeCallCount, 3, "RETRY must re-invoke the full plane load (options + snapshot), not just the snapshot");
	assert.deepEqual(calls.plane[2], { year: "2025", period: "7" }, "RETRY must reload for the NEW period, not the old one");
	assert.ok(actor.getSnapshot().matches("ready"), "RETRY succeeds this time");
	actor.stop();
});

// #189 PR2 row 70q2 (follow-up to 70q): `failed` accepted only RETRY, so
// picking a DIFFERENT period from the navigator after a failed period change
// was silently ignored (and RETRY alone would just re-fail for the same
// rejected period, wedging the user). `failed` must also accept SET_PERIOD,
// same as `ready` does — recording the new period and going through
// `changingPeriod`'s full reload for it.
test("SET_PERIOD from `failed` moves to `changingPeriod` and reloads for the newly chosen period", async () => {
	let planeCallCount = 0;
	const calls = { plane: [] };
	const machine = closeMachine.provide({
		actors: {
			fetchPlane: fromPromise(async ({ input }) => {
				planeCallCount += 1;
				calls.plane.push(input?.period ?? null);
				if (planeCallCount === 2) {
					throw new Error("boom");
				}
				const period = input?.period || { year: "2026", period: "9" };
				return {
					data: { worker_healthy: true, processes: {} },
					options: { fiscal_years: [period.year], fiscal_periods: [] },
					period,
				};
			}),
			fetchSnapshot: fromPromise(async ({ input }) => ({
				data: { worker_healthy: true, processes: {} },
				period: input?.period ?? null,
			})),
			pollTicker: fromCallback(() => () => {}),
		},
	});
	const actor = createActor(machine).start();
	await flush(); await flush();
	assert.ok(actor.getSnapshot().matches("ready"), "first load succeeds");

	actor.send({ type: "SET_PERIOD", year: "2025", period: "7" });
	await flush(); await flush();
	assert.ok(actor.getSnapshot().matches("failed"), "the rejected period change lands in `failed`");

	actor.send({ type: "SET_PERIOD", year: "2024", period: "3" });
	await flush(); await flush();

	assert.equal(planeCallCount, 3, "SET_PERIOD from `failed` must invoke the full plane load, not be ignored");
	assert.deepEqual(calls.plane[2], { year: "2024", period: "3" }, "the reload must be for the newly chosen period, not the one that failed");
	assert.deepEqual(actor.getSnapshot().context.period, { year: "2024", period: "3" }, "context.period must reflect the new choice");
	assert.ok(actor.getSnapshot().matches("ready"), "the new period's load succeeds");
	actor.stop();
});

test("RETRY from `failed` still goes to `loading`, unaffected by the new SET_PERIOD transition", async () => {
	let planeCallCount = 0;
	const machine = closeMachine.provide({
		actors: {
			fetchPlane: fromPromise(async ({ input }) => {
				planeCallCount += 1;
				if (planeCallCount === 2) {
					throw new Error("boom");
				}
				const period = input?.period || { year: "2026", period: "9" };
				return {
					data: { worker_healthy: true, processes: {} },
					options: { fiscal_years: [period.year], fiscal_periods: [] },
					period,
				};
			}),
			fetchSnapshot: fromPromise(async ({ input }) => ({
				data: { worker_healthy: true, processes: {} },
				period: input?.period ?? null,
			})),
			pollTicker: fromCallback(() => () => {}),
		},
	});
	const actor = createActor(machine).start();
	await flush(); await flush();

	actor.send({ type: "SET_PERIOD", year: "2025", period: "7" });
	await flush(); await flush();
	assert.ok(actor.getSnapshot().matches("failed"));

	actor.send({ type: "RETRY" });
	await flush(); await flush();

	assert.equal(planeCallCount, 3, "RETRY must still trigger a full plane load");
	assert.ok(actor.getSnapshot().matches("ready"), "RETRY still succeeds");
	actor.stop();
});
