/**
 * Bulk trial balance upload: choose a file → it is uploaded and checked →
 * the user loads it (all, or only the ready entity-periods) → progress is
 * polled until the load finishes.
 *
 * Nothing loads until LOAD. A problem with the file itself (a missing
 * column) comes back as a checked upload with status Failed and an error,
 * so the user can read it and choose another file.
 */
import { setup, assign, fromPromise, fromCallback } from "xstate";
import { uploadFile, checkFile, loadUpload, getUpload } from "../uploadApi.js";

const POLL_MS = 2000;
const TERMINAL = new Set(["Loaded", "Partly Loaded", "Failed"]);

export const uploadMachine = setup({
	actors: {
		upload: fromPromise(({ input }) => uploadFile(input.file)),
		check: fromPromise(({ input }) => checkFile(input.fileUrl)),
		load: fromPromise(({ input }) => loadUpload(input.name, input.skipInvalid)),
		fetchUpload: fromPromise(({ input }) => getUpload(input.name)),
		ticker: fromCallback(({ sendBack }) => {
			const id = setInterval(() => sendBack({ type: "TICK" }), POLL_MS);
			return () => clearInterval(id);
		}),
	},
	guards: {
		finished: ({ event }) => TERMINAL.has(event.output?.status),
	},
	actions: {
		choose: assign({ file: ({ event }) => event.file, fileUrl: null, upload: null, error: null }),
		assignUpload: assign({ upload: ({ event }) => event.output, error: null }),
		assignError: assign({ error: ({ event }) => event.error }),
		reset: assign({ file: null, fileUrl: null, upload: null, error: null, skipInvalid: false }),
	},
}).createMachine({
	id: "upload",
	initial: "idle",
	context: { file: null, fileUrl: null, upload: null, error: null, skipInvalid: false },
	states: {
		idle: {
			on: { CHOOSE: { target: "uploading", actions: "choose" } },
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
				input: ({ context }) => ({ fileUrl: context.fileUrl }),
				onDone: { target: "checked", actions: "assignUpload" },
				onError: { target: "idle", actions: "assignError" },
			},
		},
		checked: {
			on: {
				LOAD: { target: "starting", actions: assign({ skipInvalid: ({ event }) => Boolean(event.skipInvalid), error: null }) },
				CHOOSE: { target: "uploading", actions: "choose" },
				RESET: { target: "idle", actions: "reset" },
			},
		},
		starting: {
			invoke: {
				src: "load",
				input: ({ context }) => ({ name: context.upload.name, skipInvalid: context.skipInvalid }),
				onDone: { target: "loading", actions: "assignUpload" },
				// The server re-checks before loading; its refusal is shown on the checked upload.
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
				CHOOSE: { target: "uploading", actions: "choose" },
				RESET: { target: "idle", actions: "reset" },
			},
		},
	},
});
