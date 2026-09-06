<script setup>
/**
 * Step-by-step view of a launched run, with the recovery actions the
 * orchestrator supports. Renders from the framework-free run model, so the
 * shape shown here is exactly the shape its unit tests cover.
 */
import { computed } from "vue";
import { Badge, Button, Progress, FeatherIcon } from "frappe-ui";
import { orderSteps, progressPct } from "../orchestrator/runModel.js";
import { statusTone, isTerminal } from "../orchestrator/status.js";
import { TONE_THEME } from "../constants.js";

const props = defineProps({ run: { type: Object, default: null } });
const emit = defineEmits(["send"]);

const steps = computed(() => orderSteps(props.run?.steps));
const pct = computed(() => Math.round(progressPct(steps.value)));
const settled = computed(() => isTerminal(props.run?.status));
</script>

<template>
	<div v-if="run" class="rounded border border-outline-gray-1 bg-surface-white px-5 py-4">
		<div class="mb-3 flex flex-wrap items-center gap-3">
			<Badge :theme="TONE_THEME[statusTone(run.status)] || 'gray'" variant="subtle">
				{{ run.status }}
			</Badge>
			<span class="tnum text-sm text-ink-gray-6">{{ run.name }}</span>
			<span class="tnum ml-auto text-sm text-ink-gray-5">{{ pct }}%</span>
		</div>

		<Progress :value="pct" size="md" class="mb-4" />

		<ol v-if="steps.length" class="space-y-px">
			<li
				v-for="(s, i) in steps"
				:key="s.id || i"
				class="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-outline-gray-1 py-2 first:border-t-0"
			>
				<Badge :theme="TONE_THEME[statusTone(s.status)] || 'gray'" variant="subtle" size="sm">
					{{ s.status }}
				</Badge>
				<span class="flex-1 text-base text-ink-gray-9">{{ s.id }}</span>
				<span v-if="s.rows" class="tnum text-sm text-ink-gray-5">{{ s.rows }} rows</span>
				<Button
					v-if="s.status === 'Failed'"
					variant="subtle"
					size="sm"
					@click="emit('send', { type: 'RETRY_STEP', stepId: s.id })"
				>Retry step</Button>
				<p v-if="s.error" class="w-full text-sm text-ink-red-3">{{ s.error }}</p>
			</li>
		</ol>

		<div v-if="!settled" class="mt-3 border-t border-outline-gray-1 pt-3">
			<Button theme="red" variant="subtle" size="sm" @click="emit('send', { type: 'CANCEL' })">
				<template #prefix><FeatherIcon name="x-circle" class="h-3.5 w-3.5" /></template>
				Cancel run
			</Button>
		</div>
	</div>
</template>
