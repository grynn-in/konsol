<script setup>
/**
 * The no-period home (row "/"): what shows when nothing declared covers
 * today (or the server can't be reached to say), so the router's redirect
 * guard (router.js) leaves the URL at "/" instead of guessing a month.
 * Never invents a period — just points at the navigator, which lists what
 * is actually declared.
 */
import { FeatherIcon } from "frappe-ui";
</script>

<template>
	<div class="mx-auto max-w-lg px-6 py-24 text-center">
		<div class="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-full bg-surface-gray-2">
			<FeatherIcon name="calendar" class="h-5 w-5 text-ink-gray-6" />
		</div>
		<h1 class="mb-2 text-lg font-semibold text-ink-gray-9">No declared period covers today</h1>
		<p class="text-base text-ink-gray-6">Pick a year and period in the navigator.</p>
	</div>
</template>
