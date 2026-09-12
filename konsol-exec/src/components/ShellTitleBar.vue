<script setup>
/**
 * The title bar: where you are, who you are, and whether the machinery is
 * alive. The period is NOT chosen here (the user asked for no dropdown in the
 * top menu); it is shown as a path, and chosen in the navigator.
 */
import { FeatherIcon } from "frappe-ui";

defineProps({
	me: { type: Object, default: null },
	crumbs: { type: Array, default: () => [] },
	busy: { type: Boolean, default: false },
	workerHealthy: { type: Boolean, default: true },
});
defineEmits(["refresh"]);
</script>

<template>
	<header class="flex min-h-11 flex-wrap items-center gap-x-4 bg-gray-900 px-4 text-gray-100">
		<RouterLink
			to="/"
			class="flex items-center gap-2 border-r border-gray-700 py-2.5 pr-4 font-semibold tracking-tight focus-visible:outline focus-visible:outline-2 focus-visible:outline-gray-400"
		>
			<span class="grid h-5 w-5 place-items-center rounded bg-gray-100 text-[11px] font-bold text-gray-900">K</span>
			Konsol
		</RouterLink>

		<nav aria-label="Location" class="flex min-w-0 flex-1 flex-wrap items-center gap-1.5 py-2 text-sm text-gray-400">
			<template v-for="(c, i) in crumbs" :key="i">
				<span v-if="i" aria-hidden="true" class="text-gray-600">/</span>
				<RouterLink v-if="c.to && i < crumbs.length - 1" :to="c.to" class="hover:text-gray-100">{{ c.label }}</RouterLink>
				<span
					v-else
					:class="i === crumbs.length - 1 ? 'font-medium text-gray-100' : ''"
					:aria-current="i === crumbs.length - 1 ? 'page' : undefined"
				>{{ c.label }}</span>
			</template>
		</nav>

		<span v-if="!workerHealthy" class="flex items-center gap-1.5 text-sm text-red-300">
			<FeatherIcon name="alert-triangle" class="h-3.5 w-3.5" />
			worker down
		</span>

		<button
			type="button"
			class="rounded p-1.5 text-gray-400 hover:bg-gray-800 hover:text-gray-100"
			title="Refresh"
			aria-label="Refresh"
			@click="$emit('refresh')"
		>
			<FeatherIcon name="refresh-cw" class="h-4 w-4" :class="busy ? 'animate-spin' : ''" />
		</button>

		<a href="/app" class="flex items-center gap-1 rounded px-2 py-1 text-sm text-gray-400 hover:bg-gray-800 hover:text-gray-100">
			Desk
			<FeatherIcon name="external-link" class="h-3.5 w-3.5" />
		</a>

		<div v-if="me" class="flex items-center gap-2 py-2 text-sm">
			<span class="grid h-7 w-7 place-items-center rounded-full bg-gray-700 text-xs font-semibold">{{ me.initials }}</span>
			<span class="hidden leading-tight sm:block">
				{{ me.full_name }}
				<span class="block text-xs text-gray-400">{{ me.title }}</span>
			</span>
		</div>
	</header>
</template>
