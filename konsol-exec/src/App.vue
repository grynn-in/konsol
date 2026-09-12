<script setup>
/**
 * The workspace shell (F7, 12 Sep 2026): a title bar with the path, the
 * fiscal navigator on the left, the page in the middle, a status bar at the
 * bottom. Nothing chooses the period but the navigator and the URL.
 *
 * Two machines: `home` (who is looking, the fiscal tree, one month) drives
 * the shell and the month view; `plane` (the close snapshot) still drives the
 * close steps. When the route names a period, both are told, so a step opened
 * from the lane shows the same month the navigator has selected.
 */
import { computed, provide, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useMachine } from "@xstate/vue";
import { FrappeUIProvider, Toast, toast } from "frappe-ui";
import ShellTitleBar from "./components/ShellTitleBar.vue";
import Navigator from "./components/Navigator.vue";
import StatusBar from "./components/StatusBar.vue";
import AppSkeleton from "./components/AppSkeleton.vue";
import ErrorState from "./components/ErrorState.vue";
import { closeMachine } from "./machines/index.js";
import { homeMachine } from "./machines/homeMachine.js";
import { formatPeriod } from "./period.js";
import { closeSteps } from "./domain.js";
import { crumbsFor, openCount, parsePeriodRoute } from "./home.js";

const router = useRouter();
const route = useRoute();

// ── close plane (the existing close steps) ──
const { snapshot, send } = useMachine(closeMachine);
const data = computed(() => snapshot.value.context.data);
const options = computed(() => snapshot.value.context.options);
const period = computed(() => snapshot.value.context.period);
const loadError = computed(() => snapshot.value.context.loadError);
const periodLabel = computed(() => formatPeriod(period.value, options.value) || "this period");
provide("plane", { data, options, period, periodLabel, send, router });

watch(
	() => snapshot.value.context.toast,
	(t) => {
		if (!t) return;
		toast({ title: t.text, variant: t.theme === "red" ? "error" : "success" });
		send({ type: "DISMISS_TOAST" });
	}
);

const planeLoading = computed(() => snapshot.value.matches("loading"));
const planeFailed = computed(() => snapshot.value.matches("failed"));

// ── workspace ──
const { snapshot: homeSnap, send: homeSend } = useMachine(homeMachine);
const me = computed(() => homeSnap.value.context.me);
const tree = computed(() => homeSnap.value.context.tree);
const month = computed(() => homeSnap.value.context.month);
const homeError = computed(() => homeSnap.value.context.error);
const homeFailed = computed(() => homeSnap.value.matches("failed"));
provide("home", { me, tree, month, error: homeError, send: homeSend, router });

const isMonth = computed(() => route.name === "month");

/** The period the shell is showing: the URL on a month page, the plane's on a step. */
const selected = computed(() => {
	if (isMonth.value) return parsePeriodRoute(route.params);
	const p = period.value;
	return p?.year && p?.period !== "" && p?.period != null ? { year: Number(p.year), period: Number(p.period) } : null;
});

// Route → both machines. Re-run when the plane finishes loading, because the
// plane ignores SET_PERIOD until it is ready.
watch(
	() => [route.name, route.params.year, route.params.period, planeLoading.value],
	() => {
		if (!isMonth.value) return;
		const want = parsePeriodRoute(route.params);
		if (!want) return;
		homeSend({ type: "OPEN", year: want.year, period: want.period });
		const cur = period.value;
		if (!planeLoading.value && (String(cur?.year) !== String(want.year) || String(cur?.period) !== String(want.period))) {
			send({ type: "SET_PERIOD", year: String(want.year), period: String(want.period) });
		}
	},
	{ immediate: true }
);

const stepLabel = computed(() => {
	const id = route.params.step;
	return id ? closeSteps(data.value).find((s) => s.id === id)?.label : null;
});
const crumbs = computed(() =>
	crumbsFor({ name: route.name, year: selected.value?.year, period: selected.value?.period, stepLabel: stepLabel.value })
);

const mineCount = computed(() => {
	const s = selected.value;
	const m = month.value;
	return m && s && m.period.fiscal_year === s.year && m.period.fiscal_period === s.period ? openCount(m.mine) : 0;
});

const busy = computed(() => snapshot.value.matches("refreshing") || homeSnap.value.matches({ ready: "loading" }));
const workerHealthy = computed(() => (month.value?.health ? month.value.health.worker : data.value?.worker_healthy !== false));

function refresh() {
	send({ type: "REFRESH" });
	homeSend({ type: "REFRESH" });
}
</script>

<template>
	<FrappeUIProvider>
		<div class="flex h-screen flex-col bg-surface-white">
			<Toast />
			<ShellTitleBar :me="me" :crumbs="crumbs" :busy="busy" :worker-healthy="workerHealthy" @refresh="refresh" />
			<div class="flex min-h-0 flex-1 flex-col md:flex-row">
				<Navigator
					class="max-h-64 shrink-0 md:max-h-none md:w-64"
					:tree="tree"
					:selected="selected"
					:mine-count="mineCount"
					:me="me"
				/>
				<main class="min-w-0 flex-1 overflow-y-auto">
					<ErrorState
						v-if="homeFailed"
						:error="homeError"
						:busy="false"
						@retry="homeSend({ type: 'RETRY' })"
					/>
					<RouterView v-else-if="isMonth" />
					<AppSkeleton v-else-if="planeLoading" />
					<ErrorState v-else-if="planeFailed" :error="loadError" :busy="false" @retry="send({ type: 'RETRY' })" />
					<RouterView v-else />
				</main>
			</div>
			<StatusBar :health="month?.health" />
		</div>
	</FrappeUIProvider>
</template>
