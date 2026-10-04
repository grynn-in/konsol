<script setup>
/**
 * konsol#305 U45: the drill panel (E8; story 8.2, three clicks or fewer;
 * D2-5). Numbers.vue opens this only for a heading row (`isDrillable`,
 * U41) — never for the net result, an "of which" sub-row, "not in the
 * group chart" or the unmatched residual.
 *
 * Data: GET `statement_api.get_drill(fiscal_year, fiscal_period,
 * consolidation_group, heading)` (N52), turned into `{heading,
 * headingName, section, dimensionsNote, state, rows}` by numbers.js's
 * `drillView` (U42). The panel never re-derives a row's shape itself: it
 * walks `view.rows` as `drillView` built them.
 *
 * A `seq` guard drops a stale response: clicking a second heading before
 * the first drill has loaded never shows the first heading's rows (the
 * fetch is keyed off the `heading` prop, which Numbers.vue changes on
 * every click, including a fast second one before the first resolves).
 *
 * A row's `accounts` (line → entity → account) are collapsed by default
 * and expand in place; the expanded set is local component state —
 * cleared on every new heading, never remembered across one, and never
 * written anywhere a browser keeps data between loads (D5). A row's
 * `link` (set by `drillView`'s own source rule) opens the TB screen or
 * Adjustments for that period through RouterLink — the target screen
 * carries its own period in its own URL; this panel invents no path of
 * its own.
 *
 * A non-ok `state` (not_built/error) shows the server's message and no
 * rows — drillView already returns `rows: []` for that case, so the
 * template's row loop naturally renders nothing beside it.
 */
import { computed, reactive, ref, watch } from "vue";
import { RouterLink } from "vue-router";
import { FeatherIcon } from "frappe-ui";
import LoadState from "./LoadState.vue";
import { get } from "../api.js";
import { drillView } from "../numbers.js";

const GET_DRILL = "konsol.close.statement_api.get_drill";

const props = defineProps({
	/** The clicked heading's account code (N52's `heading`). */
	heading: { type: String, required: true },
	/** `{fiscal_year, fiscal_period, ...}` — the statement payload's own
	 * `period` object is passed through unchanged; only these two keys
	 * are read. */
	period: { type: Object, required: true },
	consolidationGroup: { type: String, required: true },
});
const emit = defineEmits(["close"]);

const drill = reactive({ status: "loading", payload: null, error: null, busy: false });
let seq = 0;

/** Which row indexes are expanded to their accounts. Local to this panel,
 * cleared whenever the heading changes — never kept across a close/open
 * and never written anywhere that survives a reload. */
const expanded = reactive(new Set());

async function load() {
	const mine = ++seq;
	drill.status = "loading";
	drill.busy = true;
	try {
		const payload = await get(GET_DRILL, {
			fiscal_year: props.period.fiscal_year,
			fiscal_period: props.period.fiscal_period,
			consolidation_group: props.consolidationGroup,
			heading: props.heading,
		});
		if (mine !== seq) return; // a later click already superseded this one
		drill.payload = payload;
		drill.error = null;
		drill.status = "ready";
	} catch (e) {
		if (mine !== seq) return;
		drill.error = e.message;
		drill.status = "error";
	} finally {
		if (mine === seq) drill.busy = false;
	}
}

watch(
	() => props.heading,
	() => {
		expanded.clear();
		load();
	},
	{ immediate: true },
);

// drillView throws on anything it does not recognise (mirrors Numbers.vue's
// own `view`, never swallowed).
const viewError = ref(null);
const view = computed(() => {
	if (drill.status !== "ready" || !drill.payload) return null;
	try {
		viewError.value = null;
		return drillView(drill.payload);
	} catch (e) {
		viewError.value = e.message;
		return null;
	}
});
const loadState = computed(() => {
	if (drill.status !== "ready") return drill.status;
	return view.value ? "ready" : "error";
});
const loadError = computed(() => viewError.value || drill.error);
const what = computed(() => `the drill for heading ${props.heading}`);

function toggle(i) {
	if (expanded.has(i)) {
		expanded.delete(i);
	} else {
		expanded.add(i);
	}
}
</script>

<template>
	<div class="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true">
		<div class="absolute inset-0 bg-ink-gray-9/40" @click="emit('close')" />
		<aside
			class="relative z-10 flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-outline-gray-2 bg-surface-white p-5 shadow-xl"
		>
			<div class="flex items-start justify-between gap-2">
				<h2 class="text-base font-semibold text-ink-gray-9">
					{{ view ? view.headingName : `Heading ${heading}` }}
				</h2>
				<button
					type="button"
					class="rounded p-1 text-ink-gray-6 hover:bg-surface-gray-2"
					aria-label="Close drill panel"
					@click="emit('close')"
				>
					<FeatherIcon name="x" class="h-4 w-4" />
				</button>
			</div>

			<LoadState class="mt-4" compact :state="loadState" :what="what" :source="GET_DRILL" :error="loadError" :busy="drill.busy" @retry="load">
				<template v-if="view">
					<div v-if="view.state" class="rounded border border-outline-red-1 bg-surface-red-1 px-3 py-3 text-sm text-ink-red-4">
						{{ view.state.message }}
					</div>

					<template v-else>
						<p v-if="view.dimensionsNote" class="mt-1 text-xs text-ink-gray-5">{{ view.dimensionsNote }}</p>

						<ul class="mt-3 space-y-2">
							<li v-for="(row, i) in view.rows" :key="i" class="rounded border border-outline-gray-2 p-3">
								<div class="flex items-center justify-between gap-2">
									<div class="min-w-0">
										<p class="truncate text-sm font-medium text-ink-gray-9">{{ row.entity || row.label }}</p>
										<p v-if="row.entity" class="text-xs text-ink-gray-5">{{ row.label }}</p>
									</div>
									<span class="font-mono text-sm text-ink-gray-8 whitespace-nowrap">{{ row.amount }}</span>
								</div>

								<button
									v-if="row.accounts.length"
									type="button"
									class="mt-2 text-xs font-medium text-ink-blue-3 hover:underline"
									@click="toggle(i)"
								>
									{{ expanded.has(i) ? "Hide accounts" : `Show accounts (${row.accounts.length})` }}
								</button>
								<ul v-if="expanded.has(i)" class="mt-2 space-y-1 border-t border-outline-gray-2 pt-2">
									<li v-for="account in row.accounts" :key="account.mainAccount" class="flex justify-between gap-2 text-xs text-ink-gray-7">
										<span class="truncate">{{ account.mainAccount }} · {{ account.accountName }}</span>
										<span class="font-mono whitespace-nowrap">{{ account.amount }}</span>
									</li>
								</ul>

								<div v-if="row.journals && row.journals.length" class="mt-2 space-y-1 border-t border-outline-gray-2 pt-2 text-xs text-ink-gray-7">
									<p v-for="journal in row.journals" :key="journal.journalId">
										{{ journal.journalId }} — {{ journal.description }} — {{ journal.amount }}
										(posted {{ journal.postedBy }}, approved {{ journal.approvedBy }})
									</p>
									<p class="text-ink-gray-5">{{ row.journalsBasis }}</p>
								</div>

								<RouterLink
									v-if="row.link"
									:to="row.link"
									class="mt-2 inline-flex items-center gap-1 text-xs font-medium text-ink-blue-3 hover:underline"
								>
									Open source
									<FeatherIcon name="arrow-right" class="h-3 w-3" />
								</RouterLink>
							</li>
						</ul>
					</template>
				</template>
			</LoadState>
		</aside>
	</div>
</template>
