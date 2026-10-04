<script setup>
/**
 * konsol#305 A19: the Approvals screen (E6; stories 6.2, 6.3, 6.4; R2, R5;
 * D2-8).
 *
 * Data:
 * - A10 GET `approvals_api.get_queue()`. The queue is site-wide, not
 *   period-keyed (E6-P9): the period in the URL is used only for nav, and
 *   this screen reads no period and sends none. The payload is turned into
 *   `{header, items, sentBack, hiddenNote, canApprove, selfApproval}` by
 *   approvals.js's `queueView` (A14). The screen never re-decides a status,
 *   an age or an approve mode: those come from the server through
 *   queueView, which throws when asked for a time zone or `now` it was not
 *   given (shown through LoadState, never rendered as current). A `reason`
 *   item's inline label is `approve.message` alone, the server's own
 *   no-reason-refusal sentence (U12): `queueView` throws if a `reason` item
 *   ever carries none, rather than this screen inventing a label.
 * - P08 POST `approval_api.approve`, through the ONE function `approve`
 *   below; its body comes only from `approveBody` (rates.js, reused through
 *   approvals.js's re-export — the one-call-site rule). A `button` item
 *   posts at once; a `reason` item opens an inline reason first.
 * - J06a / #305-D2-8 POST `approval_api.reject`, through the ONE function
 *   `reject` below; its body comes only from `rejectBody` (approvals.js),
 *   which refuses a blank reason on the client, before anything is sent.
 * - After a successful approve or reject: reload `get_queue`, then call
 *   the injected `CONTEXT_RELOAD` once.
 *
 * Who sees what (R2, R5): the Approve and Reject controls (`canAct`) render
 * only for an `inline` item whose `approve.kind` is `button` or `reason`.
 * A `refused` or `none` (not-approver) item shows the server's own sentence
 * as text instead, never a button a click would only refuse. Business
 * Combination and Business Disposal are never inline (the W3-1..4
 * engineering call): they show a summary and "Open in Desk ↗" only, with no
 * approve control at all. A sent-back item shows its reason, who sent it
 * back and when, with no control at all (E6-P11): the preparer, not the
 * approver, acts next.
 *
 * W44: selecting a journal also loads the statement for that item's own
 * period and group (W41's `fiscal_year`/`fiscal_period`/`consolidation_group`
 * keys, one GET, stale-guarded by its own `detailSeq` — distinct from the
 * queue's own `seq`) and shows Before / Change / After per heading, reusing
 * numbers.js's `statementView`/`beforeAfter` (U41/W42/N54) rather than
 * re-deriving a sign or a balance here. A non-ok payload state, or any
 * thrown contract break, shows its own message text in place of the table
 * — never blank or zero columns. Only a journal item can ever be selected
 * (the `doctype !== JOURNAL` guard below), so Business Combination/
 * Disposal items never fetch a statement.
 *
 * Not built here: evidence attachments, and the wireframe's role-suffix
 * text, unless the payload sends it.
 */
import { computed, inject, onMounted, reactive, ref } from "vue";
import { Button, FeatherIcon } from "frappe-ui";
import LoadState from "../components/LoadState.vue";
import { get, post } from "../api.js";
import { queueView, approveBody, rejectBody } from "../approvals.js";
import { beforeAfter, statementView } from "../numbers.js";
import { messageLines } from "../signoff.js";
import { userTimeZone } from "../timefmt.js";
import { CONTEXT_RELOAD } from "../contextRefresh.js";

const GET_QUEUE = "konsol.close.approvals_api.get_queue";
const APPROVE = "konsol.close.approval_api.approve";
const REJECT = "konsol.close.approval_api.reject";
const GET_STATEMENT = "konsol.close.statement_api.get_statement";
const JOURNAL = "Consolidation Journal";

const NO_ZONE = "Your browser reported no time zone, so times cannot be shown.";
const WHAT = "the approvals queue";

// No default: a screen outside the shell is a wiring bug, and Vue warns about it.
const reloadContext = inject(CONTEXT_RELOAD);
const timeZone = userTimeZone();

const queue = reactive({ status: "loading", payload: null, error: null, busy: false });
let seq = 0;

/** `{doctype, name}` -> a key that never collides across doctypes (A08/A10
 * key by `(doctype, name)`, never by name alone, for the same reason). */
function refKey(item) {
	return `${item.doctype}::${item.name}`;
}

/** Whether `item`'s Approve/Reject controls render at all (R2, R5): only an
 * inline item (never Business Combination/Disposal) whose approve mode is
 * one the caller may actually use. A `refused` or `none` item shows the
 * server's own sentence instead — never a button a click would refuse. */
function canAct(item) {
	return item.inline && (item.approve.kind === "button" || item.approve.kind === "reason");
}

const approveReasonOpen = reactive({});
const approveReasonText = reactive({});
const approveErrors = reactive({});
const rejectOpen = reactive({});
const rejectReasonText = reactive({});
const rejectErrors = reactive({});
const busyKey = ref(null);

const selectedName = ref(null);
const selectedItem = ref(null);

// queueView throws when it is not given a time zone or a valid `now`
// (mirrors Rates.vue's/Adjustments.vue's `view`, never swallowed).
const viewError = ref(null);
const view = computed(() => {
	if (queue.status !== "ready" || !queue.payload) return null;
	if (!timeZone) {
		viewError.value = NO_ZONE;
		return null;
	}
	try {
		viewError.value = null;
		return queueView(queue.payload, new Date(), timeZone);
	} catch (e) {
		viewError.value = e.message;
		return null;
	}
});
const loadState = computed(() => {
	if (queue.status !== "ready") return queue.status;
	return view.value ? "ready" : "error";
});
const loadError = computed(() => viewError.value || queue.error);

async function loadQueue({ quiet = false } = {}) {
	const mine = ++seq;
	if (!quiet) queue.status = "loading";
	queue.busy = true;
	try {
		const payload = await get(GET_QUEUE);
		if (mine !== seq) return;
		queue.payload = payload;
		queue.error = null;
		queue.status = "ready";
	} catch (e) {
		if (mine !== seq) return;
		queue.error = e.message;
		queue.status = "error";
	} finally {
		if (mine === seq) queue.busy = false;
	}
}

onMounted(() => {
	loadQueue();
});

// W44: the journal detail panel's own statement fetch — one GET per opened
// journal, for that item's own period and group (W41's keys), guarded by
// `detailSeq` (distinct from the queue's `seq`) so a fast second selection
// never shows the first journal's statement.
const detail = reactive({ status: "idle", payload: null, error: null });
let detailSeq = 0;

async function loadDetailStatement(item) {
	const mine = ++detailSeq;
	detail.status = "loading";
	detail.payload = null;
	detail.error = null;
	try {
		const payload = await get(GET_STATEMENT, {
			fiscal_year: item.fiscal_year,
			fiscal_period: item.fiscal_period,
			consolidation_group: item.consolidation_group,
		});
		if (mine !== detailSeq) return;
		detail.payload = payload;
		detail.status = "ready";
	} catch (e) {
		if (mine !== detailSeq) return;
		detail.error = e.message;
		detail.status = "error";
	}
}

// W44: `beforeAfter`'s rows for the selected journal's effect, read against
// the statement fetched above. `null` while nothing is selected or the item
// carries no effect (Business Combination/Disposal never reach here — they
// are never selectable). While the fetch is in flight: `{status:
// "loading"}`. On a fetch failure, a non-ok payload state, or any thrown
// statementView/beforeAfter contract break: `{status: "error", message}` —
// the server's or the thrown error's own text, never a blank or zero table.
const detailBeforeAfter = computed(() => {
	if (!selectedItem.value || !selectedItem.value.effect) return null;
	if (detail.status === "loading" || detail.status === "idle") {
		return { status: "loading" };
	}
	if (detail.status === "error") {
		return { status: "error", message: detail.error };
	}
	if (!timeZone) {
		return { status: "error", message: NO_ZONE };
	}
	try {
		const statement = statementView(detail.payload, new Date(), timeZone);
		if (statement.state) {
			return { status: "error", message: statement.state.message };
		}
		return { status: "ready", rows: beforeAfter(selectedItem.value.effect, statement) };
	} catch (e) {
		return { status: "error", message: e.message };
	}
});

function selectJournal(item) {
	if (item.doctype !== JOURNAL) return;
	selectedName.value = item.name;
	selectedItem.value = item;
	loadDetailStatement(item);
}
function closePanel() {
	selectedName.value = null;
	selectedItem.value = null;
	detailSeq++;
	detail.status = "idle";
	detail.payload = null;
	detail.error = null;
}

async function approve(item) {
	const key = refKey(item);
	delete approveErrors[key];
	if (item.approve.kind === "reason" && !approveReasonOpen[key]) {
		approveReasonOpen[key] = true;
		return;
	}
	const reason = item.approve.kind === "reason" ? approveReasonText[key] : null;
	const built = approveBody(item.doctype, item.name, item.approve, reason);
	if (built.error) {
		approveErrors[key] = built.error;
		return;
	}
	busyKey.value = key;
	try {
		await post(APPROVE, built.body);
	} catch (e) {
		approveErrors[key] = e.message;
		return;
	} finally {
		busyKey.value = null;
	}
	delete approveReasonOpen[key];
	delete approveReasonText[key];
	if (selectedName.value === item.name) closePanel();
	await loadQueue({ quiet: true });
	reloadContext();
}

function startReject(item) {
	rejectOpen[refKey(item)] = true;
}
function cancelReject(item) {
	const key = refKey(item);
	delete rejectOpen[key];
	delete rejectReasonText[key];
	delete rejectErrors[key];
}
async function reject(item) {
	const key = refKey(item);
	delete rejectErrors[key];
	const built = rejectBody(item.doctype, item.name, rejectReasonText[key]);
	if (built.error) {
		rejectErrors[key] = built.error;
		return;
	}
	busyKey.value = key;
	try {
		await post(REJECT, built.body);
	} catch (e) {
		rejectErrors[key] = e.message;
		return;
	} finally {
		busyKey.value = null;
	}
	delete rejectOpen[key];
	delete rejectReasonText[key];
	if (selectedName.value === item.name) closePanel();
	await loadQueue({ quiet: true });
	reloadContext();
}

function lines(text) {
	const out = messageLines(text);
	return out.length ? out : ["The server gave no reason."];
}

function balanceText(item) {
	if (item.total_debit == null) return null;
	return `Dr/Cr ${item.total_debit.toFixed(2)}${item.currency ? ` ${item.currency}` : ""}`;
}
</script>

<template>
	<div class="mx-auto max-w-6xl px-6 py-6">
		<header>
			<h1 class="text-xl font-semibold text-ink-gray-9">Approvals</h1>
			<p class="mt-1 text-sm text-ink-gray-6">
				<template v-if="view">{{ view.header }}</template>
			</p>
			<p v-if="view && view.canApprove && view.selfApproval" class="mt-1 text-sm text-ink-gray-6">
				Self-approval policy: {{ view.selfApproval }}
			</p>
		</header>

		<LoadState class="mt-4" :state="loadState" :what="WHAT" :source="GET_QUEUE" :error="loadError" :busy="queue.busy" @retry="loadQueue">
			<template v-if="view">
				<p
					v-if="view.hiddenNote"
					role="status"
					class="mb-3 rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-sm text-ink-gray-7"
				>
					{{ view.hiddenNote }}
				</p>

				<p
					v-if="!view.items.length"
					class="rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-sm text-ink-gray-7"
				>
					No approvals waiting.
				</p>
				<ul v-else class="divide-y divide-outline-gray-2 rounded border border-outline-gray-2">
					<li v-for="item in view.items" :key="refKey(item)" class="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
						<div class="min-w-0">
							<span class="inline-block rounded bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7">{{ item.kind_label }}</span>
							<p class="mt-1 text-sm font-medium text-ink-gray-9">
								<button
									v-if="item.doctype === 'Consolidation Journal'"
									type="button"
									class="text-left underline-offset-2 hover:underline"
									@click="selectJournal(item)"
								>
									{{ item.title }}
								</button>
								<template v-else>{{ item.title }}</template>
							</p>
							<p class="text-sm text-ink-gray-7">{{ item.detail }}</p>
							<p class="mt-1 text-xs text-ink-gray-5">
								Prepared by {{ item.preparer }}
								<template v-if="item.edited_by && item.edited_by.length"> · edited by {{ item.edited_by.join(", ") }}</template>
								· {{ item.createdText }}
							</p>
						</div>
						<div class="flex min-w-[12rem] flex-col items-end gap-1">
							<template v-if="item.deskLink">
								<a :href="item.deskLink" target="_blank" rel="noopener" class="text-sm text-ink-blue-3 hover:underline">Open in Desk ↗</a>
							</template>
							<template v-else-if="canAct(item)">
								<template v-if="item.approve.kind === 'reason' && approveReasonOpen[refKey(item)]">
									<label :for="`approve-reason-${refKey(item)}`" class="text-xs text-ink-gray-6">
										{{ item.approve.message }}
									</label>
									<input
										:id="`approve-reason-${refKey(item)}`"
										v-model="approveReasonText[refKey(item)]"
										type="text"
										class="w-48 rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-ink-gray-8"
									/>
								</template>
								<Button size="sm" :loading="busyKey === refKey(item)" :disabled="busyKey !== null" @click="approve(item)"> Approve </Button>

								<template v-if="!rejectOpen[refKey(item)]">
									<Button variant="outline" size="sm" :disabled="busyKey !== null" @click="startReject(item)">Reject with reason…</Button>
								</template>
								<template v-else>
									<label :for="`reject-reason-${refKey(item)}`" class="text-xs text-ink-gray-6">Reason for rejecting</label>
									<input
										:id="`reject-reason-${refKey(item)}`"
										v-model="rejectReasonText[refKey(item)]"
										type="text"
										required
										class="w-48 rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-ink-gray-8"
									/>
									<div class="flex gap-1">
										<Button variant="outline" theme="red" size="sm" :loading="busyKey === refKey(item)" :disabled="busyKey !== null" @click="reject(item)">
											Submit reject
										</Button>
										<Button variant="ghost" size="sm" :disabled="busyKey !== null" @click="cancelReject(item)">Cancel</Button>
									</div>
								</template>
							</template>
							<p v-else class="max-w-xs text-right text-xs text-ink-gray-6">{{ item.approve.message }}</p>
						</div>
						<div
							v-if="approveErrors[refKey(item)] || rejectErrors[refKey(item)]"
							role="alert"
							class="w-full rounded border border-outline-red-1 bg-surface-red-1 px-3 py-2 text-sm text-ink-gray-8"
						>
							<p v-for="(line, i) in lines(approveErrors[refKey(item)] || rejectErrors[refKey(item)])" :key="i">{{ line }}</p>
						</div>
					</li>
				</ul>

				<section class="mt-6">
					<h2 class="mb-2 text-base font-semibold text-ink-gray-9">Sent back, waiting for the preparer</h2>
					<p
						v-if="!view.sentBack.length"
						class="rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-sm text-ink-gray-7"
					>
						Nothing has been sent back.
					</p>
					<ul v-else class="divide-y divide-outline-gray-2 rounded border border-outline-gray-2">
						<li v-for="item in view.sentBack" :key="refKey(item)" class="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
							<div class="min-w-0">
								<span class="inline-block rounded bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7">{{ item.kind_label }}</span>
								<p class="mt-1 text-sm font-medium text-ink-gray-9">{{ item.title }}</p>
								<p class="text-sm text-ink-gray-7">{{ item.detail }}</p>
								<p class="mt-1 text-xs text-ink-gray-5">Prepared by {{ item.preparer }} · {{ item.createdText }}</p>
								<div class="mt-2 rounded border border-outline-amber-1 bg-surface-amber-1 px-3 py-2 text-sm text-ink-amber-3">
									<p v-for="(line, i) in lines(item.rejection.reason)" :key="i">{{ line }}</p>
									<p class="mt-1 text-xs">{{ item.rejection.actor }} · {{ item.rejection.at }}</p>
								</div>
							</div>
							<a v-if="item.deskLink" :href="item.deskLink" target="_blank" rel="noopener" class="text-sm text-ink-blue-3 hover:underline">
								Open in Desk ↗
							</a>
						</li>
					</ul>
				</section>
			</template>
		</LoadState>

		<!-- Journal detail panel: lines, balance and the effect per heading. -->
		<div
			v-if="selectedItem"
			role="dialog"
			aria-label="Journal detail"
			class="fixed inset-y-0 right-0 z-10 w-full max-w-md overflow-y-auto border-l border-outline-gray-2 bg-surface-white p-5 shadow-lg"
		>
			<div class="flex items-start justify-between gap-2">
				<h2 class="text-base font-semibold text-ink-gray-9">{{ selectedItem.title }}</h2>
				<Button variant="ghost" size="sm" aria-label="Close" @click="closePanel">
					<FeatherIcon name="x" class="h-4 w-4" />
				</Button>
			</div>
			<p v-if="balanceText(selectedItem)" class="mt-1 text-sm font-mono text-ink-gray-7">{{ balanceText(selectedItem) }}</p>

			<section class="mt-4">
				<h3 class="mb-2 text-sm font-semibold text-ink-gray-9">Lines</h3>
				<div v-if="!selectedItem.lines || !selectedItem.lines.length" class="text-sm text-ink-gray-6">No lines.</div>
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
						<tr v-for="line in selectedItem.lines" :key="line.idx" class="border-t border-outline-gray-2">
							<td class="py-1 text-ink-gray-8">{{ line.data_area_id }}</td>
							<td class="py-1 text-ink-gray-8">{{ line.account_name || line.main_account }}</td>
							<td class="py-1 font-mono text-ink-gray-8">{{ line.debit_amount || "" }}</td>
							<td class="py-1 font-mono text-ink-gray-8">{{ line.credit_amount || "" }}</td>
						</tr>
					</tbody>
				</table>
			</section>

			<section v-if="selectedItem.effect" class="mt-4">
				<h3 class="mb-2 text-sm font-semibold text-ink-gray-9">Effect</h3>
				<div v-if="!selectedItem.effect.headings.length" class="text-sm text-ink-gray-6">No effect.</div>
				<ul v-else class="space-y-1 text-sm">
					<li v-for="(heading, i) in selectedItem.effect.headings" :key="i" class="flex items-center justify-between gap-2">
						<span class="text-ink-gray-7">{{ heading.label }}</span>
						<span class="font-mono text-ink-gray-8">{{ heading.amountText }}</span>
					</li>
				</ul>
				<p v-if="selectedItem.effect.noHeading" class="mt-1 text-xs text-ink-gray-5">{{ selectedItem.effect.noHeading }} account(s) outside any heading.</p>
			</section>

			<!-- W44: Before/Change/After per heading, read against the statement
			     for this item's own period and group. -->
			<section v-if="selectedItem.effect" class="mt-4">
				<h3 class="mb-2 text-sm font-semibold text-ink-gray-9">Before / Change / After</h3>
				<p v-if="!detailBeforeAfter || detailBeforeAfter.status === 'loading'" class="text-sm text-ink-gray-6">
					Loading the statement…
				</p>
				<p v-else-if="detailBeforeAfter.status === 'error'" role="alert" class="text-sm text-ink-red-4">
					{{ detailBeforeAfter.message }}
				</p>
				<table v-else class="w-full text-left text-sm">
					<thead class="text-xs uppercase tracking-wide text-ink-gray-6">
						<tr>
							<th class="py-1 font-medium">Heading</th>
							<th class="py-1 font-medium">Before</th>
							<th class="py-1 font-medium">Change</th>
							<th class="py-1 font-medium">After</th>
						</tr>
					</thead>
					<tbody>
						<tr v-for="row in detailBeforeAfter.rows" :key="`${row.section}-${row.heading}`" class="border-t border-outline-gray-2">
							<td class="py-1 text-ink-gray-8">{{ row.headingName }}</td>
							<td class="py-1 font-mono text-ink-gray-8">{{ row.before }}</td>
							<td class="py-1 font-mono text-ink-gray-8">{{ row.change ?? "—" }}</td>
							<td class="py-1 font-mono text-ink-gray-8">{{ row.after ?? "—" }}</td>
						</tr>
					</tbody>
				</table>
			</section>
		</div>
	</div>
</template>
