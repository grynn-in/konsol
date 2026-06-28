import { setup, assign, fromPromise } from "xstate";
import { startRun, getRun, retryStep, resumeRun, cancelRun } from "../api";
import { normalizeRun } from "../orchestrator/runModel";
import { isTerminal } from "../orchestrator/status";

/**
 * Exec state machine — drives a complete orchestrator run from the SPA.
 *
 * Effects (api client) are invoked as `fromPromise` actors; all data-shaping
 * stays in the pure ESM core (`runModel.normalizeRun`, `status.isTerminal`).
 * The machine only orchestrates transitions:
 *
 *   idle --LAUNCH--> launching --(startRun)--> watching
 *   watching --REFRESH | RUN_STEP--> refreshing --(getRun→normalizeRun)--> back
 *   refreshing settles to `done` when `isTerminal(run.status)`, else `watching`
 *   watching/done --RETRY_STEP--> retrying ; --RESUME_FROM--> resuming ;
 *                  --CANCEL--> cancelling   (each re-enqueues then refreshes)
 *
 * Context: `{ run, error }` — `run` is the normalized view-model (or null),
 * `error` the last effect error (or null).
 */
export const runExecMachine = setup({
	types: {
		context: {},
		events: {},
	},
	actors: {
		launchRun: fromPromise(({ input }) =>
			startRun(input.definition, input.params),
		),
		fetchRun: fromPromise(({ input }) => getRun(input.name)),
		retryStepActor: fromPromise(({ input }) =>
			retryStep(input.name, input.stepId),
		),
		resumeRunActor: fromPromise(({ input }) =>
			resumeRun(input.name, input.stepId),
		),
		cancelRunActor: fromPromise(({ input }) => cancelRun(input.name)),
	},
	guards: {
		runSettled: ({ context }) => isTerminal(context.run?.status),
	},
	actions: {
		assignRunName: assign({
			run: ({ event }) => ({ name: event.output, status: "Queued", steps: [] }),
			error: null,
		}),
		assignRun: assign({
			run: ({ event }) => normalizeRun(event.output),
			error: null,
		}),
		assignError: assign({
			error: ({ event }) => event.error,
		}),
		rememberStep: assign({
			pendingStepId: ({ event }) => event.stepId,
		}),
	},
}).createMachine({
	id: "runExec",
	initial: "idle",
	context: {
		run: null,
		error: null,
		pendingStepId: null,
	},
	states: {
		idle: {
			on: {
				LAUNCH: "launching",
			},
		},
		launching: {
			invoke: {
				src: "launchRun",
				input: ({ event }) => ({
					definition: event.definition,
					params: event.params,
				}),
				onDone: {
					target: "refreshing",
					actions: "assignRunName",
				},
				onError: {
					target: "idle",
					actions: "assignError",
				},
			},
		},
		refreshing: {
			invoke: {
				src: "fetchRun",
				input: ({ context }) => ({ name: context.run?.name }),
				onDone: [
					{
						target: "done",
						guard: "runSettled",
						actions: "assignRun",
					},
					{
						target: "watching",
						actions: "assignRun",
					},
				],
				onError: {
					target: "watching",
					actions: "assignError",
				},
			},
		},
		watching: {
			on: {
				REFRESH: "refreshing",
				RUN_STEP: "refreshing",
				RETRY_STEP: { target: "retrying", actions: "rememberStep" },
				RESUME_FROM: { target: "resuming", actions: "rememberStep" },
				CANCEL: "cancelling",
			},
		},
		retrying: {
			invoke: {
				src: "retryStepActor",
				input: ({ context }) => ({
					name: context.run?.name,
					stepId: context.pendingStepId,
				}),
				onDone: { target: "refreshing" },
				onError: { target: "watching", actions: "assignError" },
			},
		},
		resuming: {
			invoke: {
				src: "resumeRunActor",
				input: ({ context }) => ({
					name: context.run?.name,
					stepId: context.pendingStepId,
				}),
				onDone: { target: "refreshing" },
				onError: { target: "watching", actions: "assignError" },
			},
		},
		cancelling: {
			invoke: {
				src: "cancelRunActor",
				input: ({ context }) => ({ name: context.run?.name }),
				onDone: { target: "refreshing" },
				onError: { target: "watching", actions: "assignError" },
			},
		},
		done: {
			on: {
				REFRESH: "refreshing",
				RUN_STEP: "refreshing",
				RETRY_STEP: { target: "retrying", actions: "rememberStep" },
				RESUME_FROM: { target: "resuming", actions: "rememberStep" },
				LAUNCH: "launching",
			},
		},
	},
});
