<script setup>
/**
 * Step 1 — readiness, aggregated across all four processes.
 *
 * The old app showed the same prerequisite (EPM Settings, Fiscal Period…)
 * once per process, so a single missing record appeared as four problems.
 * These are de-duplicated by doctype with the worst status winning, and each
 * row names which processes it blocks.
 */
import { computed, inject } from "vue";
import { Button, FeatherIcon } from "frappe-ui";
import StatusBadge from "./StatusBadge.vue";
import { readinessSummary, getDomainMeta } from "../domain.js";
import { openDoctype } from "../api.js";

const plane = inject("plane");
const summary = computed(() => readinessSummary(plane.data.value));
</script>

<template>
	<div>
		<p class="mb-4 text-base text-ink-gray-7">
			{{ summary.ok }} of {{ summary.total }} configured.
			<span v-if="summary.blocking" class="text-ink-red-3">
				{{ summary.blocking }} still blocking the close.
			</span>
		</p>

		<div class="overflow-hidden rounded border border-outline-gray-1">
			<div
				v-for="(row, i) in summary.rows"
				:key="row.doctype"
				class="flex flex-wrap items-center gap-x-4 gap-y-2 bg-surface-white px-4 py-3"
				:class="i > 0 ? 'border-t border-outline-gray-1' : ''"
			>
				<StatusBadge :state="row.status" kind="setup" class="shrink-0" />
				<div class="min-w-[12rem] flex-1">
					<div class="text-base text-ink-gray-9">{{ row.doctype }}</div>
					<div class="text-sm text-ink-gray-5">
						{{ (row.processes || []).map((p) => getDomainMeta(p).label).join(" · ") }}
					</div>
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
