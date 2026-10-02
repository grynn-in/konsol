<script setup>
/**
 * konsol#305 E410: the Rates & ownership screen's "Historical equity rates"
 * tab — every Historical Equity Rate and Ownership Period draft awaiting
 * approval (E4-P8, E4-P12; story 4.3; R2, R5).
 *
 * Presentational only: it takes `pendingView(payload)` (rates.js, E407) as
 * its `view` prop and never calls api.js. Approving an item emits
 * `approve(doctype, name, reason)`; Rates.vue (E409) sends it through its
 * single APPROVE call site, the same function the group-rates grid uses,
 * so a second call site never appears.
 *
 * Each item's control follows the same rule as the grid: `button` and
 * `reason` render an Approve control (a `reason` kind opens an inline input
 * first); `refused` shows the server's own sentence as text; `none` says
 * the Close Lead approves (R2). None of this is re-decided here — the mode
 * came from the server through `approveAction` (rates.js), already applied
 * by `pendingView`.
 */
import { computed, reactive } from "vue";
import { Button } from "frappe-ui";
import { messageLines } from "../signoff.js";
import { pendingEmptyMessage } from "../rates.js";

const props = defineProps({
	/** `pendingView(payload)` (rates.js): `{items, counts, selfApproval, canApprove}`, or null while loading. */
	view: { type: Object, default: null },
	/** Request-time refusals, keyed by document name (Rates.vue's shared approveErrors). */
	errors: { type: Object, default: () => ({}) },
	/** The name currently posting an approve, or null. */
	approving: { type: String, default: null },
});
const emit = defineEmits(["approve"]);

/** #305-R01p: the empty-state text (pure helper, rates.js) -- "none
 * awaiting" only when nothing is hidden; a scoped user with
 * `view.counts.hidden > 0` sees the outside-scope count instead, never
 * false reassurance that nothing is pending. */
const emptyMessage = computed(() => pendingEmptyMessage(props.view || { items: [], counts: { hidden: 0 } }));

/** A `reason` kind opens its input first; a `button` kind posts at once. */
const reasonOpen = reactive({});
const reasonText = reactive({});

function canApprove(item) {
	return item.approve.kind === "button" || item.approve.kind === "reason";
}

function kindLabel(item) {
	return item.doctype === "Ownership Period" ? "Ownership period" : "Historical equity rate";
}

function start(item) {
	if (item.approve.kind === "reason" && !reasonOpen[item.name]) {
		reasonOpen[item.name] = true;
		return;
	}
	emit("approve", item.doctype, item.name, item.approve.kind === "reason" ? reasonText[item.name] : null);
}

function lines(text) {
	const out = messageLines(text);
	return out.length ? out : ["The server gave no reason."];
}
</script>

<template>
	<div>
		<p
			v-if="!view || !view.items.length"
			class="rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-sm text-ink-gray-7"
		>
			{{ emptyMessage }}
		</p>
		<ul v-else class="divide-y divide-outline-gray-2 rounded border border-outline-gray-2">
			<li v-for="item in view.items" :key="item.name" class="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
				<div class="min-w-0">
					<span class="inline-block rounded bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7">
						{{ kindLabel(item) }}
					</span>
					<p class="mt-1 text-sm font-medium text-ink-gray-9">{{ item.title }}</p>
					<p class="text-sm text-ink-gray-7">{{ item.detail }}</p>
					<p class="mt-1 text-xs text-ink-gray-5">
						Prepared by {{ item.preparer }}
						<template v-if="item.edited_by && item.edited_by.length"> · edited by {{ item.edited_by.join(", ") }}</template>
						· {{ item.created }}
					</p>
				</div>
				<div class="flex min-w-[10rem] flex-col items-end gap-1">
					<template v-if="canApprove(item)">
						<template v-if="item.approve.kind === 'reason' && reasonOpen[item.name]">
							<label :for="`pending-reason-${item.name}`" class="text-xs text-ink-gray-6">
								{{ item.approve.message || "Reason for approving your own draft" }}
							</label>
							<input
								:id="`pending-reason-${item.name}`"
								v-model="reasonText[item.name]"
								type="text"
								class="w-48 rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-ink-gray-8"
							/>
						</template>
						<Button
							size="sm"
							:loading="approving === item.name"
							:disabled="approving !== null"
							@click="start(item)"
						>
							Approve
						</Button>
					</template>
					<p v-else-if="item.approve.kind === 'refused'" class="max-w-xs text-right text-xs text-ink-gray-6">
						{{ item.approve.message }}
					</p>
					<p v-else class="text-xs text-ink-gray-6">The Close Lead approves (R2)</p>
				</div>
				<div
					v-if="errors[item.name]"
					role="alert"
					class="w-full rounded border border-outline-red-1 bg-surface-red-1 px-3 py-2 text-sm text-ink-gray-8"
				>
					<p v-for="(line, i) in lines(errors[item.name])" :key="i">{{ line }}</p>
				</div>
			</li>
		</ul>
	</div>
</template>
