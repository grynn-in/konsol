// konsol#305 B11: tbUploadMachine.test.mjs
//
// The trial-balance upload machine, run for real with stub services injected
// through machine.provide({actors}). Every stub hands back a promise the test
// settles by hand, so a test can hold the machine in any state (a check in
// flight, a submit in flight) and send events there.
//
// States and the events each one takes (everything else is refused):
//   idle              FILE_CHOSEN → reading
//   reading           FILE_CHOSEN → reading (the old read is dropped)
//                     readFile done → checking; error → readFailed
//   readFailed        FILE_CHOSEN → reading
//   checking          FILE_CHOSEN → reading (the old check is dropped)
//                     check done → checked.ok | checked.problems; error → checkFailed
//   checkFailed       RETRY → checking (same content); FILE_CHOSEN → reading
//   checked.ok        SUBMIT → submitting; FILE_CHOSEN → reading
//   checked.problems  FILE_CHOSEN → reading (D1: re-upload is the only fix)
//   submitting        nothing (a submit in flight is never abandoned)
//                     submit done → received; error → checked.ok
//   received          final: nothing
import { test } from "node:test";
import assert from "node:assert/strict";
import { createActor, fromPromise } from "xstate";
import { tbUploadMachine } from "./tbUploadMachine.js";

const flush = async () => {
	for (let i = 0; i < 5; i += 1) await new Promise((r) => setTimeout(r, 0));
};

function deferred() {
	let resolve;
	let reject;
	const promise = new Promise((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

function start() {
	const calls = { readFile: [], check: [], submit: [] };
	const stub = (name) =>
		fromPromise(({ input }) => {
			const d = deferred();
			calls[name].push({ input, ...d });
			return d.promise;
		});
	const machine = tbUploadMachine.provide({
		actors: { readFile: stub("readFile"), check: stub("check"), submit: stub("submit") },
	});
	const actor = createActor(machine).start();
	const last = (name) => calls[name][calls[name].length - 1];
	return { actor, calls, last };
}

const OK = { ok: true, rows: [], file_problems: [], totals: {}, period_problem: null, replaces: null };
const OK_REPLACING = { ...OK, replaces: "TBS-0007" };
const PROBLEMS = {
	ok: false,
	rows: [{ line: 3, problems: [{ code: "UNKNOWN_ACCOUNT", message: "4001 is not in the chart", suggestion: "Use 4000" }] }],
	file_problems: [],
	totals: {},
	period_problem: null,
	replaces: null,
};
const CLOSED = { ...OK, period_problem: "FY2026 P08 is Closed: a trial balance can't be submitted" };

const fileA = { name: "a.csv" };
const fileB = { name: "b.csv" };

function value(actor) {
	return JSON.stringify(actor.getSnapshot().value);
}

function snap(actor) {
	const s = actor.getSnapshot();
	return { value: JSON.stringify(s.value), context: structuredClone(s.context), status: s.status };
}

function callCounts(calls) {
	return { readFile: calls.readFile.length, check: calls.check.length, submit: calls.submit.length };
}

/** Sends each event and asserts it changed nothing: state, context, status, and no service called. */
function assertRefuses(h, events, where) {
	for (const ev of events) {
		const before = snap(h.actor);
		const countsBefore = callCounts(h.calls);
		h.actor.send(ev);
		assert.deepEqual(snap(h.actor), before, `${where}: ${ev.type} must be refused`);
		assert.deepEqual(callCounts(h.calls), countsBefore, `${where}: ${ev.type} must call no service`);
	}
}

// Drivers that walk the machine to a given state.
async function toReading(h, file = fileA) {
	h.actor.send({ type: "FILE_CHOSEN", file });
	await flush();
}
async function toChecking(h, file = fileA, content = "a,b\n1,2") {
	await toReading(h, file);
	h.last("readFile").resolve(content);
	await flush();
}
async function toChecked(h, result, file = fileA, content = "a,b\n1,2") {
	await toChecking(h, file, content);
	h.last("check").resolve(result);
	await flush();
}
async function toSubmitting(h, result = OK_REPLACING) {
	await toChecked(h, result);
	h.actor.send({ type: "SUBMIT" });
	await flush();
}

const EDIT = { type: "EDIT_ROW", line: 3, account: "4000" };
const SUBMIT = { type: "SUBMIT" };
const RETRY = { type: "RETRY" };

// ---- happy path -----------------------------------------------------------

test("happy path: FILE_CHOSEN → reading → checking → checked.ok → SUBMIT → received", async () => {
	const h = start();
	assert.equal(value(h.actor), '"idle"');
	h.actor.send({ type: "FILE_CHOSEN", file: fileA });
	assert.equal(value(h.actor), '"reading"');
	await flush();
	assert.deepEqual(h.last("readFile").input, { file: fileA });
	h.last("readFile").resolve("account,debit\n1000,5");
	await flush();
	assert.equal(value(h.actor), '"checking"');
	assert.deepEqual(h.last("check").input, { file: fileA, content: "account,debit\n1000,5" });
	h.last("check").resolve(OK_REPLACING);
	await flush();
	assert.ok(h.actor.getSnapshot().matches({ checked: "ok" }));
	// `replaces` is visible before the submit that would replace it.
	assert.equal(h.actor.getSnapshot().context.replaces, "TBS-0007");
	h.actor.send(SUBMIT);
	assert.equal(value(h.actor), '"submitting"');
	await flush();
	assert.deepEqual(h.last("submit").input, {
		file: fileA,
		content: "account,debit\n1000,5",
		replaces: "TBS-0007",
	});
	h.last("submit").resolve({ name: "TBS-0008", replaced: "TBS-0007", on_behalf: false });
	await flush();
	const s = h.actor.getSnapshot();
	assert.equal(value(h.actor), '"received"');
	assert.equal(s.status, "done");
	assert.equal(s.context.received.name, "TBS-0008");
	assert.equal(s.context.received.replaced, "TBS-0007");
	assert.equal(s.context.error, null);
});

test("a first upload for the period has replaces null, and submit is told so", async () => {
	const h = start();
	await toChecked(h, OK);
	assert.equal(h.actor.getSnapshot().context.replaces, null);
	h.actor.send(SUBMIT);
	await flush();
	assert.equal(h.last("submit").input.replaces, null);
});

test("context starts empty", () => {
	const h = start();
	assert.deepEqual(h.actor.getSnapshot().context, {
		file: null,
		content: null,
		result: null,
		replaces: null,
		error: null,
		received: null,
	});
});

// ---- forbidden transitions, state by state -------------------------------

test("idle refuses SUBMIT, RETRY and EDIT_ROW", () => {
	const h = start();
	assertRefuses(h, [SUBMIT, RETRY, EDIT], "idle");
	assert.equal(value(h.actor), '"idle"');
});

test("reading refuses SUBMIT, RETRY and EDIT_ROW", async () => {
	const h = start();
	await toReading(h);
	assertRefuses(h, [SUBMIT, RETRY, EDIT], "reading");
	assert.equal(value(h.actor), '"reading"');
});

test("checking refuses SUBMIT, RETRY and EDIT_ROW", async () => {
	const h = start();
	await toChecking(h);
	assertRefuses(h, [SUBMIT, RETRY, EDIT], "checking");
	assert.equal(value(h.actor), '"checking"');
});

test("checked.ok refuses RETRY and EDIT_ROW", async () => {
	const h = start();
	await toChecked(h, OK);
	assertRefuses(h, [RETRY, EDIT], "checked.ok");
	assert.ok(h.actor.getSnapshot().matches({ checked: "ok" }));
});

test("checked.problems refuses SUBMIT, RETRY and EDIT_ROW (D1: no row editing)", async () => {
	const h = start();
	await toChecked(h, PROBLEMS);
	assertRefuses(h, [SUBMIT, RETRY, EDIT], "checked.problems");
	assert.ok(h.actor.getSnapshot().matches({ checked: "problems" }));
});

test("submitting refuses SUBMIT (no double submit), FILE_CHOSEN, RETRY and EDIT_ROW", async () => {
	const h = start();
	await toSubmitting(h);
	assertRefuses(h, [SUBMIT, { type: "FILE_CHOSEN", file: fileB }, RETRY, EDIT], "submitting");
	assert.equal(value(h.actor), '"submitting"');
	assert.equal(h.calls.submit.length, 1);
});

test("readFailed refuses SUBMIT, RETRY and EDIT_ROW", async () => {
	const h = start();
	await toReading(h);
	h.last("readFile").reject(new Error("unreadable"));
	await flush();
	assertRefuses(h, [SUBMIT, RETRY, EDIT], "readFailed");
	assert.equal(value(h.actor), '"readFailed"');
});

test("checkFailed refuses SUBMIT and EDIT_ROW", async () => {
	const h = start();
	await toChecking(h);
	h.last("check").reject(new Error("503"));
	await flush();
	assertRefuses(h, [SUBMIT, EDIT], "checkFailed");
	assert.equal(value(h.actor), '"checkFailed"');
});

test("received is final: it refuses every event", async () => {
	const h = start();
	await toSubmitting(h);
	h.last("submit").resolve({ name: "TBS-0008", replaced: "TBS-0007", on_behalf: false });
	await flush();
	assertRefuses(h, [SUBMIT, { type: "FILE_CHOSEN", file: fileB }, RETRY, EDIT], "received");
});

test("the machine declares no row-editing event in any state (D1)", () => {
	const ids = [];
	const walk = (node) => {
		for (const t of Object.keys(node.on || {})) if (!t.startsWith("xstate.")) ids.push(t);
		for (const child of Object.values(node.states || {})) walk(child);
	};
	walk(tbUploadMachine.root);
	for (const t of ids) assert.doesNotMatch(t, /EDIT|ROW|FIX/i, `unexpected event ${t}`);
	assert.deepEqual([...new Set(ids)].sort(), ["FILE_CHOSEN", "RETRY", "SUBMIT"]);
});

// ---- error paths: target state and the context left behind ----------------

test("readFile rejects → readFailed, error set, result/content/replaces null", async () => {
	const h = start();
	await toReading(h);
	h.last("readFile").reject(new Error("The file could not be read"));
	await flush();
	const s = h.actor.getSnapshot();
	assert.equal(value(h.actor), '"readFailed"');
	assert.equal(s.context.error, "The file could not be read");
	assert.equal(s.context.result, null);
	assert.equal(s.context.content, null);
	assert.equal(s.context.replaces, null);
	assert.equal(s.context.received, null);
	assert.equal(s.context.file, fileA);
	assert.equal(h.calls.check.length, 0);
});

test("readFailed → FILE_CHOSEN reads the new file and clears the error", async () => {
	const h = start();
	await toReading(h);
	h.last("readFile").reject(new Error("unreadable"));
	await flush();
	h.actor.send({ type: "FILE_CHOSEN", file: fileB });
	assert.equal(value(h.actor), '"reading"');
	assert.equal(h.actor.getSnapshot().context.error, null);
	assert.equal(h.actor.getSnapshot().context.file, fileB);
	await flush();
	assert.deepEqual(h.last("readFile").input, { file: fileB });
});

test("check rejects → checkFailed, error kept, content kept, result null; RETRY re-checks the same content", async () => {
	const h = start();
	await toChecking(h, fileA, "the,csv");
	h.last("check").reject(new Error("Server unavailable"));
	await flush();
	let s = h.actor.getSnapshot();
	assert.equal(value(h.actor), '"checkFailed"');
	assert.equal(s.context.error, "Server unavailable");
	assert.equal(s.context.content, "the,csv");
	assert.equal(s.context.result, null);
	assert.equal(s.context.replaces, null);
	h.actor.send(RETRY);
	assert.equal(value(h.actor), '"checking"');
	assert.equal(h.actor.getSnapshot().context.error, null);
	await flush();
	assert.equal(h.calls.check.length, 2);
	assert.deepEqual(h.last("check").input, { file: fileA, content: "the,csv" });
	assert.equal(h.calls.readFile.length, 1, "RETRY does not read the file again");
	h.last("check").resolve(OK);
	await flush();
	s = h.actor.getSnapshot();
	assert.ok(s.matches({ checked: "ok" }));
	assert.equal(s.context.error, null);
});

test("an error without a message is still named (never blank)", async () => {
	const h = start();
	await toChecking(h);
	h.last("check").reject("plain string failure");
	await flush();
	assert.equal(h.actor.getSnapshot().context.error, "plain string failure");
});

test("check failing for a new file leaves no result from the previous file", async () => {
	const h = start();
	await toChecked(h, OK_REPLACING, fileA, "a");
	assert.ok(h.actor.getSnapshot().context.result);
	await toChecking(h, fileB, "b");
	h.last("check").reject(new Error("timeout"));
	await flush();
	const s = h.actor.getSnapshot();
	assert.equal(value(h.actor), '"checkFailed"');
	assert.equal(s.context.file, fileB);
	assert.equal(s.context.content, "b");
	assert.equal(s.context.result, null, "file A's result must not survive as file B's");
	assert.equal(s.context.replaces, null, "file A's replaces must not survive as file B's");
});

test("read failing for a new file leaves no result or content from the previous file", async () => {
	const h = start();
	await toChecked(h, OK, fileA, "a");
	await toReading(h, fileB);
	h.last("readFile").reject(new Error("unreadable"));
	await flush();
	const s = h.actor.getSnapshot();
	assert.equal(s.context.file, fileB);
	assert.equal(s.context.content, null);
	assert.equal(s.context.result, null);
});

test("submit rejects → back to checked.ok with the error; received stays null, never 'Received'", async () => {
	const h = start();
	await toSubmitting(h);
	h.last("submit").reject(new Error("The trial balance changed since you checked it; check the file again."));
	await flush();
	const s = h.actor.getSnapshot();
	assert.ok(s.matches({ checked: "ok" }));
	assert.notEqual(value(h.actor), '"received"');
	assert.equal(s.status, "active");
	assert.equal(s.context.received, null);
	assert.equal(s.context.error, "The trial balance changed since you checked it; check the file again.");
	// The same file's check result stays (it is still this file's), so the user sees what they submitted.
	assert.deepEqual(s.context.result, OK_REPLACING);
	assert.equal(s.context.replaces, "TBS-0007");
});

test("after a failed submit, SUBMIT again clears the error and submits once more", async () => {
	const h = start();
	await toSubmitting(h);
	h.last("submit").reject(new Error("network"));
	await flush();
	h.actor.send(SUBMIT);
	assert.equal(value(h.actor), '"submitting"');
	assert.equal(h.actor.getSnapshot().context.error, null);
	await flush();
	assert.equal(h.calls.submit.length, 2);
	h.last("submit").resolve({ name: "TBS-0009", replaced: "TBS-0007", on_behalf: true });
	await flush();
	assert.equal(value(h.actor), '"received"');
	assert.equal(h.actor.getSnapshot().context.received.name, "TBS-0009");
});

test("after a failed submit, FILE_CHOSEN starts over with nothing carried from the old file", async () => {
	const h = start();
	await toSubmitting(h);
	h.last("submit").reject(new Error("network"));
	await flush();
	h.actor.send({ type: "FILE_CHOSEN", file: fileB });
	const c = h.actor.getSnapshot().context;
	assert.equal(value(h.actor), '"reading"');
	assert.deepEqual(c, { file: fileB, content: null, result: null, replaces: null, error: null, received: null });
});

// ---- the server's verdict --------------------------------------------------

test("period_problem → checked.problems even when the file is ok (A19: period not Open)", async () => {
	const h = start();
	await toChecked(h, CLOSED);
	const s = h.actor.getSnapshot();
	assert.ok(s.matches({ checked: "problems" }));
	assert.equal(s.context.result.period_problem, CLOSED.period_problem);
	assertRefuses(h, [SUBMIT], "checked.problems (closed period)");
	assert.equal(h.calls.submit.length, 0);
});

test("ok false → checked.problems; a result with no ok at all is problems, never ok", async () => {
	let h = start();
	await toChecked(h, PROBLEMS);
	assert.ok(h.actor.getSnapshot().matches({ checked: "problems" }));
	h = start();
	await toChecked(h, { rows: [], file_problems: [] });
	assert.ok(h.actor.getSnapshot().matches({ checked: "problems" }));
});

test("re-upload from checked.problems clears result and error, and checks the new file", async () => {
	const h = start();
	await toChecked(h, PROBLEMS, fileA, "bad");
	h.actor.send({ type: "FILE_CHOSEN", file: fileB });
	const c = h.actor.getSnapshot().context;
	assert.equal(value(h.actor), '"reading"');
	assert.equal(c.result, null);
	assert.equal(c.error, null);
	assert.equal(c.content, null);
	assert.equal(c.replaces, null);
	assert.equal(c.file, fileB);
	await flush();
	h.last("readFile").resolve("good");
	await flush();
	h.last("check").resolve(OK);
	await flush();
	assert.ok(h.actor.getSnapshot().matches({ checked: "ok" }));
	assert.deepEqual(h.last("check").input, { file: fileB, content: "good" });
});

test("re-upload from checked.ok also starts over", async () => {
	const h = start();
	await toChecked(h, OK_REPLACING, fileA, "a");
	h.actor.send({ type: "FILE_CHOSEN", file: fileB });
	assert.equal(value(h.actor), '"reading"');
	assert.equal(h.actor.getSnapshot().context.result, null);
	assert.equal(h.actor.getSnapshot().context.replaces, null);
});

// ---- races: an old file's service result must not land on a new file ------

test("race: a new file chosen while a check is in flight; the old check's result is dropped", async () => {
	const h = start();
	await toChecking(h, fileA, "a");
	const oldCheck = h.last("check");
	h.actor.send({ type: "FILE_CHOSEN", file: fileB });
	await flush();
	oldCheck.resolve(OK_REPLACING);
	await flush();
	let s = h.actor.getSnapshot();
	assert.equal(value(h.actor), '"reading"', "the old check must not move the new file to checked");
	assert.equal(s.context.result, null);
	assert.equal(s.context.replaces, null);
	assert.equal(s.context.file, fileB);
	h.last("readFile").resolve("b");
	await flush();
	h.last("check").resolve(PROBLEMS);
	await flush();
	s = h.actor.getSnapshot();
	assert.ok(s.matches({ checked: "problems" }));
	assert.deepEqual(s.context.result, PROBLEMS);
	assert.equal(s.context.content, "b");
});

test("race: an old check that rejects after a new file is chosen leaves no error on the new file", async () => {
	const h = start();
	await toChecking(h, fileA, "a");
	const oldCheck = h.last("check");
	await toChecking(h, fileB, "b");
	oldCheck.reject(new Error("old failure"));
	await flush();
	const s = h.actor.getSnapshot();
	assert.equal(value(h.actor), '"checking"');
	assert.equal(s.context.error, null);
	assert.equal(s.context.content, "b");
});

test("race: a new file chosen while reading; the old read's content never gets checked", async () => {
	const h = start();
	await toReading(h, fileA);
	const oldRead = h.last("readFile");
	h.actor.send({ type: "FILE_CHOSEN", file: fileB });
	await flush();
	oldRead.resolve("old content");
	await flush();
	assert.equal(value(h.actor), '"reading"');
	assert.equal(h.actor.getSnapshot().context.content, null);
	assert.equal(h.calls.check.length, 0);
	h.last("readFile").resolve("new content");
	await flush();
	assert.equal(h.calls.check.length, 1);
	assert.deepEqual(h.last("check").input, { file: fileB, content: "new content" });
});

test("the default services refuse loudly when the screen did not provide them", async () => {
	const actor = createActor(tbUploadMachine).start();
	actor.send({ type: "FILE_CHOSEN", file: fileA });
	await flush();
	assert.equal(JSON.stringify(actor.getSnapshot().value), '"readFailed"');
	assert.match(actor.getSnapshot().context.error, /readFile.*not provided/);
});
