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

/**
 * Options first, then the snapshot for the period they imply.
 *
 * Sequential rather than parallel, and deliberately: the snapshot reports the
 * close state of *one* period, and which period that is comes out of the
 * options. Fetching both at once would mean a first paint whose period block
 * is always empty, then a second request to fill it — one extra round trip on
 * a cold start buys a shell that is correct the first time it renders.
 */
export async function loadPlane(period, previousOptions = null, previousOptionsYear = null) {
	// Ask for the shown year's declared periods, not always the newest one
	// (review finding 4b, PR #192): with no period known yet (the very first
	// load) there is no year to ask for, so this still gets the newest. A
	// failed launch_options request keeps whatever options were already
	// loaded (review finding 1, PR #192 re-review) rather than blanking the
	// period labels to null until the next successful refresh — but ONLY when
	// those previous options were fetched for the SAME fiscal year as the one
	// now being requested (re-review 2 finding 1): otherwise a failed refetch
	// while moving from FY2025 to FY2026 would keep showing FY2025's periods
	// labelled as FY2026's. When the year truly can't be told apart (no
	// period known yet on either side), that counts as "same".
	const requestedYear = period?.year ?? null;
	const sameYear = requestedYear === previousOptionsYear;
	let options;
	let optionsYear;
	try {
		options = await getLaunchOptions(requestedYear);
		// No year was requested (the very first load): the server picked its
		// newest declared year, so that's whose periods these are.
		optionsYear = requestedYear ?? options?.fiscal_years?.[0] ?? null;
	} catch {
		options = sameYear ? previousOptions : null;
		optionsYear = sameYear ? previousOptionsYear : null;
	}
	const resolved = period || defaultPeriod(options);
	const data = await getSnapshot(resolved);
	return { data, options, optionsYear, period: resolved };
}

/**
 * Snapshot-only refresh: the poll tick, the Refresh button, and the refresh
 * after a Start all re-read the close state of the SAME period. None of them
 * change which fiscal year is shown, so none of them need launch_options
 * again — only SET_PERIOD does (see `loadPlane`).
 */
export async function loadSnapshot(period) {
	const data = await getSnapshot(period);
	return { data, period };
}

export const closeMachine = setup({
	types: { context: {}, events: {} },
	actors: {
		fetchPlane: fromPromise(({ input }) =>
			loadPlane(input?.period, input?.previousOptions, input?.previousOptionsYear)
		),
		fetchSnapshot: fromPromise(({ input }) => loadSnapshot(input?.period)),
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
			optionsYear: ({ event }) => event.output.optionsYear,
			loadError: null,
			period: ({ event }) => event.output.period,
		}),
		assignSnapshot: assign({
			data: ({ event }) => event.output.data,
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
		optionsYear: null,
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
				input: ({ context }) => ({
					period: context.period,
					previousOptions: context.options,
					previousOptionsYear: context.optionsYear,
				}),
				onDone: { target: "ready", actions: "assignPlane" },
				onError: { target: "failed", actions: "assignLoadError" },
			},
		},
		failed: { on: { RETRY: "loading" } },
		// A plain REFRESH (the button, the poll tick, and the refresh after a
		// Start) re-reads the SAME period's close state. It never needs a new
		// set of launch_options — only SET_PERIOD does (review finding 1, PR
		// #192 re-review: re-asking launch_options on every 2s poll tick was
		// pointless network traffic, and a failed poll blanked the loaded
		// options to null until the next tick).
		refreshing: {
			invoke: {
				src: "fetchSnapshot",
				input: ({ context }) => ({ period: context.period }),
				onDone: { target: "ready", actions: "assignSnapshot" },
				onError: { target: "ready", actions: "assignLoadError" },
			},
		},
		// SET_PERIOD alone gets the full reload: a step page shown for a
		// different fiscal year needs THAT year's launch_options (review
		// finding 4b, PR #192), not the stale one from whichever year loaded
		// first. A failed launch_options request here keeps the previous
		// options (`loadPlane`'s `previousOptions` fallback) instead of
		// blanking period labels to null.
		// A REJECTED period change goes to `failed`, not back to `ready`
		// (re-review 2 finding 2, PR #192): `ready` with the new period but the
		// old data/options would show the wrong period's close state with no
		// visible error, and a plain Refresh from there would only reload the
		// snapshot, leaving the options wrong forever. `failed` shows the
		// error, and RETRY re-runs this same full load (options + snapshot)
		// for the new period, since `context.period` was already updated by
		// `setPeriod` before this state was entered.
		changingPeriod: {
			invoke: {
				src: "fetchPlane",
				input: ({ context }) => ({
					period: context.period,
					previousOptions: context.options,
					previousOptionsYear: context.optionsYear,
				}),
				onDone: { target: "ready", actions: "assignPlane" },
				onError: { target: "failed", actions: "assignLoadError" },
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
				SET_PERIOD: { target: "changingPeriod", actions: "setPeriod" },
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
 *
 * "Newest" is decided by value, never by list position: the server
 * (`orchestrator.api.launch_options`) sends `fiscal_years` newest first, so
 * the last entry is the OLDEST year, not the newest.
 *
 * When no fiscal year is declared at all, there is nothing to default to —
 * not even today's calendar year (konsol#189 removes every such guess).
 */
export function defaultPeriod(options) {
	const years = (options?.fiscal_years || []).map(String);
	if (!years.length) return { year: null, period: null };

	const real = accountingPeriods(options);
	const all = (options?.fiscal_periods || []).map((p) => String(p.value));
	const last = real.length ? real[real.length - 1].value : all[all.length - 1];
	const newestYear = years.reduce((newest, y) => (Number(y) > Number(newest) ? y : newest));
	return {
		year: newestYear,
		period: last ?? "",
	};
}
