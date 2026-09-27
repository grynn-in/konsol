<script setup>
/**
 * konsol#305 B20: upload one entity-period's trial balance (stories 3.2, 3.3,
 * 3.5, 3.7). Mounted in the Trial balances detail area (B19).
 *
 * - The flow is B11's tbUploadMachine. This section only provides its three
 *   services through machine.provide({actors}) and shows each state:
 *   - readFile: FileReader.readAsText;
 *   - check:    POST A19 `check_tb`, the CSV text in the body (writes nothing);
 *   - submit:   POST A24 `submit_tb`, passing the `replaces` the check named,
 *               so a TB submitted since the check is refused, not overwritten.
 * - D1: there is no row editing. The only fix for a problem is choosing a
 *   corrected file, which starts the check again.
 * - The amount basis is the user's declaration; nothing is preselected.
 *   Changing it re-checks the chosen file, because the check depends on it.
 * - A `period_problem` (the period is not Open) is shown and blocks Submit;
 *   the machine routes it to checked.problems as well.
 * - After a submit, the server's `on_behalf` is shown (R4) and the screen is
 *   told, so its list shows the server's own label.
 */
import { computed, ref, watch } from "vue";
import { useMachine } from "@xstate/vue";
import { fromPromise } from "xstate";
import { tbUploadMachine } from "../machines/tbUploadMachine.js";
import { post } from "../api.js";
import { checkRows } from "../tbTable.js";
import { messageLines } from "../signoff.js";

const CHECK = "konsol.close.tb_api.check_tb";
const SUBMIT = "konsol.close.tb_api.submit_tb";

// Trial Balance Submission.amount_basis options (trial_balance_submission.json).
const BASES = ["Period movement", "Year-to-date movement", "Period-end balance"];

const props = defineProps({
	entity: { type: String, required: true },
	entityName: { type: String, required: true },
	fiscalYear: { type: Number, required: true },
	fiscalPeriod: { type: Number, required: true },
});
const emit = defineEmits(["submitted"]);

const basis = ref(null);
const fileInput = ref(null);

function readText({ input }) {
	return new Promise((resolve, reject) => {
		const reader = new FileReader();
		reader.onload = () => resolve(String(reader.result));
		reader.onerror = () =>
			reject(new Error(`Could not read ${input.file.name}: ${reader.error ? reader.error.message : "the browser gave no reason"}`));
		reader.readAsText(input.file);
	});
}

function where() {
	return {
		entity: props.entity,
		fiscal_year: props.fiscalYear,
		fiscal_period: props.fiscalPeriod,
		amount_basis: basis.value,
	};
}

const { snapshot, send } = useMachine(
	tbUploadMachine.provide({
		actors: {
			readFile: fromPromise(readText),
			check: fromPromise(({ input }) => post(CHECK, { ...where(), content: input.content })),
			submit: fromPromise(({ input }) => post(SUBMIT, { ...where(), content: input.content, replaces: input.replaces })),
		},
	}),
);

const context = computed(() => snapshot.value.context);
const is = (state) => snapshot.value.matches(state);

const table = computed(() => (context.value.result ? checkRows(context.value.result) : null));
const problemRows = computed(() => (table.value ? table.value.rows.filter((r) => r.hasProblems).length : 0));
const periodProblem = computed(() => (context.value.result && context.value.result.period_problem) || null);
const canSubmit = computed(() => is("checked.ok") && !periodProblem.value);
const errorLines = computed(() => {
	const lines = messageLines(context.value.error);
	return lines.length ? lines : ["The server gave no reason."];
});
const busy = computed(() => is("reading") || is("checking") || is("submitting"));
const canChoose = computed(() => Boolean(basis.value) && !is("submitting") && !is("received"));

function choose(event) {
	const file = event.target.files && event.target.files[0];
	event.target.value = ""; // choosing the same corrected file again still fires
	if (file) send({ type: "FILE_CHOSEN", file });
}

// The check judged the file against the basis; a new basis needs a new check.
watch(basis, () => {
	const file = context.value.file;
	if (file && !is("idle") && !is("submitting") && !is("received")) {
		send({ type: "FILE_CHOSEN", file });
	}
});

watch(
	() => is("received"),
	(done) => {
		if (done) emit("submitted", context.value.received);
	},
);

</script>

<template>
	<section class="mt-4 border-t border-outline-gray-1 pt-4" aria-label="Upload a trial balance">
		<h3 class="text-sm font-semibold text-ink-gray-9">Upload a trial balance</h3>

		<div class="mt-3 flex flex-wrap items-end gap-4 text-sm">
			<label class="flex flex-col gap-1">
				<span class="text-ink-gray-6">Amount basis</span>
				<select
					v-model="basis"
					:disabled="is('submitting') || is('received')"
					class="rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-ink-gray-8"
				>
					<option :value="null" disabled>Choose what the amounts are</option>
					<option v-for="b in BASES" :key="b" :value="b">{{ b }}</option>
				</select>
			</label>
			<label class="flex flex-col gap-1">
				<span class="text-ink-gray-6">{{ context.file ? "Choose a corrected file" : "Trial balance file (CSV)" }}</span>
				<input
					ref="fileInput"
					type="file"
					accept=".csv,text/csv"
					:disabled="!canChoose"
					class="text-ink-gray-8"
					@change="choose"
				/>
			</label>
		</div>
		<p v-if="!basis" class="mt-2 text-xs text-ink-gray-5">Choose the amount basis first: the check depends on it.</p>

		<div aria-live="polite" class="mt-4 text-sm">
			<p v-if="is('idle')" class="text-ink-gray-6">
				No file chosen. The file is checked first; nothing is saved until you submit.
			</p>

			<p v-else-if="is('reading')" role="status" class="text-ink-gray-6">
				Reading {{ context.file && context.file.name }} in the browser
			</p>

			<div v-else-if="is('readFailed')" role="alert" class="rounded border border-outline-red-1 bg-surface-red-1 px-3 py-2">
				<p class="font-medium text-ink-gray-9">The file could not be read</p>
				<p v-for="(line, i) in errorLines" :key="i" class="mt-1 text-ink-gray-7">{{ line }}</p>
				<p class="mt-1 text-ink-gray-6">Choose the file again, or another file.</p>
			</div>

			<p v-else-if="is('checking')" role="status" class="text-ink-gray-6">
				Checking {{ context.file && context.file.name }} against the group chart (<code class="text-xs">{{ CHECK }}</code>)
			</p>

			<div v-else-if="is('checkFailed')" role="alert" class="rounded border border-outline-red-1 bg-surface-red-1 px-3 py-2">
				<p class="font-medium text-ink-gray-9">The check could not run</p>
				<p v-for="(line, i) in errorLines" :key="i" class="mt-1 text-ink-gray-7">{{ line }}</p>
				<button type="button" class="mt-2 rounded bg-surface-gray-7 px-3 py-1 text-ink-white" @click="send({ type: 'RETRY' })">
					Check again
				</button>
			</div>

			<div v-else-if="is('checked.ok')" class="rounded border border-outline-gray-2 bg-surface-green-1 px-3 py-2">
				<p class="font-medium text-ink-green-3">{{ context.file && context.file.name }} passed every check.</p>
				<div v-if="context.error" role="alert" class="mt-2">
					<p class="font-medium text-ink-red-3">The trial balance was not submitted</p>
					<p v-for="(line, i) in errorLines" :key="i" class="mt-1 text-ink-gray-7">{{ line }}</p>
				</div>
			</div>

			<div v-else-if="is('checked.problems')" role="alert" class="rounded border border-outline-amber-1 bg-surface-amber-1 px-3 py-2">
				<p v-if="context.result && context.result.period_problem" class="font-medium text-ink-gray-9">{{ context.result.period_problem }}.</p>
				<p v-if="table && !table.ok" class="font-medium text-ink-gray-9">
					The file has problems: {{ table.file_problems.length }} in the file, {{ problemRows }} row(s) with problems.
				</p>
				<p class="mt-1 text-ink-gray-7">Fix them in your file and choose the corrected file. Rows cannot be edited here.</p>
			</div>

			<p v-else-if="is('submitting')" role="status" class="text-ink-gray-6">
				Submitting {{ context.file && context.file.name }}{{ context.replaces ? ` in place of ${context.replaces}` : "" }}
			</p>

			<div v-else-if="is('received')" class="rounded border border-outline-gray-2 bg-surface-green-1 px-3 py-2">
				<p class="font-medium text-ink-green-3">Received: {{ context.received.name }}</p>
				<p v-if="context.received.replaced" class="mt-1 text-ink-gray-7">It replaces {{ context.received.replaced }}, which is now cancelled.</p>
				<p v-if="context.received.on_behalf === true" class="mt-1 text-ink-gray-7">Recorded as uploaded on behalf of {{ entityName }} (R4).</p>
				<p v-else-if="context.received.on_behalf === false" class="mt-1 text-ink-gray-7">Recorded as uploaded by an Entity Accountant assigned to this entity.</p>
				<p v-else class="mt-1 text-ink-gray-7">On behalf: not recorded by the server.</p>
			</div>

			<p v-else role="alert" class="text-ink-red-3">Unknown upload state: {{ JSON.stringify(snapshot.value) }}</p>
		</div>

		<template v-if="table && (is('checked.ok') || is('checked.problems') || is('submitting'))">
			<p v-if="context.result.period_problem && is('checked.ok')" role="alert" class="mt-3 text-sm font-medium text-ink-red-3">{{ context.result.period_problem }}.</p>

			<ul v-if="table.file_problems.length" class="mt-3 list-disc pl-5 text-sm text-ink-gray-8">
				<li v-for="(p, i) in table.file_problems" :key="i">
					<span v-for="(line, j) in messageLines(p)" :key="j" class="block">{{ line }}</span>
				</li>
			</ul>

			<p class="mt-3 text-sm text-ink-gray-7">
				Totals: debit {{ table.totals.debit }}, credit {{ table.totals.credit }}, difference {{ table.totals.difference }}
			</p>

			<div v-if="table.rows.length" class="mt-2 max-h-96 overflow-auto rounded border border-outline-gray-2">
				<table class="w-full text-left text-sm">
					<thead class="sticky top-0 bg-surface-gray-1 text-xs uppercase tracking-wide text-ink-gray-6">
						<tr>
							<th class="px-3 py-2 font-medium">Line</th>
							<th class="px-3 py-2 font-medium">Account</th>
							<th class="px-3 py-2 font-medium">Partner</th>
							<th class="px-3 py-2 text-right font-medium">Debit</th>
							<th class="px-3 py-2 text-right font-medium">Credit</th>
							<th class="px-3 py-2 font-medium">Problems</th>
						</tr>
					</thead>
					<tbody>
						<tr
							v-for="row in table.rows"
							:key="row.line"
							class="border-t border-outline-gray-1 align-top"
							:class="{ 'bg-surface-amber-1': row.hasProblems }"
						>
							<td class="px-3 py-1.5 font-mono text-xs text-ink-gray-6">{{ row.line }}</td>
							<td class="px-3 py-1.5 font-mono text-ink-gray-8">{{ row.main_account }}</td>
							<td class="px-3 py-1.5 font-mono text-ink-gray-7">{{ row.partner || "—" }}</td>
							<td class="px-3 py-1.5 text-right font-mono text-ink-gray-8">{{ row.debit }}</td>
							<td class="px-3 py-1.5 text-right font-mono text-ink-gray-8">{{ row.credit }}</td>
							<td class="px-3 py-1.5 text-ink-gray-8">
								<div v-for="(p, i) in row.problems" :key="i" class="mb-1">
									<div>{{ p.message }}</div>
									<div v-if="p.suggestion" class="text-xs text-ink-gray-6">Suggestion: {{ p.suggestion }}</div>
								</div>
							</td>
						</tr>
					</tbody>
				</table>
			</div>
		</template>

		<p v-if="context.replaces && (is('checked.ok') || is('checked.problems'))" class="mt-3 text-sm font-medium text-ink-gray-9">
			This replaces {{ context.replaces }}. Submitting cancels it and submits this file in one step.
		</p>

		<div v-if="!is('received')" class="mt-3">
			<button
				type="button"
				:disabled="!is('checked.ok') || !canSubmit || busy"
				class="rounded bg-surface-gray-7 px-3 py-1.5 text-sm text-ink-white disabled:cursor-not-allowed disabled:opacity-50"
				@click="send({ type: 'SUBMIT' })"
			>
				{{ is('submitting') ? "Submitting" : "Submit trial balance" }}
			</button>
		</div>
	</section>
</template>
