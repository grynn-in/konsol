// konsol#305 B11: tbUploadMachine.js
//
// One entity-period's trial-balance upload (D1, D4): choose a file → it is
// read → the server checks it (A19 `check_tb`, which writes nothing) → the
// user submits it (A24 `submit_tb`) → received.
//
// The services are injected by the screen with machine.provide({actors}):
//   readFile({file})                    → the file's text
//   check({file, content})              → A19's result {ok, rows, file_problems, totals, period_problem, replaces}
//   submit({file, content, replaces})   → A24's result {name, replaced, on_behalf}
// The screen closes over the entity, period and amount basis. The defaults
// below refuse by name, so a screen that forgets one fails visibly.
//
// States and the events each takes (every other event is refused):
//   idle              FILE_CHOSEN → reading
//   reading           FILE_CHOSEN → reading (restarts; the old read is dropped)
//                     done → checking; error → readFailed
//   readFailed        FILE_CHOSEN → reading
//   checking          FILE_CHOSEN → reading (the old check is dropped)
//                     done → checked.ok | checked.problems; error → checkFailed
//   checkFailed       RETRY → checking (same content); FILE_CHOSEN → reading
//   checked.ok        SUBMIT → submitting; FILE_CHOSEN → reading
//   checked.problems  FILE_CHOSEN → reading
//   submitting        nothing: a submit in flight is never abandoned
//                     done → received; error → checked.ok with the error
//   received          final
//
// D1: there is no row editing. The only way out of checked.problems is a new
// file. A result goes to checked.ok only when the file is ok AND the server
// names no period_problem (a period that is not Open cannot take a submit).
// Choosing a file clears everything the previous file left behind, and a
// service started for an earlier file is stopped with its state, so its
// result can never land on the new one.
import { setup, assign, fromPromise } from "xstate";

function notProvided(name) {
	return fromPromise(async () => {
		throw new Error(`tbUploadMachine: the ${name} service was not provided`);
	});
}

/** The message of a rejected service, never blank. */
function messageOf(error) {
	if (error && typeof error.message === "string" && error.message) return error.message;
	if (error === undefined || error === null || error === "") return "The request failed with no message";
	return String(error);
}

const EMPTY = { content: null, result: null, replaces: null, error: null, received: null };

const chooseFile = {
	target: "#tbUpload.reading",
	reenter: true,
	actions: assign(({ event }) => ({ ...EMPTY, file: event.file })),
};

export const tbUploadMachine = setup({
	actors: {
		readFile: notProvided("readFile"),
		check: notProvided("check"),
		submit: notProvided("submit"),
	},
	actions: {
		keepResult: assign({
			result: ({ event }) => event.output,
			replaces: ({ event }) => (event.output && event.output.replaces) || null,
			error: null,
		}),
	},
	guards: {
		submittable: ({ event }) =>
			Boolean(event.output && event.output.ok === true && !event.output.period_problem),
	},
}).createMachine({
	id: "tbUpload",
	initial: "idle",
	context: { file: null, ...EMPTY },
	states: {
		idle: {
			on: { FILE_CHOSEN: chooseFile },
		},
		reading: {
			invoke: {
				src: "readFile",
				input: ({ context }) => ({ file: context.file }),
				onDone: {
					target: "checking",
					actions: assign({ content: ({ event }) => event.output }),
				},
				onError: {
					target: "readFailed",
					// content/result/replaces are already null: chooseFile cleared them.
					actions: assign({ error: ({ event }) => messageOf(event.error) }),
				},
			},
			on: { FILE_CHOSEN: chooseFile },
		},
		readFailed: {
			on: { FILE_CHOSEN: chooseFile },
		},
		checking: {
			invoke: {
				src: "check",
				input: ({ context }) => ({ file: context.file, content: context.content }),
				onDone: [
					{
						guard: "submittable",
						target: "checked.ok",
						actions: "keepResult",
					},
					{ target: "checked.problems", actions: "keepResult" },
				],
				onError: {
					target: "checkFailed",
					// result/replaces are already null: checking is entered only from
					// reading (after chooseFile cleared them) or from checkFailed.
					actions: assign({ error: ({ event }) => messageOf(event.error) }),
				},
			},
			on: { FILE_CHOSEN: chooseFile },
		},
		checkFailed: {
			on: {
				RETRY: { target: "checking", actions: assign({ error: null }) },
				FILE_CHOSEN: chooseFile,
			},
		},
		checked: {
			// Every transition names its child; the initial is the safe one.
			initial: "problems",
			on: { FILE_CHOSEN: chooseFile },
			states: {
				ok: {
					on: {
						SUBMIT: { target: "#tbUpload.submitting", actions: assign({ error: null }) },
					},
				},
				problems: {},
			},
		},
		submitting: {
			invoke: {
				src: "submit",
				input: ({ context }) => ({
					file: context.file,
					content: context.content,
					replaces: context.replaces,
				}),
				onDone: {
					target: "received",
					actions: assign({ received: ({ event }) => event.output, error: null }),
				},
				onError: {
					target: "checked.ok",
					actions: assign({ received: null, error: ({ event }) => messageOf(event.error) }),
				},
			},
		},
		received: { type: "final" },
	},
});
