/**
 * Workspace machine: who is looking and the fiscal tree (once), then one
 * month at a time.
 *
 * The month follows the URL: the shell sends OPEN whenever the route names a
 * period. OPEN is kept in every state (booting, failed, ready), so a period
 * chosen before boot or while boot has failed is the one loaded afterwards.
 * An OPEN during `loading` re-enters it, which cancels the request for the
 * month the user already left. A slow ticker refreshes the open month, but
 * only from `idle`: a tick never cancels a slow request in flight.
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
		// A new period starts clean: the last month's error is not this one's.
		setWant: assign({
			want: ({ event }) => ({ year: Number(event.year), period: Number(event.period) }),
			error: null,
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
		failed: {
			on: {
				RETRY: "booting",
				OPEN: { actions: "setWant" },
			},
		},
		ready: {
			initial: "idle",
			invoke: { src: "ticker" },
			on: {
				OPEN: { target: ".loading", reenter: true, actions: "setWant" },
				REFRESH: { guard: "hasWant", target: ".loading", reenter: true },
			},
			states: {
				idle: {
					on: { TICK: { guard: "hasWant", target: "loading" } },
				},
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
