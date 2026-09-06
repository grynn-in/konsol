/**
 * Close-plane machine.
 *
 * Reworked from konsolAppMachine. What changed and why:
 *
 *  - Navigation left the machine. The router owns section/subview now, so
 *    SELECT_SECTION / SELECT_SUBVIEW / NAVIGATE_DOMAIN / OPEN_* / RESOLVE_SETUP
 *    are gone. A machine that also stores "which tab is open" makes every
 *    back-button bug a state-chart bug.
 *
 *  - Theme left the machine (U10). frappe-ui follows the desk's theme; a
 *    second, unpersisted toggle was one preference too many.
 *
 *  - Period arrived (U8). It is the spine of the close-first arrangement, so
 *    it is machine state, not a form field buried in Execute.
 *
 * Everything else — the load / refresh / start / remind lifecycle, the poll
 * ticker gated on active runs, and the toast events — is carried over
 * unchanged, because that part was already right.
 */
import { setup, assign, fromPromise, fromCallback, raise } from "xstate";
import { getSnapshot, getLaunchOptions, sendReminder, startProcess } from "../api.js";
import { hasActiveRuns } from "./helpers.js";
import { accountingPeriods } from "../period.js";

const POLL_MS = 2000;

/** Snapshot + launch options in one shot: the period selector cannot render
 *  until it knows which periods exist, so the shell waits for both. */
async function loadPlane() {
	const [data, options] = await Promise.all([
		getSnapshot(),
		getLaunchOptions().catch(() => null),
	]);
	return { data, options };
}

export const closeMachine = setup({
	types: { context: {}, events: {} },
	actors: {
		fetchPlane: fromPromise(() => loadPlane()),
		fetchSnapshot: fromPromise(() => getSnapshot()),
		startProcessActor: fromPromise(({ input }) => startProcess(input.processId)),
		sendReminderActor: fromPromise(({ input }) => sendReminder(input.owner, input.item)),
		pollTicker: fromCallback(({ sendBack }) => {
			const id = setInterval(() => sendBack({ type: "POLL_TICK" }), POLL_MS);
			return () => clearInterval(id);
		}),
	},
	guards: {
		shouldPoll: ({ context }) => hasActiveRuns(context.data),
	},
	actions: {
		assignPlane: assign({
			data: ({ event }) => event.output.data,
			options: ({ event }) => event.output.options,
			loadError: null,
			period: ({ event, context }) => context.period || defaultPeriod(event.output.options),
		}),
		assignSnapshot: assign({
			data: ({ event }) => event.output,
			loadError: null,
		}),
		assignLoadError: assign({ loadError: ({ event }) => event.error }),
		assignStartResult: assign({ lastStartResult: ({ event }) => event.output }),
		clearToast: assign({ toast: null }),
		setPeriod: assign({
			period: ({ event }) => ({ year: event.year, period: event.period }),
		}),
	},
}).createMachine({
	id: "close",
	initial: "loading",
	context: {
		data: null,
		options: null,
		period: null,
		loadError: null,
		toast: null,
		lastStartResult: null,
		pendingProcessId: null,
		pendingReminder: null,
	},
	on: {
		START_SUCCEEDED: {
			actions: assign({
				toast: ({ event }) => ({
					theme: "green",
					text: `Started ${event.processId} · ${event.output?.name || "run"}`,
				}),
			}),
		},
		START_FAILED: {
			actions: assign({
				toast: ({ event }) => ({
					theme: "red",
					text: `Start failed: ${event.error?.message || String(event.error)}`,
				}),
			}),
		},
		REMIND_SUCCEEDED: {
			actions: assign({
				toast: ({ event }) => ({ theme: "green", text: `Reminder sent to ${event.owner}` }),
			}),
		},
		REMIND_FAILED: {
			actions: assign({
				toast: ({ event }) => ({
					theme: "red",
					text: `Reminder failed: ${event.error?.message || String(event.error)}`,
				}),
			}),
		},
		DISMISS_TOAST: { actions: "clearToast" },
	},
	states: {
		loading: {
			invoke: {
				src: "fetchPlane",
				onDone: { target: "ready", actions: "assignPlane" },
				onError: { target: "failed", actions: "assignLoadError" },
			},
		},
		failed: { on: { RETRY: "loading" } },
		refreshing: {
			invoke: {
				src: "fetchSnapshot",
				onDone: { target: "ready", actions: "assignSnapshot" },
				onError: { target: "ready", actions: "assignLoadError" },
			},
		},
		starting: {
			invoke: {
				src: "startProcessActor",
				input: ({ context }) => ({ processId: context.pendingProcessId }),
				onDone: {
					target: "refreshing",
					actions: [
						"assignStartResult",
						raise(({ event, context }) => ({
							type: "START_SUCCEEDED",
							output: event.output,
							processId: context.pendingProcessId,
						})),
						assign({ pendingProcessId: null }),
					],
				},
				onError: {
					target: "ready",
					actions: [
						raise(({ event }) => ({ type: "START_FAILED", error: event.error })),
						assign({ pendingProcessId: null }),
					],
				},
			},
		},
		reminding: {
			invoke: {
				src: "sendReminderActor",
				input: ({ context }) => context.pendingReminder,
				onDone: {
					target: "ready",
					actions: [
						raise(({ context }) => ({
							type: "REMIND_SUCCEEDED",
							owner: context.pendingReminder?.owner,
						})),
						assign({ pendingReminder: null }),
					],
				},
				onError: {
					target: "ready",
					actions: [
						raise(({ event }) => ({ type: "REMIND_FAILED", error: event.error })),
						assign({ pendingReminder: null }),
					],
				},
			},
		},
		ready: {
			type: "parallel",
			on: {
				REFRESH: "refreshing",
				SET_PERIOD: { actions: "setPeriod" },
				START_PROCESS: {
					target: "starting",
					actions: assign({ pendingProcessId: ({ event }) => event.processId }),
				},
				REMIND: {
					target: "reminding",
					actions: assign({
						pendingReminder: ({ event }) => ({ owner: event.owner, item: event.item }),
					}),
				},
			},
			states: {
				poll: {
					invoke: { src: "pollTicker" },
					on: {
						POLL_TICK: { guard: "shouldPoll", target: "#close.refreshing" },
					},
				},
				shell: {},
			},
		},
	},
});

/**
 * Where the app opens.
 *
 * Newest fiscal year, and the latest *accounting* period in it — the close in
 * progress is almost always the most recent one. Deliberately not simply "the
 * last period in the list": a real fiscal calendar ends with an adjustment
 * period (CLS), and opening the console on CLS would be wrong every month of
 * the year except one.
 */
export function defaultPeriod(options) {
	const years = (options?.fiscal_years || []).map(String);
	const real = accountingPeriods(options);
	const all = (options?.fiscal_periods || []).map((p) => String(p.value));
	const last = real.length ? real[real.length - 1].value : all[all.length - 1];
	return {
		year: years.length ? years[years.length - 1] : String(new Date().getFullYear()),
		period: last ?? "",
	};
}
