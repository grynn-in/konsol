<script setup>
/**
 * The period spine (U8).
 *
 * In the old app the fiscal period was a dropdown inside the Execute form, so
 * nothing above it could be scoped to a period and no screen could answer
 * "where is September". Here it is the first control on the page and it scopes
 * everything below.
 *
 * The right-hand side is the answer to U7: the console used to hide the Frappe
 * chrome entirely while sending people into the desk on every Setup row. It now
 * carries a visible way back.
 */
import { computed } from "vue";
import { Button, Select, FeatherIcon, Tooltip } from "frappe-ui";

const props = defineProps({
	period: { type: Object, default: null },
	options: { type: Object, default: null },
	periodStatus: { type: String, default: "" },
	busy: { type: Boolean, default: false },
	workerHealthy: { type: Boolean, default: true },
});
const emit = defineEmits(["update:period", "refresh"]);

const yearOptions = computed(() =>
	(props.options?.fiscal_years || []).map((y) => ({ label: `FY${y}`, value: String(y) }))
);

const periodOptions = computed(() =>
	(props.options?.fiscal_periods || []).map((p) => ({
		label: p.label || `Period ${p.value}`,
		value: String(p.value),
	}))
);

function setYear(year) {
	emit("update:period", { ...props.period, year });
}
function setPeriod(period) {
	emit("update:period", { ...props.period, period });
}
function toDesk() {
	window.location.href = "/app";
}
</script>

<template>
	<header
		class="sticky top-0 z-10 flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-outline-gray-1 bg-surface-white px-6 py-2.5"
	>
		<RouterLink to="/close" class="flex items-center gap-2 text-ink-gray-9">
			<span class="text-base font-semibold tracking-tight">Konsol</span>
			<span class="text-base text-ink-gray-5">Close</span>
		</RouterLink>

		<div class="mx-1 h-5 w-px bg-outline-gray-2" aria-hidden="true" />

		<!-- The spine: which period are we closing -->
		<div class="flex items-center gap-2">
			<Select
				v-if="yearOptions.length"
				:model-value="period?.year"
				:options="yearOptions"
				size="sm"
				@update:model-value="setYear"
			/>
			<Select
				v-if="periodOptions.length"
				:model-value="period?.period"
				:options="periodOptions"
				size="sm"
				placeholder="Whole year"
				@update:model-value="setPeriod"
			/>
			<!-- Period status needs Fiscal Period.status, which does not exist yet
			     (architecture finding F6). Until it does, say so rather than
			     showing a state the backend cannot vouch for. -->
			<Tooltip
				v-if="!periodStatus"
				text="Period status isn't tracked yet — Fiscal Period has no status field"
			>
				<span class="cursor-help text-sm text-ink-gray-4">status not tracked</span>
			</Tooltip>
			<span v-else class="text-sm text-ink-gray-6">{{ periodStatus }}</span>
		</div>

		<div class="ml-auto flex items-center gap-2">
			<Tooltip v-if="!workerHealthy" text="No background worker is responding — runs will queue but not start">
				<span class="flex items-center gap-1.5 text-sm text-ink-red-3">
					<FeatherIcon name="alert-triangle" class="h-3.5 w-3.5" />
					worker down
				</span>
			</Tooltip>

			<Button variant="ghost" size="sm" :loading="busy" @click="$emit('refresh')">
				<template #icon><FeatherIcon name="refresh-cw" class="h-4 w-4" /></template>
			</Button>

			<Button variant="ghost" size="sm" @click="toDesk">
				<template #suffix><FeatherIcon name="external-link" class="h-3.5 w-3.5" /></template>
				Desk
			</Button>
		</div>
	</header>
</template>
