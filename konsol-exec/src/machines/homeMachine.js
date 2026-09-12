/**
 * Workspace machine: who is looking and the fiscal tree (once), then one
 * month at a time.
 *
 * The month follows the URL: the shell sends OPEN whenever the route names a
 * period, and a later OPEN re-enters `loading`, which cancels the request for
 * the month the user has already left. A slow ticker keeps the open month
 * fresh while people work in the desk in another tab.
 */
import { setup, assign, fromPromise, fromCallback } from "xstate";
import { whoami, periodTree, getMonth } from "../homeApi.js";

const POLL_MS = 15000;

export const homeMachine = setup({
	actors: {
		boot: fromPromise(async () => {
			const [me, tree] = await Promise.all([whoami(), periodTree()]);
			return { me, tree };
		}),
		loadMonth: fromPromise(({ input }) => getMonth(input.year, input.period)),
		ticker: fromCallback(({ sendBack }) => {
			const id = setInterval(() => sendBack({ type: "TICK" }), POLL_MS);
			return () => clearInterval(id);
		}),
	},
	guards: {
		hasWant: ({ context }) => Boolean(context.want),
	},
	actions: {
		setWant: assign({
			want: ({ event }) => ({ year: Number(event.year), period: Number(event.period) }),
		}),
		assignBoot: assign({
			me: ({ event }) => event.output.me,
			tree: ({ event }) => event.output.tree,
			error: null,
		}),
		assignMonth: assign({ month: ({ event }) => event.output, error: null }),
		assignError: assign({ error: ({ event }) => event.error }),
	},
}).createMachine({
	id: "home",
	initial: "booting",
	context: { me: null, tree: null, month: null, want: null, error: null },
	states: {
		booting: {
			on: { OPEN: { actions: "setWant" } },
			invoke: {
				src: "boot",
				onDone: [
					{ guard: "hasWant", target: "ready.loading", actions: "assignBoot" },
					{ target: "ready.idle", actions: "assignBoot" },
				],
				onError: { target: "failed", actions: "assignError" },
			},
		},
		failed: { on: { RETRY: "booting" } },
		ready: {
			invoke: { src: "ticker" },
			on: {
				OPEN: { target: ".loading", reenter: true, actions: "setWant" },
				TICK: { guard: "hasWant", target: ".loading", reenter: true },
				REFRESH: { guard: "hasWant", target: ".loading", reenter: true },
			},
			states: {
				idle: {},
				loading: {
					invoke: {
						src: "loadMonth",
						input: ({ context }) => context.want,
						onDone: { target: "idle", actions: "assignMonth" },
						onError: { target: "idle", actions: "assignError" },
					},
				},
			},
		},
	},
});
