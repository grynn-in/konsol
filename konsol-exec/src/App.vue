<script setup>
/**
 * Shell. Owns the machine, provides it to the tree, and renders the router.
 *
 * The old App.jsx also owned navigation — parsing and writing `location.hash`
 * across two effects with a `didHydrate` ref to stop them fighting on mount.
 * vue-router does that now, and the machine is left with the job it was good
 * at: the data lifecycle.
 */
import { computed, provide, watch } from "vue";
import { useRouter } from "vue-router";
import { useMachine } from "@xstate/vue";
import { FrappeUIProvider, Toast, toast } from "frappe-ui";
import TopBar from "./components/TopBar.vue";
import AppSkeleton from "./components/AppSkeleton.vue";
import ErrorState from "./components/ErrorState.vue";
import { closeMachine } from "./machines/index.js";

const router = useRouter();
const { snapshot, send } = useMachine(closeMachine);

const data = computed(() => snapshot.value.context.data);
const options = computed(() => snapshot.value.context.options);
const period = computed(() => snapshot.value.context.period);
const loadError = computed(() => snapshot.value.context.loadError);

const periodLabel = computed(() => {
	const p = period.value;
	if (!p) return "this period";
	const match = (options.value?.fiscal_periods || []).find((o) => String(o.value) === String(p.period));
	return match ? `${match.label} FY${p.year}` : `FY${p.year}`;
});

provide("plane", { data, options, period, periodLabel, send, router });

/** Machine toasts surface through frappe-ui's toast, then clear. */
watch(
	() => snapshot.value.context.toast,
	(t) => {
		if (!t) return;
		toast({ title: t.text, variant: t.theme === "red" ? "error" : "success" });
		send({ type: "DISMISS_TOAST" });
	}
);

const loading = computed(() => snapshot.value.matches("loading"));
const failed = computed(() => snapshot.value.matches("failed"));
const busy = computed(() => snapshot.value.matches("refreshing") || loading.value);
</script>

<template>
	<FrappeUIProvider>
		<div class="min-h-screen bg-surface-white">
			<Toast />
			<AppSkeleton v-if="loading" />
			<ErrorState v-else-if="failed" :error="loadError" :busy="busy" @retry="send({ type: 'RETRY' })" />
			<template v-else>
				<TopBar
					:period="period"
					:options="options"
					:period-status="data?.period?.status || ''"
					:busy="busy"
					:worker-healthy="data?.worker_healthy !== false"
					@update:period="(p) => send({ type: 'SET_PERIOD', year: p.year, period: p.period })"
					@refresh="send({ type: 'REFRESH' })"
				/>
				<RouterView />
			</template>
		</div>
	</FrappeUIProvider>
</template>
