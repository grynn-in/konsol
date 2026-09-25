// konsol#305 B14: signoffMachine.test.mjs
//
// The sign-off machine, run for real with stub services injected through
// machine.provide({actors}). Every stub hands back a promise the test settles
// by hand, so a test can hold the machine in any state and send events there.
//
// The server's summary (A21 via A30 get_signoff) carries `action`, one of
// signed | blocked | run_checks | rerun | wait | sign | acknowledge | override,
// its `label`, and A30's `can_sign` / `can_override`.
//
// States and the events each one takes (everything else is refused):
//   loading        REFRESH → loading (restarts; the old load is dropped)
//                  load done → signed (action "signed") | review (any other known action)
//                  load done with an unknown action → loadFailed; load error → loadFailed
//   loadFailed     RETRY → loading
//   review         SIGN        (action "sign", can_sign)                 → signing
//                  ACKNOWLEDGE (action "acknowledge", can_sign)          → acknowledging
//                  OVERRIDE    (action "override", can_sign, can_override) → overriding
//                  REFRESH → loading
//   acknowledging  CONFIRM_ACK {text} (non-blank) → signing; CANCEL → review; REFRESH → loading
//   overriding     CONFIRM_OVERRIDE {text} (non-blank) → signing; CANCEL → review; REFRESH → loading
//   signing        nothing (a sign-off in flight is never abandoned)
//                  sign done → confirming; sign error → review with the server message
//   confirming     nothing (reloads the summary after a sign-off)
//                  done "signed" → signed; done other → review with a note; error → loadFailed
//   signed         CLOSE {note?} → closing; REFRESH → loading
//   closing        nothing; close done → closed; close error → signed with the error
//   closed         REOPEN {reason} (non-blank) → reopening
//   reopening      nothing; reopen done → loading; reopen error → closed with the error
import { test } from "node:test";
import assert from "node:assert/strict";
import { createActor, fromPromise } from "xstate";
import { signoffMachine, ACTIONS } from "./signoffMachine.js";

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

const SERVICES = ["load", "sign", "close", "reopen"];

function start() {
	const calls = Object.fromEntries(SERVICES.map((n) => [n, []]));
	const stub = (name) =>
		fromPromise(({ input }) => {
			const d = deferred();
			calls[name].push({ input, ...d });
			return d.promise;
		});
	const machine = signoffMachine.provide({
		actors: Object.fromEntries(SERVICES.map((n) => [n, stub(n)])),
	});
	const actor = createActor(machine).start();
	const last = (name) => calls[name][calls[name].length - 1];
	return { actor, calls, last };
}

function summaryOf(action, extra = {}) {
	return {
		action,
		label: `label for ${action}`,
		gates: { config_gaps: [], order: null, completeness: null, messages: [] },
		checks: {},
		acknowledgements: {},
		on_behalf: [],
		exceptions: [],
		covers: [],
		previous: [],
		can_sign: true,
		can_override: false,
		// A49: get_signoff always carries these three. "Open" is the ordinary
		// case (the period is signed off but not yet closed).
		period_status: "Open",
		closed_by: null,
		closed_on: null,
		...extra,
	};
}

const SIGN_S = summaryOf("sign");
const ACK_S = summaryOf("acknowledge");
const OVR_S = summaryOf("override", { can_override: true });
const SIGNED_S = summaryOf("signed", { label: "Signed Off" });
const SIGNED_CLOSED_S = summaryOf("signed", {
	label: "Signed Off",
	period_status: "Closed",
	closed_by: "lead@example.com",
	closed_on: "2026-09-25",
});
const SIGNED_LOCKED_S = summaryOf("signed", {
	label: "Signed Off",
	period_status: "Locked",
	closed_by: "lead@example.com",
	closed_on: "2026-01-15",
});

function value(actor) {
	return JSON.stringify(actor.getSnapshot().value);
}

function snap(actor) {
	const s = actor.getSnapshot();
	return { value: JSON.stringify(s.value), context: structuredClone(s.context), status: s.status };
}

function callCounts(calls) {
	return Object.fromEntries(SERVICES.map((n) => [n, calls[n].length]));
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

const SIGN = { type: "SIGN" };
const ACKNOWLEDGE = { type: "ACKNOWLEDGE" };
const OVERRIDE = { type: "OVERRIDE" };
const CONFIRM_ACK = { type: "CONFIRM_ACK", text: "I have read the warnings" };
const CONFIRM_OVERRIDE = { type: "CONFIRM_OVERRIDE", text: "Known timing difference, fixed in P08" };
const CANCEL = { type: "CANCEL" };
const CLOSE = { type: "CLOSE" };
const REOPEN = { type: "REOPEN", reason: "Late journal from ZZ01" };
const RETRY = { type: "RETRY" };
const REFRESH = { type: "REFRESH" };
const ALL = [SIGN, ACKNOWLEDGE, OVERRIDE, CONFIRM_ACK, CONFIRM_OVERRIDE, CANCEL, CLOSE, REOPEN, RETRY, REFRESH];
const except = (...keep) => ALL.filter((e) => !keep.includes(e.type));

// Drivers that walk the machine to a given state.
async function toLoaded(h, summary) {
	h.last("load").resolve(summary);
	await flush();
}
async function toReview(h, summary = SIGN_S) {
	await toLoaded(h, summary);
	assert.equal(value(h.actor), '"review"');
}
async function toAcknowledging(h) {
	await toReview(h, ACK_S);
	h.actor.send(ACKNOWLEDGE);
	await flush();
	assert.equal(value(h.actor), '"acknowledging"');
}
async function toOverriding(h) {
	await toReview(h, OVR_S);
	h.actor.send(OVERRIDE);
	await flush();
	assert.equal(value(h.actor), '"overriding"');
}
async function toSigning(h) {
	await toReview(h, SIGN_S);
	h.actor.send(SIGN);
	await flush();
	assert.equal(value(h.actor), '"signing"');
}
async function toConfirming(h) {
	await toSigning(h);
	h.last("sign").resolve({ signoff_status: "Signed Off" });
	await flush();
	assert.equal(value(h.actor), '"confirming"');
}
async function toSigned(h) {
	await toLoaded(h, SIGNED_S);
	assert.equal(value(h.actor), '"signed"');
}
async function toClosing(h) {
	await toSigned(h);
	h.actor.send(CLOSE);
	await flush();
	assert.equal(value(h.actor), '"closing"');
}
async function toClosed(h) {
	await toClosing(h);
	h.last("close").resolve({ status: "Closed", closed_by: "lead@example.com", closed_on: "2026-09-25" });
	await flush();
	assert.equal(value(h.actor), '"closed"');
}
async function toReopening(h) {
	await toClosed(h);
	h.actor.send(REOPEN);
	await flush();
	assert.equal(value(h.actor), '"reopening"');
}
async function toLoadFailed(h) {
	h.last("load").reject(new Error("502 Bad Gateway"));
	await flush();
	assert.equal(value(h.actor), '"loadFailed"');
}

// ---- the declared shape ---------------------------------------------------

test("ACTIONS lists exactly A21's eight actions", () => {
	assert.deepEqual(
		[...ACTIONS].sort(),
		["acknowledge", "blocked", "override", "rerun", "run_checks", "sign", "signed", "wait"],
	);
});

test("context starts empty and the first state is loading, with load called once", () => {
	const h = start();
	assert.equal(value(h.actor), '"loading"');
	assert.deepEqual(h.actor.getSnapshot().context, { summary: null, error: null, closed: null });
	assert.equal(h.calls.load.length, 1);
});

test("the machine declares only the documented events", () => {
	const ids = [];
	const walk = (node) => {
		for (const t of Object.keys(node.on || {})) if (!t.startsWith("xstate.")) ids.push(t);
		for (const child of Object.values(node.states || {})) walk(child);
	};
	walk(signoffMachine.root);
	assert.deepEqual([...new Set(ids)].sort(), [
		"ACKNOWLEDGE", "CANCEL", "CLOSE", "CONFIRM_ACK", "CONFIRM_OVERRIDE", "OVERRIDE", "REFRESH", "REOPEN", "RETRY", "SIGN",
	]);
});

test("window.prompt is never used (#298 stage 1: typed text comes in the event)", async () => {
	const { readFile } = await import("node:fs/promises");
	const src = await readFile(new URL("./signoffMachine.js", import.meta.url), "utf8");
	assert.doesNotMatch(src, /\b(prompt|confirm|alert)\s*\(/);
});

// ---- happy paths ----------------------------------------------------------

test("sign: review → SIGN → signing → confirming (load again) → signed", async () => {
	const h = start();
	await toReview(h, SIGN_S);
	assert.deepEqual(h.actor.getSnapshot().context.summary, SIGN_S);
	h.actor.send(SIGN);
	assert.equal(value(h.actor), '"signing"');
	await flush();
	assert.deepEqual(h.last("sign").input, { acknowledgement: null, override_reason: null });
	h.last("sign").resolve({ signoff_status: "Signed Off" });
	await flush();
	assert.equal(value(h.actor), '"confirming"');
	assert.equal(h.calls.load.length, 2, "load is called again after a sign-off");
	await toLoaded(h, SIGNED_S);
	const s = h.actor.getSnapshot();
	assert.equal(value(h.actor), '"signed"');
	assert.deepEqual(s.context.summary, SIGNED_S);
	assert.equal(s.context.error, null);
});

test("acknowledge (Amber): the typed text is sent, trimmed, as the acknowledgement", async () => {
	const h = start();
	await toAcknowledging(h);
	h.actor.send({ type: "CONFIRM_ACK", text: "  I have read the 3 warnings  " });
	assert.equal(value(h.actor), '"signing"');
	await flush();
	assert.deepEqual(h.last("sign").input, { acknowledgement: "I have read the 3 warnings", override_reason: null });
	h.last("sign").resolve({});
	await flush();
	await toLoaded(h, summaryOf("signed", { label: "Acknowledged" }));
	assert.equal(value(h.actor), '"signed"');
});

test("override (Red, Close Lead): the typed reason is sent, trimmed, as the override reason", async () => {
	const h = start();
	await toOverriding(h);
	h.actor.send({ type: "CONFIRM_OVERRIDE", text: "\tKnown timing difference\n" });
	assert.equal(value(h.actor), '"signing"');
	await flush();
	assert.deepEqual(h.last("sign").input, { acknowledgement: null, override_reason: "Known timing difference" });
	h.last("sign").resolve({});
	await flush();
	await toLoaded(h, summaryOf("signed", { label: "Overridden" }));
	assert.equal(value(h.actor), '"signed"');
});

test("an already signed period loads straight into signed", async () => {
	const h = start();
	await toSigned(h);
	assert.equal(h.calls.sign.length, 0);
});

// ---- B14b: a loaded, already-closed period (A49's period_status) ----------

test("a loaded signed+Closed period lands in closed (offers Reopen, never Close)", async () => {
	const h = start();
	await toLoaded(h, SIGNED_CLOSED_S);
	assert.equal(value(h.actor), '"closed"');
	assert.deepEqual(h.actor.getSnapshot().context.summary, SIGNED_CLOSED_S);
	assertRefuses(h, except("REOPEN"), "closed (loaded Closed)");
	h.actor.send(REOPEN);
	assert.equal(value(h.actor), '"reopening"');
});

test("a loaded signed+Locked period lands in closed, but REOPEN is refused: no Reopen offered", async () => {
	const h = start();
	await toLoaded(h, SIGNED_LOCKED_S);
	assert.equal(value(h.actor), '"closed"');
	assert.deepEqual(h.actor.getSnapshot().context.summary, SIGNED_LOCKED_S);
	assertRefuses(h, ALL, "closed (loaded Locked)");
});

test("load returns signed with an unknown period_status → loadFailed, not signed", async () => {
	for (const bad of ["Frozen", "", null, undefined]) {
		const h = start();
		await toLoaded(h, summaryOf("signed", { label: "Signed Off", period_status: bad }));
		assert.equal(value(h.actor), '"loadFailed"');
		const c = h.actor.getSnapshot().context;
		assert.equal(c.summary, null);
		assert.match(c.error, /Unknown period status/);
	}
});

test("after signing, a reload that reports Closed lands in closed (confirming → closed)", async () => {
	const h = start();
	await toSigning(h);
	h.last("sign").resolve({});
	await flush();
	assert.equal(value(h.actor), '"confirming"');
	await toLoaded(h, SIGNED_CLOSED_S);
	assert.equal(value(h.actor), '"closed"');
	assert.deepEqual(h.actor.getSnapshot().context.summary, SIGNED_CLOSED_S);
});

test("after signing, a reload that reports Locked lands in closed with Reopen refused", async () => {
	const h = start();
	await toSigning(h);
	h.last("sign").resolve({});
	await flush();
	await toLoaded(h, SIGNED_LOCKED_S);
	assert.equal(value(h.actor), '"closed"');
	assertRefuses(h, ALL, "closed (confirming → Locked)");
});

test("after signing, a reload with an unknown period_status → loadFailed, not signed", async () => {
	const h = start();
	await toSigning(h);
	h.last("sign").resolve({});
	await flush();
	await toLoaded(h, summaryOf("signed", { label: "Signed Off", period_status: "Frozen" }));
	assert.equal(value(h.actor), '"loadFailed"');
	assert.match(h.actor.getSnapshot().context.error, /Unknown period status/);
});

test("CANCEL leaves acknowledging and overriding for review, the summary unchanged", async () => {
	let h = start();
	await toAcknowledging(h);
	h.actor.send(CANCEL);
	assert.equal(value(h.actor), '"review"');
	assert.deepEqual(h.actor.getSnapshot().context.summary, ACK_S);
	h = start();
	await toOverriding(h);
	h.actor.send(CANCEL);
	assert.equal(value(h.actor), '"review"');
	assert.deepEqual(h.actor.getSnapshot().context.summary, OVR_S);
});

test("close (9.3): signed → CLOSE → closing → closed, the note passed and the result kept", async () => {
	const h = start();
	await toSigned(h);
	h.actor.send({ type: "CLOSE", note: "P07 done" });
	assert.equal(value(h.actor), '"closing"');
	await flush();
	assert.deepEqual(h.last("close").input, { note: "P07 done" });
	const result = { status: "Closed", closed_by: "lead@example.com", closed_on: "2026-09-25" };
	h.last("close").resolve(result);
	await flush();
	assert.equal(value(h.actor), '"closed"');
	assert.deepEqual(h.actor.getSnapshot().context.closed, result);
});

test("CLOSE with no note passes note null", async () => {
	const h = start();
	await toClosing(h);
	assert.deepEqual(h.last("close").input, { note: null });
});

test("reopen (9.5): closed → REOPEN → reopening → loading, the trimmed reason passed and closed cleared", async () => {
	const h = start();
	await toClosed(h);
	h.actor.send({ type: "REOPEN", reason: "  Late journal  " });
	assert.equal(value(h.actor), '"reopening"');
	await flush();
	assert.deepEqual(h.last("reopen").input, { reason: "Late journal" });
	h.last("reopen").resolve({ status: "Open" });
	await flush();
	assert.equal(value(h.actor), '"loading"');
	assert.equal(h.actor.getSnapshot().context.closed, null);
	assert.equal(h.calls.load.length, 2);
	await toLoaded(h, SIGNED_S);
	assert.equal(value(h.actor), '"signed"');
});

// ---- every action lands in review, and which of them can be signed --------

test("every non-signed action lands in review; only its own event leaves review", async () => {
	const own = { sign: SIGN, acknowledge: ACKNOWLEDGE, override: OVERRIDE };
	for (const action of ["blocked", "run_checks", "rerun", "wait", "sign", "acknowledge", "override"]) {
		const h = start();
		const summary = summaryOf(action, { can_override: true });
		await toReview(h, summary);
		const refused = [SIGN, ACKNOWLEDGE, OVERRIDE].filter((e) => e !== own[action]);
		assertRefuses(h, [...refused, CONFIRM_ACK, CONFIRM_OVERRIDE, CANCEL, CLOSE, REOPEN, RETRY], `review(${action})`);
	}
});

test("SIGN is refused when the action is blocked, run_checks, rerun or wait", async () => {
	for (const action of ["blocked", "run_checks", "rerun", "wait"]) {
		const h = start();
		await toReview(h, summaryOf(action, { can_override: true }));
		assertRefuses(h, [SIGN], `review(${action})`);
		assert.equal(h.calls.sign.length, 0);
	}
});

test("SIGN is refused for acknowledge and override: they need the typed text", async () => {
	for (const s of [ACK_S, OVR_S]) {
		const h = start();
		await toReview(h, s);
		assertRefuses(h, [SIGN], `review(${s.action})`);
	}
});

test("a Viewer (can_sign false or missing) cannot SIGN, ACKNOWLEDGE or OVERRIDE", async () => {
	for (const canSign of [false, undefined, null, "true"]) {
		for (const [s, ev] of [[SIGN_S, SIGN], [ACK_S, ACKNOWLEDGE], [OVR_S, OVERRIDE]]) {
			const h = start();
			await toReview(h, { ...s, can_sign: canSign });
			assertRefuses(h, [ev], `review(${s.action}, can_sign ${canSign})`);
		}
	}
});

test("OVERRIDE is refused when can_override is not true, even with action override", async () => {
	for (const canOverride of [false, undefined, null, 1]) {
		const h = start();
		await toReview(h, summaryOf("override", { can_override: canOverride }));
		assertRefuses(h, [OVERRIDE], `review(override, can_override ${canOverride})`);
	}
});

test("OVERRIDE is refused when the action is blocked, even for the Close Lead", async () => {
	const h = start();
	await toReview(h, summaryOf("blocked", { can_override: true }));
	assertRefuses(h, [OVERRIDE], "review(blocked)");
});

// ---- forbidden transitions, state by state -------------------------------

test("loading refuses everything but REFRESH", async () => {
	const h = start();
	assertRefuses(h, except("REFRESH"), "loading");
});

test("loadFailed refuses everything but RETRY", async () => {
	const h = start();
	await toLoadFailed(h);
	assertRefuses(h, except("RETRY"), "loadFailed");
});

test("acknowledging refuses everything but CONFIRM_ACK, CANCEL and REFRESH", async () => {
	const h = start();
	await toAcknowledging(h);
	assertRefuses(h, except("CONFIRM_ACK", "CANCEL", "REFRESH"), "acknowledging");
});

test("overriding refuses everything but CONFIRM_OVERRIDE, CANCEL and REFRESH", async () => {
	const h = start();
	await toOverriding(h);
	assertRefuses(h, except("CONFIRM_OVERRIDE", "CANCEL", "REFRESH"), "overriding");
});

test("signing refuses every event: no double sign, no cancel, no refetch", async () => {
	const h = start();
	await toSigning(h);
	assertRefuses(h, ALL, "signing");
	assert.equal(h.calls.sign.length, 1);
});

test("confirming refuses every event", async () => {
	const h = start();
	await toConfirming(h);
	assertRefuses(h, ALL, "confirming");
});

test("signed refuses everything but CLOSE and REFRESH (REOPEN only once closed)", async () => {
	const h = start();
	await toSigned(h);
	assertRefuses(h, except("CLOSE", "REFRESH"), "signed");
});

test("closing refuses every event", async () => {
	const h = start();
	await toClosing(h);
	assertRefuses(h, ALL, "closing");
});

test("closed refuses everything but REOPEN", async () => {
	const h = start();
	await toClosed(h);
	assertRefuses(h, except("REOPEN"), "closed");
});

test("reopening refuses every event", async () => {
	const h = start();
	await toReopening(h);
	assertRefuses(h, ALL, "reopening");
});

// ---- typed text: blank and whitespace-only are refused --------------------

const BLANKS = [undefined, null, "", "   ", "\t\n ", 42];

test("CONFIRM_ACK with blank, whitespace-only or non-text is refused", async () => {
	const h = start();
	await toAcknowledging(h);
	assertRefuses(h, BLANKS.map((text) => ({ type: "CONFIRM_ACK", text })), "acknowledging");
});

test("CONFIRM_OVERRIDE with blank, whitespace-only or non-text is refused", async () => {
	const h = start();
	await toOverriding(h);
	assertRefuses(h, BLANKS.map((text) => ({ type: "CONFIRM_OVERRIDE", text })), "overriding");
});

test("REOPEN with a blank, whitespace-only or non-text reason is refused", async () => {
	const h = start();
	await toClosed(h);
	assertRefuses(h, BLANKS.map((reason) => ({ type: "REOPEN", reason })), "closed");
});

// ---- close is offered only after a successful sign-off (9.3) --------------

test("CLOSE is refused in every state before signed", async () => {
	const h = start();
	assertRefuses(h, [CLOSE], "loading");
	await toReview(h, SIGN_S);
	assertRefuses(h, [CLOSE], "review");
	h.actor.send(SIGN);
	await flush();
	assertRefuses(h, [CLOSE], "signing");
	h.last("sign").resolve({});
	await flush();
	assertRefuses(h, [CLOSE], "confirming");
});

test("a refused sign-off never reaches signed and never offers CLOSE", async () => {
	const h = start();
	await toSigning(h);
	h.last("sign").reject(new Error("Sign off FY2026 P06 first"));
	await flush();
	assert.equal(value(h.actor), '"review"');
	assert.equal(h.calls.load.length, 1, "a refused sign-off does not reload");
	assertRefuses(h, [CLOSE], "review after a refused sign-off");
});

// ---- error paths: target state and the context left behind ----------------

test("load rejects → loadFailed, summary null, error set; RETRY loads again and clears the error", async () => {
	const h = start();
	await toLoadFailed(h);
	let c = h.actor.getSnapshot().context;
	assert.equal(c.summary, null);
	assert.equal(c.error, "502 Bad Gateway");
	h.actor.send(RETRY);
	assert.equal(value(h.actor), '"loading"');
	assert.equal(h.actor.getSnapshot().context.error, null);
	assert.equal(h.calls.load.length, 2);
	await toReview(h, SIGN_S);
	c = h.actor.getSnapshot().context;
	assert.deepEqual(c.summary, SIGN_S);
	assert.equal(c.error, null);
});

test("a load that fails on a refetch drops the old summary (no stale action survives)", async () => {
	const h = start();
	await toReview(h, SIGN_S);
	h.actor.send(REFRESH);
	await flush();
	await toLoadFailed(h);
	assert.equal(h.actor.getSnapshot().context.summary, null);
});

test("load returns an unknown action → loadFailed naming it, summary null", async () => {
	for (const bad of [summaryOf("sign_now"), { ...SIGN_S, action: undefined }, null, "oops"]) {
		const h = start();
		await toLoaded(h, bad);
		assert.equal(value(h.actor), '"loadFailed"');
		const c = h.actor.getSnapshot().context;
		assert.equal(c.summary, null);
		assert.match(c.error, /Unknown sign-off action/);
	}
});

test("sign rejects → review with the server message; summary unchanged; no reload", async () => {
	const h = start();
	await toSigning(h);
	h.last("sign").reject(new Error("Sign off FY2026 P06 first"));
	await flush();
	const c = h.actor.getSnapshot().context;
	assert.equal(value(h.actor), '"review"');
	assert.equal(c.error, "Sign off FY2026 P06 first");
	assert.deepEqual(c.summary, SIGN_S);
});

test("an acknowledged sign that is refused returns to review, not acknowledging; ACKNOWLEDGE again clears the error", async () => {
	const h = start();
	await toAcknowledging(h);
	h.actor.send(CONFIRM_ACK);
	await flush();
	h.last("sign").reject(new Error("Type an acknowledgement"));
	await flush();
	assert.equal(value(h.actor), '"review"');
	assert.equal(h.actor.getSnapshot().context.error, "Type an acknowledgement");
	h.actor.send(ACKNOWLEDGE);
	assert.equal(value(h.actor), '"acknowledging"');
	assert.equal(h.actor.getSnapshot().context.error, null, "an old error must not survive into a new attempt");
});

test("an error from a failed sign does not survive into the next SIGN, OVERRIDE or REFRESH", async () => {
	for (const [s, ev, target] of [
		[SIGN_S, SIGN, '"signing"'],
		[OVR_S, OVERRIDE, '"overriding"'],
		[SIGN_S, REFRESH, '"loading"'],
	]) {
		const h = start();
		await toReview(h, s);
		if (s === OVR_S) {
			h.actor.send(OVERRIDE);
			h.actor.send(CONFIRM_OVERRIDE);
		} else {
			h.actor.send(SIGN);
		}
		await flush();
		h.last("sign").reject(new Error("refused"));
		await flush();
		assert.equal(h.actor.getSnapshot().context.error, "refused");
		h.actor.send(ev);
		assert.equal(value(h.actor), target);
		assert.equal(h.actor.getSnapshot().context.error, null, `${ev.type} starts with no error`);
	}
});

test("an error without a message is still named (never blank)", async () => {
	for (const bad of [undefined, null, "", {}]) {
		const h = start();
		await toSigning(h);
		h.last("sign").reject(bad);
		await flush();
		const e = h.actor.getSnapshot().context.error;
		assert.equal(typeof e, "string");
		assert.ok(e.trim().length > 0, `error for ${JSON.stringify(bad)} must not be blank`);
	}
});

test("sign accepted but the reload does not say signed → review with a visible note, never signed", async () => {
	const h = start();
	await toConfirming(h);
	await toLoaded(h, summaryOf("acknowledge"));
	assert.equal(value(h.actor), '"review"');
	const c = h.actor.getSnapshot().context;
	assert.equal(c.summary.action, "acknowledge");
	assert.match(c.error, /label for acknowledge/);
	assertRefuses(h, [CLOSE], "review after an unconfirmed sign-off");
});

test("sign accepted but the reload fails → loadFailed that says the sign-off went through", async () => {
	const h = start();
	await toConfirming(h);
	h.last("load").reject(new Error("timeout"));
	await flush();
	assert.equal(value(h.actor), '"loadFailed"');
	const c = h.actor.getSnapshot().context;
	assert.equal(c.summary, null);
	assert.match(c.error, /signed off/i);
	assert.match(c.error, /timeout/);
	h.actor.send(RETRY);
	await toLoaded(h, SIGNED_S);
	assert.equal(value(h.actor), '"signed"');
	assert.equal(h.actor.getSnapshot().context.error, null);
});

test("sign accepted but the reload returns an unknown action → loadFailed, never signed", async () => {
	const h = start();
	await toConfirming(h);
	await toLoaded(h, summaryOf("mystery"));
	assert.equal(value(h.actor), '"loadFailed"');
	assert.match(h.actor.getSnapshot().context.error, /Unknown sign-off action/);
});

test("close rejects → signed with the error; closed stays null; CLOSE again clears it", async () => {
	const h = start();
	await toClosing(h);
	h.last("close").reject(new Error("Rates missing for P07"));
	await flush();
	let c = h.actor.getSnapshot().context;
	assert.equal(value(h.actor), '"signed"');
	assert.equal(c.error, "Rates missing for P07");
	assert.equal(c.closed, null);
	assert.deepEqual(c.summary, SIGNED_S);
	h.actor.send(CLOSE);
	assert.equal(value(h.actor), '"closing"');
	assert.equal(h.actor.getSnapshot().context.error, null);
	c = h.actor.getSnapshot().context;
	assert.equal(h.calls.close.length, 2);
});

test("a close error does not survive a REFRESH from signed", async () => {
	const h = start();
	await toClosing(h);
	h.last("close").reject(new Error("Rates missing"));
	await flush();
	h.actor.send(REFRESH);
	assert.equal(value(h.actor), '"loading"');
	assert.equal(h.actor.getSnapshot().context.error, null);
});

test("reopen rejects → closed with the error, closed kept; REOPEN again clears it", async () => {
	const h = start();
	await toReopening(h);
	const closed = h.actor.getSnapshot().context.closed;
	h.last("reopen").reject(new Error("Only the Close Lead can reopen"));
	await flush();
	const c = h.actor.getSnapshot().context;
	assert.equal(value(h.actor), '"closed"');
	assert.equal(c.error, "Only the Close Lead can reopen");
	assert.deepEqual(c.closed, closed);
	assert.equal(h.calls.load.length, 1, "a refused reopen does not reload");
	h.actor.send(REOPEN);
	assert.equal(value(h.actor), '"reopening"');
	assert.equal(h.actor.getSnapshot().context.error, null);
});

// ---- races: a refetch that changes the action -----------------------------

test("race: a refetch turns sign into acknowledge; the old SIGN is refused against the new state", async () => {
	const h = start();
	await toReview(h, SIGN_S);
	h.actor.send(REFRESH);
	assertRefuses(h, [SIGN], "loading during a refetch");
	await toLoaded(h, ACK_S);
	assert.equal(value(h.actor), '"review"');
	assertRefuses(h, [SIGN], "review after the action changed to acknowledge");
	assert.equal(h.calls.sign.length, 0);
});

test("race: a refetch while typing an acknowledgement returns to review; the old CONFIRM_ACK is refused", async () => {
	const h = start();
	await toAcknowledging(h);
	h.actor.send(REFRESH);
	assert.equal(value(h.actor), '"loading"');
	await toLoaded(h, summaryOf("blocked", { label: "Sign off P07 first" }));
	assert.equal(value(h.actor), '"review"');
	assertRefuses(h, [CONFIRM_ACK, SIGN, ACKNOWLEDGE], "review after acknowledge became blocked");
	assert.equal(h.calls.sign.length, 0);
});

test("race: a refetch while typing an override that loses can_override refuses the old CONFIRM_OVERRIDE and OVERRIDE", async () => {
	const h = start();
	await toOverriding(h);
	h.actor.send(REFRESH);
	await toLoaded(h, summaryOf("override", { can_override: false }));
	assertRefuses(h, [CONFIRM_OVERRIDE, OVERRIDE], "review after can_override was lost");
});

test("race: a refetch that still says acknowledge makes the user open the dialog again", async () => {
	const h = start();
	await toAcknowledging(h);
	h.actor.send(REFRESH);
	await toLoaded(h, ACK_S);
	assert.equal(value(h.actor), '"review"');
	assertRefuses(h, [CONFIRM_ACK], "review");
});

test("race: two refetches; the first load's late result is dropped", async () => {
	const h = start();
	await toReview(h, SIGN_S);
	h.actor.send(REFRESH);
	await flush();
	const first = h.last("load");
	h.actor.send(REFRESH);
	await flush();
	first.resolve(SIGN_S);
	await flush();
	assert.equal(value(h.actor), '"loading"', "the dropped load must not move the machine");
	await toLoaded(h, ACK_S);
	assert.deepEqual(h.actor.getSnapshot().context.summary, ACK_S);
	assertRefuses(h, [SIGN], "review");
});

test("race: a dropped load that rejects late leaves no error", async () => {
	const h = start();
	const first = h.last("load");
	h.actor.send(REFRESH);
	await flush();
	first.reject(new Error("old failure"));
	await flush();
	assert.equal(value(h.actor), '"loading"');
	assert.equal(h.actor.getSnapshot().context.error, null);
});

test("race: a refetch while signed that finds the run re-sign needed leaves signed and refuses CLOSE", async () => {
	const h = start();
	await toSigned(h);
	h.actor.send(REFRESH);
	await toLoaded(h, summaryOf("rerun"));
	assert.equal(value(h.actor), '"review"');
	assertRefuses(h, [CLOSE, SIGN], "review(rerun)");
});

test("the default services refuse loudly when the screen did not provide them", async () => {
	const actor = createActor(signoffMachine).start();
	await flush();
	assert.equal(JSON.stringify(actor.getSnapshot().value), '"loadFailed"');
	assert.match(actor.getSnapshot().context.error, /load.*not provided/);
});

// ---- konsol#305 B32: the shell's context is reloaded after sign, close, reopen ----
//
// C1 run 3: the header kept "Open · Not Signed Off" after sign + close, and
// "Closed · Acknowledged" after reopen, until a navigation. The machine emits
// PERIOD_CHANGED exactly once when a SIGN, CLOSE or REOPEN request succeeds on
// the server; the screen answers it with the shell's quiet context reload.
// A failed or cancelled step emits nothing.

async function withEmits(run) {
	const { PERIOD_CHANGED } = await import("./signoffMachine.js");
	assert.equal(typeof PERIOD_CHANGED, "string", "signoffMachine exports PERIOD_CHANGED");
	const h = start();
	const emitted = [];
	h.actor.on(PERIOD_CHANGED, (e) => emitted.push(e));
	await run(h);
	return emitted;
}

test("B32: a successful sign emits PERIOD_CHANGED once (action sign)", async () => {
	const emitted = await withEmits(async (h) => {
		await toConfirming(h);
		await toLoaded(h, SIGNED_S);
		assert.equal(value(h.actor), '"signed"');
	});
	assert.equal(emitted.length, 1);
	assert.equal(emitted[0].action, "sign");
});

test("B32: an acknowledged or overridden sign also emits once", async () => {
	for (const [drive, confirm] of [[toAcknowledging, CONFIRM_ACK], [toOverriding, CONFIRM_OVERRIDE]]) {
		const emitted = await withEmits(async (h) => {
			await drive(h);
			h.actor.send(confirm);
			await flush();
			h.last("sign").resolve({ signoff_status: "Signed Off" });
			await flush();
			await toLoaded(h, SIGNED_S);
		});
		assert.deepEqual(emitted.map((e) => e.action), ["sign"], confirm.type);
	}
});

test("B32: a sign accepted by the server but whose reload fails still emits once (the period did change)", async () => {
	const emitted = await withEmits(async (h) => {
		await toConfirming(h);
		await toLoadFailed(h);
	});
	assert.deepEqual(emitted.map((e) => e.action), ["sign"]);
});

test("B32: a successful close emits once (action close)", async () => {
	const emitted = await withEmits(async (h) => {
		await toClosed(h);
	});
	assert.deepEqual(emitted.map((e) => e.action), ["close"]);
});

test("B32: a successful reopen emits once (action reopen), and the reload that follows emits nothing more", async () => {
	const emitted = await withEmits(async (h) => {
		await toReopening(h);
		h.last("reopen").resolve({ status: "Open" });
		await flush();
		await toLoaded(h, SIGNED_S);
		assert.equal(value(h.actor), '"signed"');
	});
	assert.deepEqual(emitted.map((e) => e.action), ["close", "reopen"]);
});

test("B32 failure path: a refused sign, close or reopen emits nothing", async () => {
	const signFail = await withEmits(async (h) => {
		await toSigning(h);
		h.last("sign").reject(new Error("Sign off P07 first"));
		await flush();
		assert.equal(value(h.actor), '"review"');
	});
	assert.deepEqual(signFail, []);
	const closeFail = await withEmits(async (h) => {
		await toClosing(h);
		h.last("close").reject(new Error("Rates missing for P07"));
		await flush();
		assert.equal(value(h.actor), '"signed"');
	});
	assert.deepEqual(closeFail, []);
	const reopenFail = await withEmits(async (h) => {
		await toReopening(h);
		h.last("reopen").reject(new Error("Only the Close Lead can reopen"));
		await flush();
		assert.equal(value(h.actor), '"closed"');
	});
	assert.deepEqual(reopenFail.map((e) => e.action), ["close"], "only the earlier close, not the refused reopen");
});

test("B32 failure path: cancel, loads and refreshes emit nothing", async () => {
	const emitted = await withEmits(async (h) => {
		await toAcknowledging(h);
		h.actor.send(CANCEL);
		h.actor.send(OVERRIDE);
		h.actor.send(REFRESH);
		await toLoaded(h, OVR_S);
		h.actor.send(OVERRIDE);
		h.actor.send(CANCEL);
		h.actor.send(REFRESH);
		await toLoaded(h, SIGNED_S);
		h.actor.send(REFRESH);
		await toLoaded(h, SIGNED_CLOSED_S);
		assert.equal(value(h.actor), '"closed"');
	});
	assert.deepEqual(emitted, []);
});
