<script setup>
/**
 * One close step. Process steps keep the Setup / Execute / Monitor / History
 * tabs from the previous app — that structure worked; only its location in the
 * hierarchy changed. Gate steps (readiness, sign-off) have no run of their own,
 * so they render a single panel instead of tabs.
 */
import { computed, inject } from "vue";
import { Breadcrumbs, TabButtons } from "frappe-ui";
import StatusBadge from "./StatusBadge.vue";
import StepSetup from "./StepSetup.vue";
import StepExecute from "./StepExecute.vue";
import StepMonitor from "./StepMonitor.vue";
import StepHistory from "./StepHistory.vue";
import ReadinessPanel from "./ReadinessPanel.vue";
import SignoffPanel from "./SignoffPanel.vue";
import { STEP_TABS } from "../constants.js";
import { closeSteps, getDomainMeta } from "../domain.js";

const props = defineProps({
	step: { type: String, required: true },
	tab: { type: String, default: "setup" },
});

const plane = inject("plane");

const stepModel = computed(() => closeSteps(plane.data.value).find((s) => s.id === props.step));
const isProcess = computed(() => stepModel.value?.kind === "process");
const meta = computed(() => (isProcess.value ? getDomainMeta(props.step) : null));
const activeTab = computed(() => (STEP_TABS.some((t) => t.id === props.tab) ? props.tab : "setup"));

const crumbs = computed(() => [
	{ label: "Close", route: { path: "/close" } },
	{ label: stepModel.value?.label || props.step },
]);

const tabButtons = computed(() =>
	STEP_TABS.map((t) => ({ label: t.id === "execute" ? meta.value.verb : t.label, value: t.id }))
);

function setTab(tab) {
	plane.router.push(`/close/${props.step}/${tab}`);
}
</script>

<template>
	<div class="mx-auto max-w-4xl px-6 py-6">
		<Breadcrumbs :items="crumbs" class="mb-4" />

		<div v-if="stepModel" class="mb-6 flex flex-wrap items-start justify-between gap-3">
			<div>
				<!-- U3: name first, at heading size. Description below, in body text. -->
				<h1 class="text-2xl font-semibold tracking-tight text-ink-gray-9">
					{{ stepModel.label }}
				</h1>
				<p class="mt-1 max-w-xl text-base text-ink-gray-6">
					{{ meta?.desc || stepModel.blurb }}
				</p>
			</div>
			<StatusBadge :state="stepModel.state" size="lg" />
		</div>

		<ReadinessPanel v-if="step === 'readiness'" />
		<SignoffPanel v-else-if="step === 'signoff'" />

		<template v-else-if="isProcess">
			<TabButtons
				:buttons="tabButtons"
				:model-value="activeTab"
				class="mb-5"
				@update:model-value="setTab"
			/>
			<StepSetup v-if="activeTab === 'setup'" :domain="step" />
			<StepExecute v-else-if="activeTab === 'execute'" :domain="step" />
			<StepMonitor v-else-if="activeTab === 'monitor'" :domain="step" />
			<StepHistory v-else :domain="step" />
		</template>

		<p v-else class="text-base text-ink-gray-6">This step is not reported by the control plane.</p>
	</div>
</template>
