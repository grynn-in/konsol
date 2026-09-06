<script setup>
/**
 * Failure state (U9). Says what failed, where, and offers a retry that shows
 * it is retrying — rather than one sentence and a button.
 */
import { Button, FeatherIcon } from "frappe-ui";

defineProps({
	error: { type: [Object, String], default: null },
	busy: { type: Boolean, default: false },
});
defineEmits(["retry"]);
</script>

<template>
	<div class="mx-auto max-w-lg px-6 py-24 text-center">
		<div class="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-full bg-surface-red-2">
			<FeatherIcon name="alert-triangle" class="h-5 w-5 text-ink-red-3" />
		</div>
		<h1 class="mb-2 text-lg font-semibold text-ink-gray-9">Can't reach the control plane</h1>
		<p class="mb-1 text-base text-ink-gray-7">
			{{ typeof error === "string" ? error : error?.message || "The server did not respond." }}
		</p>
		<p class="mb-6 text-sm text-ink-gray-5">
			<code class="rounded bg-surface-gray-2 px-1.5 py-0.5">konsol.control_api.get_snapshot</code>
		</p>
		<Button theme="gray" variant="solid" :loading="busy" @click="$emit('retry')">
			{{ busy ? "Retrying…" : "Retry" }}
		</Button>
	</div>
</template>
