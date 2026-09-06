<script setup>
/**
 * Past runs for a process.
 */
import { computed, inject } from "vue";
import { Badge } from "frappe-ui";
import { getDomainRuns } from "../domain.js";
import { statusTone } from "../orchestrator/status.js";
import { TONE_THEME } from "../constants.js";

const props = defineProps({ domain: { type: String, required: true } });
const plane = inject("plane");
const runs = computed(() => getDomainRuns(plane.data.value, props.domain));
</script>

<template>
	<div v-if="runs.length" class="overflow-hidden rounded border border-outline-gray-1">
		<div
			v-for="(r, i) in runs"
			:key="r.name || i"
			class="flex flex-wrap items-center gap-x-4 gap-y-1 bg-surface-white px-4 py-3"
			:class="i > 0 ? 'border-t border-outline-gray-1' : ''"
		>
			<Badge :theme="TONE_THEME[statusTone(r.status)] || 'gray'" variant="subtle">
				{{ r.status }}
			</Badge>
			<span class="flex-1 text-base text-ink-gray-9">{{ r.name }}</span>
			<span class="tnum text-sm text-ink-gray-5">{{ r.started_at || r.started || "—" }}</span>
			<span class="text-sm text-ink-gray-5">{{ r.owner || r.by || "" }}</span>
		</div>
	</div>
	<p v-else class="text-base text-ink-gray-5">No runs recorded for this process yet.</p>
</template>
