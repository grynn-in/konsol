<script setup>
/**
 * konsol#305 T08b: the Audit trail screen (E10; story 10.1, board 8).
 *
 * Data:
 * - trail_api.get_trail(fiscal_year, fiscal_period), turned into
 *   {signedOff, result, closedLocked, exceptions, rows, hiddenNote} by
 *   trailView (T08a). The screen never re-decides a label, a tone or the
 *   hidden-entities note: an unknown kind or sign-off state throws there,
 *   and the thrown error is shown through LoadState rather than rendering
 *   silently.
 * - The period comes from the URL (route.js, D5): nothing is remembered in
 *   browser storage.
 * - Read only: no write call, no raw-HTML rendering, no CSV export button
 *   and no filter chips (story 10.2, P2 — E10-P6(f)). Reached as the
 *   `audit-trail` slug through the router's glob (router.js); unreachable
 *   until T08c adds it to route.js and nav.js.
 */
import { computed, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import LoadState from "../components/LoadState.vue";
import { get } from "../api.js";
import { parse } from "../route.js";
import { trailView } from "../auditTrail.js";
import { userTimeZone } from "../timefmt.js";

const GET_TRAIL = "konsol.close.trail_api.get_trail";

// The four tones trailView hands back (ok, warn, block, mute); mirrors
// periodGrid.js's toneClass — an unknown tone throws rather than rendering
// an unstyled chip.
const TONE_CLASS = {
	ok: "bg-surface-green-1 text-ink-green-3",
	warn: "bg-surface-amber-1 text-ink-amber-3",
	block: "bg-surface-red-2 text-ink-red-4",
	mute: "bg-surface-gray-1 text-ink-gray-6",
};
function toneClass(tone) {
	if (!Object.prototype.hasOwnProperty.call(TONE_CLASS, tone)) {
		throw new Error(`Unknown tone: ${tone}`);
	}
	return TONE_CLASS[tone];
}

const route = useRoute();
const period = computed(() => {
	const p = parse(`/close${route.path}`);
	return p.error || p.year == null ? null : { year: p.year, period: p.period };
});
const periodName = computed(() =>
	period.value ? `FY${period.value.year} P${String(period.value.period).padStart(2, "0")}` : "this period",
);
const trailWhat = computed(() => `the audit trail for ${periodName.value}`);
const emptyText = computed(() => `No events recorded for ${periodName.value}.`);

const trail = reactive({ status: "loading", payload: null, error: null, busy: false });
let seq = 0;

async function loadTrail() {
	if (!period.value) {
		trail.status = "error";
		trail.error = "This address names no period.";
		return;
	}
	const mine = ++seq;
	trail.status = "loading";
	trail.busy = true;
	try {
		const payload = await get(GET_TRAIL, {
			fiscal_year: period.value.year,
			fiscal_period: period.value.period,
		});
		if (mine !== seq) return;
		trail.payload = payload;
		trail.error = null;
		trail.status = "ready";
	} catch (e) {
		if (mine !== seq) return;
		trail.error = e.message;
		trail.status = "error";
	} finally {
		if (mine === seq) trail.busy = false;
	}
}

function retryTrail() {
	loadTrail();
}

watch(
	() => (period.value ? `${period.value.year}/${period.value.period}` : null),
	() => {
		trail.payload = null;
		loadTrail();
	},
	{ immediate: true },
);

// trailView throws on an unknown kind or sign-off state (T08a): that throw
// is shown as the screen's error, never swallowed or rendered blank.
const trailViewError = ref(null);
const trailViewResult = computed(() => {
	if (trail.status !== "ready" || !trail.payload) return null;
	try {
		trailViewError.value = null;
		return trailView(trail.payload, new Date(), userTimeZone());
	} catch (e) {
		trailViewError.value = e.message;
		return null;
	}
});

const trailState = computed(() => {
	if (trail.status !== "ready") return trail.status;
	if (!trailViewResult.value) return "error";
	return trailViewResult.value.rows.length === 0 ? "empty" : "ready";
});
const trailError = computed(() => trailViewError.value || trail.error);

const code = computed(() => (trail.payload && trail.payload.period && trail.payload.period.code) || periodName.value);
</script>

<template>
	<div class="mx-auto max-w-5xl px-6 py-6">
		<header class="mb-4">
			<h1 class="text-xl font-semibold text-ink-gray-9">Audit trail · {{ code }}</h1>
			<p class="mt-1 text-sm text-ink-gray-6">
				Read-only · every approval, exception and status change for the period
			</p>
		</header>

		<LoadState
			:state="trailState"
			:what="trailWhat"
			:source="GET_TRAIL"
			:error="trailError"
			:busy="trail.busy"
			:empty-text="emptyText"
			@retry="retryTrail"
		>
			<section v-if="trailViewResult" class="mb-4 overflow-hidden rounded border border-outline-gray-2">
				<div class="grid grid-cols-2 gap-x-6 gap-y-4 px-5 py-4 sm:grid-cols-4">
					<div>
						<p class="text-xs uppercase tracking-wide text-ink-gray-5">Signed off</p>
						<p class="mt-1 text-sm font-medium text-ink-gray-9">{{ trailViewResult.signedOff }}</p>
					</div>
					<div>
						<p class="text-xs uppercase tracking-wide text-ink-gray-5">Result</p>
						<p class="mt-1 text-sm font-medium text-ink-gray-9">{{ trailViewResult.result || "—" }}</p>
					</div>
					<div>
						<p class="text-xs uppercase tracking-wide text-ink-gray-5">Closed · Locked</p>
						<p class="mt-1 text-sm font-medium text-ink-gray-9">{{ trailViewResult.closedLocked }}</p>
					</div>
					<div>
						<p class="text-xs uppercase tracking-wide text-ink-gray-5">Exceptions</p>
						<p class="mt-1 text-sm font-medium text-ink-gray-9">{{ trailViewResult.exceptions }}</p>
					</div>
				</div>
			</section>

			<p
				v-if="trailViewResult && trailViewResult.hiddenNote"
				class="mb-3 rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-sm text-ink-gray-7"
			>{{ trailViewResult.hiddenNote }}</p>

			<div v-if="trailViewResult" class="overflow-hidden rounded border border-outline-gray-2">
				<table class="w-full text-left text-sm">
					<thead class="bg-surface-gray-1 text-xs uppercase tracking-wide text-ink-gray-6">
						<tr>
							<th class="px-4 py-2 font-medium">When</th>
							<th class="px-4 py-2 font-medium">Event</th>
							<th class="px-4 py-2 font-medium">Item</th>
							<th class="px-4 py-2 font-medium">By</th>
							<th class="px-4 py-2 font-medium">Detail</th>
						</tr>
					</thead>
					<tbody>
						<tr v-for="row in trailViewResult.rows" :key="row.name" class="border-t border-outline-gray-1">
							<td class="whitespace-nowrap px-4 py-2 font-mono text-xs text-ink-gray-6">{{ row.time }}</td>
							<td class="px-4 py-2">
								<span
									class="inline-block rounded px-2 py-0.5 text-xs font-medium"
									:class="toneClass(row.tone)"
								>{{ row.label }}</span>
							</td>
							<td class="px-4 py-2 text-ink-gray-8">{{ row.item }}</td>
							<td class="px-4 py-2 text-ink-gray-7">{{ row.by }}</td>
							<td class="px-4 py-2 text-ink-gray-7">{{ row.detail }}</td>
						</tr>
					</tbody>
				</table>
			</div>
		</LoadState>
	</div>
</template>
