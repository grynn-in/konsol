<script setup>
/**
 * Step 6 — sign off the period.
 *
 * Closing means writing a `Period Status` record for this (fiscal year, fiscal
 * period). It is deliberately not a field on `Fiscal Period`: that DocType is a
 * template of fourteen records reused by every year, so a status there would
 * make closing one September close all of them.
 *
 * Closed stops new runs. Locked additionally refuses being reopened by anyone
 * below a System Manager — the difference between "we have finished" and "do
 * not touch this again". Both are enforced server-side, so the buttons here
 * are a convenience, not the control.
 */
import { computed, inject, ref } from "vue";
import { Button, FeatherIcon, Tooltip } from "frappe-ui";
import StatusBadge from "./StatusBadge.vue";
import { getProcess } from "../domain.js";
import { formatPeriod } from "../period.js";
import { setPeriodStatus } from "../api.js";

const plane = inject("plane");

const assertionsPassed = computed(
	() => getProcess(plane.data.value, "assertions")?.machine_status === "done"
);
const status = computed(() => plane.data.value?.period?.status || "Open");
const label = computed(() => formatPeriod(plane.period.value, plane.options.value));

const busy = ref("");
const failure = ref("");

async function move(to) {
	const p = plane.period.value;
	if (!p?.period) return;
	busy.value = to;
	failure.value = "";
	try {
		await setPeriodStatus(p.year, p.period, to);
		plane.send({ type: "REFRESH" });
	} catch (e) {
		failure.value = e?.message || String(e);
	} finally {
		busy.value = "";
	}
}

const badgeState = computed(
	() => ({ Open: "idle", Closed: "done", Locked: "done" }[status.value] || "idle")
);
</script>

<template>
	<div class="rounded border border-outline-gray-1 bg-surface-white">
		<div class="flex flex-wrap items-center gap-3 border-b border-outline-gray-1 px-5 py-4">
			<StatusBadge :state="badgeState" :label="status" />
			<span class="text-base text-ink-gray-7">{{ label }}</span>
		</div>

		<div class="px-5 py-4">
			<p v-if="status === 'Open'" class="mb-4 text-base text-ink-gray-6">
				{{ assertionsPassed
					? "Close assertions have passed. Closing stops new runs against this period."
					: "Close assertions have not passed yet. You can still close the period, but the numbers have not been proven." }}
			</p>
			<p v-else-if="status === 'Closed'" class="mb-4 text-base text-ink-gray-6">
				<template v-if="plane.data.value?.period?.closed_by">
					Closed by {{ plane.data.value.period.closed_by }}<template
						v-if="plane.data.value.period.closed_on"
					>, {{ plane.data.value.period.closed_on.slice(0, 16) }}</template>.
				</template>
				New runs against this period are refused. It can be reopened, or locked to
				prevent that.
			</p>
			<p v-else class="mb-4 text-base text-ink-gray-6">
				Locked. Only a System Manager can reopen this period.
			</p>

			<p v-if="failure" class="mb-3 text-sm text-ink-red-3">{{ failure }}</p>

			<div class="flex flex-wrap gap-2">
				<Tooltip v-if="status === 'Open'" :text="assertionsPassed ? '' : 'Assertions have not passed'">
					<Button
						theme="gray"
						variant="solid"
						:loading="busy === 'Closed'"
						@click="move('Closed')"
					>
						<template #suffix><FeatherIcon name="check" class="h-3.5 w-3.5" /></template>
						Close {{ label }}
					</Button>
				</Tooltip>

				<Button
					v-if="status === 'Closed'"
					variant="subtle"
					:loading="busy === 'Open'"
					@click="move('Open')"
				>Reopen</Button>

				<Button
					v-if="status === 'Closed'"
					variant="subtle"
					:loading="busy === 'Locked'"
					@click="move('Locked')"
				>
					<template #prefix><FeatherIcon name="lock" class="h-3.5 w-3.5" /></template>
					Lock
				</Button>

				<Button
					v-if="status === 'Locked'"
					variant="subtle"
					:loading="busy === 'Open'"
					@click="move('Open')"
				>Reopen</Button>
			</div>
		</div>
	</div>
</template>
