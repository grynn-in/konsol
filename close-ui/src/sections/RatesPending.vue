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
 *
 * L01d/L01f: each item's `created` is formatted like the TB list
 * (`pendingCreatedText`, rates.js — B29's one timefmt.js formatter, never a
 * second one here), in the viewer's own zone (`userTimeZone()`, same
 * lookup TrialBalances.vue uses). `rates_api.get_pending`'s `created` now
 * arrives zoned (L01e); `pendingCreatedText` no longer carries the L01d-era
 * naive-time re-zoning helper, deleted in L01f — a `created` with no
 * time zone can now only mean a server regression, so `createdText` below
 * shows the same "no time zone" error `pendingCreatedText`/`parseZoned`
 * throws for it (mirrors how the TB screen surfaces a timestamp it cannot
 * parse, tbTable.js's `timestampText`) rather than silently re-zoning it or
 * inventing different wording.
 *
 * konsol#305 O61 (wireframe-4.2.md section 3, confirmed by Deepak Pai
 * 7 Oct): an Ownership Period draft carries `effect` (O57); the item shows
 * the read-only EFFECT IF APPROVED panel above Approve, built by rates.js's
 * `ownershipEffectView` (O59) — the screen computes no pct, method, date or
 * period itself. A Desk "Record ownership" draft arrives with `effect: null`
 * and says "Drafted in Desk: effect not previewed." instead, never an empty
 * panel. An effect `ownershipEffectView` refuses (a server regression)
 * shows its thrown sentence, never a guessed panel. Approve is unchanged:
 * the same `approve` emit, so `approval_api.approve` and the self-approval
 * policy still decide.
 */
import { computed, reactive } from "vue";
import { Button } from "frappe-ui";
import { messageLines } from "../signoff.js";
import { pendingEmptyMessage, pendingCreatedText, ownershipEffectView } from "../rates.js";
import { userTimeZone } from "../timefmt.js";

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

// L01d/L01f: the zone the created time displays in (same lookup as the TB
// screen, B29). `now` is captured once per mount, same simplification
// TrialBalances.vue uses per load.
const timeZone = userTimeZone();
const now = new Date();
const NO_ZONE = "Your browser reported no time zone, so the created time cannot be shown.";

/** `pendingCreatedText` throws `parseZoned`'s own "no time zone" error when
 * `created` is not zoned (L01f: a server regression now that L01e sends it
 * zoned). That error is shown as this item's created text, unchanged --
 * the same text the TB screen would show for the identical defect
 * (tbTable.js's `timestampText`), never an invented message, and never a
 * silent re-zoning. One item's bad timestamp therefore shows its own
 * error without hiding the rest of the list. */
function createdText(created) {
	if (!timeZone) return NO_ZONE;
	try {
		return pendingCreatedText(created, now, timeZone);
	} catch (e) {
		return e.message;
	}
}

const DESK_DRAFT = "Drafted in Desk: effect not previewed.";

/** O61: an item's effect panel. Null for a Historical Equity Rate;
 * `{desk}` for a Desk draft (`effect: null`); `{error}` when the effect is
 * missing or malformed; otherwise `{view}`. Section 3 shows Ownership and
 * Method as before → after and the current period's end as its own
 * "Ends" line, so the Covers row is left out here. */
function opEffect(item) {
	if (item.doctype !== "Ownership Period") return null;
	if (item.effect === null) return { desk: DESK_DRAFT };
	try {
		const view = ownershipEffectView(item.effect);
		return { view: { ...view, rows: view.rows.filter((row) => row.label !== "Covers") } };
	} catch (e) {
		return { error: e.message };
	}
}

/** Each item's panel, keyed by name, computed once per payload. */
const effects = computed(() => {
	const out = {};
	for (const item of (props.view && props.view.items) || []) out[item.name] = opEffect(item);
	return out;
});

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
						· {{ createdText(item.created) }}
					</p>
				</div>
				<div
					v-if="effects[item.name] && effects[item.name].view"
					class="w-full rounded border border-outline-gray-2 bg-surface-gray-1 px-3 py-2 text-sm text-ink-gray-8"
				>
					<p class="text-xs font-medium uppercase tracking-wide text-ink-gray-6">EFFECT IF APPROVED</p>
					<dl class="mt-1 grid grid-cols-[6rem_1fr] gap-x-3 gap-y-0.5">
						<template v-for="row in effects[item.name].view.rows" :key="row.label">
							<dt class="text-ink-gray-6">{{ row.label }}</dt>
							<dd>{{ row.before }} → {{ row.after }}</dd>
						</template>
						<dt class="text-ink-gray-6">Ends</dt>
						<dd>{{ effects[item.name].view.endsLine }}</dd>
						<dt class="text-ink-gray-6">Periods</dt>
						<dd>{{ effects[item.name].view.periods }}</dd>
						<dt class="text-ink-gray-6">Re-sign</dt>
						<dd v-if="effects[item.name].view.resign.length">
							{{ effects[item.name].view.resign.join(", ") }} will be marked "Re-sign Needed"
						</dd>
						<dd v-else>{{ effects[item.name].view.resignNone }}</dd>
					</dl>
					<p class="mt-1 text-xs text-ink-gray-6">ⓘ {{ effects[item.name].view.notShown }}</p>
				</div>
				<p v-else-if="effects[item.name] && effects[item.name].desk" class="w-full text-sm text-ink-gray-7">
					{{ effects[item.name].desk }}
				</p>
				<p
					v-else-if="effects[item.name] && effects[item.name].error"
					role="alert"
					class="w-full rounded border border-outline-red-1 bg-surface-red-1 px-3 py-2 text-sm text-ink-gray-8"
				>
					{{ effects[item.name].error }}
				</p>
				<div class="ml-auto flex min-w-[10rem] flex-col items-end gap-1">
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
