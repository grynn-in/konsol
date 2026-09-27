<script>
/**
 * konsol#305 B24: the sign-off period actions (E9; stories 9.1, 9.3, 9.5).
 *
 * - `periodActors(key)` is the machine's `close` and `reopen` services (B14),
 *   A34 POST `signoff_api.close_period` (with the optional note) and
 *   `signoff_api.reopen_period` (with the reason). SignOff.vue spreads them
 *   into signoffMachine.provide({actors}); each endpoint is posted from one place in this file.
 * - Close is offered only when the machine accepts CLOSE (a signed, Open
 *   period); Reopen only when it accepts REOPEN (a closed period that is not
 *   Locked: the machine's canReopen guard decides, not this file).
 * - Reopen's reason is typed in a frappe-ui Dialog that says plainly that later
 *   signed periods will need re-signing (A31), and travels in the REOPEN event.
 * - Declare TB exception (A33) is shown to the Close Lead only while the
 *   summary's completeness gate names missing trial balances. The Close Lead
 *   is the server's own answer: `can_override` is OVERRIDE_ROLES (EPM Admin,
 *   System Manager), the same roles `declare_tb_exception` admits. Every rule
 *   (blank reason, duplicate, closed period, group entity) is the server's; its
 *   refusal is shown verbatim. After a declaration lands, REFRESH reloads the
 *   summary through the machine.
 */
import { fromPromise } from "xstate";
import { post } from "../api.js";

const CLOSE_PERIOD = "konsol.close.signoff_api.close_period";
const REOPEN_PERIOD = "konsol.close.signoff_api.reopen_period";
const DECLARE_TB_EXCEPTION = "konsol.close.signoff_api.declare_tb_exception";

/** The machine's close/reopen services for one period ({fiscal_year, fiscal_period}). */
export function periodActors(key) {
	return {
		close: fromPromise(({ input }) => post(CLOSE_PERIOD, { ...key, note: input.note })),
		reopen: fromPromise(({ input }) => post(REOPEN_PERIOD, { ...key, reason: input.reason })),
	};
}
</script>

<script setup>
import { computed, ref, watch } from "vue";
import { Button, Dialog } from "frappe-ui";
import { messageLines } from "../signoff.js";

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
function send(event) {
	emit("send", event);
}
const is = (state) => Boolean(props.snapshot && props.snapshot.matches(state));

/** A stand-in reason: asks the machine whether REOPEN is taken at all right now. */
const ANY_REASON = "a reason";

const summary = computed(() => (props.snapshot ? props.snapshot.context.summary : null));

// --- Close (9.3) and reopen (9.5) ------------------------------------------
const closeNote = ref("");
const reopenOpen = ref(false);
const reopenReason = ref("");

function openReopen() {
	reopenReason.value = "";
	reopenOpen.value = true;
}
function confirmReopen() {
	send({ type: "REOPEN", reason: reopenReason.value });
	reopenOpen.value = false;
}

// A new period starts with nothing typed.
watch(
	() => `${props.periodKey.fiscal_year}/${props.periodKey.fiscal_period}`,
	() => {
		closeNote.value = "";
		reopenReason.value = "";
		reopenOpen.value = false;
		resetDeclare();
	},
);

// --- Declare a TB exception (9.1, A33) --------------------------------------
const completeness = computed(() =>
	summary.value && summary.value.gates ? summary.value.gates.completeness || null : null,
);
const missing = computed(() => (completeness.value && completeness.value.missing) || []);
const hiddenMissing = computed(() => (completeness.value && completeness.value.hidden) || 0);
const isCloseLead = computed(() => Boolean(summary.value) && summary.value.can_override === true);
const showDeclare = computed(
	() => isCloseLead.value && (missing.value.length > 0 || hiddenMissing.value > 0),
);

const entity = ref("");
const exceptionReason = ref("");
const declaring = ref(false);
const declareError = ref([]);
const declared = ref("");

function resetDeclare() {
	entity.value = "";
	exceptionReason.value = "";
	declareError.value = [];
	declared.value = "";
}

async function declare() {
	if (declaring.value) return;
	declaring.value = true;
	declareError.value = [];
	declared.value = "";
	try {
		const name = await post(DECLARE_TB_EXCEPTION, {
			...props.periodKey,
			entity: entity.value,
			reason: exceptionReason.value,
		});
		declared.value = `Declared ${name} for ${entity.value}.`;
		entity.value = "";
		exceptionReason.value = "";
		if (accepts({ type: "REFRESH" })) send({ type: "REFRESH" });
		else declared.value += " Reload the summary to see it.";
	} catch (err) {
		declareError.value = messageLines(err && err.message ? err.message : String(err));
	} finally {
		declaring.value = false;
	}
}
</script>

<template>
	<div>
		<section v-if="accepts({ type: 'CLOSE', note: closeNote })" class="mt-4 rounded border border-outline-gray-2 px-4 py-3">
			<h2 class="text-base font-semibold text-ink-gray-9">Close {{ periodName }}</h2>
			<label for="signoff-close-note" class="mt-2 block text-sm font-medium text-ink-gray-8">
				Closing note (optional)
			</label>
			<textarea
				id="signoff-close-note"
				v-model="closeNote"
				rows="2"
				class="mt-1 w-full rounded border border-outline-gray-2 px-3 py-2 text-base"
			></textarea>
			<div class="mt-2">
				<Button
					v-if="accepts({ type: 'CLOSE', note: closeNote })"
					theme="gray"
					variant="solid"
					@click="send({ type: 'CLOSE', note: closeNote })"
				>
					Close {{ periodName }}
				</Button>
			</div>
		</section>
		<p v-if="is('closing')" role="status" class="mt-2 text-sm text-ink-gray-6">Closing {{ periodName }}…</p>

		<div v-if="accepts({ type: 'REOPEN', reason: ANY_REASON })" class="mt-4">
			<Button theme="gray" variant="subtle" @click="openReopen">Reopen {{ periodName }}</Button>
		</div>
		<p v-if="is('reopening')" role="status" class="mt-2 text-sm text-ink-gray-6">Reopening {{ periodName }}…</p>

		<Dialog v-model="reopenOpen" :options="{ title: `Reopen ${periodName}` }">
			<template #body-content>
				<p class="text-base text-ink-gray-8">
					Later signed periods will need re-signing: their sign-off stops counting until the checks run
					again and they are signed off again.
				</p>
				<label for="signoff-reopen-reason" class="mt-4 block text-sm font-medium text-ink-gray-8">
					Reason for reopening (required)
				</label>
				<textarea
					id="signoff-reopen-reason"
					v-model="reopenReason"
					required
					rows="3"
					class="mt-1 w-full rounded border border-outline-gray-2 px-3 py-2 text-base"
				></textarea>
			</template>
			<template #actions>
				<div class="flex gap-2">
					<Button
						theme="red"
						variant="solid"
						:disabled="!accepts({ type: 'REOPEN', reason: reopenReason })"
						@click="send({ type: 'REOPEN', reason: reopenReason }); reopenOpen = false"
					>
						Reopen {{ periodName }}
					</Button>
					<Button variant="subtle" @click="reopenOpen = false">Cancel</Button>
				</div>
			</template>
		</Dialog>

		<section v-if="showDeclare" class="mt-6 rounded border border-outline-gray-2 px-4 py-3">
			<h2 class="text-base font-semibold text-ink-gray-9">Declare a trial balance exception</h2>
			<p class="mt-1 text-sm text-ink-gray-6">
				For an entity that will send no trial balance for {{ periodName }}.
				<template v-if="missing.length">Missing: {{ missing.join(", ") }}.</template>
			</p>
			<form class="mt-3" @submit.prevent="declare">
				<label for="signoff-exception-entity" class="block text-sm font-medium text-ink-gray-8">Entity</label>
				<input
					id="signoff-exception-entity"
					v-model="entity"
					required
					list="signoff-exception-missing"
					class="mt-1 w-full rounded border border-outline-gray-2 px-3 py-2 text-base"
				/>
				<datalist id="signoff-exception-missing">
					<option v-for="code in missing" :key="code" :value="code"></option>
				</datalist>
				<label for="signoff-exception-reason" class="mt-3 block text-sm font-medium text-ink-gray-8">
					Reason there is no trial balance (required)
				</label>
				<textarea
					id="signoff-exception-reason"
					v-model="exceptionReason"
					required
					rows="3"
					class="mt-1 w-full rounded border border-outline-gray-2 px-3 py-2 text-base"
				></textarea>
				<div class="mt-2">
					<Button type="submit" theme="gray" variant="solid" :loading="declaring" :disabled="declaring">
						Declare exception
					</Button>
				</div>
			</form>
			<div
				v-if="declareError.length"
				role="alert"
				class="mt-3 rounded border border-outline-red-1 bg-surface-red-1 px-4 py-3"
			>
				<p v-for="(line, i) in declareError" :key="i" class="text-base text-ink-gray-9">{{ line }}</p>
			</div>
			<p v-if="declared" role="status" class="mt-3 text-sm text-ink-gray-7">{{ declared }}</p>
		</section>
	</div>
</template>
