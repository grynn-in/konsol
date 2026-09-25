<script setup>
/**
 * konsol#305 B17: the designed loading, empty and error states.
 *
 * Every screen wraps its data in this, so no screen is ever blank:
 * - `loading` says WHAT is being fetched and from where (#298 stage 1
 *   forbids a loading text that names nothing);
 * - `empty` says why there is nothing, in the caller's words;
 * - `error` shows the server's message (split on `<br>` into plain-text lines,
 *   never rendered as HTML; B06 note) and a Retry that shows it is retrying;
 * - an unknown `state` is itself shown as an error, never as nothing.
 *
 * Each state is a named slot, so a screen may replace the default.
 */
import { computed } from "vue";
import { Button, FeatherIcon } from "frappe-ui";
import { messageLines } from "../signoff.js";

const STATES = ["loading", "empty", "error", "ready"];

const props = defineProps({
	state: { type: String, required: true },
	/** What is being fetched, e.g. "the period FY2025 P07". */
	what: { type: String, required: true },
	/** The endpoint, shown small, so a user can quote it. */
	source: { type: String, default: null },
	error: { type: [String, Object], default: null },
	busy: { type: Boolean, default: false },
	emptyText: { type: String, default: null },
	/** A compact rendering, for a panel inside a screen. */
	compact: { type: Boolean, default: false },
});
const emit = defineEmits(["retry"]);

const known = computed(() => STATES.includes(props.state));
const errorLines = computed(() => {
	if (!known.value) return [`Unknown load state: ${props.state}`];
	const e = props.error;
	const text = typeof e === "string" ? e : e && e.message;
	const lines = messageLines(text);
	return lines.length ? lines : ["The server gave no reason."];
});
</script>

<template>
	<div
		v-if="state === 'loading'"
		role="status"
		aria-live="polite"
		:aria-busy="true"
		:class="compact ? 'px-4 py-4' : 'mx-auto max-w-3xl px-6 py-10'"
	>
		<slot name="loading" :what="what">
			<div class="animate-pulse space-y-3" aria-hidden="true">
				<div class="h-5 w-48 rounded bg-surface-gray-2" />
				<div v-if="!compact" class="h-4 w-80 rounded bg-surface-gray-2" />
				<div v-if="!compact" class="h-4 w-64 rounded bg-surface-gray-1" />
			</div>
			<p class="mt-4 text-sm text-ink-gray-6">Fetching {{ what }}</p>
			<p v-if="source" class="mt-1 text-xs text-ink-gray-5"><code>{{ source }}</code></p>
		</slot>
	</div>

	<div
		v-else-if="state === 'empty'"
		:class="compact ? 'px-4 py-4' : 'mx-auto max-w-3xl px-6 py-10'"
	>
		<slot name="empty" :what="what">
			<div class="flex items-start gap-3 rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-4">
				<FeatherIcon name="inbox" class="mt-0.5 h-4 w-4 shrink-0 text-ink-gray-5" />
				<p class="text-base text-ink-gray-7">{{ emptyText || `Nothing to show for ${what}.` }}</p>
			</div>
		</slot>
	</div>

	<div
		v-else-if="state === 'error' || !known"
		role="alert"
		:class="compact ? 'px-4 py-4' : 'mx-auto max-w-3xl px-6 py-10'"
	>
		<slot name="error" :error="error" :lines="errorLines" :retry="() => emit('retry')">
			<div class="rounded border border-outline-red-1 bg-surface-red-1 px-4 py-4">
				<div class="flex items-start gap-3">
					<FeatherIcon name="alert-triangle" class="mt-0.5 h-4 w-4 shrink-0 text-ink-red-3" />
					<div class="min-w-0 flex-1">
						<p class="text-base font-medium text-ink-gray-9">Could not fetch {{ what }}</p>
						<p v-for="(errorLine, i) in errorLines" :key="i" class="mt-1 text-base text-ink-gray-7">{{ errorLine }}</p>
						<p v-if="source" class="mt-1 text-xs text-ink-gray-5"><code>{{ source }}</code></p>
						<Button class="mt-3" theme="gray" variant="solid" :loading="busy" @click="emit('retry')">
							{{ busy ? "Retrying" : "Retry" }}
						</Button>
					</div>
				</div>
			</div>
		</slot>
	</div>

	<slot v-else />
</template>
