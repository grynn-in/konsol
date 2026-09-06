<script setup>
/**
 * Stage progress for a process run.
 *
 * Two review findings shaped this:
 *
 *  U1 — no metals. Stage identity is a numeral and a label; the only colour is
 *       status. A stage that is running, done or failed says so; a stage that
 *       is merely "the third one" is neutral, because that is not information
 *       colour should be spent on.
 *
 *  U4 — this component no longer doubles as the build-range control. It shows
 *       progress and nothing else. The range moved to an explicit picker, so
 *       that a click here can never silently change what a run will do.
 */
import { FeatherIcon, Spinner } from "frappe-ui";

defineProps({
	stages: { type: Array, required: true },
	compact: { type: Boolean, default: false },
});

const ringClass = {
	done: "border-outline-green-2 bg-surface-green-2 text-ink-green-3",
	now: "border-outline-blue-2 bg-surface-blue-2 text-ink-blue-3",
	fail: "border-outline-red-2 bg-surface-red-2 text-ink-red-3",
	idle: "border-outline-gray-2 bg-surface-gray-2 text-ink-gray-5",
};
</script>

<template>
	<ol class="flex items-center gap-0 overflow-x-auto" :class="compact ? 'py-1' : 'py-2'">
		<li v-for="(s, i) in stages" :key="s.id" class="flex shrink-0 items-center">
			<div class="flex items-center gap-2">
				<span
					class="tnum grid place-items-center rounded-full border text-xs font-medium"
					:class="[ringClass[s.state] || ringClass.idle, compact ? 'h-5 w-5' : 'h-6 w-6']"
				>
					<Spinner v-if="s.state === 'now'" class="h-3 w-3" />
					<FeatherIcon v-else-if="s.state === 'done'" name="check" class="h-3 w-3" />
					<FeatherIcon v-else-if="s.state === 'fail'" name="x" class="h-3 w-3" />
					<template v-else>{{ s.n }}</template>
				</span>
				<span
					v-if="!compact"
					class="whitespace-nowrap text-sm"
					:class="s.state === 'idle' ? 'text-ink-gray-5' : 'text-ink-gray-8'"
				>{{ s.label }}</span>
			</div>
			<span
				v-if="i < stages.length - 1"
				class="mx-2 h-px shrink-0 bg-outline-gray-2"
				:class="compact ? 'w-4' : 'w-6'"
				aria-hidden="true"
			/>
		</li>
	</ol>
</template>
