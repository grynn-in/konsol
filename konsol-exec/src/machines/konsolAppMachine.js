import { setup, assign, fromPromise, fromCallback, raise } from "xstate";
import { getSnapshot, sendReminder, startProcess } from "../api";
import { hasActiveRuns } from "./helpers";

const POLL_MS = 2000;

export const konsolAppMachine = setup({
	types: {
		context: {},
		events: {},
	},
	actors: {
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
		assignSnapshot: assign({
			data: ({ event }) => event.output,
			loadError: null,
		}),
		assignLoadError: assign({
			loadError: ({ event }) => event.error,
		}),
		assignStartResult: assign({
			lastStartResult: ({ event }) => event.output,
		}),
		clearToast: assign({ toast: null }),
		setSection: assign({
			section: ({ event }) => event.section,
		}),
		setSubview: assign({
			subview: ({ event }) => event.subview,
		}),
		navigateDomain: assign({
			section: ({ event }) => event.domain,
			subview: ({ event }) => event.subview || "setup",
		}),
		toggleTheme: assign({
			dark: ({ context }) => !context.dark,
		}),
	},
}).createMachine({
	id: "konsolApp",
	initial: "loading",
	on: {
		START_SUCCEEDED: {
			actions: assign({
				section: ({ event }) => event.processId,
				subview: "monitor",
				toast: ({ event }) => `Started ${event.processId} · ${event.output.name}`,
			}),
		},
		START_FAILED: {
			actions: assign({
				toast: ({ event }) =>
					`Start failed: ${event.error?.message || String(event.error)}`,
			}),
		},
		REMIND_SUCCEEDED: {
			actions: assign({
				toast: ({ event }) =>
					`Reminder sent to ${event.owner} · ${new Date().toLocaleTimeString("en-GB")}`,
			}),
		},
		REMIND_FAILED: {
			actions: assign({
				toast: ({ event }) =>
					`Reminder failed: ${event.error?.message || String(event.error)}`,
			}),
		},
		DISMISS_TOAST: {
			actions: "clearToast",
		},
	},
	context: {
		section: "overview",
		subview: "setup",
		dark: false,
		toast: null,
		data: null,
		loadError: null,
		lastStartResult: null,
		pendingProcessId: null,
		pendingReminder: null,
	},
	states: {
		loading: {
			invoke: {
				src: "fetchSnapshot",
				onDone: {
					target: "ready",
					actions: "assignSnapshot",
				},
				onError: {
					target: "failed",
					actions: "assignLoadError",
				},
			},
		},
		failed: {
			on: {
				RETRY: "loading",
			},
		},
		refreshing: {
			invoke: {
				src: "fetchSnapshot",
				onDone: {
					target: "ready",
					actions: "assignSnapshot",
				},
				onError: {
					target: "ready",
					actions: "assignLoadError",
				},
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
						raise(({ event }) => ({
							type: "START_FAILED",
							error: event.error,
						})),
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
						raise(({ event }) => ({
							type: "REMIND_FAILED",
							error: event.error,
						})),
						assign({ pendingReminder: null }),
					],
				},
			},
		},
		ready: {
			type: "parallel",
			on: {
				REFRESH: "refreshing",
				START_PROCESS: {
					target: "starting",
					actions: assign({
						pendingProcessId: ({ event }) => event.processId,
					}),
				},
				REMIND: {
					target: "reminding",
					actions: assign({
						pendingReminder: ({ event }) => ({
							owner: event.owner,
							item: event.item,
						}),
					}),
				},
				SELECT_SECTION: {
					actions: [
						"setSection",
						assign({
							subview: ({ event, context }) =>
								event.section === "overview"
									? context.subview
									: event.section === context.section
										? context.subview
										: "setup",
						}),
					],
				},
				SELECT_SUBVIEW: {
					actions: "setSubview",
				},
				NAVIGATE_DOMAIN: {
					actions: "navigateDomain",
				},
				TOGGLE_THEME: {
					actions: "toggleTheme",
				},
				OPEN_SETUP: {
					actions: assign({ subview: "setup" }),
				},
				OPEN_MONITOR: {
					actions: assign({ subview: "monitor" }),
				},
				RESOLVE_SETUP: {
					actions: assign({ subview: "setup" }),
				},
			},
			states: {
				poll: {
					invoke: {
						src: "pollTicker",
					},
					on: {
						POLL_TICK: {
							guard: "shouldPoll",
							target: "#konsolApp.refreshing",
						},
					},
				},
				shell: {},
			},
		},
	},
});