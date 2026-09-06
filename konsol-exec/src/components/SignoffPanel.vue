<script setup>
/**
 * Step 6 — sign off.
 *
 * This step cannot work yet and says so plainly rather than showing an
 * invented state. Closing a period means setting Fiscal Period to Closed, and
 * Fiscal Period currently has four fields — period number, label, quarter,
 * half — with no status, no dates and no fiscal year. That is finding F6 in the
 * architecture review; until it lands, this panel is a placeholder that
 * explains itself.
 */
import { computed, inject } from "vue";
import { FeatherIcon } from "frappe-ui";
import { getProcess } from "../domain.js";

const plane = inject("plane");
const assertionsPassed = computed(() => getProcess(plane.data.value, "assertions")?.machine_status === "done");
const tracked = computed(() => Boolean(plane.data.value?.period?.status));
</script>

<template>
	<div class="rounded border border-outline-gray-1 bg-surface-white px-5 py-5">
		<div v-if="!tracked" class="flex gap-3">
			<FeatherIcon name="info" class="mt-0.5 h-5 w-5 shrink-0 text-ink-gray-5" />
			<div>
				<p class="text-base text-ink-gray-9">Period status isn't tracked yet.</p>
				<p class="mt-1 text-base text-ink-gray-6">
					Signing off means setting the period to Closed, but Fiscal Period has no
					status field — so nothing can be closed and nothing can be rejected against
					a closed period. Adding <code class="rounded bg-surface-gray-2 px-1 py-0.5">status</code>,
					<code class="rounded bg-surface-gray-2 px-1 py-0.5">start_date</code>,
					<code class="rounded bg-surface-gray-2 px-1 py-0.5">end_date</code> and
					<code class="rounded bg-surface-gray-2 px-1 py-0.5">fiscal_year</code>
					to that DocType is what makes this step real.
				</p>
				<p class="mt-3 text-sm text-ink-gray-5">
					{{ assertionsPassed
						? "Close assertions have passed, so the work this step gates is done."
						: "Close assertions have not passed yet." }}
				</p>
			</div>
		</div>
		<div v-else class="text-base text-ink-gray-9">
			Period is {{ plane.data.value.period.status }}.
		</div>
	</div>
</template>
