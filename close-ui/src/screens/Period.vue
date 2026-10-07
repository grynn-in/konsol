<script setup>
/**
 * konsol#305 E208a: the Period screen (E2; stories 2.1, 2.2, 2.3; 0.1; W2-4).
 *
 * Data:
 * - grid_api.py GET `get_readiness` and `get_period_grid` (E203, E204), each
 *   turned into what this screen renders by periodGrid.js's `readinessView`
 *   and `gridView` (E207). The screen never re-decides tone, glyph or note
 *   text: an unknown tone or state throws there, and the thrown error is
 *   shown through LoadState rather than rendering silently.
 * - The period comes from the URL (route.js, D5): nothing is remembered in
 *   browser storage, and the All/Problems filter is a plain `ref`, never
 *   stored either.
 * - Read only: no POST, no Desk link, no IC or Checks column (W2-4; E2-7:
 *   no Entity Accountant reads this screen or its endpoints). Reached as the
 *   `period` slug through the router's glob (router.js); unreachable until
 *   E208b adds it to route.js and nav.js.
 * - Y63 (stories 1.5, 2.2): a Missing TB cell shows gridView's `tbReminded`
 *   under its status. There is no Remind button here (C-R1).
 * - D61 (stories 2.4, 2.2): above the grid, gridView's `deadlines` strip
 *   ("TB due … · IC due … · Journals due … · Sign-off due …"), and an
 *   Overdue chip on a TB cell whose `tbOverdue` is set. Overdue is only ever
 *   the server's flag; this screen never compares dates.
 * - D61b (#305-Q5-1): the strip's IC and Journals items carry the Overdue
 *   chip from the server's IC and journals flags, read by gridView's
 *   strip (`item.overdue`); this screen never reads or derives them.
 * - R52m (U2): both Overdue chips bind dueDate.js's OVERDUE_TONE (amber):
 *   a deadline never blocks (#305-2.4-1), so never the red block tone.
 * - R52m (S4): a strip step the server could not read carries gridView's
 *   `item.error` sentence, shown in place of its chip; the rows still render.
 */
import { computed, reactive, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";
import LoadState from "../components/LoadState.vue";
import { get } from "../api.js";
import { parse, format } from "../route.js";
import { gridView, readinessView, toneClass, COLUMNS } from "../periodGrid.js";
import { periodName as formatPeriod } from "../periodName.js";
import { userTimeZone } from "../timefmt.js";
import { OVERDUE_TONE } from "../dueDate.js";

const GET_READINESS = "konsol.close.grid_api.get_readiness";
const GET_PERIOD_GRID = "konsol.close.grid_api.get_period_grid";

const route = useRoute();
const period = computed(() => {
	const p = parse(`/close${route.path}`);
	return p.error || p.year == null ? null : { year: p.year, period: p.period };
});
const periodName = computed(() =>
	period.value ? formatPeriod(period.value.year, period.value.period) : "this period",
);
const readinessWhat = computed(() => `the readiness of ${periodName.value}`);
const gridWhat = computed(() => `the entity grid of ${periodName.value}`);

/** Not stored anywhere (D5): a fresh load always starts on All. */
const problemsOnly = ref(false);

const readiness = reactive({ status: "loading", payload: null, error: null, busy: false });
const grid = reactive({ status: "loading", payload: null, error: null, busy: false, now: null });

// Y63: the TB cell's reminded text is shown in the user's zone (timefmt.js,
// B29). With no zone the grid is an error, never a guessed zone.
const timeZone = userTimeZone();
const NO_ZONE = "Your browser reported no time zone, so reminder times cannot be shown.";
let readinessSeq = 0;
let gridSeq = 0;

async function loadReadiness() {
	if (!period.value) {
		readiness.status = "error";
		readiness.error = "This address names no period.";
		return;
	}
	const mine = ++readinessSeq;
	readiness.status = "loading";
	readiness.busy = true;
	try {
		const payload = await get(GET_READINESS, {
			fiscal_year: period.value.year,
			fiscal_period: period.value.period,
		});
		if (mine !== readinessSeq) return;
		readiness.payload = payload;
		readiness.error = null;
		readiness.status = "ready";
	} catch (e) {
		if (mine !== readinessSeq) return;
		readiness.error = e.message;
		readiness.status = "error";
	} finally {
		if (mine === readinessSeq) readiness.busy = false;
	}
}

async function loadGrid() {
	if (!period.value) {
		grid.status = "error";
		grid.error = "This address names no period.";
		return;
	}
	const mine = ++gridSeq;
	grid.status = "loading";
	grid.busy = true;
	try {
		const payload = await get(GET_PERIOD_GRID, {
			fiscal_year: period.value.year,
			fiscal_period: period.value.period,
		});
		if (mine !== gridSeq) return;
		grid.payload = payload;
		grid.now = new Date();
		grid.error = null;
		grid.status = "ready";
	} catch (e) {
		if (mine !== gridSeq) return;
		grid.error = e.message;
		grid.status = "error";
	} finally {
		if (mine === gridSeq) grid.busy = false;
	}
}

function retryReadiness() {
	loadReadiness();
}
function retryGrid() {
	loadGrid();
}

watch(
	() => (period.value ? `${period.value.year}/${period.value.period}` : null),
	() => {
		readiness.payload = null;
		grid.payload = null;
		problemsOnly.value = false;
		loadReadiness();
		loadGrid();
	},
	{ immediate: true },
);

// gridView throws on an unknown cell tone (E207): that throw is shown as the
// grid's error, never swallowed or rendered as a blank row.
const gridViewError = ref(null);
const gridViewResult = computed(() => {
	if (grid.status !== "ready" || !grid.payload) return null;
	try {
		if (!timeZone) {
			gridViewError.value = NO_ZONE;
			return null;
		}
		gridViewError.value = null;
		return gridView(grid.payload, problemsOnly.value, grid.now, timeZone);
	} catch (e) {
		gridViewError.value = e.message;
		return null;
	}
});

// readinessView throws on an unknown item state: shown as the strip's error.
const readinessViewError = ref(null);
const readinessViewResult = computed(() => {
	if (readiness.status !== "ready" || !readiness.payload) return null;
	try {
		readinessViewError.value = null;
		return readinessView(readiness.payload);
	} catch (e) {
		readinessViewError.value = e.message;
		return null;
	}
});

const readinessState = computed(() => {
	if (readiness.status !== "ready") return readiness.status;
	return readinessViewResult.value ? "ready" : "error";
});
const readinessError = computed(() => readinessViewError.value || readiness.error);

const gridState = computed(() => {
	if (grid.status !== "ready") return grid.status;
	if (!gridViewResult.value) return "error";
	return gridViewResult.value.rows.length === 0 ? "empty" : "ready";
});
const gridError = computed(() => gridViewError.value || grid.error);

//: review-w5: the header is gridView's title ("Period FY2025 P07"), never
//: the bare payload period code ("P07" live).
const title = computed(() => (gridViewResult.value ? gridViewResult.value.title : `Period ${periodName.value}`));

function countText(n) {
	return n == null ? "unknown" : String(n);
}

const signOffPath = computed(() =>
	period.value
		? format({ year: period.value.year, period: period.value.period, screen: "sign-off" }).replace(/^\/close/, "")
		: null,
);
</script>

<template>
	<div class="mx-auto max-w-5xl px-6 py-6">
		<header class="mb-4 flex flex-wrap items-start justify-between gap-3">
			<div>
				<h1 class="text-xl font-semibold text-ink-gray-9">{{ title }}</h1>
				<p class="mt-1 text-sm text-ink-gray-6">
					{{ countText(gridViewResult && gridViewResult.all) }} entities in scope
				</p>
			</div>
			<RouterLink
				v-if="signOffPath"
				:to="signOffPath"
				class="inline-flex items-center gap-1.5 rounded border border-outline-gray-2 bg-surface-white px-3 py-1.5 text-sm text-ink-gray-8 hover:bg-surface-gray-2"
			>
				Go to sign-off
			</RouterLink>
		</header>

		<section class="mb-6">
			<LoadState
				compact
				:state="readinessState"
				:what="readinessWhat"
				:source="GET_READINESS"
				:error="readinessError"
				:busy="readiness.busy"
				@retry="retryReadiness"
			>
				<div v-if="readinessViewResult" class="rounded border border-outline-gray-2 px-4 py-3">
					<h2 class="text-base font-semibold text-ink-gray-9">{{ readinessViewResult.title }}</h2>
					<ul class="mt-2 divide-y divide-outline-gray-1">
						<li
							v-for="item in readinessViewResult.items"
							:key="item.text"
							class="flex items-start gap-2 py-2 text-sm"
						>
							<span class="mt-0.5 shrink-0 font-mono text-ink-gray-6">{{ item.glyph }}</span>
							<span class="min-w-0 flex-1">
								<span class="text-ink-gray-8">{{ item.text }}</span>
								<span v-if="item.entities" class="ml-1 text-ink-gray-5">({{ item.entities }})</span>
							</span>
						</li>
					</ul>
				</div>
			</LoadState>
		</section>

		<section>
			<p
				v-if="gridViewResult"
				class="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-ink-gray-7"
			>
				<template v-for="(item, i) in gridViewResult.deadlines" :key="item.step">
					<span v-if="i > 0" class="text-ink-gray-4">·</span>
					<span>{{ item.text }}</span>
					<span v-if="item.error" class="text-xs text-ink-amber-3">{{ item.error }}</span>
					<span v-else-if="item.overdue" class="inline-block rounded px-2 py-0.5 text-xs font-medium" :class="OVERDUE_TONE">Overdue</span>
				</template>
			</p>

			<div class="mb-3 flex flex-wrap items-center gap-2">
				<button
					type="button"
					class="rounded px-3 py-1.5 text-sm font-medium"
					:class="!problemsOnly ? 'bg-surface-gray-7 text-ink-white' : 'bg-surface-gray-1 text-ink-gray-7 hover:bg-surface-gray-2'"
					:aria-pressed="!problemsOnly"
					@click="problemsOnly = false"
				>
					All {{ countText(gridViewResult && gridViewResult.all) }}
				</button>
				<button
					type="button"
					class="rounded px-3 py-1.5 text-sm font-medium"
					:class="problemsOnly ? 'bg-surface-gray-7 text-ink-white' : 'bg-surface-gray-1 text-ink-gray-7 hover:bg-surface-gray-2'"
					:aria-pressed="problemsOnly"
					@click="problemsOnly = true"
				>
					Problems {{ countText(gridViewResult && gridViewResult.problems) }}
				</button>
			</div>

			<p
				v-if="gridViewResult && gridViewResult.hiddenNote"
				class="mb-3 rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-sm text-ink-gray-7"
			>
				{{ gridViewResult.hiddenNote }}
			</p>
			<p
				v-if="gridViewResult && gridViewResult.ratesNote"
				role="alert"
				class="mb-3 rounded border border-outline-amber-1 bg-surface-amber-1 px-4 py-3 text-sm text-ink-amber-3"
			>
				{{ gridViewResult.ratesNote }}
			</p>

			<LoadState
				:state="gridState"
				:what="gridWhat"
				:source="GET_PERIOD_GRID"
				:error="gridError"
				:busy="grid.busy"
				:empty-text="gridViewResult && gridViewResult.empty"
				@retry="retryGrid"
			>
				<div v-if="gridViewResult" class="overflow-hidden rounded border border-outline-gray-2">
					<table class="w-full text-left text-sm">
						<thead class="bg-surface-gray-1 text-xs uppercase tracking-wide text-ink-gray-6">
							<tr>
								<th class="px-4 py-2 font-medium">Entity</th>
								<th class="px-4 py-2 font-medium">Ccy</th>
								<th v-for="col in COLUMNS" :key="col" class="px-4 py-2 font-medium">{{ col }}</th>
							</tr>
						</thead>
						<tbody>
							<tr
								v-for="row in gridViewResult.rows"
								:key="row.entity"
								class="border-t border-outline-gray-1"
							>
								<td class="px-4 py-2">
									<div class="font-medium text-ink-gray-9">{{ row.name }}</div>
									<div class="font-mono text-xs text-ink-gray-5">{{ row.entity }}</div>
								</td>
								<td class="px-4 py-2 text-ink-gray-7">{{ row.currency }}</td>
								<td class="px-4 py-2">
									<span
										class="inline-block rounded px-2 py-0.5 text-xs font-medium"
										:class="toneClass(row.ownership.tone)"
									>{{ row.ownership.label }}</span>
								</td>
								<td class="px-4 py-2">
									<span
										class="inline-block rounded px-2 py-0.5 text-xs font-medium"
										:class="toneClass(row.tb.tone)"
									>{{ row.tb.label }}</span>
									<span v-if="row.tbOverdue" class="ml-1 inline-block rounded px-2 py-0.5 text-xs font-medium" :class="OVERDUE_TONE">Overdue</span>
									<div v-if="row.tbReminded" class="mt-1 text-xs text-ink-gray-6">{{ row.tbReminded }}</div>
								</td>
								<td class="px-4 py-2">
									<span
										class="inline-block rounded px-2 py-0.5 text-xs font-medium"
										:class="toneClass(row.rate.tone)"
									>{{ row.rate.label }}</span>
								</td>
							</tr>
						</tbody>
					</table>
				</div>
			</LoadState>
		</section>
	</div>
</template>
