// konsol#305 B14 (+B14b): signoffMachine.js
//
// One period's sign-off (stories 9.1, 9.2, 9.3, 9.5; D4). The server decides
// what may happen next: A30's get_signoff returns A21's summary, whose
// `action` is one of ACTIONS and whose `label` is the button text, plus
// `can_sign`, `can_override`, and (A49) `period_status` ("Open", "Closed" or
// "Locked", the effective status of the requested period) with `closed_by` /
// `closed_on`. The machine never guesses an action; it only refuses what the
// latest summary does not allow.
//
// The services are injected by the screen with machine.provide({actors}); the
// screen closes over the period:
//   load()                                     → the get_signoff summary (A30, A49)
//   sign({acknowledgement, override_reason})   → A32 sign
//   close({note})                              → A34 close_period
//   reopen({reason})                           → A34 reopen_period
// The defaults below refuse by name, so a screen that forgets one fails visibly.
//
// States and the events each takes (every other event is refused):
//   loading        REFRESH → loading (restarts; the old load is dropped)
//                  done, action "signed":
//                    period_status "Open"          → signed (offers Close)
//                    period_status "Closed"/"Locked" → closed (B14b: a reload
//                      of an already-closed period never offers Close again;
//                      Reopen is offered for Closed, refused for Locked)
//                    an unrecognised period_status  → loadFailed
//                  done, any other known action → review
//                  an unknown action or an error → loadFailed (summary null)
//   loadFailed     RETRY → loading
//   review         SIGN        when action "sign" and can_sign            → signing
//                  ACKNOWLEDGE when action "acknowledge" and can_sign     → acknowledging
//                  OVERRIDE    when action "override", can_sign and can_override → overriding
//                  REFRESH → loading
//   acknowledging  CONFIRM_ACK {text}, non-blank → signing; CANCEL → review; REFRESH → loading
//   overriding     CONFIRM_OVERRIDE {text}, non-blank → signing; CANCEL → review; REFRESH → loading
//   signing        nothing: a sign-off in flight is never abandoned
//                  done → confirming; error → review with the server message
//   confirming     nothing: the summary is loaded again after a sign-off
//                  same branching as `loading`'s done above (B14b: a re-sign
//                  of an already-closed/locked period lands straight in
//                  `closed`, not `signed`); another known action → review
//                  with a note; an unknown action or status, or an error →
//                  loadFailed
//   signed         CLOSE {note?} → closing; REFRESH → loading
//   closing        nothing; done → closed; error → signed with the error
//   closed         REOPEN {reason}, non-blank, and the loaded period_status is
//                  not "Locked" → reopening (B14b: a Locked period offers no
//                  Reopen at all)
//   reopening      nothing; done → loading; error → closed with the error
//
// Typed text comes in the event (#298 stage 1: no browser dialogs). A refetch
// always lands in review or signed, never back in a half-typed dialog, so an
// acknowledgement or override typed against an old summary cannot be sent
// against a new one. Only the server saying "signed" reaches `signed` or
// `closed`, so Close (9.3) is offered only after a sign-off that went
// through, and never re-offered once the period reports Closed or Locked.
import { setup, assign, fromPromise } from "xstate";

export const ACTIONS = Object.freeze([
	"signed",
	"blocked",
	"run_checks",
	"rerun",
	"wait",
	"sign",
	"acknowledge",
	"override",
]);

// A49: fiscal_status_model's three effective statuses, exactly.
export const PERIOD_STATUSES = Object.freeze(["Open", "Closed", "Locked"]);

function notProvided(name) {
	return fromPromise(async () => {
		throw new Error(`signoffMachine: the ${name} service was not provided`);
	});
}

/** The message of a rejected service, never blank. */
function messageOf(error) {
	if (error && typeof error.message === "string" && error.message.trim()) return error.message;
	if (typeof error === "string" && error.trim()) return error;
	return "The request failed with no message";
}

/** The trimmed text, or null when it is not text or only whitespace. */
function typed(text) {
	return typeof text === "string" && text.trim() ? text.trim() : null;
}

const known = (output) => Boolean(output && typeof output === "object" && ACTIONS.includes(output.action));

function unknownMessage(output) {
	const action = output && typeof output === "object" ? output.action : output;
	return `Unknown sign-off action ${JSON.stringify(action ?? null)}; expected one of ${ACTIONS.join(", ")}`;
}

const isSignedAction = (output) => known(output) && output.action === "signed";

function unknownStatusMessage(output) {
	return `Unknown period status ${JSON.stringify(output.period_status ?? null)}; expected one of ${PERIOD_STATUSES.join(", ")}`;
}

const allowed = (context, action) =>
	Boolean(context.summary && context.summary.action === action && context.summary.can_sign === true);

const refresh = { target: "#signoff.loading", actions: assign({ error: null }) };

export const signoffMachine = setup({
	actors: {
		load: notProvided("load"),
		sign: notProvided("sign"),
		close: notProvided("close"),
		reopen: notProvided("reopen"),
	},
	guards: {
		unknownAction: ({ event }) => !known(event.output),
		// action "signed" but period_status is missing/not one of the three
		// effective statuses (A49; B14b's failure path: loadFailed, never signed).
		signedStatusUnknown: ({ event }) =>
			isSignedAction(event.output) && !PERIOD_STATUSES.includes(event.output.period_status),
		isSignedOpen: ({ event }) => isSignedAction(event.output) && event.output.period_status === "Open",
		isSignedClosed: ({ event }) => isSignedAction(event.output) && event.output.period_status === "Closed",
		isSignedLocked: ({ event }) => isSignedAction(event.output) && event.output.period_status === "Locked",
		canSign: ({ context }) => allowed(context, "sign"),
		canAcknowledge: ({ context }) => allowed(context, "acknowledge"),
		canOverride: ({ context }) => allowed(context, "override") && context.summary.can_override === true,
		hasText: ({ event }) => typed(event.text) !== null,
		hasReason: ({ event }) => typed(event.reason) !== null,
		// closed: REOPEN is refused outright once the loaded period is Locked (B14b).
		canReopen: ({ context, event }) =>
			typed(event.reason) !== null && context.summary?.period_status !== "Locked",
	},
	actions: {
		keepSummary: assign({ summary: ({ event }) => event.output }),
		refuseUnknown: assign({ summary: null, error: ({ event }) => unknownMessage(event.output) }),
		refuseUnknownStatus: assign({ summary: null, error: ({ event }) => unknownStatusMessage(event.output) }),
	},
}).createMachine({
	id: "signoff",
	initial: "loading",
	context: { summary: null, error: null, closed: null },
	states: {
		loading: {
			invoke: {
				src: "load",
				onDone: [
					{ guard: "unknownAction", target: "loadFailed", actions: "refuseUnknown" },
					{ guard: "signedStatusUnknown", target: "loadFailed", actions: "refuseUnknownStatus" },
					{ guard: "isSignedClosed", target: "closed", actions: "keepSummary" },
					{ guard: "isSignedLocked", target: "closed", actions: "keepSummary" },
					{ guard: "isSignedOpen", target: "signed", actions: "keepSummary" },
					{ target: "review", actions: "keepSummary" },
				],
				onError: {
					target: "loadFailed",
					actions: assign({ summary: null, error: ({ event }) => messageOf(event.error) }),
				},
			},
			on: { REFRESH: { target: "loading", reenter: true } },
		},
		loadFailed: {
			on: { RETRY: refresh },
		},
		review: {
			on: {
				SIGN: { guard: "canSign", target: "signing", actions: assign({ error: null }) },
				ACKNOWLEDGE: { guard: "canAcknowledge", target: "acknowledging", actions: assign({ error: null }) },
				OVERRIDE: { guard: "canOverride", target: "overriding", actions: assign({ error: null }) },
				REFRESH: refresh,
			},
		},
		acknowledging: {
			on: {
				CONFIRM_ACK: { guard: "hasText", target: "signing" },
				CANCEL: "review",
				REFRESH: "loading",
			},
		},
		overriding: {
			on: {
				CONFIRM_OVERRIDE: { guard: "hasText", target: "signing" },
				CANCEL: "review",
				REFRESH: "loading",
			},
		},
		signing: {
			invoke: {
				src: "sign",
				input: ({ event }) => ({
					acknowledgement: event.type === "CONFIRM_ACK" ? typed(event.text) : null,
					override_reason: event.type === "CONFIRM_OVERRIDE" ? typed(event.text) : null,
				}),
				onDone: "confirming",
				onError: {
					target: "review",
					actions: assign({ error: ({ event }) => messageOf(event.error) }),
				},
			},
		},
		confirming: {
			invoke: {
				src: "load",
				onDone: [
					{ guard: "unknownAction", target: "loadFailed", actions: "refuseUnknown" },
					{ guard: "signedStatusUnknown", target: "loadFailed", actions: "refuseUnknownStatus" },
					{ guard: "isSignedClosed", target: "closed", actions: "keepSummary" },
					{ guard: "isSignedLocked", target: "closed", actions: "keepSummary" },
					{ guard: "isSignedOpen", target: "signed", actions: "keepSummary" },
					{
						target: "review",
						actions: assign({
							summary: ({ event }) => event.output,
							error: ({ event }) =>
								`The sign-off was accepted, but the period now reads: ${event.output.label}`,
						}),
					},
				],
				onError: {
					target: "loadFailed",
					actions: assign({
						summary: null,
						error: ({ event }) =>
							`Signed off, but the summary could not be reloaded: ${messageOf(event.error)}`,
					}),
				},
			},
		},
		signed: {
			on: {
				CLOSE: { target: "closing", actions: assign({ error: null }) },
				REFRESH: refresh,
			},
		},
		closing: {
			invoke: {
				src: "close",
				input: ({ event }) => ({ note: typed(event.note) }),
				onDone: {
					target: "closed",
					actions: assign({ closed: ({ event }) => event.output }),
				},
				onError: {
					target: "signed",
					actions: assign({ error: ({ event }) => messageOf(event.error) }),
				},
			},
		},
		closed: {
			on: {
				REOPEN: { guard: "canReopen", target: "reopening", actions: assign({ error: null }) },
			},
		},
		reopening: {
			invoke: {
				src: "reopen",
				input: ({ event }) => ({ reason: typed(event.reason) }),
				onDone: {
					target: "loading",
					actions: assign({ closed: null }),
				},
				onError: {
					target: "closed",
					actions: assign({ error: ({ event }) => messageOf(event.error) }),
				},
			},
		},
	},
});
