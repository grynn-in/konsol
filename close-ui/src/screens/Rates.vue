<script setup>
/**
 * konsol#305 E409: the Rates & ownership screen, first tab "Group rates"
 * (E4; stories 4.1, 4.3; R2, R5; 0.4 no Desk for operations; #305-W2-3).
 *
 * Data:
 * - E403 GET `rates_api.get_rates(fiscal_year, fiscal_period)`, turned into
 *   `{rows, unrequired, banner, summary, canEnter, canApprove, thresholdText}`
 *   by rates.js's gridView (E407). The screen never re-decides a status
 *   label, a delta, a move flag or an approve mode: those come from the
 *   server through gridView, and gridView throws on a status or mode it does
 *   not know (shown through LoadState, never rendered as current).
 * - E404 POST `rates_api.save_rate`: "Save rates" posts each edited cell in
 *   turn (E4-P13: one POST per cell, no batch endpoint), each body built by
 *   saveBody. A refused cell keeps its edit, shows the server's sentence as
 *   plain lines, and opens its row's Reason for Change. The others save.
 *   Then get_rates is reloaded once.
 * - P08 POST `approval_api.approve`, through the ONE function
 *   `approve(doctype, name, reason)`. E410 reuses it through an emitted
 *   event, so no second call site appears. The mode is the server's
 *   (approveAction over the loaded payload); approveBody refuses a blank
 *   reason, a `refused` and a `none` before anything is posted. After an
 *   accepted approve the shell's context reloads once, then get_rates.
 *
 * Who sees what (#305-W2-10, R2): "Save rates" and the cell inputs render
 * only when the server says `can_enter` (the Analyst or Close Lead, period
 * Open). An approve control renders only for the kinds `button` and
 * `reason`; `refused` shows the server's sentence and `none` says the Close
 * Lead approves. A Viewer therefore sees no Save and no Approve.
 *
 * "Draft" is the label of a cell with an unsaved edit only (E4-P1); a saved
 * draft is "Awaiting approval" as the server says. Quoted Per for a new
 * cell starts blank: a missing cell carries no previous rate
 * (rates_model._empty_cell), so there is nothing to start it from, and no
 * default is invented.
 *
 * The period comes from the URL (route.js, D5); nothing is kept in the
 * browser. There is no Import from file (#305-W2-3).
 *
 * E410: the "Historical equity rates" tab lists every HER/OP draft awaiting
 * approval (RatesPending.vue), fed by `get_pending` and `pendingView`
 * (rates.js). It is presentational: it emits `approve`, which lands on the
 * same `approve(doctype, name, reason)` as the group-rates grid, so no
 * second APPROVE call site appears. `actionFor` therefore looks a document
 * up in whichever of the two loaded payloads carries it.
 *
 * E411: the "Ownership" tab lists the period's ownership gaps
 * (OwnershipGaps.vue), fed by `get_ownership` and `ownershipView`
 * (rates.js). It is period-keyed, like the grid, so it reloads on period
 * change. It posts nothing: the "Record ownership" link opens the Desk URL
 * the server built (E406), never one this screen constructs.
 *
 * O60 (story 4.2; wireframe-4.2.md section 1): below the gaps, the
 * "Change ownership" form (OwnershipChange.vue) renders only when the
 * server says `can_record`, fed `get_ownership`'s `change` choices (O63).
 * The form owns the preview GET and the save POST; a saved draft reloads
 * the pending list here, where the Close Lead approves it.
 */
import { computed, inject, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { Button, FeatherIcon } from "frappe-ui";
import LoadState from "../components/LoadState.vue";
import RatesPending from "../sections/RatesPending.vue";
import OwnershipGaps from "../sections/OwnershipGaps.vue";
import OwnershipChange from "../sections/OwnershipChange.vue";
import { get, post } from "../api.js";
import { parse } from "../route.js";
import { gridView, saveBody, approveAction, approveBody, pendingView, pendingCount, ownershipView, ownershipGapsCount, mergeDrafts } from "../rates.js";
import { messageLines } from "../signoff.js";
import { CONTEXT_RELOAD } from "../contextRefresh.js";
import { periodName as formatPeriod } from "../periodName.js";

const GET_RATES = "konsol.close.rates_api.get_rates";
const SAVE_RATE = "konsol.close.rates_api.save_rate";
const APPROVE = "konsol.close.approval_api.approve";
const GET_PENDING = "konsol.close.rates_api.get_pending";
const GET_OWNERSHIP = "konsol.close.rates_api.get_ownership";
const GER = "Group Exchange Rate";
const RATE_TYPES = ["Closing", "Average"];

const route = useRoute();
// No default: a screen outside the shell is a wiring bug, and Vue warns about it.
const reloadContext = inject(CONTEXT_RELOAD);
const period = computed(() => {
	const p = parse(`/close${route.path}`);
	return p.error || p.year == null ? null : { year: p.year, period: p.period };
});
const periodName = computed(() =>
	period.value ? formatPeriod(period.value.year, period.value.period) : "this period",
);
const what = computed(() => `the group rates for ${periodName.value}`);

const tab = ref("group");
const rates = reactive({ status: "loading", payload: null, error: null, busy: false });
let seq = 0;
/** E410: the "Historical equity rates" tab's pending HER/OP drafts. Not
 * period-keyed (E4-P12), so it loads once on mount, not on period change. */
const pending = reactive({ status: "loading", payload: null, error: null, busy: false });
let pendingSeq = 0;
/** E411: the "Ownership" tab's gaps for the period in the URL. Period-keyed
 * (like `rates`, unlike `pending`), so it reloads on period change. */
const ownership = reactive({ status: "loading", payload: null, error: null, busy: false });
let ownershipSeq = 0;

/** Unsaved edits, keyed `${from}|${to}|${rateType}`: `{quote, quotedPer, orig}`. */
const drafts = reactive({});
/** Reason for Change per row, keyed `${from}|${to}`. */
const reasons = reactive({});
const reasonOpen = reactive({});
/** Refusals per cell key (save) and per document name (approve). */
const cellErrors = reactive({});
const approveErrors = reactive({});
const approveReasons = reactive({});
const approveReasonOpen = reactive({});
const saving = ref(false);
const approving = ref(null);

function rowKey(row) {
	return `${row.fromCurrency}|${row.toCurrency}`;
}
function cellKey(row, rateType) {
	return `${rowKey(row)}|${rateType}`;
}
function cellOf(row, rateType) {
	return rateType === "Closing" ? row.closing : row.average;
}
function clear(obj) {
	for (const k of Object.keys(obj)) delete obj[k];
}

// gridView throws on an unknown status or mode: shown as the error, never swallowed.
const viewError = ref(null);
const view = computed(() => {
	if (rates.status !== "ready" || !rates.payload) return null;
	try {
		viewError.value = null;
		return gridView(rates.payload);
	} catch (e) {
		viewError.value = e.message;
		return null;
	}
});
const loadState = computed(() => {
	if (rates.status !== "ready") return rates.status;
	return view.value ? "ready" : "error";
});
const loadError = computed(() => viewError.value || rates.error);

// pendingView throws on an unknown approve mode: shown as the error, same rule as `view` above.
const pendingViewError = ref(null);
const pendingViewData = computed(() => {
	if (pending.status !== "ready" || !pending.payload) return null;
	try {
		pendingViewError.value = null;
		return pendingView(pending.payload);
	} catch (e) {
		pendingViewError.value = e.message;
		return null;
	}
});
const pendingLoadState = computed(() => {
	if (pending.status !== "ready") return pending.status;
	return pendingViewData.value ? "ready" : "error";
});
const pendingLoadError = computed(() => pendingViewError.value || pending.error);

// ownershipView never throws (E407: it only reshapes the payload), so this
// needs no error-catching wrapper like `view`/`pendingViewData` above.
const ownershipViewData = computed(() => {
	if (ownership.status !== "ready" || !ownership.payload) return null;
	return ownershipView(ownership.payload);
});
const ownershipLoadState = computed(() => ownership.status);
const ownershipWhat = computed(() => `the ownership gaps for ${periodName.value}`);

function editable(cell) {
	return Boolean(view.value && view.value.canEnter) && (cell.status === "missing" || cell.status === "awaiting_approval");
}

function canApproveNow(cell) {
	return Boolean(cell.approve) && (cell.approve.kind === "button" || cell.approve.kind === "reason");
}

/** Rebuilds the edit buffers from the server (R01n): each cell's entry goes
 * through mergeDrafts, which keeps a dirty or refused edit untouched and
 * otherwise starts a clean draft from the server's current value. Without
 * this, a reload (the one every approve() triggers for the whole grid, not
 * only the cell just approved) silently threw away any other unsaved,
 * un-refused edit. */
function resetDrafts() {
	const keep = new Set(Object.keys(cellErrors));
	const keepRows = new Set([...keep].map((k) => k.split("|").slice(0, 2).join("|")));
	for (const k of Object.keys(reasons)) if (!keepRows.has(k)) delete reasons[k];
	for (const k of Object.keys(reasonOpen)) if (!keepRows.has(k)) delete reasonOpen[k];
	if (!view.value) {
		clear(drafts);
		return;
	}
	const liveKeys = new Set();
	for (const row of [...view.value.rows, ...view.value.unrequired]) {
		for (const rateType of RATE_TYPES) {
			const key = cellKey(row, rateType);
			liveKeys.add(key);
			drafts[key] = mergeDrafts(drafts[key], cellOf(row, rateType), keep.has(key));
		}
	}
	for (const k of Object.keys(drafts)) if (!liveKeys.has(k)) delete drafts[k];
}

function dirty(row, rateType) {
	const d = drafts[cellKey(row, rateType)];
	return Boolean(d) && (d.quote !== d.orig.quote || d.quotedPer !== d.orig.quotedPer);
}

const dirtyCount = computed(() => {
	if (!view.value) return 0;
	let n = 0;
	for (const row of [...view.value.rows, ...view.value.unrequired]) {
		for (const rateType of RATE_TYPES) if (editable(cellOf(row, rateType)) && dirty(row, rateType)) n++;
	}
	return n;
});

async function loadRates({ quiet = false } = {}) {
	if (!period.value) {
		rates.status = "error";
		rates.error = "This address names no period.";
		return;
	}
	const mine = ++seq;
	if (!quiet) rates.status = "loading";
	rates.busy = true;
	try {
		const payload = await get(GET_RATES, {
			fiscal_year: period.value.year,
			fiscal_period: period.value.period,
		});
		if (mine !== seq) return;
		rates.payload = payload;
		rates.error = null;
		rates.status = "ready";
		resetDrafts();
	} catch (e) {
		if (mine !== seq) return;
		rates.error = e.message;
		rates.status = "error";
	} finally {
		if (mine === seq) rates.busy = false;
	}
}

/** E410: the pending HER/OP drafts. Site-wide, not period-keyed, so it takes no args. */
async function loadPending({ quiet = false } = {}) {
	const mine = ++pendingSeq;
	if (!quiet) pending.status = "loading";
	pending.busy = true;
	try {
		const payload = await get(GET_PENDING, {});
		if (mine !== pendingSeq) return;
		pending.payload = payload;
		pending.error = null;
		pending.status = "ready";
	} catch (e) {
		if (mine !== pendingSeq) return;
		pending.error = e.message;
		pending.status = "error";
	} finally {
		if (mine === pendingSeq) pending.busy = false;
	}
}

/** E411: the period's ownership gaps. Period-keyed, like `loadRates`. */
async function loadOwnership({ quiet = false } = {}) {
	if (!period.value) {
		ownership.status = "error";
		ownership.error = "This address names no period.";
		return;
	}
	const mine = ++ownershipSeq;
	if (!quiet) ownership.status = "loading";
	ownership.busy = true;
	try {
		const payload = await get(GET_OWNERSHIP, {
			fiscal_year: period.value.year,
			fiscal_period: period.value.period,
		});
		if (mine !== ownershipSeq) return;
		ownership.payload = payload;
		ownership.error = null;
		ownership.status = "ready";
	} catch (e) {
		if (mine !== ownershipSeq) return;
		ownership.error = e.message;
		ownership.status = "error";
	} finally {
		if (mine === ownershipSeq) ownership.busy = false;
	}
}

async function saveRates() {
	if (!period.value || !view.value || saving.value) return;
	// R01n: captured before any await, so a period change mid-save (the
	// route watch increments seq and clears cellErrors/reasons for the new
	// period) is detectable at every later write, not just assumed absent.
	const mySeq = seq;
	saving.value = true;
	clear(cellErrors);
	const periodKey = { fiscal_year: period.value.year, fiscal_period: period.value.period };
	try {
		for (const row of [...view.value.rows, ...view.value.unrequired]) {
			for (const rateType of RATE_TYPES) {
				if (!editable(cellOf(row, rateType)) || !dirty(row, rateType)) continue;
				const key = cellKey(row, rateType);
				const d = drafts[key];
				const built = saveBody(periodKey, row, rateType, {
					quote: d.quote,
					quotedPer: d.quotedPer,
					changeReason: reasons[rowKey(row)],
				});
				if (built.error) {
					if (mySeq === seq) cellErrors[key] = built.error;
					continue;
				}
				try {
					await post(SAVE_RATE, built.body);
				} catch (e) {
					if (mySeq === seq) {
						cellErrors[key] = e.message;
						reasonOpen[rowKey(row)] = true;
					}
				}
			}
		}
	} finally {
		saving.value = false;
	}
	// A save whose period changed under it reloads nothing: the watch
	// already loaded the new period, and reloading here would only redo
	// that load for a period this save no longer concerns.
	if (mySeq === seq) await loadRates({ quiet: true });
}

/** The server's approve mode for a loaded document, read afresh from the
 * payload that carries it: the grid (GER) or the pending list (HER, OP). */
function actionFor(doctype, name) {
	if (doctype === GER) {
		if (!rates.payload) return null;
		for (const row of [...(rates.payload.rows || []), ...(rates.payload.unrequired || [])]) {
			for (const cell of [row.closing, row.average]) {
				if (cell && cell.name === name && cell.approve) return approveAction(cell.approve);
			}
		}
		return null;
	}
	if (!pending.payload) return null;
	const item = (pending.payload.items || []).find((it) => it.doctype === doctype && it.name === name);
	return item && item.approve ? approveAction(item.approve) : null;
}

async function approve(doctype, name, reason) {
	delete approveErrors[name];
	const action = actionFor(doctype, name);
	if (!action) {
		approveErrors[name] = `${name} is not awaiting approval on this screen; reload it.`;
		return;
	}
	const built = approveBody(doctype, name, action, reason);
	if (built.error) {
		approveErrors[name] = built.error;
		return;
	}
	approving.value = name;
	try {
		await post(APPROVE, built.body);
		reloadContext();
	} catch (e) {
		approveErrors[name] = e.message;
		return;
	} finally {
		approving.value = null;
	}
	delete approveReasons[name];
	delete approveReasonOpen[name];
	await Promise.all([loadRates({ quiet: true }), loadPending({ quiet: true })]);
}

/** A `reason` approve asks for the reason first; a `button` approve posts at once. */
function approveCell(cell) {
	if (cell.approve.kind === "reason" && !approveReasonOpen[cell.name]) {
		approveReasonOpen[cell.name] = true;
		return;
	}
	approve(GER, cell.name, cell.approve.kind === "reason" ? approveReasons[cell.name] : null);
}

watch(
	() => (period.value ? `${period.value.year}/${period.value.period}` : null),
	() => {
		rates.payload = null;
		ownership.payload = null;
		for (const obj of [drafts, reasons, reasonOpen, cellErrors, approveErrors, approveReasons, approveReasonOpen]) clear(obj);
		loadRates();
		loadOwnership();
	},
	{ immediate: true },
);

// Pending HER/OP drafts are not period-keyed (E4-P12): loaded once on mount.
onMounted(() => {
	loadPending();
});

onBeforeUnmount(() => {
	seq++;
	pendingSeq++;
	ownershipSeq++;
});

/** Each group currency the grid names, from the rows' to_currency: never assumed. */
const groupCurrencies = computed(() => {
	if (!view.value) return [];
	return [...new Set([...view.value.rows, ...view.value.unrequired].map((r) => r.toCurrency))].sort();
});
const periodStatus = computed(() => (rates.payload && rates.payload.period && rates.payload.period.status) || null);

function lines(text) {
	const out = messageLines(text);
	return out.length ? out : ["The server gave no reason."];
}

/** An unsaved edit: the only cell labelled "Draft" (E4-P1). */
function isDraft(row, rateType) {
	return editable(cellOf(row, rateType)) && dirty(row, rateType);
}

function statusClass(row, rateType) {
	const cell = cellOf(row, rateType);
	if (isDraft(row, rateType)) return "bg-surface-gray-2 text-ink-gray-7";
	if (cell.status === "approved") return "bg-surface-green-2 text-ink-green-4";
	if (cell.status === "awaiting_approval") return "bg-surface-amber-2 text-ink-amber-4";
	return "bg-surface-red-2 text-ink-red-4";
}

/** Flag and refusal lines shown under a currency's two rows. R01v: a
 * per-cell warning box only for a judged, declared-threshold move
 * (`flagMessage`, from `moveFlagView` in rates.js) — never for an Approved
 * cell, and never the "declare the threshold" sentence again when the
 * threshold is undeclared (that already shows once, in the banner). */
function rowNotes(row) {
	const notes = [];
	for (const rateType of RATE_TYPES) {
		const cell = cellOf(row, rateType);
		if (cell.flagMessage) notes.push({ key: `${rateType}-flag`, tone: "flag", rateType, lines: [cell.flagMessage] });
		const err = cellErrors[cellKey(row, rateType)];
		if (err) notes.push({ key: `${rateType}-save`, tone: "error", rateType, lines: lines(err) });
		if (cell.name && approveErrors[cell.name]) {
			notes.push({ key: `${rateType}-approve`, tone: "error", rateType, lines: lines(approveErrors[cell.name]) });
		}
	}
	return notes;
}

/** E410/#305-R01p: the "Historical equity rates" tab label carries every
 * pending item, HER and OP together (`pendingCount`, rates.js): the tab
 * holds both doctypes' drafts, so "N pending" undercounts if it names HER
 * alone. */
const herPendingCount = computed(() => {
	if (!pendingViewData.value) return null;
	return pendingCount(pendingViewData.value.counts);
});
/** E411/#305-R01v (folded in from R01q's gate): the "Ownership" tab label's
 * gap count, from the pure `ownershipGapsCount` (rates.js) — blocking plus
 * hidden, never blocking alone: a scoped user whose own entities carry no
 * gap must still see that gaps exist outside their scope, not plain
 * "Ownership" read as none. */
const ownershipTabCount = computed(() => ownershipGapsCount(ownershipViewData.value));
const TABS = computed(() => [
	{ key: "group", label: "Group rates" },
	{
		key: "her",
		label: herPendingCount.value != null ? `Historical equity rates · ${herPendingCount.value} pending` : "Historical equity rates",
	},
	{
		key: "ownership",
		label: ownershipTabCount.value ? `Ownership · ${ownershipTabCount.value} gap(s)` : "Ownership",
	},
]);
</script>

<template>
	<div class="mx-auto max-w-6xl px-6 py-6">
		<header class="flex flex-wrap items-start justify-between gap-3">
			<div>
				<h1 class="text-xl font-semibold text-ink-gray-9">Rates &amp; ownership</h1>
				<p class="mt-1 text-sm text-ink-gray-6">
					{{ periodName }}<template v-if="periodStatus"> · {{ periodStatus }}</template>
					<template v-for="ccy in groupCurrencies" :key="ccy"> · group currency {{ ccy }}</template>
				</p>
			</div>
			<Button
				v-if="tab === 'group' && view && view.canEnter"
				theme="gray"
				variant="solid"
				:loading="saving"
				:disabled="saving || dirtyCount === 0"
				@click="saveRates"
			>
				Save rates
			</Button>
		</header>

		<div role="tablist" class="mt-4 flex gap-1 border-b border-outline-gray-2">
			<button
				v-for="t in TABS"
				:key="t.key"
				type="button"
				role="tab"
				:aria-selected="tab === t.key"
				class="-mb-px border-b-2 px-3 py-2 text-sm"
				:class="tab === t.key ? 'border-ink-gray-9 font-semibold text-ink-gray-9' : 'border-transparent text-ink-gray-6 hover:text-ink-gray-8'"
				@click="tab = t.key"
			>
				{{ t.label }}
			</button>
		</div>

		<section v-if="tab === 'group'" role="tabpanel" aria-label="Group rates" class="mt-4">
			<LoadState
				:state="loadState"
				:what="what"
				:source="GET_RATES"
				:error="loadError"
				:busy="rates.busy"
				@retry="loadRates"
			>
				<template v-if="view">
					<div
						v-if="view.banner.length"
						role="alert"
						class="mb-4 rounded border border-outline-amber-1 bg-surface-amber-1 px-4 py-3 text-sm text-ink-amber-3"
					>
						<p v-for="(line, i) in view.banner" :key="i" :class="i ? 'mt-1' : ''">{{ line }}</p>
					</div>

					<p class="mb-3 text-sm text-ink-gray-6">
						<template v-if="view.thresholdText">{{ view.thresholdText }} · </template><template v-if="view.summary">{{ view.summary.missing }} missing · {{ view.summary.awaiting_approval }} awaiting approval
							· {{ view.summary.approved }} approved</template>
					</p>

					<template v-for="(secRows, s) in [view.rows, view.unrequired]" :key="s">
					<section v-if="s === 0 || secRows.length" :class="s ? 'mt-6' : ''">
						<h2 v-if="s === 1" class="mb-2 text-base font-semibold text-ink-gray-9">Rates this period does not need</h2>
						<p
							v-if="!secRows.length"
							class="rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-sm text-ink-gray-7"
						>
							No group rates for {{ periodName }}.
						</p>
						<div v-else class="overflow-x-auto rounded border border-outline-gray-2">
							<table class="w-full text-left text-sm">
								<thead class="bg-surface-gray-1 text-xs uppercase tracking-wide text-ink-gray-6">
									<tr>
										<th class="px-3 py-2 font-medium">Currency</th>
										<th class="px-3 py-2 font-medium">Rate</th>
										<th class="px-3 py-2 font-medium">{{ periodName }}</th>
										<th class="px-3 py-2 font-medium">Previous approved</th>
										<th class="px-3 py-2 font-medium">Δ</th>
										<th class="px-3 py-2 font-medium">Status</th>
										<th class="px-3 py-2 font-medium">Prepared by</th>
										<th class="px-3 py-2 font-medium">Approve</th>
									</tr>
								</thead>
								<tbody v-for="row in secRows" :key="rowKey(row)" class="border-t border-outline-gray-2">
									<tr v-for="rateType in RATE_TYPES" :key="rateType" class="align-top">
										<td class="px-3 py-2">
											<template v-if="rateType === 'Closing'">
												<div class="font-medium text-ink-gray-9">{{ row.fromCurrency }}</div>
												<div class="text-xs text-ink-gray-5">{{ row.toCurrency }} per {{ row.fromCurrency }}</div>
											</template>
										</td>
										<td class="px-3 py-2 text-ink-gray-7">{{ rateType }}</td>
										<td class="px-3 py-2">
											<div v-if="editable(cellOf(row, rateType)) && drafts[cellKey(row, rateType)]" class="flex items-center gap-1">
												<input
													v-model="drafts[cellKey(row, rateType)].quote"
													type="text"
													inputmode="decimal"
													:aria-label="`${rateType} quote ${row.fromCurrency} to ${row.toCurrency}`"
													class="w-28 rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-right font-mono text-ink-gray-8"
												/>
												<span class="text-xs text-ink-gray-5">per</span>
												<select
													v-model="drafts[cellKey(row, rateType)].quotedPer"
													:aria-label="`${rateType} Quoted Per ${row.fromCurrency}`"
													class="rounded border border-outline-gray-2 bg-surface-white px-1 py-1 text-ink-gray-8"
												>
													<option value="" disabled>Quoted Per</option>
													<option v-for="q in view.quotedPerOptions" :key="q" :value="q">{{ q }}</option>
												</select>
											</div>
											<span v-else class="font-mono text-ink-gray-9">
												<template v-if="cellOf(row, rateType).value != null">
													{{ cellOf(row, rateType).value }}
													<span class="text-xs text-ink-gray-5">per {{ cellOf(row, rateType).quotedPer }}</span>
													<div v-if="cellOf(row, rateType).source" class="text-xs text-ink-gray-5">{{ cellOf(row, rateType).source }}</div>
												</template>
												<template v-else>—</template>
											</span>
										</td>
										<td class="px-3 py-2">
											<div v-if="cellOf(row, rateType).previousValue != null" class="font-mono text-ink-gray-8">
												{{ cellOf(row, rateType).previousValue }}
												<span class="text-xs text-ink-gray-5">per {{ cellOf(row, rateType).previousQuotedPer }}</span>
											</div>
											<div class="text-xs text-ink-gray-5">{{ cellOf(row, rateType).previousLabel }}</div>
										</td>
										<td
											class="px-3 py-2 font-mono"
											:class="cellOf(row, rateType).flagTag ? 'font-semibold text-ink-amber-4' : 'text-ink-gray-8'"
										>
											{{ cellOf(row, rateType).deltaText }}<span v-if="cellOf(row, rateType).flagTag"> · {{ cellOf(row, rateType).flagTag }}</span>
										</td>
										<td class="px-3 py-2">
											<span
												class="inline-block rounded px-2 py-0.5 text-xs font-medium"
												:class="statusClass(row, rateType)"
											>{{ isDraft(row, rateType) ? "Draft" : cellOf(row, rateType).statusLabel }}</span>
										</td>
										<td class="px-3 py-2 text-ink-gray-7">
											{{ cellOf(row, rateType).preparer || "—" }}
											<template v-if="cellOf(row, rateType).editedBy && cellOf(row, rateType).editedBy.length"> · edited by {{ cellOf(row, rateType).editedBy.join(", ") }}</template>
											<div v-if="cellOf(row, rateType).changeReason" class="text-xs text-ink-gray-5">Reason: {{ cellOf(row, rateType).changeReason }}</div>
											<div v-if="cellOf(row, rateType).extraDraftsText" class="text-xs text-ink-gray-5">{{ cellOf(row, rateType).extraDraftsText }}</div>
										</td>
										<td class="px-3 py-2">
											<template v-if="cellOf(row, rateType).approve">
												<div v-if="canApproveNow(cellOf(row, rateType))" class="flex flex-col gap-1">
													<template v-if="cellOf(row, rateType).approve.kind === 'reason' && approveReasonOpen[cellOf(row, rateType).name]">
														<label
															:for="`approve-reason-${cellOf(row, rateType).name}`"
															class="text-xs text-ink-gray-6"
														>{{ cellOf(row, rateType).approve.message || "Reason for approving your own rate" }}</label>
														<input
															:id="`approve-reason-${cellOf(row, rateType).name}`"
															v-model="approveReasons[cellOf(row, rateType).name]"
															type="text"
															class="w-48 rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-ink-gray-8"
														/>
													</template>
													<Button
														v-if="canApproveNow(cellOf(row, rateType))"
														size="sm"
														:loading="approving === cellOf(row, rateType).name"
														:disabled="approving !== null"
														@click="approveCell(cellOf(row, rateType))"
													>
														Approve
													</Button>
												</div>
												<p
													v-else-if="cellOf(row, rateType).approve.kind === 'refused'"
													class="max-w-xs text-xs text-ink-gray-6"
												>{{ cellOf(row, rateType).approve.message }}</p>
												<p v-else class="text-xs text-ink-gray-6">The Close Lead approves (R2)</p>
											</template>
										</td>
									</tr>
									<tr v-if="rowNotes(row).length || reasonOpen[rowKey(row)] || (view.canEnter && (dirty(row, 'Closing') || dirty(row, 'Average')))">
										<td></td>
										<td colspan="7" class="px-3 pb-3">
											<div
												v-for="note in rowNotes(row)"
												:key="note.key"
												:role="note.tone === 'error' ? 'alert' : null"
												class="mb-2 flex items-start gap-2 rounded border px-3 py-2 text-sm"
												:class="note.tone === 'error'
													? 'border-outline-red-1 bg-surface-red-1 text-ink-gray-8'
													: 'border-outline-amber-1 bg-surface-amber-1 text-ink-amber-3'"
											>
												<FeatherIcon name="alert-triangle" class="mt-0.5 h-4 w-4 shrink-0" />
												<div class="min-w-0 flex-1">
													<p class="text-xs font-medium uppercase tracking-wide">{{ note.rateType }}</p>
													<p v-for="(line, i) in note.lines" :key="i">{{ line }}</p>
												</div>
											</div>
											<label
												v-if="view.canEnter"
												class="flex flex-col gap-1 text-sm"
											>
												<span class="text-ink-gray-6">Reason for Change ({{ row.fromCurrency }})</span>
												<input
													v-model="reasons[rowKey(row)]"
													type="text"
													class="w-full max-w-xl rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-ink-gray-8"
												/>
											</label>
										</td>
									</tr>
								</tbody>
							</table>
						</div>
					</section>
					</template>
				</template>
			</LoadState>
		</section>

		<section v-else-if="tab === 'her'" role="tabpanel" aria-label="Historical equity rates" class="mt-4">
			<LoadState
				:state="pendingLoadState"
				:what="'the pending historical equity rates and ownership periods'"
				:source="GET_PENDING"
				:error="pendingLoadError"
				:busy="pending.busy"
				@retry="loadPending"
			>
				<RatesPending
					v-if="pendingViewData"
					:view="pendingViewData"
					:errors="approveErrors"
					:approving="approving"
					@approve="approve"
				/>
			</LoadState>
		</section>

		<section v-else role="tabpanel" aria-label="Ownership" class="mt-4">
			<LoadState
				:state="ownershipLoadState"
				:what="ownershipWhat"
				:source="GET_OWNERSHIP"
				:error="ownership.error"
				:busy="ownership.busy"
				@retry="loadOwnership"
			>
				<OwnershipGaps
					v-if="ownershipViewData"
					:view="ownershipViewData"
					:out-of-scope="ownership.payload ? ownership.payload.out_of_scope || [] : []"
				/>
				<OwnershipChange
					v-if="ownershipViewData && ownershipViewData.canRecord"
					:change="ownership.payload.change"
					@saved="loadPending({ quiet: true })"
				/>
			</LoadState>
		</section>
	</div>
</template>
