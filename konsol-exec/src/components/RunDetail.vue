<script setup>
/**
 * Drill-down for one run: its steps, its console output, and the desk documents
 * it touched.
 *
 * Driven by runDetailMachine, which is unchanged from the React app — it
 * already modelled loading / ready / error plus reselect-while-loading, and
 * none of that is view-layer concern.
 */
import { computed } from "vue";
import { Badge, Button, FeatherIcon, Spinner } from "frappe-ui";
import StatusBadge from "./StatusBadge.vue";
import { roleLabel } from "../domain.js";
import { openDoc } from "../api.js";

const props = defineProps({
	detail: { type: Object, default: null },
	loading: { type: Boolean, default: false },
	error: { type: [Object, String], default: null },
});
const emit = defineEmits(["retry", "close"]);

const run = computed(() => props.detail?.run || {});
const steps = computed(() => run.value.steps || []);
const logs = computed(() => run.value.logs || []);
const related = computed(() => props.detail?.related_docs || []);

/** Log levels get the same semantic colours as everything else (U1). */
const logClass = {
	error: "text-ink-red-3",
	warn: "text-ink-amber-3",
	ok: "text-ink-green-3",
};

const stepRing = {
	done: "border-outline-green-2 bg-surface-green-2 text-ink-green-3",
	running: "border-outline-blue-2 bg-surface-blue-2 text-ink-blue-3",
	error: "border-outline-red-2 bg-surface-red-2 text-ink-red-3",
	pending: "border-outline-gray-2 bg-surface-gray-2 text-ink-gray-5",
};
</script>

<template>
	<div class="mt-3 rounded border border-outline-gray-2 bg-surface-white">
		<div v-if="loading" class="flex items-center gap-2 px-5 py-6 text-base text-ink-gray-6">
			<Spinner class="h-4 w-4" /> Loading run…
		</div>

		<div v-else-if="error" class="px-5 py-6">
			<p class="mb-3 text-base text-ink-red-3">
				Couldn't load this run: {{ typeof error === "string" ? error : error?.message }}
			</p>
			<Button variant="subtle" size="sm" @click="emit('retry')">Retry</Button>
		</div>

		<template v-else-if="detail">
			<div class="flex flex-wrap items-center gap-3 border-b border-outline-gray-1 px-5 py-3">
				<span class="tnum text-base font-medium text-ink-gray-9">{{ detail.id }}</span>
				<StatusBadge :state="detail.status || 'idle'" />
				<span class="text-sm text-ink-gray-5">{{ detail.kind }} · {{ detail.by }}</span>
				<Button class="ml-auto" variant="ghost" size="sm" @click="emit('close')">
					<template #icon><FeatherIcon name="x" class="h-4 w-4" /></template>
				</Button>
			</div>

			<dl class="grid grid-cols-3 gap-px border-b border-outline-gray-1 bg-outline-gray-1">
				<div v-for="f in [
					{ k: 'Period', v: detail.period },
					{ k: 'Started', v: detail.started },
					{ k: 'Duration', v: detail.duration },
				]" :key="f.k" class="bg-surface-white px-5 py-3">
					<dt class="text-sm text-ink-gray-5">{{ f.k }}</dt>
					<dd class="tnum mt-0.5 text-base text-ink-gray-9">{{ f.v || "—" }}</dd>
				</div>
			</dl>

			<div v-if="related.length" class="border-b border-outline-gray-1 px-5 py-3">
				<div class="mb-2 text-sm text-ink-gray-5">Related documents</div>
				<div
					v-for="(doc, i) in related"
					:key="`${doc.doctype}-${doc.name}-${i}`"
					class="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5"
				>
					<span class="w-28 shrink-0 text-sm text-ink-gray-5">{{ roleLabel(doc.role) }}</span>
					<span class="text-base text-ink-gray-9">{{ doc.doctype }}</span>
					<span class="tnum text-sm text-ink-gray-6">{{ doc.name }}</span>
					<Button variant="ghost" size="sm" @click="openDoc(doc.doctype, doc.name)">
						Open
						<template #suffix><FeatherIcon name="external-link" class="h-3.5 w-3.5" /></template>
					</Button>
				</div>
			</div>

			<div v-if="steps.length" class="border-b border-outline-gray-1 px-5 py-3">
				<div class="mb-2 text-sm text-ink-gray-5">Steps</div>
				<div v-for="(s, i) in steps" :key="`${s.num}-${i}`" class="py-1.5">
					<div class="flex flex-wrap items-center gap-x-3">
						<span
							class="tnum grid h-5 w-5 shrink-0 place-items-center rounded-full border text-xs"
							:class="stepRing[s.state] || stepRing.pending"
						>
							<Spinner v-if="s.state === 'running'" class="h-2.5 w-2.5" />
							<FeatherIcon v-else-if="s.state === 'done'" name="check" class="h-2.5 w-2.5" />
							<FeatherIcon v-else-if="s.state === 'error'" name="x" class="h-2.5 w-2.5" />
							<template v-else>{{ s.num }}</template>
						</span>
						<span class="text-base text-ink-gray-9">{{ s.name }}</span>
						<span v-if="s.detail" class="text-sm text-ink-gray-5">{{ s.detail }}</span>
						<span v-if="s.rows" class="tnum ml-auto text-sm text-ink-gray-5">{{ s.rows }}</span>
						<span v-if="s.pct" class="tnum text-sm text-ink-gray-5">{{ s.pct }}%</span>
					</div>
					<p v-if="s.error" class="ml-8 mt-1 text-sm text-ink-red-3">{{ s.error }}</p>
				</div>
			</div>

			<div class="px-5 py-3">
				<div class="mb-2 text-sm text-ink-gray-5">Console</div>
				<div
					v-if="logs.length"
					class="max-h-64 overflow-auto rounded bg-surface-gray-2 p-3 font-mono text-xs leading-relaxed"
				>
					<div v-for="(l, i) in logs" :key="i">
						<span class="text-ink-gray-5">{{ l.t }} </span>
						<span :class="logClass[l.level] || 'text-ink-gray-7'">{{ l.text }}</span>
					</div>
				</div>
				<p v-else class="text-sm text-ink-gray-5">No log output for this run.</p>
			</div>
		</template>
	</div>
</template>
