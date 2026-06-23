import { setup, assign, fromPromise } from "xstate";
import { getRunDetail } from "../api";
import { clearRunDetail } from "./helpers";

export const runDetailMachine = setup({
	types: {
		context: {},
		events: {},
	},
	actors: {
		fetchRunDetail: fromPromise(({ input }) =>
			getRunDetail(input.domain, input.kind, input.runId)
		),
	},
	actions: {
		assignSelection: assign({
			domain: ({ event }) => event.domain,
			selected: ({ event }) => event.run,
			detail: null,
			error: null,
		}),
		clearSelection: assign(clearRunDetail),
		assignDetail: assign({
			detail: ({ event }) => event.output,
			error: null,
		}),
		assignDetailError: assign({
			error: ({ event }) => event.error,
		}),
	},
}).createMachine({
	id: "runDetail",
	initial: "idle",
	context: {
		domain: null,
		selected: null,
		detail: null,
		error: null,
	},
	states: {
		idle: {
			on: {
				SELECT: {
					target: "loading",
					actions: "assignSelection",
				},
				DOMAIN_CHANGED: {
					actions: "clearSelection",
				},
			},
		},
		loading: {
			invoke: {
				src: "fetchRunDetail",
				input: ({ context }) => ({
					domain: context.domain,
					kind: context.selected.kind,
					runId: context.selected.id,
				}),
				onDone: {
					target: "ready",
					actions: "assignDetail",
				},
				onError: {
					target: "error",
					actions: "assignDetailError",
				},
			},
			on: {
				DESELECT: {
					target: "idle",
					actions: "clearSelection",
				},
				SELECT: {
					target: "loading",
					reenter: true,
					actions: "assignSelection",
				},
				DOMAIN_CHANGED: {
					target: "idle",
					actions: "clearSelection",
				},
			},
		},
		ready: {
			on: {
				DESELECT: {
					target: "idle",
					actions: "clearSelection",
				},
				SELECT: {
					target: "loading",
					actions: "assignSelection",
				},
				RETRY: "loading",
				DOMAIN_CHANGED: {
					target: "idle",
					actions: "clearSelection",
				},
			},
		},
		error: {
			on: {
				DESELECT: {
					target: "idle",
					actions: "clearSelection",
				},
				RETRY: "loading",
				SELECT: {
					target: "loading",
					actions: "assignSelection",
				},
				DOMAIN_CHANGED: {
					target: "idle",
					actions: "clearSelection",
				},
			},
		},
	},
});