<script>
/**
 * konsol#305 story 9.4 (#157), #305-W5-1 (Deepak Pai, 6 Oct 2026): the Close
 * Lead rejects a signed period with a typed reason.
 *
 * - `rejectActors(key)` is the machine's `reject` service: POST
 *   `signoff_api.reject` with the run the summary showed (A58) and the
 *   reason. SignOff.vue spreads it into signoffMachine.provide({actors}).
 * - Reject is offered only when the machine accepts REJECT (a signed, Open
 *   period, and the summary says the caller is the Close Lead); the reason's
 *   blank check is the machine's too.
 * - The reason is typed in a frappe-ui Dialog whose whole state is
 *   signoff.js rejectDialogNext's (konsol#305 U3/U10). A refusal keeps the
 *   typed text and shows the server's message, including A58's stale-run
 *   refusal, which reloads the summary: Reject is clicked again against the
 *   new run. An accepted reject reloads the summary; the dialog closes and
 *   its text goes. Every close (Cancel, Esc, the overlay) resets it.
 */
import { fromPromise } from "xstate";
import { post } from "../api.js";

const REJECT = "konsol.close.signoff_api.reject";

/** The machine's reject service for one period ({fiscal_year, fiscal_period}). */
export function rejectActors(key) {
	return {
		reject: fromPromise(({ input }) => post(REJECT, { ...key, run: input.run, reason: input.reason })),
	};
}
</script>

<script setup>
import { ref, watch } from "vue";
import { Button, Dialog } from "frappe-ui";
import { REJECT_DIALOG_CLOSED, rejectDialogNext } from "../signoff.js";

const props = defineProps({
	/** The signoffMachine snapshot (or null before the actor starts). */
	snapshot: { type: Object, default: null },
	/** {fiscal_year, fiscal_period} of the period in the URL. */
	periodKey: { type: Object, required: true },
	periodName: { type: String, required: true },
});
const emit = defineEmits(["send"]);

function accepts(event) {
	return Boolean(props.snapshot && props.snapshot.can(event));
}
const is = (state) => Boolean(props.snapshot && props.snapshot.matches(state));

/** A stand-in reason: asks the machine whether REJECT is taken at all right now. */
const ANY_REASON = "a reason";

/** {open, reason, refused}: changed only through rejectDialogNext. */
const dialog = ref(REJECT_DIALOG_CLOSED);
function apply(event) {
	dialog.value = rejectDialogNext(dialog.value, event);
}
/** The Dialog's own open/close (Esc, the overlay, its X): a close resets. */
function onDialogModel(value) {
	apply({ type: value ? "OPEN" : "CLOSE" });
}
function send(event) {
	emit("send", event);
}
function confirmReject() {
	apply({ type: "SENT" });
	send({ type: "REJECT", reason: dialog.value.reason });
}

watch(
	() => (props.snapshot ? props.snapshot.value : null),
	(state, prev) => {
		apply({ type: "MACHINE", prev, state, error: props.snapshot ? props.snapshot.context.error : null });
	},
);
// A new period starts with nothing typed.
watch(() => `${props.periodKey.fiscal_year}/${props.periodKey.fiscal_period}`, () => apply({ type: "CLOSE" }));
</script>

<template>
	<div>
		<div v-if="accepts({ type: 'REJECT', reason: ANY_REASON })" class="mt-4">
			<Button theme="red" variant="subtle" @click="apply({ type: 'OPEN' })">Reject sign-off</Button>
		</div>

		<Dialog :model-value="dialog.open" @update:model-value="onDialogModel" :options="{ title: `Reject the sign-off of ${periodName}` }">
			<template #body-content>
				<p class="text-base text-ink-gray-8">
					{{ periodName }} goes back to Not signed. The person who ran the checks sees your reason in My
					work until the period is signed again.
				</p>
				<label for="signoff-reject-reason" class="mt-4 block text-sm font-medium text-ink-gray-8">
					Reason for rejecting (required)
				</label>
				<textarea
					id="signoff-reject-reason"
					:value="dialog.reason"
					@input="apply({ type: 'TYPE', text: $event.target.value })"
					required
					rows="3"
					class="mt-1 w-full rounded border border-outline-gray-2 px-3 py-2 text-base"
				></textarea>
				<div
					v-if="dialog.refused.length"
					role="alert"
					class="mt-3 rounded border border-outline-red-1 bg-surface-red-1 px-4 py-3"
				>
					<p v-for="(line, i) in dialog.refused" :key="i" class="text-base text-ink-gray-9">{{ line }}</p>
				</div>
				<p v-if="is('rejecting')" role="status" class="mt-2 text-sm text-ink-gray-6">Sending the reject…</p>
			</template>
			<template #actions>
				<div class="flex gap-2">
					<Button
						theme="red"
						variant="solid"
						:disabled="is('rejecting') || !accepts({ type: 'REJECT', reason: dialog.reason })"
						@click="confirmReject"
					>
						Reject sign-off
					</Button>
					<Button variant="subtle" :disabled="is('rejecting')" @click="apply({ type: 'CLOSE' })">Cancel</Button>
				</div>
			</template>
		</Dialog>
	</div>
</template>
