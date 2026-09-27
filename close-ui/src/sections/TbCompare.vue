<script setup>
/**
 * konsol#305 B21: TB compare — this period's trial balance against the
 * previous period's, by account (story 3.4). Mounted in the Trial balances
 * detail area (B19), alongside B20's upload section, when the selected
 * entity's trial balance was received.
 *
 * - Data: A28 GET `tb_read_api.tb_compare(entity, fiscal_year,
 *   fiscal_period)`, turned into table rows by B12's `compareRows`. A null
 *   `previous`/`change` renders as the dash (never "0.00") — compareRows'
 *   own rule, not repeated here.
 * - `basis_note` and `previous_note` are shown exactly as the server wrote
 *   them: this never composes its own sentence for either.
 * - `previous_code` is shown as the server sent it. When the previous period
 *   falls in another year, the server's code already carries the year (e.g.
 *   "FY2024 P12"); this never re-derives or reformats it.
 * - A28's refusal "No submitted trial balance for <entity> <code>: submit
 *   one for the period first." is the ordinary case right after a fresh
 *   upload, or when this is mounted before a TB exists. It is shown through
 *   LoadState's empty state, in the server's own words, never the red error
 *   box (which is for a failure, not this expected case).
 * - Read only (D1): nothing here is editable.
 */
import { computed, reactive, watch } from "vue";
import LoadState from "../components/LoadState.vue";
import { get } from "../api.js";
import { compareRows } from "../tbTable.js";

const COMPARE = "konsol.close.tb_read_api.tb_compare";
const NO_TB = "No submitted trial balance";

const props = defineProps({
	entity: { type: String, required: true },
	fiscalYear: { type: Number, required: true },
	fiscalPeriod: { type: Number, required: true },
});

const load = reactive({ status: "loading", data: null, error: null, note: null });
let seq = 0;

async function fetchCompare() {
	const mine = ++seq;
	load.status = "loading";
	load.error = null;
	load.note = null;
	try {
		const data = await get(COMPARE, {
			entity: props.entity,
			fiscal_year: props.fiscalYear,
			fiscal_period: props.fiscalPeriod,
		});
		if (mine !== seq) return;
		load.data = data;
		load.status = "ready";
	} catch (e) {
		if (mine !== seq) return;
		// The refusal is the expected shape of "not received yet", not a
		// failure: it is shown as a state, never the red error box.
		if (typeof e.message === "string" && e.message.startsWith(NO_TB)) {
			load.note = e.message;
			load.status = "empty";
		} else {
			load.error = e.message;
			load.status = "error";
		}
	}
}

watch(
	() => [props.entity, props.fiscalYear, props.fiscalPeriod],
	() => fetchCompare(),
	{ immediate: true },
);

const table = computed(() => (load.data ? compareRows(load.data) : null));
const what = computed(() => `the trial balance comparison for ${props.entity}`);
</script>

<template>
	<section class="mt-4 border-t border-outline-gray-1 pt-4" aria-label="Compare with the previous period">
		<h3 class="text-sm font-semibold text-ink-gray-9">Compare with the previous period</h3>

		<LoadState
			:state="load.status"
			:what="what"
			:source="COMPARE"
			:error="load.error"
			:empty-text="load.note"
			compact
			@retry="fetchCompare"
		>
			<template v-if="table">
				<p class="mt-2 text-sm text-ink-gray-7">
					{{ load.data.current.code }} vs {{ table.previous_code || "no previous period declared" }}
				</p>
				<p v-if="table.previous_note" class="mt-1 text-sm text-ink-gray-6">{{ table.previous_note }}</p>
				<p v-if="table.basis_note" role="alert" class="mt-1 text-sm text-ink-amber-3">{{ table.basis_note }}</p>

				<div v-if="table.rows.length" class="mt-2 max-h-96 overflow-auto rounded border border-outline-gray-2">
					<table class="w-full text-left text-sm">
						<thead class="sticky top-0 bg-surface-gray-1 text-xs uppercase tracking-wide text-ink-gray-6">
							<tr>
								<th class="px-3 py-2 font-medium">Account</th>
								<th class="px-3 py-2 font-medium">Partner</th>
								<th class="px-3 py-2 text-right font-medium">Current</th>
								<th class="px-3 py-2 text-right font-medium">Previous</th>
								<th class="px-3 py-2 text-right font-medium">Change</th>
							</tr>
						</thead>
						<tbody>
							<tr
								v-for="row in table.rows"
								:key="`${row.account}-${row.partner}`"
								class="border-t border-outline-gray-1"
								:class="{ 'bg-surface-gray-1': row.is_ic }"
							>
								<td class="px-3 py-1.5 font-mono text-ink-gray-8">{{ row.account }}</td>
								<td class="px-3 py-1.5 font-mono text-ink-gray-7">
									{{ row.partner || "—" }}
									<span v-if="row.is_ic" class="ml-1 rounded bg-surface-gray-2 px-1 text-xs text-ink-gray-6">IC</span>
								</td>
								<td class="px-3 py-1.5 text-right font-mono text-ink-gray-8">{{ row.current }}</td>
								<td class="px-3 py-1.5 text-right font-mono text-ink-gray-8">{{ row.previous }}</td>
								<td class="px-3 py-1.5 text-right font-mono text-ink-gray-8">{{ row.change }}</td>
							</tr>
						</tbody>
					</table>
				</div>
				<p v-else class="mt-2 text-sm text-ink-gray-6">No accounts to compare.</p>
			</template>
		</LoadState>
	</section>
</template>
