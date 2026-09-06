<script setup>
/**
 * The build range, made explicit (U4).
 *
 * This was the most consequential input in the old app and the least visible:
 * the progress rail doubled as a range selector via click and shift-click, with
 * no affordance and no hint. It decided whether a run rebuilt one stage or all
 * five, and nobody who had not read the source could find it.
 *
 * Now it is two labelled selects with the resulting range spelled out in words.
 * Both the caption and the params come from the same tested pure functions, so
 * what the operator reads and what the backend receives cannot disagree.
 */
import { computed } from "vue";
import { Select, FeatherIcon } from "frappe-ui";
import { describeStageRange, isFullRange } from "../orchestrator/params.js";

const props = defineProps({
	stages: { type: Array, required: true },
	from: { type: Number, required: true },
	to: { type: Number, required: true },
});
const emit = defineEmits(["update:from", "update:to"]);

const opts = computed(() => props.stages.map((s, i) => ({ label: s.label, value: String(i) })));
const summary = computed(() => describeStageRange(props.stages, props.from, props.to));
const full = computed(() => isFullRange(props.stages, props.from, props.to));
</script>

<template>
	<div class="rounded border border-outline-gray-1 bg-surface-gray-1 px-4 py-3">
		<div class="flex flex-wrap items-end gap-3">
			<label class="block">
				<span class="mb-1 block text-sm text-ink-gray-6">Build from</span>
				<Select
					:model-value="String(from)"
					:options="opts"
					size="sm"
					@update:model-value="(v) => emit('update:from', Number(v))"
				/>
			</label>
			<FeatherIcon name="arrow-right" class="mb-2 h-4 w-4 text-ink-gray-4" />
			<label class="block">
				<span class="mb-1 block text-sm text-ink-gray-6">Through</span>
				<Select
					:model-value="String(to)"
					:options="opts"
					size="sm"
					@update:model-value="(v) => emit('update:to', Number(v))"
				/>
			</label>
		</div>
		<p class="mt-2.5 text-sm" :class="full ? 'text-ink-gray-6' : 'text-ink-amber-3'">
			{{ summary }}
			<span v-if="!full"> Everything downstream is rebuilt too.</span>
		</p>
	</div>
</template>
