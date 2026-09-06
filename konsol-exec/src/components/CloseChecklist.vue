<script setup>
/**
 * The home screen, and the whole point of the rearrangement (U8).
 *
 * The old app opened on four process cards and asked "which process?". An
 * operator's actual job is "close September", so this opens on the period and
 * lists what closing it requires, in order. The four processes did not go away
 * — they became steps 2 to 5, and their Setup / Execute / Monitor / History
 * tabs live inside each step, which is where that structure always belonged.
 *
 * Numbering is real here: these steps genuinely run in sequence, so a numeral
 * carries information rather than decoration.
 */
import { computed, inject } from "vue";
import { Button, FeatherIcon } from "frappe-ui";
import StatusBadge from "./StatusBadge.vue";
import StageRail from "./StageRail.vue";
import { closeSteps, primaryAction, getDomainMeta, getProcess, railStages } from "../domain.js";

const plane = inject("plane");
const steps = computed(() => closeSteps(plane.data.value));

function railFor(step) {
	if (step.kind !== "process") return null;
	const proc = getProcess(plane.data.value, step.id);
	if (!proc) return null;
	return railStages(getDomainMeta(step.id), proc);
}

function go(step, tab) {
	plane.router.push(tab ? `/close/${step.id}/${tab}` : `/close/${step.id}`);
}

const SETTLED = new Set(["done", "ready"]);
const remaining = computed(() => steps.value.filter((s) => !SETTLED.has(s.state)).length);
</script>

<template>
	<div class="mx-auto max-w-4xl px-6 py-8">
		<div class="mb-7">
			<!-- U3: the heading is what the page IS. The description is secondary,
			     not set at 25px above the identity. -->
			<h1 class="text-2xl font-semibold tracking-tight text-ink-gray-9">
				Close {{ plane.periodLabel.value }}
			</h1>
			<p class="mt-1 text-base text-ink-gray-6">
				{{ remaining === 0 ? "Every step is complete." : `${remaining} of ${steps.length} steps outstanding.` }}
			</p>
		</div>

		<ol class="overflow-hidden rounded border border-outline-gray-1">
			<li
				v-for="(step, i) in steps"
				:key="step.id"
				class="flex flex-wrap items-center gap-x-4 gap-y-3 bg-surface-white px-5 py-4"
				:class="i > 0 ? 'border-t border-outline-gray-1' : ''"
			>
				<span
					class="tnum grid h-7 w-7 shrink-0 place-items-center rounded-full border border-outline-gray-2 text-sm font-medium text-ink-gray-6"
				>{{ step.n }}</span>

				<div class="min-w-[13rem] flex-1">
					<div class="flex items-center gap-2">
						<button
							class="text-left text-base font-medium text-ink-gray-9 hover:underline"
							:disabled="!step.available"
							@click="go(step)"
						>
							{{ step.label }}
						</button>
						<span
							v-if="!step.available"
							class="rounded bg-surface-gray-2 px-1.5 py-0.5 text-xs text-ink-gray-5"
						>not tracked yet</span>
					</div>
					<p class="mt-0.5 text-sm text-ink-gray-5">{{ step.detail }}</p>
				</div>

				<StageRail v-if="railFor(step)" :stages="railFor(step)" compact class="shrink-0" />

				<StatusBadge :state="step.state" class="shrink-0" />

				<Button
					v-if="primaryAction(step)"
					:theme="primaryAction(step).theme"
					variant="subtle"
					size="sm"
					class="shrink-0"
					@click="go(step, primaryAction(step).tab)"
				>
					{{ primaryAction(step).label }}
					<template #suffix><FeatherIcon name="arrow-right" class="h-3.5 w-3.5" /></template>
				</Button>
			</li>
		</ol>

		<p class="mt-4 text-sm text-ink-gray-5">
			Steps run in order. A step can be opened at any time — the order describes
			dependency, not permission.
		</p>
	</div>
</template>
