<script setup>
/**
 * konsol#305 B23: the Sign-off screen (E9; stories 9.1, 9.2).
 *
 * The screen drives B14's signoffMachine and decides nothing itself:
 * - `load` is A30 GET `signoff_api.get_signoff`, `sign` is A32 POST
 *   `signoff_api.sign`; both are injected with machine.provide({actors}) and
 *   close over the period in the URL (route.js, D5).
 * - Close and reopen are B24's (SignOffPeriodActions); this screen only
 *   renders the signed and closed states. Their services stay the machine's
 *   defaults, which refuse by name.
 * - What the summary says is rendered through B15's summaryView as plain-text
 *   lines ("unknown" for a count the server did not read, "None" for an empty
 *   section); the gate text ("Sign off P07 first") is the server's `label`.
 * - A button is shown only when the machine accepts its event right now
 *   (snapshot.can). The typed Amber acknowledgement and Red override reason
 *   travel in CONFIRM_ACK / CONFIRM_OVERRIDE; whether blank text, the role or
 *   the action allow them is the machine's guards' call, never re-checked here.
 * - A refused sign returns the machine to `review` with the server's message,
 *   shown verbatim (split on <br> into lines). "Signed" appears only in the
 *   signed/closed region, which only a server reload saying "signed" reaches.
 *
 * One actor per period: the router reuses this component when only the period
 * in the URL changes, and the machine's `closed` state takes no REFRESH, so a
 * new period gets a fresh actor instead of an event.
 */
import { computed, onBeforeUnmount, ref, shallowRef, watch } from "vue";
import { useRoute } from "vue-router";
import { createActor, fromPromise } from "xstate";
import { Button, FeatherIcon } from "frappe-ui";
import LoadState from "../components/LoadState.vue";
import { get, post } from "../api.js";
import { parse } from "../route.js";
import { summaryView, messageLines } from "../signoff.js";
import { signoffMachine } from "../machines/signoffMachine.js";

const GET_SIGNOFF = "konsol.close.signoff_api.get_signoff";
const SIGN = "konsol.close.signoff_api.sign";

const route = useRoute();
const period = computed(() => {
	const p = parse(`/close${route.path}`);
	return p.error || p.year == null ? null : { year: p.year, period: p.period };
});
const periodName = computed(() =>
	period.value ? `FY${period.value.year} P${String(period.value.period).padStart(2, "0")}` : "this period",
);

const snap = shallowRef(null);
const ackText = ref("");
const overrideText = ref("");
let actor = null;

function machineFor(p) {
	const key = { fiscal_year: p.year, fiscal_period: p.period };
	return signoffMachine.provide({
		actors: {
			load: fromPromise(() => get(GET_SIGNOFF, key)),
			sign: fromPromise(({ input }) =>
				post(SIGN, {
					...key,
					acknowledgement: input.acknowledgement,
					override_reason: input.override_reason,
				}),
			),
		},
	});
}

function stopActor() {
	if (actor) {
		actor.stop();
		actor = null;
	}
}

watch(
	() => (period.value ? `${period.value.year}/${period.value.period}` : null),
	() => {
		stopActor();
		snap.value = null;
		ackText.value = "";
		overrideText.value = "";
		if (!period.value) return;
		actor = createActor(machineFor(period.value));
		actor.subscribe((s) => {
			snap.value = s;
		});
		actor.start();
		snap.value = actor.getSnapshot();
	},
	{ immediate: true },
);

onBeforeUnmount(stopActor);

function send(event) {
	if (actor) actor.send(event);
}

/** Whether the machine takes this event now: the only test a button uses. */
function accepts(event) {
	return Boolean(snap.value && snap.value.can(event));
}

const is = (state) => Boolean(snap.value && snap.value.matches(state));

// A dialog opens empty: text typed against an older summary is never re-sent.
watch(
	() => snap.value && snap.value.value,
	(state) => {
		if (state !== "acknowledging") ackText.value = "";
		if (state !== "overriding") overrideText.value = "";
	},
);

const context = computed(() => (snap.value ? snap.value.context : { summary: null, error: null }));

const loadState = computed(() => {
	if (!period.value) return "error";
	if (!snap.value || is("loading")) return "loading";
	if (is("loadFailed")) return "error";
	if (!context.value.summary) return "loading";
	return "ready";
});
const loadError = computed(() =>
	period.value ? context.value.error : "This address names no period.",
);

const view = computed(() => (context.value.summary ? summaryView(context.value.summary) : null));

// Only a server reload saying "signed" reaches these states (B14); closing and
// reopening are B24's requests in flight from them.
const signedOrClosed = computed(() =>
	Boolean(snap.value) &&
	(snap.value.matches("signed") || snap.value.matches("closed") ||
		snap.value.matches("closing") || snap.value.matches("reopening")),
);
const isClosed = computed(() => is("closed") || is("reopening"));

// The event the summary's action asks for; the machine decides whether it is taken.
const ACTION_EVENTS = { sign: "SIGN", acknowledge: "ACKNOWLEDGE", override: "OVERRIDE" };
const actionEvent = computed(() => (view.value && ACTION_EVENTS[view.value.action]) || null);
const errorLines = computed(() => messageLines(context.value.error));

const sections = computed(() =>
	view.value
		? [
				{ key: "gates", title: "Gates", rows: view.value.gates.rows, empty: view.value.gates.empty },
				{ key: "checks", title: "Checks", rows: view.value.checks.rows, empty: view.value.checks.empty },
				{
					key: "acknowledgements",
					title: "Acknowledged warnings",
					rows: view.value.acknowledgements.rows,
					empty: view.value.acknowledgements.empty,
				},
				{
					key: "onBehalf",
					title: "Trial balances uploaded on behalf",
					rows: view.value.onBehalf.rows,
					empty: view.value.onBehalf.empty,
				},
				{
					key: "exceptions",
					title: "Trial balance exceptions",
					rows: view.value.exceptions.rows,
					empty: view.value.exceptions.empty,
				},
				{ key: "covers", title: "Covers", rows: view.value.covers.rows, empty: view.value.covers.empty },
				{
					key: "previous",
					title: "Earlier periods",
					rows: view.value.previous.rows,
					empty: view.value.previous.empty,
				},
			]
		: [],
);

const unknownOr = (value) => (value === null || value === undefined || value === "" ? "unknown" : value);
</script>

<template>
	<div class="mx-auto max-w-4xl px-6 py-6">
		<div class="flex flex-wrap items-start justify-between gap-3">
			<div>
				<h1 class="text-xl font-semibold text-ink-gray-9">Sign-off</h1>
				<p class="mt-1 text-sm text-ink-gray-6">Sign-off for {{ periodName }}</p>
			</div>
			<Button
				v-if="accepts({ type: 'REFRESH' })"
				theme="gray"
				variant="subtle"
				@click="send({ type: 'REFRESH' })"
			>
				Reload
			</Button>
		</div>

		<div
			v-if="context.error && loadState === 'ready'"
			role="alert"
			class="mt-4 rounded border border-outline-red-1 bg-surface-red-1 px-4 py-3"
		>
			<div class="flex items-start gap-3">
				<FeatherIcon name="alert-triangle" class="mt-0.5 h-4 w-4 shrink-0 text-ink-red-3" />
				<div class="min-w-0 flex-1">
					<p v-for="(line, i) in errorLines" :key="i" class="text-base text-ink-gray-9">{{ line }}</p>
				</div>
			</div>
		</div>

		<LoadState
			v-if="loadState !== 'ready'"
			compact
			:state="loadState"
			:what="`the sign-off summary for ${periodName}`"
			:source="GET_SIGNOFF"
			:error="loadError"
			:busy="loadState === 'loading'"
			@retry="send({ type: 'RETRY' })"
		/>

		<template v-else>
			<div
				v-if="signedOrClosed"
				class="mt-4 rounded border border-outline-green-2 bg-surface-green-1 px-4 py-3"
				role="status"
			>
				<p class="flex items-center gap-2 text-base font-medium text-ink-gray-9">
					<FeatherIcon name="check-circle" class="h-4 w-4 text-ink-green-3" />
					{{ isClosed ? "Signed and closed" : "Signed" }}
				</p>
				<p class="mt-1 text-sm text-ink-gray-7">{{ view.label }}</p>
				<p v-if="isClosed" class="mt-1 text-sm text-ink-gray-7">
					Period {{ unknownOr(context.summary.period_status) }}
					· closed by {{ unknownOr(context.summary.closed_by) }}
					on {{ unknownOr(context.summary.closed_on) }}
				</p>
			</div>
			<!-- end signed region -->

			<div v-else class="mt-4 rounded border border-outline-gray-2 px-4 py-3">
				<div class="flex flex-wrap items-center justify-between gap-3">
					<p v-if="!actionEvent || !accepts({ type: actionEvent })" class="text-base font-medium text-ink-gray-9">
						{{ view.label }}
					</p>
					<Button
						v-if="accepts({ type: 'SIGN' })"
						theme="gray"
						variant="solid"
						@click="send({ type: 'SIGN' })"
					>
						{{ view.label }}
					</Button>
					<Button
						v-if="accepts({ type: 'ACKNOWLEDGE' })"
						theme="gray"
						variant="solid"
						@click="send({ type: 'ACKNOWLEDGE' })"
					>
						{{ view.label }}
					</Button>
					<Button
						v-if="accepts({ type: 'OVERRIDE' })"
						theme="red"
						variant="solid"
						@click="send({ type: 'OVERRIDE' })"
					>
						{{ view.label }}
					</Button>
				</div>
				<p v-if="actionEvent && !accepts({ type: actionEvent }) && is('review')" class="mt-1 text-sm text-ink-gray-6">
					Your role cannot take this step.
				</p>
				<p v-if="is('signing')" role="status" class="mt-2 text-sm text-ink-gray-6">Sending the sign-off to the server…</p>
				<p v-if="is('confirming')" role="status" class="mt-2 text-sm text-ink-gray-6">
					Reloading the summary to confirm the sign-off…
				</p>

				<div v-if="is('acknowledging')" class="mt-4">
					<label for="signoff-acknowledgement" class="block text-sm font-medium text-ink-gray-8">
						Acknowledge the warnings: say why the period can be signed with them
					</label>
					<textarea
						id="signoff-acknowledgement"
						v-model="ackText"
						rows="3"
						class="mt-1 w-full rounded border border-outline-gray-2 px-3 py-2 text-base"
					></textarea>
					<div class="mt-2 flex gap-2">
						<Button
							v-if="accepts({ type: 'CONFIRM_ACK', text: ackText })"
							theme="gray"
							variant="solid"
							@click="send({ type: 'CONFIRM_ACK', text: ackText })"
						>
							Acknowledge and sign off
						</Button>
						<Button v-if="accepts({ type: 'CANCEL' })" variant="subtle" @click="send({ type: 'CANCEL' })">Cancel</Button>
					</div>
				</div>

				<div v-if="is('overriding')" class="mt-4">
					<label for="signoff-override-reason" class="block text-sm font-medium text-ink-gray-8">
						Override reason: why the period can be signed although checks failed
					</label>
					<textarea
						id="signoff-override-reason"
						v-model="overrideText"
						rows="3"
						class="mt-1 w-full rounded border border-outline-gray-2 px-3 py-2 text-base"
					></textarea>
					<div class="mt-2 flex gap-2">
						<Button
							v-if="accepts({ type: 'CONFIRM_OVERRIDE', text: overrideText })"
							theme="red"
							variant="solid"
							@click="send({ type: 'CONFIRM_OVERRIDE', text: overrideText })"
						>
							Override and sign off
						</Button>
						<Button v-if="accepts({ type: 'CANCEL' })" variant="subtle" @click="send({ type: 'CANCEL' })">Cancel</Button>
					</div>
				</div>
			</div>

			<section v-for="s in sections" :key="s.key" class="mt-6">
				<h2 class="text-base font-semibold text-ink-gray-9">{{ s.title }}</h2>
				<ul class="mt-2 divide-y divide-outline-gray-1 rounded border border-outline-gray-2">
					<li
						v-for="(row, i) in s.rows"
						:key="i"
						class="px-4 py-2 text-base"
						:class="s.empty ? 'text-ink-gray-5' : 'text-ink-gray-8'"
					>{{ row }}</li>
				</ul>
			</section>
		</template>
	</div>
</template>
