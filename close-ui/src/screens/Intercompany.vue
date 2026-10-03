<script setup>
/**
 * konsol#305 C14: the Intercompany screen (E5; stories 5.1, 5.2, 5.3;
 * #305-W3-1, W3-2; 0.4 no Desk for operations).
 *
 * Data:
 * - C03 GET `ic_api.get_ic(fiscal_year, fiscal_period)`, turned into
 *   `{banner, chips, groups, unmatched, hiddenNote}` by intercompany.js's
 *   `intercompanyView` (C12). The screen never decides a label, status,
 *   amount or mask itself: those come from the server through
 *   `intercompanyView`, which throws on a state or match_status it does not
 *   know (shown through LoadState, never rendered as current).
 * - `chips` and `groups`/`unmatched` are only populated by the server when
 *   `state === "checked"` (intercompanyView). The tiles and the pairs table
 *   share one guard on that fact, so `not_configured`, `not_applicable`
 *   (#305-W3-7), `not_built` and `error` all show the banner alone — no new
 *   branch per state.
 * - Selecting a pair opens a side panel, built by the pure `panel(pair)`
 *   helper: both sides (or the hidden label), the difference, the
 *   difference-account sentence, and the pair's own send-back trail entry
 *   (replies and Remind are P2, not built).
 * - C04 POST `ic_api.send_back`, through the ONE function
 *   `sendBack(pair, reason)`, reached only from the panel. `sendBackBody`
 *   (C12) refuses a blank reason client-side before anything is posted. The
 *   button renders only when the server says `can_send_back` (the role and
 *   the period are right) AND the pair's own `can_send_back` (over
 *   tolerance, and the group's tolerance is declared). A refusal shows
 *   `messageLines(error)` and keeps the typed reason; success reloads
 *   `get_ic` once and asks the shell to reload its context.
 *
 * The period comes from the URL (route.js, D5): nothing is kept in the
 * browser, and there is no "last viewed" memory. Not built here (P2 / no
 * story): Remind, replies in the trail, evidence.
 */
import { computed, inject, onBeforeUnmount, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { Button, FeatherIcon } from "frappe-ui";
import LoadState from "../components/LoadState.vue";
import { get, post } from "../api.js";
import { parse } from "../route.js";
import { intercompanyView, panel, sendBackBody } from "../intercompany.js";
import { messageLines } from "../signoff.js";
import { userTimeZone } from "../timefmt.js";
import { CONTEXT_RELOAD } from "../contextRefresh.js";

const GET_IC = "konsol.close.ic_api.get_ic";
const SEND_BACK = "konsol.close.ic_api.send_back";

const NO_ZONE = "Your browser reported no time zone, so times cannot be shown.";

const route = useRoute();
// No default: a screen outside the shell is a wiring bug, and Vue warns about it.
const reloadContext = inject(CONTEXT_RELOAD);
const period = computed(() => {
	const p = parse(`/close${route.path}`);
	return p.error || p.year == null ? null : { year: p.year, period: p.period };
});
const periodName = computed(() =>
	period.value ? `FY${period.value.year} P${String(period.value.period).padStart(2, "0")}` : "this period",
);
const what = computed(() => `the intercompany reconciliation for ${periodName.value}`);
const timeZone = userTimeZone();

const ic = reactive({ status: "loading", payload: null, error: null, busy: false, now: null });
let seq = 0;

const selectedKey = ref(null);
const sendBackReason = ref("");
const sendBackError = ref(null);
const sendBackBusy = ref(false);

function pairId(pair) {
	return [pair.consolidation_group, pair.entity_a, pair.account_a, pair.entity_b, pair.account_b].join("|");
}

// intercompanyView throws on an unknown state or match_status: shown as the
// error, never swallowed (mirrors Rates.vue's `view`).
const viewError = ref(null);
const view = computed(() => {
	if (ic.status !== "ready" || !ic.payload) return null;
	if (!timeZone) {
		viewError.value = NO_ZONE;
		return null;
	}
	try {
		viewError.value = null;
		return intercompanyView(ic.payload, ic.now || new Date(), timeZone);
	} catch (e) {
		viewError.value = e.message;
		return null;
	}
});
const loadState = computed(() => {
	if (ic.status !== "ready") return ic.status;
	return view.value ? "ready" : "error";
});
const loadError = computed(() => viewError.value || ic.error);

/** The server's top-level permission flag (role + period Open); combined
 * in the template with the selected pair's own `can_send_back`. */
const canSendBack = computed(() => Boolean(ic.payload && ic.payload.can_send_back));

const selectedPair = computed(() => {
	if (!selectedKey.value || !view.value) return null;
	for (const group of view.value.groups) {
		for (const pair of group.pairs) {
			if (pairId(pair) === selectedKey.value) return pair;
		}
	}
	return null;
});
const selectedPanel = computed(() => (selectedPair.value ? panel(selectedPair.value) : null));

function selectPair(pair) {
	selectedKey.value = pairId(pair);
	sendBackReason.value = "";
	sendBackError.value = null;
}
function closePanel() {
	selectedKey.value = null;
	sendBackReason.value = "";
	sendBackError.value = null;
}

async function loadIc({ quiet = false } = {}) {
	if (!period.value) {
		ic.status = "error";
		ic.error = "This address names no period.";
		return;
	}
	const mine = ++seq;
	if (!quiet) ic.status = "loading";
	ic.busy = true;
	try {
		const payload = await get(GET_IC, {
			fiscal_year: period.value.year,
			fiscal_period: period.value.period,
		});
		if (mine !== seq) return;
		ic.payload = payload;
		ic.error = null;
		ic.now = new Date();
		ic.status = "ready";
	} catch (e) {
		if (mine !== seq) return;
		ic.error = e.message;
		ic.status = "error";
	} finally {
		if (mine === seq) ic.busy = false;
	}
}

/** The one send-back call site, reached only from the panel. */
async function sendBack(pair, reason) {
	sendBackError.value = null;
	if (!period.value) return;
	const built = sendBackBody({ fiscal_year: period.value.year, fiscal_period: period.value.period }, pair, reason);
	if (built.error) {
		sendBackError.value = built.error;
		return;
	}
	sendBackBusy.value = true;
	try {
		await post(SEND_BACK, built.body);
	} catch (e) {
		sendBackError.value = e.message;
		return;
	} finally {
		sendBackBusy.value = false;
	}
	sendBackReason.value = "";
	await loadIc({ quiet: true });
	reloadContext();
}

watch(
	() => (period.value ? `${period.value.year}/${period.value.period}` : null),
	() => {
		ic.payload = null;
		closePanel();
		loadIc();
	},
	{ immediate: true },
);

onBeforeUnmount(() => {
	seq++;
});

function lines(text) {
	const out = messageLines(text);
	return out.length ? out : ["The server gave no reason."];
}

/** The header subtitle: the period, then each group's reporting currency
 * and tolerance text, taken from the server's own groups (never assumed —
 * the wireframe's "compared in GBP" is one such group, not a default). */
const subtitleGroups = computed(() => (view.value ? view.value.groups : []));
</script>

<template>
	<div class="mx-auto max-w-6xl px-6 py-6">
		<header>
			<h1 class="text-xl font-semibold text-ink-gray-9">Intercompany</h1>
			<p class="mt-1 text-sm text-ink-gray-6">
				{{ periodName }}
				<template v-for="g in subtitleGroups" :key="g.consolidationGroup">
					· {{ g.consolidationGroup }} compared in {{ g.reportingCurrency || "an undeclared currency" }} · {{ g.toleranceText }}
				</template>
			</p>
		</header>

		<LoadState class="mt-4" :state="loadState" :what="what" :source="GET_IC" :error="loadError" :busy="ic.busy" @retry="loadIc">
			<template v-if="view">
				<div
					v-if="view.banner.lines.length"
					role="alert"
					class="mb-4 rounded border px-4 py-3 text-sm"
					:class="view.banner.tone === 'block'
						? 'border-outline-red-1 bg-surface-red-1 text-ink-gray-8'
						: 'border-outline-amber-1 bg-surface-amber-1 text-ink-amber-3'"
				>
					<p v-for="(line, i) in view.banner.lines" :key="i" :class="i ? 'mt-1' : ''">{{ line }}</p>
				</div>

				<template v-if="ic.payload && ic.payload.state === 'checked'">
					<div v-if="view.chips" class="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
						<div class="rounded border border-outline-gray-2 bg-surface-gray-1 px-3 py-2">
							<p class="text-xs uppercase tracking-wide text-ink-gray-6">Pairs</p>
							<p class="text-lg font-semibold text-ink-gray-9">{{ view.chips.pairs }}</p>
						</div>
						<div class="rounded border border-outline-gray-2 bg-surface-green-1 px-3 py-2">
							<p class="text-xs uppercase tracking-wide text-ink-gray-6">Within tolerance</p>
							<p class="text-lg font-semibold text-ink-gray-9">{{ view.chips.within }}</p>
						</div>
						<div class="rounded border border-outline-gray-2 bg-surface-red-1 px-3 py-2">
							<p class="text-xs uppercase tracking-wide text-ink-gray-6">Over tolerance</p>
							<p class="text-lg font-semibold text-ink-gray-9">{{ view.chips.differences }}</p>
						</div>
						<div class="rounded border border-outline-gray-2 bg-surface-amber-1 px-3 py-2">
							<p class="text-xs uppercase tracking-wide text-ink-gray-6">FX difference</p>
							<p class="text-lg font-semibold text-ink-gray-9">{{ view.chips.fx }}</p>
						</div>
						<div class="rounded border border-outline-gray-2 bg-surface-gray-1 px-3 py-2">
							<p class="text-xs uppercase tracking-wide text-ink-gray-6">Partnerless</p>
							<p class="text-lg font-semibold text-ink-gray-9">{{ view.chips.unmatched }}</p>
						</div>
					</div>

					<p v-if="view.hiddenNote" class="mb-3 text-xs text-ink-gray-5">{{ view.hiddenNote }}</p>

					<section v-for="group in view.groups" :key="group.consolidationGroup" class="mb-6">
						<h2 class="mb-2 text-base font-semibold text-ink-gray-9">
							{{ group.consolidationGroup }}
							<span class="text-sm font-normal text-ink-gray-6">· {{ group.toleranceText }}</span>
						</h2>
						<p v-if="!group.pairs.length" class="rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-sm text-ink-gray-7">
							No intercompany pairs for {{ periodName }}.
						</p>
						<div v-else class="overflow-x-auto rounded border border-outline-gray-2">
							<table class="w-full text-left text-sm">
								<thead class="bg-surface-gray-1 text-xs uppercase tracking-wide text-ink-gray-6">
									<tr>
										<th class="px-3 py-2 font-medium">Pair</th>
										<th class="px-3 py-2 font-medium">Accounts</th>
										<th class="px-3 py-2 font-medium">Side A</th>
										<th class="px-3 py-2 font-medium">Side B</th>
										<th class="px-3 py-2 font-medium">Difference</th>
										<th class="px-3 py-2 font-medium">Status</th>
									</tr>
								</thead>
								<tbody>
									<tr
										v-for="pair in group.pairs"
										:key="pairId(pair)"
										class="cursor-pointer border-t border-outline-gray-2 hover:bg-surface-gray-1"
										:class="selectedKey === pairId(pair) ? 'bg-surface-gray-2' : ''"
										tabindex="0"
										role="button"
										:aria-label="`${pair.entity_a} ${pair.account_a} and ${pair.entity_b} ${pair.account_b}`"
										@click="selectPair(pair)"
										@keyup.enter="selectPair(pair)"
									>
										<td class="px-3 py-2 font-medium text-ink-gray-9">{{ pair.entity_a }} ↔ {{ pair.entity_b }}</td>
										<td class="px-3 py-2 text-ink-gray-7">{{ pair.account_a }} / {{ pair.account_b }}</td>
										<td class="px-3 py-2 font-mono text-ink-gray-8">{{ pair.balanceAText }}</td>
										<td class="px-3 py-2 font-mono text-ink-gray-8">{{ pair.balanceBText }}</td>
										<td class="px-3 py-2 font-mono text-ink-gray-8">{{ pair.difference != null ? pair.difference : "—" }}</td>
										<td class="px-3 py-2">
											<span
												class="inline-block rounded px-2 py-0.5 text-xs font-medium"
												:class="pair.statusTone === 'block'
													? 'bg-surface-red-2 text-ink-red-4'
													: pair.statusTone === 'warn'
														? 'bg-surface-amber-2 text-ink-amber-4'
														: 'bg-surface-green-2 text-ink-green-4'"
											>{{ pair.statusText }}</span>
										</td>
									</tr>
								</tbody>
							</table>
						</div>
					</section>

					<section v-if="view.unmatched.length" class="mb-6">
						<h2 class="mb-2 text-base font-semibold text-ink-gray-9">Partnerless rows</h2>
						<div class="overflow-x-auto rounded border border-outline-gray-2">
							<table class="w-full text-left text-sm">
								<thead class="bg-surface-gray-1 text-xs uppercase tracking-wide text-ink-gray-6">
									<tr>
										<th class="px-3 py-2 font-medium">Entity</th>
										<th class="px-3 py-2 font-medium">Account</th>
										<th class="px-3 py-2 font-medium">Amount</th>
										<th class="px-3 py-2 font-medium">Local</th>
										<th class="px-3 py-2 font-medium">Note</th>
									</tr>
								</thead>
								<tbody>
									<tr v-for="(row, i) in view.unmatched" :key="i" class="border-t border-outline-gray-2">
										<td class="px-3 py-2 text-ink-gray-9">{{ row.entity }}</td>
										<td class="px-3 py-2 text-ink-gray-7">{{ row.account }}</td>
										<td class="px-3 py-2 font-mono text-ink-gray-8">{{ row.amount }}</td>
										<td class="px-3 py-2 font-mono text-ink-gray-8">{{ row.local }}</td>
										<td class="px-3 py-2 text-ink-gray-6">{{ row.note }}</td>
									</tr>
								</tbody>
							</table>
						</div>
					</section>
				</template>
			</template>
		</LoadState>

		<!-- Side panel for the selected pair -->
		<div v-if="selectedPair" role="dialog" aria-label="Intercompany pair detail" class="fixed inset-y-0 right-0 z-10 w-full max-w-md overflow-y-auto border-l border-outline-gray-2 bg-surface-white p-5 shadow-lg">
			<div class="flex items-start justify-between gap-2">
				<h2 class="text-base font-semibold text-ink-gray-9">{{ selectedPair.entity_a }} ↔ {{ selectedPair.entity_b }}</h2>
				<Button variant="ghost" size="sm" aria-label="Close" @click="closePanel">
					<FeatherIcon name="x" class="h-4 w-4" />
				</Button>
			</div>
			<p class="mt-1 text-sm text-ink-gray-6">{{ selectedPair.statusText }}</p>

			<div v-if="selectedPanel" class="mt-4 space-y-3 text-sm">
				<div class="rounded border border-outline-gray-2 px-3 py-2">
					<p class="text-xs uppercase tracking-wide text-ink-gray-6">Side A</p>
					<p class="text-ink-gray-9">{{ selectedPanel.sideA.entity }} · {{ selectedPanel.sideA.account }}</p>
					<p class="font-mono text-ink-gray-8">{{ selectedPanel.sideA.amount }}</p>
				</div>
				<div class="rounded border border-outline-gray-2 px-3 py-2">
					<p class="text-xs uppercase tracking-wide text-ink-gray-6">Side B</p>
					<p class="text-ink-gray-9">{{ selectedPanel.sideB.entity }} · {{ selectedPanel.sideB.account }}</p>
					<p class="font-mono text-ink-gray-8">{{ selectedPanel.sideB.amount }}</p>
				</div>
				<div class="rounded border border-outline-gray-2 px-3 py-2">
					<p class="text-xs uppercase tracking-wide text-ink-gray-6">Difference</p>
					<p class="font-mono text-ink-gray-8">{{ selectedPanel.difference }}</p>
					<p class="mt-1 text-xs text-ink-gray-6">{{ selectedPanel.accountSentence }}</p>
				</div>

				<div v-if="selectedPanel.trail" class="rounded border border-outline-gray-2 bg-surface-gray-1 px-3 py-2">
					<p class="text-xs uppercase tracking-wide text-ink-gray-6">Sent back</p>
					<p class="text-ink-gray-8">{{ selectedPanel.trail.by }} · {{ selectedPanel.trail.at }}</p>
					<p class="text-ink-gray-7">{{ selectedPanel.trail.reason }}</p>
				</div>

				<div v-if="canSendBack && selectedPair.can_send_back" class="mt-4 border-t border-outline-gray-2 pt-4">
					<label class="flex flex-col gap-1">
						<span class="text-sm text-ink-gray-6">Reason (required)</span>
						<input
							v-model="sendBackReason"
							type="text"
							required
							class="w-full rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-ink-gray-8"
						/>
					</label>
					<div v-if="sendBackError" role="alert" class="mt-2 rounded border border-outline-red-1 bg-surface-red-1 px-3 py-2 text-sm text-ink-gray-8">
						<p v-for="(line, i) in lines(sendBackError)" :key="i">{{ line }}</p>
					</div>
					<Button
						class="mt-3"
						theme="gray"
						variant="solid"
						:loading="sendBackBusy"
						:disabled="sendBackBusy"
						@click="sendBack(selectedPair, sendBackReason)"
					>
						Send back to both entities
					</Button>
				</div>
			</div>
		</div>
	</div>
</template>
