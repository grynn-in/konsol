/**
 * The workspace machine, run for real with stub actors. The first version of
 * this machine crashed on import (a compound state without `initial`) and
 * every other test still passed, because none of them imported it.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createActor, fromPromise, fromCallback } from "xstate";
import { homeMachine } from "./machines/homeMachine.js";

const flush = () => new Promise((r) => setTimeout(r, 0));

function start({ boot, load }) {
	const calls = { boot: 0, load: [], tickers: 0 };
	const machine = homeMachine.provide({
		actors: {
			boot: fromPromise(async () => {
				calls.boot += 1;
				return boot(calls.boot);
			}),
			loadMonth: fromPromise(async ({ input }) => {
				calls.load.push(input.period);
				return load(input);
			}),
			ticker: fromCallback(() => {
				calls.tickers += 1;
				return () => {};
			}),
		},
	});
	return { actor: createActor(machine).start(), calls };
}

const booted = async () => ({ me: { title: "Close Lead" }, tree: { years: [] } });
const month = async ({ year, period }) => ({ period: { fiscal_year: year, fiscal_period: period } });

test("loads the month the route asked for while booting", async () => {
	const { actor, calls } = start({ boot: booted, load: month });
	actor.send({ type: "OPEN", year: 2026, period: 9 });
	await flush(); await flush();
	assert.deepEqual(calls.load, [9]);
	assert.equal(actor.getSnapshot().context.month.period.fiscal_period, 9);
	assert.ok(actor.getSnapshot().matches({ ready: "idle" }));
});

test("an OPEN while loading loads the new month, not the old one", async () => {
	let release;
	const slow = new Promise((r) => { release = r; });
	const { actor, calls } = start({
		boot: booted,
		load: async (input) => { if (input.period === 8) await slow; return month(input); },
	});
	await flush();
	actor.send({ type: "OPEN", year: 2026, period: 8 });
	await flush();
	actor.send({ type: "OPEN", year: 2026, period: 9 });
	await flush(); await flush();
	release(); await flush();
	assert.deepEqual(calls.load, [8, 9]);
	assert.equal(actor.getSnapshot().context.month.period.fiscal_period, 9);
});

test("a new period clears the last period's error", async () => {
	const { actor } = start({
		boot: booted,
		load: async (input) => { if (input.period === 5) throw new Error("boom"); return month(input); },
	});
	await flush();
	actor.send({ type: "OPEN", year: 2026, period: 5 });
	await flush(); await flush();
	assert.equal(actor.getSnapshot().context.error.message, "boom");
	actor.send({ type: "OPEN", year: 2026, period: 6 });
	assert.equal(actor.getSnapshot().context.error, null);
});

test("a period chosen while boot has failed is loaded after retry", async () => {
	const { actor, calls } = start({
		boot: async (n) => { if (n === 1) throw new Error("down"); return booted(); },
		load: month,
	});
	await flush(); await flush();
	assert.ok(actor.getSnapshot().matches("failed"));
	actor.send({ type: "OPEN", year: 2026, period: 8 });
	actor.send({ type: "RETRY" });
	await flush(); await flush(); await flush();
	assert.deepEqual(calls.load, [8]);
	assert.equal(actor.getSnapshot().context.month.period.fiscal_period, 8);
});

test("a tick refreshes from idle but never cancels a load in flight", async () => {
	let release;
	const slow = new Promise((r) => { release = r; });
	const { actor, calls } = start({
		boot: booted,
		load: async (input) => { if (calls.load.length === 1) await slow; return month(input); },
	});
	await flush();
	actor.send({ type: "OPEN", year: 2026, period: 9 });
	await flush();
	actor.send({ type: "TICK" });   // ignored: still loading
	await flush();
	assert.deepEqual(calls.load, [9]);
	release(); await flush(); await flush();
	actor.send({ type: "TICK" });   // from idle: refreshes
	await flush(); await flush();
	assert.deepEqual(calls.load, [9, 9]);
});
