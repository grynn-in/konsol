<script setup>
/**
 * konsol#305 B22: the Checks screen (E7; stories 7.1-7.4).
 *
 * Data:
 * - A26 GET `checks_api.get_checks(fiscal_year, fiscal_period)`, turned into
 *   `{banner, domains, canRun}` by checksView (B13). The screen never
 *   re-decides that text: the stale / running / not-run banner, the
 *   "results are from an earlier run" note and the P4 "No description
 *   declared for <assertion>" line all come from checksView.
 * - A26 POST `checks_api.run_checks`: the ONE Run button (#298 stage 1 had
 *   two same-named Run buttons calling different code). It is rendered only
 *   when the server says `can_run`, and disabled while a run is in progress.
 *   A refusal (for example "A close run is already in progress: ...") is
 *   shown as the server wrote it, split into plain-text lines, never HTML.
 *
 * While the latest run is running, get_checks is polled every 5 s; the timer
 * stops when the run finishes or the screen goes. The period comes from the
 * URL (route.js, D5); nothing is stored in the browser.
 */
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { Button, FeatherIcon } from "frappe-ui";
import LoadState from "../components/LoadState.vue";
import { get, post } from "../api.js";
import { parse } from "../route.js";
import { checksView } from "../checks.js";
import { messageLines } from "../signoff.js";

const GET_CHECKS = "konsol.close.checks_api.get_checks";
const RUN_CHECKS = "konsol.close.checks_api.run_checks";
const POLL_MS = 5_000;

const route = useRoute();
const period = computed(() => {
	const p = parse(`/close${route.path}`);
	return p.error || p.year == null ? null : { year: p.year, period: p.period };
});
const periodName = computed(() =>
	period.value ? `FY${period.value.year} P${String(period.value.period).padStart(2, "0")}` : "this period",
);

const checks = reactive({ status: "loading", payload: null, view: null, error: null, busy: false });
let seq = 0;
let pollTimer = null;

function stopPolling() {
	if (pollTimer) {
		clearTimeout(pollTimer);
		pollTimer = null;
	}
}

async function loadChecks({ quiet = false } = {}) {
	stopPolling();
	if (!period.value) {
		checks.status = "error";
		checks.error = "This address names no period.";
		return;
	}
	const mine = ++seq;
	if (!quiet) checks.status = "loading";
	checks.busy = true;
	try {
		const payload = await get(GET_CHECKS, {
			fiscal_year: period.value.year,
			fiscal_period: period.value.period,
		});
		if (mine !== seq) return;
		// checksView throws on a state it does not know: shown as an error, never as current.
		checks.view = checksView(payload);
		checks.payload = payload;
		checks.error = null;
		checks.status = "ready";
		if (payload.staleness === "running") {
			pollTimer = setTimeout(() => loadChecks({ quiet: true }), POLL_MS);
		}
	} catch (e) {
		if (mine !== seq) return;
		checks.error = e.message;
		checks.status = "error";
	} finally {
		if (mine === seq) checks.busy = false;
	}
}

const running = computed(() => Boolean(checks.payload && checks.payload.staleness === "running"));
const starting = ref(false);
const inProgress = computed(() => starting.value || running.value);
const runError = ref(null);
const runErrorLines = computed(() => {
	const lines = messageLines(runError.value);
	return lines.length ? lines : ["The server gave no reason."];
});

async function runChecks() {
	if (!period.value || inProgress.value) return;
	starting.value = true;
	runError.value = null;
	try {
		await post(RUN_CHECKS, {
			fiscal_year: period.value.year,
			fiscal_period: period.value.period,
		});
	} catch (e) {
		runError.value = e.message;
	} finally {
		starting.value = false;
	}
	await loadChecks({ quiet: true });
}

watch(
	() => (period.value ? `${period.value.year}/${period.value.period}` : null),
	() => {
		runError.value = null;
		checks.payload = null;
		checks.view = null;
		loadChecks();
	},
	{ immediate: true },
);

onBeforeUnmount(() => {
	seq++;
	stopPolling();
});

const latest = computed(() => (checks.payload && checks.payload.latest) || null);
const bannerTone = computed(() => {
	const s = checks.payload && checks.payload.staleness;
	if (s === "stale") return "border-outline-amber-1 bg-surface-amber-1 text-ink-amber-3";
	if (s === "running") return "border-outline-blue-1 bg-surface-blue-1 text-ink-blue-3";
	return "border-outline-gray-2 bg-surface-gray-1 text-ink-gray-7";
});
</script>

<template>
	<div class="mx-auto max-w-4xl px-6 py-6">
		<div class="flex flex-wrap items-start justify-between gap-3">
			<div>
				<h1 class="text-xl font-semibold text-ink-gray-9">Checks</h1>
				<p class="mt-1 text-sm text-ink-gray-6">
					Close checks for {{ periodName }}<template v-if="latest">
						· latest run <code>{{ latest.name }}</code> ({{ latest.status }})</template>
				</p>
			</div>
			<Button
				v-if="checks.view && checks.view.canRun"
				theme="gray"
				variant="solid"
				:loading="inProgress"
				:disabled="inProgress"
				@click="runChecks"
			>
				{{ inProgress ? "Checks running" : "Run checks" }}
			</Button>
		</div>

		<div
			v-if="runError"
			role="alert"
			class="mt-4 rounded border border-outline-red-1 bg-surface-red-1 px-4 py-3"
		>
			<div class="flex items-start gap-3">
				<FeatherIcon name="alert-triangle" class="mt-0.5 h-4 w-4 shrink-0 text-ink-red-3" />
				<div class="min-w-0 flex-1">
					<p class="text-base font-medium text-ink-gray-9">The checks were not started</p>
					<p v-for="(line, i) in runErrorLines" :key="i" class="mt-1 text-base text-ink-gray-7">{{ line }}</p>
				</div>
			</div>
		</div>

		<LoadState
			v-if="checks.status !== 'ready'"
			compact
			:state="checks.status"
			:what="`the checks for ${periodName}`"
			:source="GET_CHECKS"
			:error="checks.error"
			:busy="checks.busy"
			@retry="loadChecks"
		/>

		<template v-else>
			<div
				v-if="checks.view.banner"
				class="mt-4 rounded border px-4 py-3 text-base"
				:class="bannerTone"
				:role="running ? 'status' : null"
			>
				<p>{{ checks.view.banner }}</p>
				<p v-if="checks.payload.staleness_note" class="mt-1 text-sm">{{ checks.payload.staleness_note }}</p>
			</div>

			<p
				v-if="!checks.view.domains.length && checks.payload.results_run"
				class="mt-4 rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-base text-ink-gray-7"
			>
				No failures or warnings in run <code>{{ checks.payload.results_run }}</code>.
			</p>

			<section v-for="domain in checks.view.domains" :key="domain.name" class="mt-6">
				<h2 class="flex items-center gap-2 text-base font-semibold text-ink-gray-9">
					{{ domain.name }}
					<span class="rounded bg-surface-gray-2 px-1.5 text-xs font-medium text-ink-gray-7">{{ domain.count }}</span>
				</h2>
				<ul class="mt-2 divide-y divide-outline-gray-1 rounded border border-outline-gray-2">
					<li v-for="cause in domain.causes" :key="cause.title" class="px-4 py-3">
						<div class="flex items-baseline justify-between gap-3">
							<p class="text-base font-medium text-ink-gray-9">{{ cause.title }}</p>
							<p class="shrink-0 text-sm text-ink-gray-6">{{ cause.rows }} {{ cause.rows === 1 ? "row" : "rows" }}</p>
						</div>
						<p class="mt-1 text-sm text-ink-gray-7">{{ cause.text }}</p>
					</li>
				</ul>
			</section>
		</template>
	</div>
</template>
