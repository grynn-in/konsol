/** The upload machine, run for real with stub actors (see homeMachine.test.mjs for why). */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createActor, fromPromise, fromCallback } from "xstate";
import { uploadMachine } from "./machines/uploadMachine.js";

const flush = () => new Promise((r) => setTimeout(r, 0));

function start(over = {}) {
	const calls = { upload: 0, check: [], load: [], fetch: 0 };
	let tick = null;
	const statuses = over.statuses || ["Loading", "Loaded"];
	const machine = uploadMachine.provide({
		actors: {
			upload: fromPromise(async ({ input }) => { calls.upload += 1; if (over.uploadFails) throw new Error("too big"); return `/private/files/${input.file.name}`; }),
			check: fromPromise(async ({ input }) => { calls.check.push(input.fileUrl); return { name: "TBU-00001", status: "Checked", valid_count: 2, report: [] }; }),
			load: fromPromise(async ({ input }) => { calls.load.push(input); if (over.loadRefused) throw new Error("2 of 3 have problems"); return { name: input.name, status: "Loading" }; }),
			fetchUpload: fromPromise(async () => ({ name: "TBU-00001", status: statuses[Math.min(calls.fetch++, statuses.length - 1)] })),
			ticker: fromCallback(({ sendBack }) => { tick = () => sendBack({ type: "TICK" }); return () => { tick = null; }; }),
		},
	});
	const actor = createActor(machine).start();
	return { actor, calls, tick: () => tick && tick() };
}

test("choose → upload → check; nothing loads until LOAD", async () => {
	const { actor, calls } = start();
	actor.send({ type: "CHOOSE", file: { name: "tb.xlsx" } });
	await flush(); await flush();
	assert.ok(actor.getSnapshot().matches("checked"));
	assert.deepEqual(calls.check, ["/private/files/tb.xlsx"]);
	assert.equal(calls.load.length, 0);
});

test("LOAD with skip polls until the upload finishes", async () => {
	const { actor, calls, tick } = start({ statuses: ["Loading", "Partly Loaded"] });
	actor.send({ type: "CHOOSE", file: { name: "tb.csv" } });
	await flush(); await flush();
	actor.send({ type: "LOAD", skipInvalid: true });
	await flush();
	assert.deepEqual(calls.load, [{ name: "TBU-00001", skipInvalid: true }]);
	assert.ok(actor.getSnapshot().matches("loading"));
	tick(); await flush(); await flush();
	assert.ok(actor.getSnapshot().matches("loading"));
	tick(); await flush(); await flush();
	assert.ok(actor.getSnapshot().matches("done"));
	assert.equal(actor.getSnapshot().context.upload.status, "Partly Loaded");
});

test("a refused load returns to the checked upload with the reason", async () => {
	const { actor } = start({ loadRefused: true });
	actor.send({ type: "CHOOSE", file: { name: "tb.csv" } });
	await flush(); await flush();
	actor.send({ type: "LOAD" });
	await flush(); await flush();
	assert.ok(actor.getSnapshot().matches("checked"));
	assert.equal(actor.getSnapshot().context.error.message, "2 of 3 have problems");
	assert.equal(actor.getSnapshot().context.upload.name, "TBU-00001");
});

test("a failed upload goes back to idle with the error, and a new file starts clean", async () => {
	const { actor } = start({ uploadFails: true });
	actor.send({ type: "CHOOSE", file: { name: "huge.xlsx" } });
	await flush(); await flush();
	assert.ok(actor.getSnapshot().matches("idle"));
	assert.equal(actor.getSnapshot().context.error.message, "too big");
	actor.send({ type: "CHOOSE", file: { name: "again.xlsx" } });
	assert.equal(actor.getSnapshot().context.error, null);
});
