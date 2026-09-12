<script setup>
/**
 * Upload trial balances: one file for many entities and periods.
 *
 * Choose a CSV or Excel file → it is checked against the same rules as a
 * single upload → the page lists every entity-period as Ready or Problem →
 * load all of them, or only the ready ones → watch progress. Nothing loads
 * until the load button is pressed.
 */
import { computed, inject, onMounted, ref } from "vue";
import { useMachine } from "@xstate/vue";
import { Button } from "frappe-ui";
import StatusBadge from "./StatusBadge.vue";
import { uploadMachine } from "../machines/uploadMachine.js";
import { recentUploads } from "../uploadApi.js";
import { isNoAccess } from "../homeApi.js";
import { summarize, rowStatus, visibleRows, loadLabel, progress, periodText, money } from "../uploads.js";

const home = inject("home");
const { snapshot, send } = useMachine(uploadMachine);
const ctx = computed(() => snapshot.value.context);
const upload = computed(() => ctx.value.upload);
const summary = computed(() => summarize(upload.value?.report));
const problemsOnly = ref(false);
const rows = computed(() => visibleRows(upload.value?.report, problemsOnly.value));
const busy = computed(() => ["uploading", "checking", "starting"].some((s) => snapshot.value.matches(s)));
const loading = computed(() => snapshot.value.matches("loading"));
const done = computed(() => snapshot.value.matches("done"));
const canLoad = computed(() => snapshot.value.matches("checked") && upload.value?.status === "Checked" && summary.value.ready > 0);
// A load that stopped (the worker restarted) or finished partly can pick up
// where it left off; rows already loaded are never loaded twice.
const canResume = computed(() => done.value && summary.value.ready > 0
	&& (upload.value?.stalled || ["Partly Loaded", "Failed"].includes(upload.value?.status)));
const stepText = computed(() => {
	if (snapshot.value.matches("uploading")) return `Uploading ${ctx.value.file?.name}…`;
	if (snapshot.value.matches("checking")) return "Checking every entity and period…";
	if (snapshot.value.matches("starting")) return "Checking again and starting the load…";
	return "";
});

const canUpload = computed(() => Boolean(home.me.value?.can?.approve));
const recent = ref([]);
onMounted(async () => {
	try { recent.value = await recentUploads(); } catch { recent.value = []; }
});

const fileInput = ref(null);
function pick(e) {
	const file = e.target.files?.[0];
	if (file) send({ type: "CHOOSE", file });
	e.target.value = "";
}
function loadNow() {
	send({ type: "LOAD", skipInvalid: summary.value.problems > 0 });
}
</script>

<template>
	<div class="mx-auto max-w-5xl px-6 py-5">
		<header class="mb-5">
			<h1 class="text-2xl font-semibold tracking-tight text-ink-gray-9">Upload trial balances</h1>
			<p class="mt-1 max-w-2xl text-base text-ink-gray-6">
				One file for many entities and periods. Every entity-period is checked like a single upload
				before anything loads, and each becomes its own trial balance submission.
			</p>
		</header>

		<p v-if="!canUpload" class="rounded-lg border border-outline-gray-2 px-4 py-6 text-base text-ink-gray-6">
			Loading trial balances in bulk is for the Close Lead (EPM Admin). Entity Accountants upload their own
			entity's trial balance from the month view.
		</p>

		<template v-else>
			<section v-if="!upload || done" class="mb-6 rounded-lg border border-dashed border-outline-gray-3 px-5 py-6">
				<div class="flex flex-wrap items-center gap-4">
					<Button variant="solid" :loading="busy" @click="fileInput?.click()">
						{{ done ? "Upload another file" : "Choose a CSV or Excel file" }}
					</Button>
					<span class="text-sm text-ink-gray-5">{{ stepText }}</span>
					<input ref="fileInput" type="file" accept=".csv,.xlsx" class="hidden" aria-label="Trial balance file" @change="pick" />
				</div>
				<p class="mt-4 text-sm text-ink-gray-6">One row per account, with a header row:</p>
				<div class="mt-2 overflow-x-auto">
					<code class="block whitespace-nowrap rounded bg-surface-gray-2 px-3 py-2 text-sm text-ink-gray-8">
						data_area_id, fiscal_year, fiscal_period, main_account, debit, credit, description
					</code>
				</div>
				<p class="mt-2 text-sm text-ink-gray-5">
					Amounts in each entity's own currency, debits and credits both positive. Period 1 to 12.
					The first sheet of an Excel workbook is read.
				</p>
			</section>

			<p v-if="ctx.error" class="mb-4 rounded-lg bg-surface-red-1 px-4 py-3 text-base text-ink-red-4" role="alert">
				{{ isNoAccess(ctx.error) ? "You don't have permission to load trial balances in bulk." : ctx.error.message }}
			</p>

			<section v-if="upload">
				<div v-if="upload.status === 'Failed' && upload.error && !upload.loaded_count" class="mb-4 rounded-lg border border-outline-gray-2 px-4 py-3">
					<h2 class="text-base font-semibold text-ink-gray-9">The file could not be read</h2>
					<pre class="mt-2 whitespace-pre-wrap text-sm text-ink-gray-7">{{ upload.error }}</pre>
				</div>

				<div class="mb-3 flex flex-wrap items-center gap-x-5 gap-y-2">
					<h2 class="text-lg font-semibold text-ink-gray-9">{{ upload.file_name }}</h2>
					<span class="text-sm text-ink-gray-6 tnum">
						{{ summary.groups }} entity-periods · {{ summary.entities }} entities · {{ summary.lines }} rows
					</span>
					<StatusBadge v-if="summary.ready" state="ready" :label="`${summary.ready} ready`" />
					<StatusBadge v-if="summary.problems" state="error" :label="`${summary.problems} with problems`" />
				</div>

				<div v-if="loading || done" class="mb-4">
					<div class="h-2 overflow-hidden rounded-full bg-surface-gray-2" role="progressbar" :aria-valuenow="progress(upload)" aria-valuemin="0" aria-valuemax="100">
						<div class="h-full bg-blue-500 transition-all" :style="{ width: `${progress(upload)}%` }" />
					</div>
					<p class="mt-2 text-sm text-ink-gray-6 tnum">
						<template v-if="loading">Loading… {{ upload.loaded_count }} of {{ upload.valid_count }} loaded<template v-if="upload.failed_count">, {{ upload.failed_count }} failed</template>.</template>
						<template v-else-if="upload.stalled">The load stopped before it finished: {{ upload.loaded_count }} loaded so far.</template>
						<template v-else>{{ upload.status }}: {{ upload.loaded_count }} loaded<template v-if="upload.failed_count">, {{ upload.failed_count }} failed</template>. The consolidation build is requested automatically.</template>
					</p>
					<p v-if="upload.error && !loading" class="mt-1 text-sm text-ink-gray-6">{{ upload.error }}</p>
					<Button v-if="canResume" class="mt-3" variant="solid" @click="send({ type: 'LOAD', skipInvalid: true })">
						Resume: load the remaining {{ summary.ready }}
					</Button>
				</div>

				<div class="mb-3 flex flex-wrap items-center gap-3">
					<Button v-if="canLoad" variant="solid" :loading="snapshot.matches('starting')" @click="loadNow">{{ loadLabel(summary) }}</Button>
					<Button v-if="snapshot.matches('checked')" variant="subtle" @click="fileInput?.click()">Choose another file</Button>
					<input v-if="snapshot.matches('checked')" ref="fileInput" type="file" accept=".csv,.xlsx" class="hidden" aria-label="Trial balance file" @change="pick" />
					<label v-if="summary.problems || summary.failed" class="ml-auto flex items-center gap-2 text-sm text-ink-gray-7">
						<input id="problems-only" v-model="problemsOnly" type="checkbox" />
						Show problems only
					</label>
				</div>

				<div class="overflow-x-auto rounded-lg border border-outline-gray-2">
					<table class="w-full min-w-[44rem] text-left text-sm">
						<thead class="bg-surface-gray-1 text-xs uppercase tracking-wider text-ink-gray-5">
							<tr>
								<th class="px-3 py-2 font-medium">Entity</th>
								<th class="px-3 py-2 font-medium">Period</th>
								<th class="px-3 py-2 text-right font-medium">Rows</th>
								<th class="px-3 py-2 text-right font-medium">Debit</th>
								<th class="px-3 py-2 text-right font-medium">Credit</th>
								<th class="px-3 py-2 font-medium">Status</th>
								<th class="px-3 py-2 font-medium">Detail</th>
							</tr>
						</thead>
						<tbody class="divide-y divide-outline-gray-1">
							<tr v-for="r in rows" :key="`${r.entity}-${r.fiscal_year}-${r.fiscal_period}`">
								<td class="px-3 py-2 font-medium text-ink-gray-9">{{ r.entity }}</td>
								<td class="px-3 py-2 text-ink-gray-7 tnum">{{ periodText(r) }}</td>
								<td class="px-3 py-2 text-right text-ink-gray-7 tnum">{{ r.rows }}</td>
								<td class="px-3 py-2 text-right text-ink-gray-7 tnum">{{ money(r.total_debit) }}</td>
								<td class="px-3 py-2 text-right text-ink-gray-7 tnum">{{ money(r.total_credit) }}</td>
								<td class="px-3 py-2"><StatusBadge :state="rowStatus(r).state" :label="rowStatus(r).label" /></td>
								<td class="px-3 py-2 text-ink-gray-6">
									<a v-if="r.loaded" :href="`/app/trial-balance-submission/${encodeURIComponent(r.loaded)}`" target="_blank" rel="noopener" class="underline">{{ r.loaded }}</a>
									<span v-else>{{ rowStatus(r).note }}</span>
								</td>
							</tr>
							<tr v-if="!rows.length">
								<td colspan="7" class="px-3 py-6 text-center text-ink-gray-5">No rows to show.</td>
							</tr>
						</tbody>
					</table>
				</div>
			</section>

			<section v-if="recent.length && !upload" class="mt-8">
				<h2 class="mb-2 text-sm font-semibold uppercase tracking-wider text-ink-gray-5">Recent uploads</h2>
				<ul class="divide-y divide-outline-gray-1 overflow-hidden rounded-lg border border-outline-gray-2 text-sm">
					<li v-for="u in recent" :key="u.name" class="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2">
						<a :href="`/app/trial-balance-upload/${encodeURIComponent(u.name)}`" target="_blank" rel="noopener" class="font-medium text-ink-gray-9 underline">{{ u.name }}</a>
						<span class="text-ink-gray-6">{{ (u.upload_file || "").split("/").pop() }}</span>
						<span class="text-ink-gray-5 tnum">{{ u.loaded_count }} of {{ u.group_count }} loaded</span>
						<span class="ml-auto text-ink-gray-5">{{ u.status }}</span>
					</li>
				</ul>
			</section>
		</template>
	</div>
</template>
