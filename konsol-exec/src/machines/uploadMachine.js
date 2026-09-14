/**
 * Bulk trial balance upload: choose a file → it is uploaded and checked →
 * the user loads it (all, or only the ready entity-periods) → progress is
 * polled until the load finishes.
 *
 * Nothing loads until LOAD. A problem with the file itself (a missing
 * column) comes back as a checked upload with status Failed and an error,
 * so the user can read it and choose another file.
 *
 * SET_BASIS ({ amountBasis }) declares what the file's amounts are (one of
 * the three konsol/tb_basis_model.py AMOUNT_BASES) for every entity-period
 * whose rows carry no amount_basis column; it goes with both the check and
 * the load. Setting it on a checked file re-checks, so the report the user
 * reads is the one the load will act on. Blank means not given: the server
 * then refuses those entity-periods by name (konsolidat#199).
 */
import { setup, assign, fromPromise, fromCallback } from "xstate";
import { uploadFile, checkFile, loadUpload, getUpload } from "../uploadApi.js";

const POLL_MS = 2000;
const TERMINAL = new Set(["Loaded", "Partly Loaded", "Failed"]);

export const uploadMachine = setup({
	actors: {
		upload: fromPromise(({ input }) => uploadFile(input.file)),
		check: fromPromise(({ input }) => checkFile(input.fileUrl, input.amountBasis)),
		load: fromPromise(({ input }) => loadUpload(input.name, input.skipInvalid, input.amountBasis)),
		fetchUpload: fromPromise(({ input }) => getUpload(input.name)),
		ticker: fromCallback(({ sendBack }) => {
			const id = setInterval(() => sendBack({ type: "TICK" }), POLL_MS);
			return () => clearInterval(id);
		}),
	},
	guards: {
		// Done, or stalled: Loading with no job left to finish it.
		finished: ({ event }) => TERMINAL.has(event.output?.status) || Boolean(event.output?.stalled),
		refused: ({ event }) => Boolean(event.output?.refused),
	},
	actions: {
		choose: assign({ file: ({ event }) => event.file, fileUrl: null, upload: null, error: null }),
		assignUpload: assign({ upload: ({ event }) => event.output, error: null }),
		assignError: assign({ error: ({ event }) => event.error }),
		setBasis: assign({ amountBasis: ({ event }) => event.amountBasis || "" }),
		reset: assign({ file: null, fileUrl: null, upload: null, error: null, skipInvalid: false, amountBasis: "" }),
	},
}).createMachine({
	id: "upload",
	initial: "idle",
	context: { file: null, fileUrl: null, upload: null, error: null, skipInvalid: false, amountBasis: "" },
	states: {
		idle: {
			on: {
				CHOOSE: { target: "uploading", actions: "choose" },
				SET_BASIS: { actions: "setBasis" },
			},
		},
		uploading: {
			invoke: {
				src: "upload",
				input: ({ context }) => ({ file: context.file }),
				onDone: { target: "checking", actions: assign({ fileUrl: ({ event }) => event.output }) },
				onError: { target: "idle", actions: "assignError" },
			},
		},
		checking: {
			invoke: {
				src: "check",
				input: ({ context }) => ({ fileUrl: context.fileUrl, amountBasis: context.amountBasis }),
				onDone: { target: "checked", actions: "assignUpload" },
				onError: { target: "idle", actions: "assignError" },
			},
		},
		checked: {
			on: {
				LOAD: { target: "starting", actions: assign({ skipInvalid: ({ event }) => Boolean(event.skipInvalid), error: null }) },
				// A new basis changes which entity-periods are ready: check again.
				SET_BASIS: { target: "checking", actions: "setBasis" },
				CHOOSE: { target: "uploading", actions: "choose" },
				RESET: { target: "idle", actions: "reset" },
			},
		},
		starting: {
			invoke: {
				src: "load",
				input: ({ context }) => ({ name: context.upload.name, skipInvalid: context.skipInvalid, amountBasis: context.amountBasis }),
				onDone: [
					// The server re-checked and found new problems: show the fresh
					// report and why, so the user can skip them or fix the file.
					{
						guard: "refused",
						target: "checked",
						actions: [
							"assignUpload",
							assign({ error: ({ event }) => new Error(event.output.refused) }),
						],
					},
					{ target: "loading", actions: "assignUpload" },
				],
				onError: { target: "checked", actions: "assignError" },
			},
		},
		loading: {
			initial: "waiting",
			invoke: { src: "ticker" },
			states: {
				waiting: { on: { TICK: "fetching" } },
				fetching: {
					invoke: {
						src: "fetchUpload",
						input: ({ context }) => ({ name: context.upload.name }),
						onDone: [
							{ guard: "finished", target: "#upload.done", actions: "assignUpload" },
							{ target: "waiting", actions: "assignUpload" },
						],
						// A missed poll is not a failed load: keep polling.
						onError: { target: "waiting" },
					},
				},
			},
		},
		done: {
			on: {
				// Resume a load that stopped or finished partly; loaded rows are skipped.
				LOAD: { target: "starting", actions: assign({ skipInvalid: ({ event }) => Boolean(event.skipInvalid), error: null }) },
				SET_BASIS: { actions: "setBasis" },
				CHOOSE: { target: "uploading", actions: "choose" },
				RESET: { target: "idle", actions: "reset" },
			},
		},
	},
});
