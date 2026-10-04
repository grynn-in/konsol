<script setup>
/**
 * konsol#305 U44: the Numbers screen — the consolidated statement,
 * read-only (E8; story 8.1; W4-2/W4-3/W4-4 as rendered by numbers.js). U45
 * adds the drill panel and U46 the commentary editor; neither is built here.
 *
 * Data:
 * - GET `statement_api.get_statement(fiscal_year, fiscal_period,
 *   consolidation_group=None)` (N51), turned into `{header, label, legend,
 *   state, groupChoice, notIncluded, gapText, tabs, comparisonNote}` by
 *   numbers.js's `statementView` (U41). The screen never decides a label,
 *   tone, column or amount itself: it walks `tabRows(view, section)` for
 *   each tab's rows (no second derivation off `view.tabs[i].rows`) and
 *   binds `isDrillable` (unused until U45 opens anything on a click).
 * - `view.state` is a business state the server itself declared —
 *   `choose_group`, `no_chart`, `not_built` or `error` — distinct from
 *   LoadState's own fetch-level states: the GET can succeed while the
 *   payload still carries a non-ok `view.state`, and then `view.tabs` is
 *   empty (statementView never returns tabs for a non-ok state), so no
 *   table is shown.
 * - `choose_group`: the screen offers `view.groupChoice` in a plain select,
 *   held only as local component state (`chosenGroup`, never browser
 *   storage, never the URL) and re-sent as `consolidation_group` on the
 *   next GET. A period change (route.js, D5) clears it — the group is not
 *   part of the address.
 * - `view.gapText`: the declared-accounts setup gap's sentence (N41/N45),
 *   shown with a "Open Close Settings" Desk link (story 0.4's "Fix in
 *   Desk" pattern, MyWork.vue). Every close role that can open Numbers can
 *   also at least read Close Settings (close_settings.json: EPM Analyst
 *   and EPM User read, EPM Admin and System Manager write); Frappe's own
 *   Desk permissions decide what the visitor can do once there, so the
 *   link is not persona-gated client-side — this payload carries no
 *   persona signal to gate it with.
 * - A thrown `statementView` error shows through LoadState as an error
 *   (never a blank table): the `view` computed mirrors Rates.vue's/
 *   Intercompany.vue's own pattern, and the table markup sits inside
 *   `<template v-if="view">`, which is false while an error is showing.
 * - No budget column (D2-6), no subtotals (W4-3): `tab.columns` already
 *   carries the exact column set per section (numbers.js), and the row
 *   loop adds no row of its own.
 *
 * The period comes from the URL (route.js, D5); nothing is kept in the
 * browser. Not built here: the drill panel (U45) and the commentary editor
 * (U46) — the per-heading commentary shown here is read-only text.
 */
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { FeatherIcon } from "frappe-ui";
import LoadState from "../components/LoadState.vue";
import NumbersDrill from "../components/NumbersDrill.vue";
import { get } from "../api.js";
import { parse } from "../route.js";
import { statementView, tabRows, isDrillable } from "../numbers.js";
import { userTimeZone } from "../timefmt.js";

const GET_STATEMENT = "konsol.close.statement_api.get_statement";

const NO_ZONE = "Your browser reported no time zone, so times cannot be shown.";

//: The columns `numbers.js` declares per section map onto these row keys.
//: A column `tab.columns` never names outside this map is a numbers.js bug,
//: not something this screen guesses at — `cellText` throws naming it.
const COLUMN_FIELD = {
	"This period": "current",
	Comparison: "comparison",
	Variance: "variance",
	"Year to date": "ytd",
};

//: `view.label.tone` (signed/provisional/resign_needed) and the residual
//: row's own `tone` share this palette.
const TONE_CLASS = {
	ok: "bg-surface-green-1 text-ink-green-4",
	warn: "bg-surface-amber-1 text-ink-amber-4",
	block: "bg-surface-red-1 text-ink-red-4",
};

//: `view.state.kind` -> which tone its banner reads as. `choose_group`
//: asks for input, not a failure; the other three are the server's own
//: non-ok states (statement_api.py module docstring).
const STATE_TONE = {
	choose_group: "warn",
	no_chart: "block",
	not_built: "block",
	error: "block",
};

const route = useRoute();
const period = computed(() => {
	const p = parse(`/close${route.path}`);
	return p.error || p.year == null ? null : { year: p.year, period: p.period };
});
const periodName = computed(() =>
	period.value ? `FY${period.value.year} P${String(period.value.period).padStart(2, "0")}` : "this period",
);
const what = computed(() => `the numbers for ${periodName.value}`);
const timeZone = userTimeZone();

const numbers = reactive({ status: "loading", payload: null, error: null, busy: false, now: null });
let seq = 0;

//: U44 fact: a group choice is local state, never stored (no browser
//: persistence, no URL). Cleared on every period change.
const chosenGroup = ref(null);

//: U45: the heading whose drill panel is open, local state only (never
//: stored, never the URL). Cleared on every period change, same as
//: chosenGroup — the panel belongs to this load, not to the address.
const selectedHeading = ref(null);

async function load({ quiet = false } = {}) {
	if (!period.value) {
		numbers.status = "error";
		numbers.error = "This address names no period.";
		return;
	}
	const mine = ++seq;
	if (!quiet) numbers.status = "loading";
	numbers.busy = true;
	try {
		const params = { fiscal_year: period.value.year, fiscal_period: period.value.period };
		if (chosenGroup.value) params.consolidation_group = chosenGroup.value;
		const payload = await get(GET_STATEMENT, params);
		if (mine !== seq) return;
		numbers.payload = payload;
		numbers.error = null;
		numbers.now = new Date();
		numbers.status = "ready";
	} catch (e) {
		if (mine !== seq) return;
		numbers.error = e.message;
		numbers.status = "error";
	} finally {
		if (mine === seq) numbers.busy = false;
	}
}

//: The only way `chosenGroup` changes: the choose_group select below.
function chooseGroup(code) {
	chosenGroup.value = code || null;
	load();
}

watch(
	() => (period.value ? `${period.value.year}/${period.value.period}` : null),
	() => {
		numbers.payload = null;
		chosenGroup.value = null;
		selectedHeading.value = null;
		load();
	},
	{ immediate: true },
);

onBeforeUnmount(() => {
	seq++;
});

// statementView throws on anything it does not recognise (mirrors
// Rates.vue's/Intercompany.vue's `view`, never swallowed).
const viewError = ref(null);
const view = computed(() => {
	if (numbers.status !== "ready" || !numbers.payload) return null;
	if (!timeZone) {
		viewError.value = NO_ZONE;
		return null;
	}
	try {
		viewError.value = null;
		return statementView(numbers.payload, numbers.now || new Date(), timeZone);
	} catch (e) {
		viewError.value = e.message;
		return null;
	}
});
const loadState = computed(() => {
	if (numbers.status !== "ready") return numbers.status;
	return view.value ? "ready" : "error";
});
const loadError = computed(() => viewError.value || numbers.error);

function stateClass(kind) {
	return TONE_CLASS[STATE_TONE[kind]] || TONE_CLASS.block;
}

/** One row's one column cell, by the column's own label (numbers.js's
 * `COLUMNS`). A row that carries no value for this column (the residual
 * row has only `current`) reads as an em dash — never a guessed amount.
 * A column this map does not know throws, naming it. */
function cellText(row, column) {
	if (!(column in COLUMN_FIELD)) {
		throw new Error(`Numbers: unknown column: ${column}`);
	}
	const field = COLUMN_FIELD[column];
	return field in row ? row[field] : "—";
}

function rowLabelClass(row) {
	if (row.indent) return "pl-8 text-ink-gray-6";
	if (row.kind === "net_result") return "font-semibold text-ink-gray-9";
	return "font-medium text-ink-gray-9";
}

function cellClass(row, column) {
	if (row.kind === "residual" && column === "This period") {
		return row.tone === "ok" ? "text-ink-green-4" : "font-semibold text-ink-red-4";
	}
	return "text-ink-gray-8";
}

//: U45: a drillable (heading) row gets a visual cursor hint on top of the
//: residual's own tone class; every other row kind gets neither.
function rowRowClass(row) {
	const classes = [];
	if (row.kind === "residual" && row.tone === "block") classes.push("bg-surface-red-1");
	if (isDrillable(row)) classes.push("cursor-pointer hover:bg-surface-gray-1");
	return classes.join(" ");
}

//: U45: opens the drill panel for a heading row. Numbers.vue never calls
//: this unconditionally from the template — the @click binding itself
//: tests isDrillable(row) first (U41: residual, not-in-chart, no-heading
//: and "of which" rows are never clickable).
function openDrill(row) {
	selectedHeading.value = row.heading;
}
</script>

<template>
	<div class="mx-auto max-w-6xl px-6 py-6">
		<header>
			<div class="flex flex-wrap items-center gap-3">
				<h1 class="text-xl font-semibold text-ink-gray-9">{{ view ? view.header : "Numbers" }}</h1>
				<span v-if="view" class="inline-block rounded px-2 py-0.5 text-xs font-medium" :class="TONE_CLASS[view.label.tone]">
					{{ view.label.text }}
				</span>
			</div>
			<p v-if="view" class="mt-1 text-sm text-ink-gray-6">{{ view.legend }}</p>
		</header>

		<div v-if="view && view.gapText" role="status" class="mt-3 flex flex-wrap items-center justify-between gap-2 rounded border border-outline-amber-1 bg-surface-amber-1 px-4 py-3 text-sm text-ink-amber-3">
			<span>{{ view.gapText }}</span>
			<a
				href="/app/close-settings"
				target="_blank"
				rel="noopener noreferrer"
				class="inline-flex items-center gap-1.5 shrink-0 rounded border border-outline-gray-2 bg-surface-white px-3 py-1.5 text-sm text-ink-gray-8 hover:bg-surface-gray-2"
				title="Opens Close Settings in the Desk, in a new tab"
			>
				Open Close Settings
				<FeatherIcon name="external-link" class="h-3.5 w-3.5" />
			</a>
		</div>

		<LoadState class="mt-4" :state="loadState" :what="what" :source="GET_STATEMENT" :error="loadError" :busy="numbers.busy" @retry="load">
			<template v-if="view">
				<div v-if="view.state" class="rounded border px-4 py-4 text-sm" :class="stateClass(view.state.kind)">
					<p>{{ view.state.message }}</p>
					<div v-if="view.state.kind === 'choose_group'" class="mt-3">
						<label class="flex flex-col gap-1 text-sm text-ink-gray-7">
							<span>Group</span>
							<select
								:value="chosenGroup || ''"
								class="w-64 rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-ink-gray-8"
								@change="chooseGroup($event.target.value)"
							>
								<option value="" disabled>Choose a group</option>
								<option v-for="g in view.groupChoice" :key="g" :value="g">{{ g }}</option>
							</select>
						</label>
					</div>
				</div>

				<template v-else>
					<p v-if="view.notIncluded" class="mb-3 text-xs text-ink-gray-5">{{ view.notIncluded }}</p>

					<section v-for="tab in view.tabs" :key="tab.section" class="mb-6">
						<h2 class="mb-2 text-base font-semibold text-ink-gray-9">{{ tab.section }}</h2>
						<div class="overflow-x-auto rounded border border-outline-gray-2">
							<table class="w-full text-left text-sm">
								<thead class="bg-surface-gray-1 text-xs uppercase tracking-wide text-ink-gray-6">
									<tr>
										<th class="px-3 py-2 font-medium">Line</th>
										<th v-for="col in tab.columns" :key="col" class="px-3 py-2 text-right font-medium">{{ col }}</th>
									</tr>
								</thead>
								<tbody>
									<template v-for="(row, i) in tabRows(view, tab.section)" :key="i">
										<tr
											class="border-t border-outline-gray-2"
											:class="rowRowClass(row)"
											@click="isDrillable(row) ? openDrill(row) : undefined"
										>
											<td class="px-3 py-2" :class="rowLabelClass(row)">
												{{ row.label }}
												<span v-if="row.kind === 'not_in_chart' && row.codes && row.codes.length" class="text-xs text-ink-gray-5">
													({{ row.codes.join(", ") }})
												</span>
												<span
													v-if="row.kind === 'heading'"
													class="ml-1 rounded bg-surface-gray-2 px-1.5 py-0.5 text-xs font-normal text-ink-gray-6"
													:title="isDrillable(row) ? 'Opens the drill panel' : ''"
												>
													{{ row.heading }}
												</span>
											</td>
											<td v-for="col in tab.columns" :key="col" class="px-3 py-2 text-right font-mono" :class="cellClass(row, col)">
												{{ cellText(row, col) }}
											</td>
										</tr>
										<tr v-if="row.kind === 'heading' && row.commentary" class="border-t border-outline-gray-2 bg-surface-gray-1">
											<td :colspan="tab.columns.length + 1" class="px-3 py-1.5 text-xs text-ink-gray-6">
												{{ row.commentary.text }} — {{ row.commentary.byText }}
											</td>
										</tr>
										<tr v-if="row.kind === 'residual' && row.tone === 'block'" class="border-t border-outline-gray-2 bg-surface-red-1">
											<td :colspan="tab.columns.length + 1" class="px-3 py-2 text-xs text-ink-red-4">
												<p v-for="(item, j) in row.explained" :key="j">{{ item.label }}: {{ item.amount }}</p>
												<p>Unexplained: {{ row.unexplained }}</p>
											</td>
										</tr>
									</template>
								</tbody>
							</table>
						</div>
					</section>
				</template>
			</template>
		</LoadState>

		<NumbersDrill
			v-if="selectedHeading"
			:heading="selectedHeading"
			:period="numbers.payload.period"
			:consolidation-group="numbers.payload.consolidation_group"
			@close="selectedHeading = null"
		/>
	</div>
</template>
