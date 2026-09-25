<script setup>
/**
 * konsol#305 B19: Trial balances — my entities for the period (stories 3.1, 3.7).
 *
 * - Data: A25 `tb_read_api.my_tbs(year, period)`, turned into rows by B12's
 *   `entityRows` (which throws on a status it does not know, so an entity is
 *   never shown with a blank or guessed status; the throw becomes the error
 *   state here). A15 `get_context` gives the persona, only to word the empty
 *   state for an Entity Accountant (A25 note).
 * - The period comes from the URL (route.js, D5). Nothing is remembered.
 * - The on-behalf label is shown exactly as the server sent it (R4). A label
 *   the server did not send reads "not recorded", never a guessed "by <owner>".
 * - Read only (D1): no cell is editable. Selecting an entity opens its detail
 *   area; B20 mounts the upload section and B21 the compare section there.
 * - The upload section (B20) is shown only to the personas who upload: the
 *   Entity Accountant (my_tbs lists only their own entities) and the Close
 *   Lead (whose upload the server labels on-behalf, R4), and only when the
 *   server says `can_upload` (the period is Open and the user may create a
 *   trial balance). After a submit the list is re-read in place, so the
 *   server's own label is shown; the detail area stays open.
 * - No due date is shown: nothing declares one (Problems 6).
 * - B27: a missing TB shows the dash from `entityRows` (never "None"), and
 *   the upload / exception times are formatted in the user's zone like the
 *   freshness bar (B09), with the zone from timefmt.js's `userTimeZone` (B29),
 *   shared with AppShell. With no zone the list is an error, never a
 *   guessed zone; a zone-less server timestamp is refused the same way.
 */
import { computed, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import LoadState from "../components/LoadState.vue";
import TbUpload from "../sections/TbUpload.vue";
import TbCompare from "../sections/TbCompare.vue";
import { get } from "../api.js";
import { parse } from "../route.js";
import { entityRows } from "../tbTable.js";
import { userTimeZone } from "../timefmt.js";

const MY_TBS = "konsol.close.tb_read_api.my_tbs";
const CONTEXT = "konsol.close.period_api.get_context";

const NOT_RECORDED = "not recorded";
const NO_ENTITIES_EA = "No entities are assigned to you. Ask the System Manager.";

// One style per A25 status (all six, per the A25 note). Missing and the two
// "not declared" gaps are the ones that need someone to act.
const STATUS_TONE = {
	"Missing": "bg-surface-red-1 text-ink-red-3",
	"Frequency not declared": "bg-surface-amber-1 text-ink-amber-3",
	"Quarter not declared": "bg-surface-amber-1 text-ink-amber-3",
	"Received": "bg-surface-green-1 text-ink-green-3",
	"Exception declared": "bg-surface-gray-2 text-ink-gray-7",
	"Not expected this period": "bg-surface-gray-1 text-ink-gray-6",
};
const STATUS_ORDER = Object.keys(STATUS_TONE);

const route = useRoute();
const period = computed(() => parse(`/close${route.path}`));

function periodName(p) {
	return `FY${p.year} P${String(p.period).padStart(2, "0")}`;
}
const what = computed(() =>
	period.value.error || period.value.year == null
		? "trial balances"
		: `trial balances for ${periodName(period.value)}`,
);

/** A count the page could not work out is "unknown", never 0 or blank. */
function countText(n) {
	return n == null ? "unknown" : String(n);
}

const timeZone = userTimeZone();
const NO_ZONE = "Your browser reported no time zone, so upload times cannot be shown.";

const load = reactive({ status: "loading", data: null, persona: null, error: null, now: null });
const busy = ref(false);
const selectedCode = ref(null);
let seq = 0;

async function fetchAll() {
	const p = period.value;
	const mine = ++seq;
	selectedCode.value = null;
	if (p.error || p.year == null) {
		load.status = "error";
		load.error = `This address has no period: ${p.error || "none given"}.`;
		return;
	}
	load.status = "loading";
	load.error = null;
	const params = { fiscal_year: p.year, fiscal_period: p.period };
	try {
		const [tbs, context] = await Promise.all([get(MY_TBS, params), get(CONTEXT, params)]);
		if (mine !== seq) return;
		load.data = tbs;
		load.now = new Date();
		load.persona = context && context.me ? context.me.persona : null;
		load.status = "ready";
	} catch (e) {
		if (mine !== seq) return;
		load.error = e.message;
		load.status = "error";
	}
}

async function retry() {
	busy.value = true;
	try {
		await fetchAll();
	} finally {
		busy.value = false;
	}
}

watch(() => route.path, () => fetchAll(), { immediate: true });

const refreshError = ref(null);

/** After a submit: re-read my_tbs without leaving the detail area. */
async function refreshAfterSubmit() {
	const p = period.value;
	const mine = ++seq;
	refreshError.value = null;
	try {
		const tbs = await get(MY_TBS, { fiscal_year: p.year, fiscal_period: p.period });
		if (mine !== seq) return;
		load.data = tbs;
		load.now = new Date();
	} catch (e) {
		if (mine !== seq) return;
		refreshError.value = `The trial balance was received, but the list could not be re-read: ${e.message}`;
	}
}

/** `entityRows` refuses an unknown status; that refusal is shown, not hidden. */
const table = computed(() => {
	if (load.status !== "ready") return { rows: null, error: null };
	if (!timeZone) return { rows: null, error: NO_ZONE };
	try {
		return { rows: entityRows(load.data, load.now, timeZone), error: null };
	} catch (e) {
		return { rows: null, error: e.message };
	}
});

const viewState = computed(() => {
	if (load.status !== "ready") return load.status;
	if (table.value.error) return "error";
	return table.value.rows.length === 0 ? "empty" : "ready";
});
const viewError = computed(() => table.value.error || load.error);

const emptyText = computed(() =>
	load.persona === "entity_accountant"
		? NO_ENTITIES_EA
		: `No entity is in scope for ${what.value}. An entity is in scope when it is Active, not a group, and has a submitted ownership period covering the period start.`,
);

/** Entities per status, in A25's order; a status with no entity is left out. */
const summary = computed(() => {
	const rows = table.value.rows;
	return STATUS_ORDER.map((status) => ({
		status,
		count: rows ? rows.filter((r) => r.status === status).length : null,
	})).filter((s) => s.count == null || s.count > 0);
});
const total = computed(() => (table.value.rows ? table.value.rows.length : null));

const selected = computed(() =>
	(table.value.rows || []).find((r) => r.entity === selectedCode.value) || null,
);

const canUpload = computed(() => Boolean(load.data && load.data.can_upload) && (load.persona === "entity_accountant" || load.persona === "close_lead"));

function select(code) {
	selectedCode.value = selectedCode.value === code ? null : code;
}

/**
 * B18b: a `?entity=` in the URL (from MyWork.vue's itemRoute, B10) opens
 * that entity's detail area directly. An entity named in the query that is
 * not one of this period's rows gets a visible note instead of a guess —
 * it is never silently swapped for the first row or any other row.
 */
const entityNote = ref(null);

watch(
	() => [table.value.rows, route.query.entity],
	() => {
		const rows = table.value.rows;
		const wanted = typeof route.query.entity === "string" ? route.query.entity : null;
		if (!rows || !wanted) {
			entityNote.value = null;
			return;
		}
		const match = rows.find((r) => r.entity === wanted);
		if (match) {
			selectedCode.value = match.entity;
			entityNote.value = null;
		} else {
			entityNote.value = `The entity "${wanted}" from the link is not in this period's list.`;
		}
	},
	{ immediate: true },
);
</script>

<template>
	<div class="mx-auto max-w-5xl px-6 py-6">
		<header class="mb-4">
			<h1 class="text-xl font-semibold text-ink-gray-9">Trial balances</h1>
			<p class="mt-1 text-sm text-ink-gray-6">
				Your entities for {{ period.error || period.year == null ? "this period" : periodName(period) }}, and whether each trial balance is in.
			</p>
		</header>

		<LoadState
			:state="viewState"
			:what="what"
			:source="MY_TBS"
			:error="viewError"
			:busy="busy"
			:empty-text="emptyText"
			@retry="retry"
		>
			<p v-if="!load.data.period_open" class="mb-4 rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-sm text-ink-gray-7">
				This period is not open. Trial balances are shown read only.
			</p>

			<p v-if="entityNote" role="alert" class="mb-4 rounded border border-outline-amber-1 bg-surface-amber-1 px-4 py-3 text-sm text-ink-amber-3">
				{{ entityNote }}
			</p>

			<p class="mb-3 text-sm text-ink-gray-7">
				<span class="font-medium text-ink-gray-9">{{ countText(total) }}</span> entities:
				<template v-for="(s, i) in summary" :key="s.status">
					<span v-if="i > 0">, </span>{{ countText(s.count) }} {{ s.status.toLowerCase() }}
				</template>
			</p>

			<div class="overflow-hidden rounded border border-outline-gray-2">
				<table class="w-full text-left text-sm">
					<thead class="bg-surface-gray-1 text-xs uppercase tracking-wide text-ink-gray-6">
						<tr>
							<th class="px-4 py-2 font-medium">Entity</th>
							<th class="px-4 py-2 font-medium">Status</th>
							<th class="px-4 py-2 font-medium">Trial balance</th>
							<th class="px-4 py-2 font-medium">Uploaded</th>
						</tr>
					</thead>
					<tbody>
						<tr
							v-for="row in table.rows"
							:key="row.entity"
							class="cursor-pointer border-t border-outline-gray-1 hover:bg-surface-gray-1"
							:class="{ 'bg-surface-gray-2': selected && selected.entity === row.entity }"
							:aria-selected="selected && selected.entity === row.entity ? 'true' : 'false'"
							tabindex="0"
							@click="select(row.entity)"
							@keydown.enter.prevent="select(row.entity)"
							@keydown.space.prevent="select(row.entity)"
						>
							<td class="px-4 py-2">
								<div class="font-medium text-ink-gray-9">{{ row.name }}</div>
								<div class="font-mono text-xs text-ink-gray-5">{{ row.entity }}</div>
							</td>
							<td class="px-4 py-2">
								<span class="inline-block rounded px-2 py-0.5 text-xs font-medium" :class="STATUS_TONE[row.status]">{{ row.status }}</span>
							</td>
							<td class="px-4 py-2 text-ink-gray-7">
								<template v-if="row.tb">
									<div class="font-mono text-xs">{{ row.tbText }}</div>
									<div>{{ row.tb.on_behalf_label || NOT_RECORDED }}</div>
								</template>
								<template v-else-if="row.exception">Exception: {{ row.exception.reason || "no reason given" }}</template>
								<span v-else class="text-ink-gray-5">{{ row.tbText }}</span>
							</td>
							<td class="px-4 py-2 text-ink-gray-7">{{ row.uploaded }}</td>
						</tr>
					</tbody>
				</table>
			</div>

			<section
				v-if="selected"
				data-detail
				:aria-label="`Detail for ${selected.name}`"
				class="mt-4 rounded border border-outline-gray-2 px-4 py-4"
			>
				<div class="flex items-start justify-between gap-4">
					<div>
						<h2 class="text-base font-semibold text-ink-gray-9">{{ selected.name }} <span class="font-mono text-xs text-ink-gray-5">{{ selected.entity }}</span></h2>
						<p class="mt-1 text-sm">
							<span class="inline-block rounded px-2 py-0.5 text-xs font-medium" :class="STATUS_TONE[selected.status]">{{ selected.status }}</span>
						</p>
					</div>
					<button type="button" class="text-sm text-ink-gray-6 hover:text-ink-gray-9" @click="selectedCode = null">Close</button>
				</div>
				<dl class="mt-3 grid grid-cols-[10rem_1fr] gap-x-4 gap-y-1 text-sm">
					<template v-if="selected.tb">
						<dt class="text-ink-gray-5">Trial balance</dt>
						<dd class="font-mono text-ink-gray-8">{{ selected.tbText }}</dd>
						<dt class="text-ink-gray-5">Uploaded</dt>
						<dd class="text-ink-gray-8">{{ selected.tb.on_behalf_label || NOT_RECORDED }}</dd>
						<dt class="text-ink-gray-5">On</dt>
						<dd class="text-ink-gray-8">{{ selected.uploaded }}</dd>
					</template>
					<template v-if="selected.exception">
						<dt class="text-ink-gray-5">Exception</dt>
						<dd class="text-ink-gray-8">{{ selected.exception.reason || "no reason given" }}</dd>
						<dt class="text-ink-gray-5">Declared by</dt>
						<dd class="text-ink-gray-8">{{ selected.exception.declared_by || NOT_RECORDED }}</dd>
						<dt class="text-ink-gray-5">Declared on</dt>
						<dd class="text-ink-gray-8">{{ selected.exception.declaredOnText }}</dd>
					</template>
					<template v-if="!selected.tb && !selected.exception">
						<dt class="text-ink-gray-5">Trial balance</dt>
						<dd class="text-ink-gray-8">None submitted for this period.</dd>
					</template>
				</dl>
				<p v-if="refreshError" role="alert" class="mt-3 text-sm text-ink-red-3">{{ refreshError }}</p>
				<TbUpload
					v-if="canUpload"
					:key="`${selected.entity}-${period.year}-${period.period}`"
					:entity="selected.entity"
					:entity-name="selected.name"
					:fiscal-year="period.year"
					:fiscal-period="period.period"
					@submitted="refreshAfterSubmit"
				/>
				<TbCompare
					v-if="selected.status === 'Received'"
					:key="`${selected.entity}-${period.year}-${period.period}-compare`"
					:entity="selected.entity"
					:fiscal-year="period.year"
					:fiscal-period="period.period"
				/>
			</section>
		</LoadState>
	</div>
</template>
