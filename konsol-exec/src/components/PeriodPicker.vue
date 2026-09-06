<script setup>
/**
 * The period control.
 *
 * Replaces two dropdowns in the top bar. Three things were wrong with those:
 *
 *  1. A period is one thing. Splitting it into a year select and a period
 *     select made the operator assemble it from parts every time.
 *  2. It duplicated the page heading, which already said "Close Sep FY2026" —
 *     the same fact twice, once as chrome and once as content.
 *  3. It sat in a top bar, away from the heading the eye actually lands on.
 *
 * So the heading became the control. Steppers flank it because a close moves
 * one period at a time; the label opens a picker for the occasional jump back.
 * Steppers disable at the ends of the range the backend reported rather than
 * clamping, so a dead control never looks live.
 */
import { computed } from "vue";
import { Popover, FeatherIcon } from "frappe-ui";
import { formatPeriod, stepPeriod, canStep, yearChoices, periodChoices } from "../period.js";

const props = defineProps({
	period: { type: Object, default: null },
	options: { type: Object, default: null },
});
const emit = defineEmits(["update:period"]);

const label = computed(() => formatPeriod(props.period, props.options) || "Select period");
const canBack = computed(() => canStep(props.period, props.options, -1));
const canFwd = computed(() => canStep(props.period, props.options, 1));

const years = computed(() => yearChoices(props.options));
const periods = computed(() => periodChoices(props.options));

function step(delta) {
	const next = stepPeriod(props.period, props.options, delta);
	if (next) emit("update:period", next);
}

function pick(year, period, close) {
	emit("update:period", { year: String(year), period: String(period) });
	close?.();
}

// Small, grouped, and to the right of the label — bracketing the label at
// heading size cages it and pushes the left chevron into the preceding word.
const stepperClass =
	"grid h-6 w-6 shrink-0 place-items-center text-ink-gray-5 " +
	"hover:bg-surface-gray-3 hover:text-ink-gray-8 disabled:cursor-not-allowed " +
	"disabled:text-ink-gray-3 disabled:hover:bg-transparent focus-visible:outline " +
	"focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-outline-gray-3";
</script>

<template>
	<span class="inline-flex items-center gap-2 align-middle">
		<Popover placement="bottom-start">
			<template #target="{ togglePopover, isOpen }">
				<button
					type="button"
					class="flex items-center gap-1 rounded px-1 text-ink-gray-9 decoration-outline-gray-3 decoration-2 underline-offset-[6px] hover:bg-surface-gray-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-outline-gray-3"
					:class="isOpen ? 'bg-surface-gray-2' : 'underline'"
					:aria-expanded="isOpen"
					@click="togglePopover()"
				>
					{{ label }}
					<FeatherIcon
						name="chevron-down"
						class="h-[0.55em] w-[0.55em] shrink-0 text-ink-gray-5 transition-transform"
						:class="isOpen ? 'rotate-180' : ''"
					/>
				</button>
			</template>

			<template #body-main="{ close }">
				<div class="w-64 p-3">
					<div v-for="y in years" :key="y" class="mb-3 last:mb-0">
						<div class="mb-1.5 font-mono text-xs uppercase tracking-wider text-ink-gray-5">
							FY{{ y }}
						</div>
						<div class="grid grid-cols-4 gap-1">
							<button
								v-for="p in periods"
								:key="p.value"
								type="button"
								class="rounded px-1 py-1.5 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-outline-gray-3"
								:class="
									String(period?.year) === String(y) && String(period?.period) === p.value
										? 'bg-surface-gray-7 text-ink-white'
										: 'text-ink-gray-7 hover:bg-surface-gray-2'
								"
								@click="pick(y, p.value, close)"
							>{{ p.label }}</button>
						</div>
					</div>
				</div>
			</template>
		</Popover>

		<span class="inline-flex overflow-hidden rounded border border-outline-gray-2">
			<button type="button" :class="stepperClass" :disabled="!canBack" aria-label="Previous period" @click="step(-1)">
				<FeatherIcon name="chevron-left" class="h-3.5 w-3.5" />
			</button>
			<span class="w-px bg-outline-gray-2" aria-hidden="true" />
			<button type="button" :class="stepperClass" :disabled="!canFwd" aria-label="Next period" @click="step(1)">
				<FeatherIcon name="chevron-right" class="h-3.5 w-3.5" />
			</button>
		</span>
	</span>
</template>
