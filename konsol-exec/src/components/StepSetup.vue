<script setup>
/**
 * Per-process readiness. Unchanged in intent from the old Setup tab — it lists
 * prerequisites and deep-links into the desk rather than rebuilding forms,
 * which was the right boundary and stays.
 */
import { computed, inject } from "vue";
import { Button, FeatherIcon } from "frappe-ui";
import StatusBadge from "./StatusBadge.vue";
import { getProcess } from "../domain.js";
import { openDoctype } from "../api.js";

const props = defineProps({ domain: { type: String, required: true } });
const plane = inject("plane");
const proc = computed(() => getProcess(plane.data.value, props.domain));
const rows = computed(() => proc.value?.prerequisites || []);
</script>

<template>
	<div>
		<p class="mb-4 text-base text-ink-gray-7">
			{{ proc?.ready_count ?? 0 }} of {{ proc?.total_count ?? 0 }} configured.
		</p>
		<div class="overflow-hidden rounded border border-outline-gray-1">
			<div
				v-for="(row, i) in rows"
				:key="row.doctype + i"
				class="flex flex-wrap items-center gap-x-4 gap-y-2 bg-surface-white px-4 py-3"
				:class="i > 0 ? 'border-t border-outline-gray-1' : ''"
			>
				<StatusBadge :state="row.status" kind="setup" class="shrink-0" />
				<div class="min-w-[12rem] flex-1">
					<div class="text-base text-ink-gray-9">{{ row.doctype }}</div>
					<div class="text-sm text-ink-gray-5">{{ row.location }}</div>
					<div v-if="row.note" class="mt-0.5 text-sm text-ink-gray-5">{{ row.note }}</div>
				</div>
				<div class="text-sm text-ink-gray-6">{{ row.owner }}</div>
				<Button variant="ghost" size="sm" @click="openDoctype(row.doctype)">
					Open
					<template #suffix><FeatherIcon name="external-link" class="h-3.5 w-3.5" /></template>
				</Button>
				<Button
					v-if="row.actionable"
					variant="subtle"
					size="sm"
					@click="plane.send({ type: 'REMIND', owner: row.owner, item: row.doctype })"
				>Remind</Button>
			</div>
		</div>
	</div>
</template>
