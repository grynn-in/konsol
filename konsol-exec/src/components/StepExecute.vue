<script setup>
/**
 * Launch plane for one process.
 *
 * The period now comes from the top-bar spine rather than from two dropdowns
 * inside this form (U8) — a launch inherits the period you are closing, which
 * is what the operator meant every time anyway. Scope and definition stay here
 * because they genuinely vary per run.
 */
import { computed, inject, ref, watch } from "vue";
import { useMachine } from "@xstate/vue";
import { Button, Select, Switch, FeatherIcon } from "frappe-ui";
import RangePicker from "./RangePicker.vue";
import StageRail from "./StageRail.vue";
import RunTimeline from "./RunTimeline.vue";
import { runExecMachine } from "../machines/index.js";
import { buildRunArgs, withStageRange } from "../orchestrator/params.js";
import { getDomainMeta } from "../domain.js";

const props = defineProps({ domain: { type: String, required: true } });
const plane = inject("plane");
const meta = computed(() => getDomainMeta(props.domain));
const stages = computed(() => meta.value.stages || []);

/** Only consolidation runs a staged dbt graph, so only it takes a range. */
const rangeable = computed(() => props.domain === "consolidation");

const { snapshot, send } = useMachine(runExecMachine);

const form = ref({ scope: "", definition: "", full_refresh: false });
const from = ref(0);
const to = ref(Math.max(0, stages.value.length - 1));

watch(stages, (s) => {
	from.value = 0;
	to.value = Math.max(0, s.length - 1);
});

const scopeOptions = computed(() => [
	{ label: "All scope", value: "" },
	...(plane.options.value?.scopes || []),
]);
const definitionOptions = computed(() => [
	{ label: "Default pipeline", value: "" },
	...(plane.options.value?.definitions || []).map((d) => ({ label: d, value: d })),
]);

const previewStages = computed(() =>
	stages.value.map((s, i) => ({ ...s, n: i + 1, state: "idle" }))
);

const busy = computed(() => !snapshot.value.matches("idle"));

function launch() {
	const period = plane.period.value || {};
	const { definition, params: base } = buildRunArgs({
		fiscal_year: period.year,
		fiscal_period: period.period,
		scope: form.value.scope,
		definition: form.value.definition,
		full_refresh: form.value.full_refresh,
	});
	const params = rangeable.value
		? withStageRange(base, stages.value, from.value, to.value)
		: base;
	send({ type: "LAUNCH", definition, params });
}
</script>

<template>
	<div class="space-y-5">
		<div>
			<div class="mb-1 text-sm text-ink-gray-6">This run does</div>
			<StageRail :stages="previewStages" />
		</div>

		<RangePicker
			v-if="rangeable"
			:stages="stages"
			:from="from"
			:to="to"
			@update:from="(v) => (from = v)"
			@update:to="(v) => (to = v)"
		/>

		<div class="flex flex-wrap items-end gap-4">
			<label class="block">
				<span class="mb-1 block text-sm text-ink-gray-6">Scope</span>
				<Select v-model="form.scope" :options="scopeOptions" size="sm" />
			</label>
			<label class="block">
				<span class="mb-1 block text-sm text-ink-gray-6">Pipeline</span>
				<Select v-model="form.definition" :options="definitionOptions" size="sm" />
			</label>
			<label class="flex items-center gap-2 pb-1.5">
				<Switch v-model="form.full_refresh" size="sm" />
				<span class="text-sm text-ink-gray-7">Full refresh</span>
			</label>
		</div>

		<div class="flex flex-wrap items-center gap-3 border-t border-outline-gray-1 pt-4">
			<Button theme="gray" variant="solid" size="md" :loading="busy" @click="launch">
				{{ meta.verb }}
				<template #suffix><FeatherIcon name="play" class="h-3.5 w-3.5" /></template>
			</Button>
			<span class="text-sm text-ink-gray-5">
				for {{ plane.periodLabel.value }}{{ form.scope ? ` · ${form.scope}` : "" }}
			</span>
		</div>

		<RunTimeline v-if="snapshot.context.run" :run="snapshot.context.run" @send="send" />
	</div>
</template>
