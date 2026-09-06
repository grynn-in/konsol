<script setup>
/**
 * Past runs for a process, with the drill-down restored.
 *
 * Clicking a row selects it and loads the detail below via runDetailMachine —
 * the same machine the React app used, unchanged. Selecting a second row
 * reselects; clicking the active row closes it.
 */
import { computed, inject, watch } from "vue";
import { useMachine } from "@xstate/vue";
import { Badge, FeatherIcon } from "frappe-ui";
import RunDetail from "./RunDetail.vue";
import { getDomainRuns } from "../domain.js";
import { statusTone } from "../orchestrator/status.js";
import { TONE_THEME } from "../constants.js";
import { runDetailMachine } from "../machines/index.js";

const props = defineProps({ domain: { type: String, required: true } });
const plane = inject("plane");
const runs = computed(() => getDomainRuns(plane.data.value, props.domain));

const { snapshot, send } = useMachine(runDetailMachine);
const selected = computed(() => snapshot.value.context.selected);

/** Moving between steps must not leave another process's run open. */
watch(() => props.domain, () => send({ type: "DOMAIN_CHANGED" }));

function toggle(run) {
	const id = run.id || run.name;
	if (selected.value && (selected.value.id || selected.value.name) === id) {
		send({ type: "DESELECT" });
	} else {
		send({ type: "SELECT", domain: props.domain, run: { ...run, id } });
	}
}

function isOpen(run) {
	const id = run.id || run.name;
	return Boolean(selected.value) && (selected.value.id || selected.value.name) === id;
}
</script>

<template>
	<div v-if="runs.length" class="overflow-hidden rounded border border-outline-gray-1">
		<div v-for="(r, i) in runs" :key="r.id || r.name || i" :class="i > 0 ? 'border-t border-outline-gray-1' : ''">
			<button
				type="button"
				class="flex w-full flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 text-left hover:bg-surface-gray-1 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-outline-gray-3"
				:class="isOpen(r) ? 'bg-surface-gray-1' : 'bg-surface-white'"
				:aria-expanded="isOpen(r)"
				@click="toggle(r)"
			>
				<FeatherIcon
					name="chevron-right"
					class="h-3.5 w-3.5 shrink-0 text-ink-gray-4 transition-transform"
					:class="isOpen(r) ? 'rotate-90' : ''"
				/>
				<Badge :theme="TONE_THEME[statusTone(r.status)] || 'gray'" variant="subtle">
					{{ r.status }}
				</Badge>
				<span class="tnum flex-1 text-base text-ink-gray-9">{{ r.id || r.name }}</span>
				<span v-if="r.period" class="text-sm text-ink-gray-6">{{ r.period }}</span>
				<span class="tnum text-sm text-ink-gray-5">{{ r.started || r.started_at || "—" }}</span>
				<span v-if="r.duration" class="tnum text-sm text-ink-gray-5">{{ r.duration }}</span>
				<span class="text-sm text-ink-gray-5">{{ r.by || r.owner || "" }}</span>
			</button>

			<div v-if="isOpen(r)" class="px-4 pb-4">
				<RunDetail
					:detail="snapshot.context.detail"
					:loading="snapshot.matches('loading')"
					:error="snapshot.context.error"
					@retry="send({ type: 'RETRY' })"
					@close="send({ type: 'DESELECT' })"
				/>
			</div>
		</div>
	</div>
	<p v-else class="text-base text-ink-gray-5">No runs recorded for this process yet.</p>
</template>
