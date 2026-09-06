<script setup>
/**
 * Live view of the current or last run for a process.
 */
import { computed, inject } from "vue";
import { Button, Progress, FeatherIcon } from "frappe-ui";
import StatusBadge from "./StatusBadge.vue";
import StageRail from "./StageRail.vue";
import { getProcess, getDomainMeta, getDomainStats, railStages } from "../domain.js";

const props = defineProps({ domain: { type: String, required: true } });
const plane = inject("plane");

const proc = computed(() => getProcess(plane.data.value, props.domain));
const meta = computed(() => getDomainMeta(props.domain));
const stats = computed(() => getDomainStats(proc.value));
const rail = computed(() => (proc.value ? railStages(meta.value, proc.value) : []));
const run = computed(() => proc.value?.run || {});
const pct = computed(() => {
	const total = run.value.step_total || 0;
	return total ? Math.round(((run.value.step_done || 0) / total) * 100) : 0;
});
const active = computed(() => ["running", "paused"].includes(proc.value?.machine_status));
</script>

<template>
	<div class="space-y-5">
		<div class="rounded border border-outline-gray-1 bg-surface-white px-5 py-4">
			<div class="mb-3 flex flex-wrap items-center gap-3">
				<StatusBadge :state="proc?.machine_status || 'idle'" />
				<span class="tnum text-sm text-ink-gray-6">{{ stats.runLabel }}</span>
				<Button
					v-if="!active"
					class="ml-auto"
					variant="subtle"
					size="sm"
					@click="plane.send({ type: 'START_PROCESS', processId: domain })"
				>
					{{ meta.verb }}
					<template #suffix><FeatherIcon name="play" class="h-3.5 w-3.5" /></template>
				</Button>
			</div>
			<Progress v-if="active" :value="pct" size="md" class="mb-3" />
			<StageRail :stages="rail" />
		</div>

		<div v-if="proc?.blockers" class="rounded border border-outline-red-2 bg-surface-red-1 px-4 py-3">
			<p class="text-base text-ink-red-3">
				{{ proc.blockers }} blocker{{ proc.blockers === 1 ? "" : "s" }} — see Setup.
			</p>
		</div>
	</div>
</template>
