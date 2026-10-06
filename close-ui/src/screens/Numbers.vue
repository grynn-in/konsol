<script setup>
/**
 * konsol#305 U44: the Numbers screen — the consolidated statement,
 * read-only (E8; story 8.1; W4-2/W4-3/W4-4 as rendered by numbers.js). U45
 * adds the drill panel and U46 the commentary editor; neither is built here.
 *
 * Data:
 * - GET `statement_api.get_statement(fiscal_year, fiscal_period,
 *   consolidation_group=None)` (N51), turned into `{header, label, legend,
 *   state, groupChoice, consolidationGroup, notIncluded, gapText, tabs,
 *   comparisonNote}` by numbers.js's `statementView` (U41). The screen never
 *   decides a label, tone, column or amount itself: it walks `tabRows(view,
 *   section)` for each tab's rows (no second derivation off
 *   `view.tabs[i].rows`) and binds `isDrillable` (unused until U45 opens
 *   anything on a click).
 * - `view.state` is a business state the server itself declared —
 *   `choose_group`, `no_chart`, `not_built`, `error` or `setup_gap`
 *   (R41d/R41i, S5: an undeclared statement account or heading side) —
 *   distinct from LoadState's own fetch-level states: the GET can succeed
 *   while the payload still carries a non-ok `view.state`, and then
 *   `view.tabs` is empty (statementView never returns tabs for a non-ok
 *   state), so no table is shown. Every one of these states shows the
 *   server's own `state.message` verbatim (U5) — this screen never
 *   substitutes wording of its own.
 * - `choose_group`: the screen offers `view.groupChoice` in a plain select,
 *   held only as local component state (`chosenGroup`, never browser
 *   storage, never the URL) and re-sent as `consolidation_group` on the
 *   next GET. A period change (route.js, D5) clears it — the group is not
 *   part of the address. U5 "a chosen group can be changed": once a group
 *   resolves (any other state), a second switcher — gated on
 *   `view.groupChoice` alone, so it shows whenever more than one group is
 *   declared — stays available so the choice is never a one-way door; both
 *   selects call the same `chooseGroup`.
 * - `setup_gap`: the configuration is missing (not a warehouse outage), so
 *   the banner also offers the "Open Close Settings" Desk link, same as
 *   `view.gapText` below.
 * - `view.gapText`: the declared-accounts setup gap's sentence (N41/N45),
 *   shown with a "Open Close Settings" Desk link (story 0.4's "Fix in
 *   Desk" pattern, MyWork.vue). Every close role that can open Numbers can
 *   also at least read Close Settings (close_settings.json: EPM Analyst
 *   and EPM User read, EPM Admin and System Manager write); Frappe's own
 *   Desk permissions decide what the visitor can do once there, so the
 *   link is not persona-gated client-side — this payload carries no
 *   persona signal to gate it with.
 * - `view.comparisonNote` (U6): the statement's own note on why the
 *   comparison column has no data (for example no rows yet for the prior
 *   period) — rendered as plain text above the tabs, the one place the
 *   screen explains the "not loaded" cells `comparisonCell` (numbers.js)
 *   already shows instead of guessing a number.
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
import { Button, FeatherIcon } from "frappe-ui";
import LoadState from "../components/LoadState.vue";
import NumbersDrill from "../components/NumbersDrill.vue";
import { download, get } from "../api.js";
import { saveFile } from "../saveFile.js";
import { parse } from "../route.js";
import { statementView, tabRows, isDrillable, canComment } from "../numbers.js";
import { userTimeZone } from "../timefmt.js";

const GET_STATEMENT = "konsol.close.statement_api.get_statement";
const EXPORT_STATEMENT = "konsol.close.statement_api.export_statement";

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

//: `view.state.kind` -> which tone its banner reads as. `choose_group` and
//: `setup_gap` (R41d/R41i, S5) ask for input/configuration, not a failure;
//: the other two are the server's own warehouse-failure states
//: (statement_api.py module docstring).
const STATE_TONE = {
	choose_group: "warn",
	setup_gap: "warn",
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
//: 8.5: the export's own busy flag and refusal sentence (cleared on a
//: period change).
const exporting = reactive({ busy: false, error: null });
let seq = 0;

//: U44 fact: a group choice is local state, never stored (no browser
//: persistence, no URL). Cleared on every period change.
const chosenGroup = ref(null);

//: U45: the heading whose drill panel is open, local state only (never
//: stored, never the URL). Cleared on every period change, same as
//: chosenGroup — the panel belongs to this load, not to the address.
const selectedHeading = ref(null);

//: L42b: whether the "entities not included" chip shows every code or only
//: the first view.notIncluded.shown (numbers.js's NOT_INCLUDED_LIMIT).
//: Local state only (never stored, never the URL); cleared on every period
//: change, same as chosenGroup/selectedHeading.
const notIncludedExpanded = ref(false);
function toggleNotIncluded() {
	notIncludedExpanded.value = !notIncludedExpanded.value;
}

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
		exporting.error = null;
		chosenGroup.value = null;
		selectedHeading.value = null;
		notIncludedExpanded.value = false;
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

//: U46: the drill panel's canComment/commentary props are fed from this
//: screen's own real get_statement payload (U42's canComment; the raw
//: `commentary` map), never invented inside the panel — get_drill carries
//: no can_comment of its own (N52).
const canCommentValue = computed(() => (numbers.payload ? canComment(numbers.payload) : false));
const selectedCommentary = computed(() =>
	numbers.payload && selectedHeading.value ? numbers.payload.commentary[selectedHeading.value] || null : null,
);

//: U46: a successful commentary save reloads the statement once, quietly
//: (no loading flash), so the table's own row.commentary picks it up too.
//: R41h/U3: a STALE refusal (the drill panel's 'stale' event) reloads the
//: same way — the fresh commentary entry it carries back down lets the
//: panel recover the current `modified` token without the user reloading
//: the page themselves.
function onCommentarySaved() {
	load({ quiet: true });
}

//: 8.5 (decision #305-W5-3): "Export to Excel" downloads the statement on
//: screen — this period and the group this payload resolved to — as the
//: server's .xlsx. A refusal shows the server's own sentence; nothing is
//: kept in the browser.
async function exportExcel() {
	if (!view.value || !period.value) return;
	exporting.busy = true;
	exporting.error = null;
	try {
		const { blob, filename } = await download(EXPORT_STATEMENT, {
			fiscal_year: period.value.year,
			fiscal_period: period.value.period,
			consolidation_group: view.value.consolidationGroup,
		});
		saveFile(blob, filename);
	} catch (e) {
		exporting.error = e.message;
	} finally {
		exporting.busy = false;
	}
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

		<!-- U5 "a chosen group can be changed": once a group is resolved
		     (any state other than the initial choose_group ask), a switcher
		     stays available whenever more than one group is declared
		     (view.groupChoice, numbers.js). The choose_group banner below
		     offers its own select while nothing is chosen yet; this one
		     takes over afterwards so the user is never stuck on the first
		     group the server picked. -->
		<div v-if="view && view.groupChoice && (!view.state || view.state.kind !== 'choose_group')" class="mt-3">
			<label class="flex items-center gap-2 text-sm text-ink-gray-7">
				<span>Group</span>
				<select
					:value="chosenGroup || view.consolidationGroup || ''"
					class="w-56 rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-ink-gray-8"
					@change="chooseGroup($event.target.value)"
				>
					<option v-for="g in view.groupChoice" :key="g" :value="g">{{ g }}</option>
				</select>
			</label>
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
					<div v-if="view.state.kind === 'setup_gap'" class="mt-3">
						<a
							href="/app/close-settings"
							target="_blank"
							rel="noopener noreferrer"
							class="inline-flex items-center gap-1.5 rounded border border-outline-gray-2 bg-surface-white px-3 py-1.5 text-sm text-ink-gray-8 hover:bg-surface-gray-2"
							title="Opens Close Settings in the Desk, in a new tab"
						>
							Open Close Settings
							<FeatherIcon name="external-link" class="h-3.5 w-3.5" />
						</a>
					</div>
				</div>

				<template v-else>
					<div class="mb-3 flex flex-wrap items-center justify-end gap-3">
						<span v-if="exporting.error" role="alert" class="text-sm text-ink-red-4">{{ exporting.error }}</span>
						<Button :loading="exporting.busy" @click="exportExcel">
							<template #prefix><FeatherIcon name="download" class="h-4 w-4" /></template>
							Export to Excel
						</Button>
					</div>
					<div v-if="view.notIncluded" class="mb-3 text-xs text-ink-gray-5">
						<p>
							{{ view.notIncluded.text }}:
							{{ (notIncludedExpanded ? view.notIncluded.codes : view.notIncluded.shown).join(", ") }}
							<span v-if="view.notIncluded.scopeText">, {{ view.notIncluded.scopeText }}</span>
						</p>
						<div v-if="view.notIncluded.moreText" class="mt-1 flex items-center gap-3">
							<span v-if="!notIncludedExpanded">{{ view.notIncluded.moreText }}</span>
							<Button variant="ghost" @click="toggleNotIncluded">
								{{ notIncludedExpanded ? "Show fewer" : `Show all ${view.notIncluded.codes.length}` }}
							</Button>
						</div>
					</div>
					<p v-if="view.comparisonNote" class="mb-3 text-xs text-ink-gray-5">{{ view.comparisonNote }}</p>

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
			:can-comment="canCommentValue"
			:commentary="selectedCommentary"
			@close="selectedHeading = null"
			@saved="onCommentarySaved"
			@stale="onCommentarySaved"
		/>
	</div>
</template>
