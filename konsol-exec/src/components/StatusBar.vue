<script setup>
/**
 * The status bar: the machinery under the close, always visible, so trouble
 * shows before anyone starts a run on stale data.
 */
import { computed } from "vue";
import { shortTime } from "../home.js";

const props = defineProps({ health: { type: Object, default: null } });

const DOT = { Success: "bg-green-500", Failed: "bg-red-500", Running: "bg-blue-500", Partial: "bg-orange-400" };
const host = computed(() => (typeof window !== "undefined" ? window.location.host : ""));
</script>

<template>
	<footer class="flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-outline-gray-1 bg-surface-gray-1 px-4 py-1.5 text-xs text-ink-gray-6">
		<template v-if="health">
			<span class="flex items-center gap-1.5">
				<span class="h-2 w-2 rounded-full" :class="health.worker ? 'bg-green-500' : 'bg-red-500'" />
				Worker {{ health.worker ? "healthy" : "not responding" }}
			</span>
			<span v-if="health.last_build">Last build {{ (health.last_build.state || "").toLowerCase() }} · {{ shortTime(health.last_build.at) }}</span>
			<span v-for="c in health.connectors" :key="c.name" class="flex items-center gap-1.5">
				<span class="h-2 w-2 rounded-full" :class="DOT[c.status] || 'bg-gray-400'" />
				{{ c.label }}<template v-if="c.status === 'Failed'"> failed</template> {{ shortTime(c.at) }}
			</span>
		</template>
		<span v-else>Status appears when a month is open.</span>
		<span class="ml-auto font-mono">{{ host }}</span>
	</footer>
</template>
