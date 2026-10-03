<script setup>
/**
 * konsol#305 A17: the Adjustments screen, read-only (E6; stories 6.1, 6.2;
 * W2-10 Viewer reads). A18 adds the draft editor and the send controls on
 * this same file; this row has no write control of any kind.
 *
 * Data:
 * - A05 GET `journal_api.get_journals(fiscal_year, fiscal_period)`, turned
 *   into `{period, journals, groups, accounts, workflowInstalled,
 *   firstState, canDraft, canSend, canEditPeriod}` by adjustments.js's
 *   `journalsView` (A13). The screen never decides a duration label, a
 *   status or a rejection's time itself: those come from the server through
 *   journalsView, which throws when asked for a time zone or `now` it was
 *   not given (shown through LoadState, never rendered as current).
 * - Selecting a journal opens a side panel: its lines, its effect per
 *   statement heading through `effectView` ("no heading" text comes from
 *   effectView itself, never a literal in this template), and the last
 *   rejection reason on a Draft.
 *
 * Who sees what (W2-10, #305-P21-1): the GET is open to every close role
 * (`JOURNAL_ROLES`, journal_api.py) except the Entity Accountant, who has no
 * journal permission; journals are not entity-scoped, so a Viewer reads
 * every journal.
 *
 * The period comes from the URL (route.js, D5); nothing is kept in the
 * browser. Not built here, and not rendered as dead controls: the Partner
 * column, Evidence/attach, and "Still in force from earlier periods"
 * (engineering call) — all P2 or belong to a later row.
 */
import { computed, reactive, ref, onBeforeUnmount, watch } from "vue";
import { useRoute } from "vue-router";
import { Button, FeatherIcon } from "frappe-ui";
import LoadState from "../components/LoadState.vue";
import { get } from "../api.js";
import { parse } from "../route.js";
import { journalsView, effectView } from "../adjustments.js";
import { messageLines } from "../signoff.js";
import { userTimeZone } from "../timefmt.js";

const GET_JOURNALS = "konsol.close.journal_api.get_journals";

//: E6-P15: the sentence send_for_approval (A07) refuses with when no
//: journal workflow is installed. Shown once, here, as a note — never
//: repeated per journal.
const NO_WORKFLOW_NOTE =
	"The journal workflow is not installed on this site (migrate installs it); the Close Lead approves a draft directly from Approvals.";
const NO_ZONE = "Your browser reported no time zone, so times cannot be shown.";

const route = useRoute();
const period = computed(() => {
	const p = parse(`/close${route.path}`);
	return p.error || p.year == null ? null : { year: p.year, period: p.period };
});
const periodName = computed(() =>
	period.value ? `FY${period.value.year} P${String(period.value.period).padStart(2, "0")}` : "this period",
);
const what = computed(() => `the adjustments for ${periodName.value}`);
const timeZone = userTimeZone();

const journals = reactive({ status: "loading", payload: null, error: null, busy: false });
let seq = 0;

const selectedName = ref(null);

// journalsView throws when it is not given a time zone or a valid `now`
// (mirrors Rates.vue's/Intercompany.vue's `view`, never swallowed).
const viewError = ref(null);
const view = computed(() => {
	if (journals.status !== "ready" || !journals.payload) return null;
	if (!timeZone) {
		viewError.value = NO_ZONE;
		return null;
	}
	try {
		viewError.value = null;
		return journalsView(journals.payload, new Date(), timeZone);
	} catch (e) {
		viewError.value = e.message;
		return null;
	}
});
const loadState = computed(() => {
	if (journals.status !== "ready") return journals.status;
	return view.value ? "ready" : "error";
});
const loadError = computed(() => viewError.value || journals.error);

const journalCountText = computed(() => {
	const n = view.value ? view.value.journals.length : 0;
	return `${n} this period`;
});

const selectedJournal = computed(() => {
	if (!selectedName.value || !view.value) return null;
	return view.value.journals.find((j) => j.name === selectedName.value) || null;
});
const selectedEffect = computed(() => (selectedJournal.value ? effectView(selectedJournal.value.effect) : null));

function selectJournal(journal) {
	selectedName.value = journal.name;
}
function closePanel() {
	selectedName.value = null;
}

async function loadJournals({ quiet = false } = {}) {
	if (!period.value) {
		journals.status = "error";
		journals.error = "This address names no period.";
		return;
	}
	const mine = ++seq;
	if (!quiet) journals.status = "loading";
	journals.busy = true;
	try {
		const payload = await get(GET_JOURNALS, {
			fiscal_year: period.value.year,
			fiscal_period: period.value.period,
		});
		if (mine !== seq) return;
		journals.payload = payload;
		journals.error = null;
		journals.status = "ready";
	} catch (e) {
		if (mine !== seq) return;
		journals.error = e.message;
		journals.status = "error";
	} finally {
		if (mine === seq) journals.busy = false;
	}
}

watch(
	() => (period.value ? `${period.value.year}/${period.value.period}` : null),
	() => {
		journals.payload = null;
		closePanel();
		loadJournals();
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
</script>

<template>
	<div class="mx-auto max-w-6xl px-6 py-6">
		<header>
			<h1 class="text-xl font-semibold text-ink-gray-9">Adjustments</h1>
			<p class="mt-1 text-sm text-ink-gray-6">{{ periodName }} · {{ journalCountText }}</p>
		</header>

		<p
			v-if="view && !view.workflowInstalled"
			role="status"
			class="mt-3 rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-sm text-ink-gray-7"
		>
			{{ NO_WORKFLOW_NOTE }}
		</p>

		<LoadState class="mt-4" :state="loadState" :what="what" :source="GET_JOURNALS" :error="loadError" :busy="journals.busy" @retry="loadJournals">
			<template v-if="view">
				<p
					v-if="!view.journals.length"
					class="rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-sm text-ink-gray-7"
				>
					No journals for {{ periodName }}.
				</p>
				<div v-else class="overflow-x-auto rounded border border-outline-gray-2">
					<table class="w-full text-left text-sm">
						<thead class="bg-surface-gray-1 text-xs uppercase tracking-wide text-ink-gray-6">
							<tr>
								<th class="px-3 py-2 font-medium">Title</th>
								<th class="px-3 py-2 font-medium">Type</th>
								<th class="px-3 py-2 font-medium">Duration</th>
								<th class="px-3 py-2 font-medium">Status</th>
								<th class="px-3 py-2 font-medium">Totals</th>
								<th class="px-3 py-2 font-medium">Prepared by</th>
							</tr>
						</thead>
						<tbody>
							<tr
								v-for="journal in view.journals"
								:key="journal.name"
								class="cursor-pointer border-t border-outline-gray-2 hover:bg-surface-gray-1"
								:class="selectedName === journal.name ? 'bg-surface-gray-2' : ''"
								tabindex="0"
								role="button"
								:aria-label="`${journal.title}, ${journal.status}`"
								@click="selectJournal(journal)"
								@keyup.enter="selectJournal(journal)"
							>
								<td class="px-3 py-2 font-medium text-ink-gray-9">{{ journal.title }}</td>
								<td class="px-3 py-2 capitalize text-ink-gray-7">{{ journal.adjustment_type }}</td>
								<td class="px-3 py-2 text-ink-gray-7">{{ journal.duration }}</td>
								<td class="px-3 py-2">
									<span class="inline-block rounded bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7">{{ journal.status }}</span>
								</td>
								<td class="px-3 py-2 font-mono text-ink-gray-8">{{ journal.total_debit }} / {{ journal.total_credit }}</td>
								<td class="px-3 py-2 text-ink-gray-7">{{ journal.preparer }}</td>
							</tr>
						</tbody>
					</table>
				</div>
			</template>
		</LoadState>

		<!-- Side panel: the selected journal's lines, effect and last rejection -->
		<div
			v-if="selectedJournal"
			role="dialog"
			aria-label="Journal detail"
			class="fixed inset-y-0 right-0 z-10 w-full max-w-md overflow-y-auto border-l border-outline-gray-2 bg-surface-white p-5 shadow-lg"
		>
			<div class="flex items-start justify-between gap-2">
				<h2 class="text-base font-semibold text-ink-gray-9">{{ selectedJournal.title }}</h2>
				<Button variant="ghost" size="sm" aria-label="Close" @click="closePanel">
					<FeatherIcon name="x" class="h-4 w-4" />
				</Button>
			</div>
			<p class="mt-1 text-sm text-ink-gray-6">{{ selectedJournal.status }} · {{ selectedJournal.duration }}</p>

			<div
				v-if="selectedJournal.docstatus === 0 && selectedJournal.lastRejection"
				class="mt-3 rounded border border-outline-amber-1 bg-surface-amber-1 px-3 py-2 text-sm text-ink-amber-3"
			>
				<p class="text-xs uppercase tracking-wide">Last rejection</p>
				<p v-for="(line, i) in lines(selectedJournal.lastRejection.reason)" :key="i">{{ line }}</p>
				<p class="mt-1 text-xs">{{ selectedJournal.lastRejection.actor }} · {{ selectedJournal.lastRejection.at }}</p>
			</div>

			<section class="mt-4">
				<h3 class="mb-2 text-sm font-semibold text-ink-gray-9">Lines</h3>
				<div v-if="!selectedJournal.lines.length" class="text-sm text-ink-gray-6">No lines.</div>
				<table v-else class="w-full text-left text-sm">
					<thead class="text-xs uppercase tracking-wide text-ink-gray-6">
						<tr>
							<th class="py-1 font-medium">Entity</th>
							<th class="py-1 font-medium">Account</th>
							<th class="py-1 font-medium">Dr</th>
							<th class="py-1 font-medium">Cr</th>
						</tr>
					</thead>
					<tbody>
						<tr v-for="line in selectedJournal.lines" :key="line.idx" class="border-t border-outline-gray-2">
							<td class="py-1 text-ink-gray-8">{{ line.data_area_id }}</td>
							<td class="py-1 text-ink-gray-8">{{ line.account_name || line.main_account }}</td>
							<td class="py-1 font-mono text-ink-gray-8">{{ line.debit_amount || "" }}</td>
							<td class="py-1 font-mono text-ink-gray-8">{{ line.credit_amount || "" }}</td>
						</tr>
					</tbody>
				</table>
			</section>

			<section v-if="selectedEffect" class="mt-4">
				<h3 class="mb-2 text-sm font-semibold text-ink-gray-9">Effect</h3>
				<div v-if="!selectedEffect.headings.length" class="text-sm text-ink-gray-6">No effect.</div>
				<ul v-else class="space-y-1 text-sm">
					<li v-for="(heading, i) in selectedEffect.headings" :key="i" class="flex items-center justify-between gap-2">
						<span class="text-ink-gray-7">{{ heading.label }}</span>
						<span class="font-mono text-ink-gray-8">{{ heading.amountText }}</span>
					</li>
				</ul>
				<p v-if="selectedEffect.noHeading" class="mt-1 text-xs text-ink-gray-5">{{ selectedEffect.noHeading }} account(s) outside any heading.</p>
			</section>
		</div>
	</div>
</template>
