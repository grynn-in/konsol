<script setup>
/**
 * Loading state (U9). The old app rendered "Loading control plane…" as centred
 * grey text on an empty page, then dropped the whole layout in at once. For an
 * app whose entire job is showing the state of long-running things, its own
 * loading state deserved better. This holds the real shell so nothing jumps.
 */
</script>

<template>
	<div class="animate-pulse">
		<div class="flex items-center justify-between border-b border-outline-gray-1 px-6 py-3">
			<div class="h-5 w-40 rounded bg-surface-gray-2" />
			<div class="h-8 w-44 rounded bg-surface-gray-2" />
		</div>
		<div class="mx-auto max-w-4xl px-6 py-8">
			<div class="mb-8 h-7 w-56 rounded bg-surface-gray-2" />
			<div class="space-y-px overflow-hidden rounded border border-outline-gray-1">
				<div v-for="i in 6" :key="i" class="flex items-center gap-4 bg-surface-white px-5 py-5">
					<div class="h-7 w-7 rounded-full bg-surface-gray-2" />
					<div class="flex-1 space-y-2">
						<div class="h-4 w-40 rounded bg-surface-gray-2" />
						<div class="h-3 w-64 rounded bg-surface-gray-1" />
					</div>
					<div class="h-6 w-24 rounded bg-surface-gray-2" />
				</div>
			</div>
		</div>
	</div>
</template>
