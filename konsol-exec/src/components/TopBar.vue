<script setup>
/**
 * Top bar.
 *
 * The period used to live here as two selects. It moved into the page heading
 * (see PeriodPicker) where it stopped duplicating the H1, so what is left is
 * genuinely global: who you are looking at, whether the worker is alive, and
 * the way back to the desk.
 *
 * That last part is the answer to U7 — the console hides the Frappe chrome
 * while sending people into the desk on every Setup row, so it has to carry a
 * visible route back itself.
 */
import { Button, FeatherIcon, Tooltip } from "frappe-ui";

defineProps({
	busy: { type: Boolean, default: false },
	workerHealthy: { type: Boolean, default: true },
});
defineEmits(["refresh"]);

function toDesk() {
	window.location.href = "/app";
}
</script>

<template>
	<header
		class="sticky top-0 z-10 flex items-center gap-4 border-b border-outline-gray-1 bg-surface-white px-6 py-2.5"
	>
		<RouterLink
			to="/close"
			class="flex items-center gap-2 rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-outline-gray-3"
		>
			<span class="text-base font-semibold tracking-tight text-ink-gray-9">Konsol</span>
			<span class="text-base text-ink-gray-5">Close</span>
		</RouterLink>

		<div class="ml-auto flex items-center gap-2">
			<Tooltip
				v-if="!workerHealthy"
				text="No background worker is responding — runs will queue but not start"
			>
				<span class="flex items-center gap-1.5 text-sm text-ink-red-3">
					<FeatherIcon name="alert-triangle" class="h-3.5 w-3.5" />
					worker down
				</span>
			</Tooltip>

			<Tooltip text="Refresh">
				<Button variant="ghost" size="sm" :loading="busy" @click="$emit('refresh')">
					<template #icon><FeatherIcon name="refresh-cw" class="h-4 w-4" /></template>
				</Button>
			</Tooltip>

			<Button variant="ghost" size="sm" @click="toDesk">
				<template #suffix><FeatherIcon name="external-link" class="h-3.5 w-3.5" /></template>
				Desk
			</Button>
		</div>
	</header>
</template>
