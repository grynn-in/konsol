<script setup>
/**
 * konsol#305 B17: the shell — header, role-filtered nav, a ⌘K stub and the
 * routed screen, with a designed state for everything that can be missing.
 *
 * Data (all GET, read-only):
 * - A15 `period_api.get_context(year, period)`: who I am, every Regular
 *   period's state, the landing (D5) and the selected period. On /close it
 *   is called with no period, for the landing and the Viewer's provisional
 *   switch.
 * - A16 `freshness_api.get_freshness`: polled every 60 s while the tab is
 *   visible, and at once when it becomes visible again.
 * - A29 `mywork_api.get_my_work().counts`: the nav counts. A count the server
 *   did not send is unknown ("?"), never 0 (B08b).
 *
 * The pure modules do the thinking: nav.js (B08) the screens per persona,
 * freshness.js (B09) the freshness text, route.js (B07) the URL, api.js (B06)
 * the calls, router.js (B16) the landing. The period lives in the URL only
 * (D5): nothing is stored in the browser.
 *
 * Time zone: Frappe's boot (`time_zone.user`) when the page has one, else the
 * browser's own zone. If neither is known, the freshness bar says so rather
 * than guessing one (B09 refuses to run without a zone).
 *
 * /close with no period (B16b): the router stays on "/" and sets
 * `landingState.reason` (first close undeclared, or a Viewer with nothing
 * signed) or `landingState.error` (get_context failed). This shell renders
 * both; a Viewer with nothing signed gets the switch to the provisional period.
 *
 * konsol#305 B31: screens reload the context through CONTEXT_RELOAD (the
 * Checks screen does when a run finishes). That reload is quiet: the screen
 * stays mounted, and a failure is shown in the header as `refreshError`.
 */
import { computed, onBeforeUnmount, onMounted, provide, reactive, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import { Button, FeatherIcon } from "frappe-ui";
import HeaderBar from "./HeaderBar.vue";
import LoadState from "./LoadState.vue";
import { get } from "../api.js";
import { navFor } from "../nav.js";
import { freshnessView } from "../freshness.js";
import { userTimeZone } from "../timefmt.js";
import { format, parse } from "../route.js";
import { landingPath, landingState } from "../router.js";
import { messageLines } from "../signoff.js";
import { CONTEXT_RELOAD } from "../contextRefresh.js";

const CONTEXT = "konsol.close.period_api.get_context";
const FRESHNESS = "konsol.close.freshness_api.get_freshness";
const MY_WORK = "konsol.close.mywork_api.get_my_work";
const FRESHNESS_POLL_MS = 60_000;

const PERSONA_LABELS = {
	close_lead: "Close Lead",
	group_accountant: "Group Accountant",
	entity_accountant: "Entity Accountant",
	viewer: "Viewer",
};

const route = useRoute();
const router = useRouter();

const timeZone = userTimeZone();

function periodName(key) {
	return `FY${key[0]} P${String(key[1]).padStart(2, "0")}`;
}

/** A path under the router's `/close` base, built by route.js. */
function routerPath(year, period, screen) {
	return format({ year, period, screen }).replace(/^\/close/, "");
}

// --- where are we --------------------------------------------------------

const ready = ref(false);
router.isReady().then(
	() => (ready.value = true),
	(e) => {
		landingState.error = e && e.message ? e.message : String(e);
		ready.value = true;
	},
);

const parsed = computed(() => (route.path === "/" ? { year: null } : parse(`/close${route.path}`)));
const current = computed(() =>
	parsed.value.error || parsed.value.year == null ? null : { year: parsed.value.year, period: parsed.value.period },
);

// --- A15 context -----------------------------------------------------------

const context = reactive({ status: "loading", data: null, error: null, refreshError: null });
let contextSeq = 0;

/**
 * `quiet` (B31): a screen asked for fresh data while showing the period. The
 * status stays "ready" so the screen is not unmounted; a failure keeps the
 * old data and is shown in the header as `refreshError`. A quiet request
 * when the context is not ready is an ordinary load.
 */
async function loadContext({ quiet = false } = {}) {
	if (quiet && context.status !== "ready") quiet = false;
	// A malformed address still loads who I am (no period), so the nav and
	// header are known; only the main area names the bad address.
	const p = parsed.value.error ? { year: null, bad: true } : parsed.value;
	const seq = ++contextSeq;
	if (!quiet) context.status = "loading";
	context.error = null;
	context.refreshError = null;
	try {
		const params = p.year == null ? null : { fiscal_year: p.year, fiscal_period: p.period };
		const data = await get(CONTEXT, params);
		if (seq !== contextSeq) return;
		context.data = data;
		context.status = "ready";
		if (p.year == null && !p.bad && !quiet) {
			// On /close: land if there is somewhere to land, else show why not.
			landingState.error = null;
			const target = landingPath(data);
			if (target != null) {
				router.replace(target.replace(/^\/close/, ""));
			} else {
				landingState.reason = data.landing.reason;
			}
		}
	} catch (e) {
		if (seq !== contextSeq) return;
		if (quiet) {
			context.refreshError = e.message;
			return;
		}
		context.error = e.message;
		context.status = "error";
	}
}

provide(CONTEXT_RELOAD, () => loadContext({ quiet: true }));

const landingBusy = ref(false);
async function retryLanding() {
	landingBusy.value = true;
	landingState.error = null;
	try {
		await loadContext();
		if (context.status === "error") landingState.error = context.error;
	} finally {
		landingBusy.value = false;
	}
}

/** The header keeps the period list while a new period loads, but not the old selection. */
const headerContext = computed(() => {
	if (!context.data) return null;
	// A bad address loaded the no-period context: its `selected` is not the URL's period.
	const ok = context.status === "ready" && !parsed.value.error;
	return ok ? context.data : { ...context.data, selected: null };
});

const me = computed(() => (context.data && context.data.me) || null);
const provisional = computed(() => {
	const landing = context.data && context.data.landing;
	return (landing && landing.provisional) || null;
});

// --- A29 counts ------------------------------------------------------------

const myWork = reactive({ status: "loading", counts: null, error: null });
async function loadMyWork() {
	myWork.status = "loading";
	myWork.error = null;
	try {
		const data = await get(MY_WORK);
		myWork.counts = (data && data.counts) || null;
		myWork.status = "ready";
	} catch (e) {
		myWork.counts = null;
		myWork.error = e.message;
		myWork.status = "error";
	}
}

// --- nav (B08) -------------------------------------------------------------

const nav = computed(() => (me.value ? navFor(me.value.persona, me.value.roles, myWork.counts) : []));
const noRole = computed(() => nav.value.length === 1 && nav.value[0].screen == null);
const screens = computed(() => nav.value.filter((item) => item.screen != null));
const screen = computed(() => (parsed.value.error ? null : parsed.value.screen || null));
const screenAllowed = computed(() => screens.value.some((item) => item.screen === screen.value));
const firstScreen = computed(() => (screens.value[0] && screens.value[0].screen) || "my-work");

// --- A16 freshness (B09) -----------------------------------------------------

const freshness = reactive({ payload: null, error: null, busy: false, now: new Date() });
async function loadFreshness() {
	freshness.busy = true;
	try {
		freshness.payload = await get(FRESHNESS);
		freshness.error = null;
	} catch (e) {
		freshness.error = e.message;
	} finally {
		freshness.now = new Date();
		freshness.busy = false;
	}
}
const freshnessDisplay = computed(() => {
	if (freshness.error) return { tone: "red", text: `Freshness unknown: ${freshness.error}`, detail: null };
	if (!freshness.payload) return null;
	if (!timeZone) {
		return { tone: "red", text: "Your browser reported no time zone, so build times cannot be shown", detail: null };
	}
	try {
		return freshnessView(freshness.payload, freshness.now, timeZone);
	} catch (e) {
		return { tone: "red", text: e.message, detail: null };
	}
});

let pollTimer = null;
function stopPolling() {
	if (pollTimer) {
		clearInterval(pollTimer);
		pollTimer = null;
	}
}
function startPolling() {
	stopPolling();
	loadFreshness();
	pollTimer = setInterval(loadFreshness, FRESHNESS_POLL_MS);
}
function onVisibility() {
	if (document.visibilityState === "visible") startPolling();
	else stopPolling();
}

// --- ⌘K stub -----------------------------------------------------------------

const paletteOpen = ref(false);
function onKey(e) {
	if ((e.metaKey || e.ctrlKey) && String(e.key).toLowerCase() === "k") {
		e.preventDefault();
		paletteOpen.value = !paletteOpen.value;
	} else if (e.key === "Escape") {
		paletteOpen.value = false;
	}
}

// --- lifecycle -------------------------------------------------------------

/** Reload when the period changes (not when only the screen changes). */
const periodKey = computed(() => {
	if (!ready.value) return null;
	const p = parsed.value;
	if (p.error) return `error:${p.error}`;
	return p.year == null ? "landing" : `${p.year}/${p.period}`;
});
watch(
	periodKey,
	(key) => {
		if (key == null) return;
		loadContext();
		loadMyWork();
	},
	{ immediate: true },
);

onMounted(() => {
	document.addEventListener("visibilitychange", onVisibility);
	window.addEventListener("keydown", onKey);
	if (document.visibilityState === "visible") startPolling();
});
onBeforeUnmount(() => {
	document.removeEventListener("visibilitychange", onVisibility);
	window.removeEventListener("keydown", onKey);
	stopPolling();
});

/** A refused call (403): the user holds no close role. */
function isRefused(message) {
	return /PermissionError|not permitted/i.test(String(message || ""));
}

/** What the main area shows; every branch is a designed, visible state. */
const view = computed(() => {
	if (!ready.value) return "booting";
	if (route.path === "/") return "landing";
	if (parsed.value.error) return "bad-url";
	if (context.status === "loading") return "loading";
	if (context.status === "error") return "error";
	if (noRole.value) return "no-role";
	if (!context.data.selected) return "no-state";
	if (!screenAllowed.value) return "not-your-screen";
	return "screen";
});

const showProvisionalBar = computed(
	() =>
		view.value === "screen" &&
		me.value &&
		me.value.persona === "viewer" &&
		provisional.value &&
		provisional.value.period &&
		current.value &&
		(provisional.value.period[0] !== current.value.year || provisional.value.period[1] !== current.value.period),
);

function goProvisional() {
	const [year, period] = provisional.value.period;
	router.push(routerPath(year, period, screen.value || firstScreen.value));
}
</script>

<template>
	<div class="flex min-h-screen flex-col bg-surface-white font-sans text-ink-gray-9">
		<HeaderBar
			:context="headerContext"
			:current="current"
			:screen="screen || firstScreen"
			:freshness="freshnessDisplay"
			:freshness-busy="freshness.busy"
			:refresh-error="context.refreshError"
		/>

		<div class="flex flex-1 flex-col md:flex-row">
			<aside class="border-b border-outline-gray-2 bg-surface-gray-1 md:w-60 md:shrink-0 md:border-b-0 md:border-r">
				<nav aria-label="Close screens" class="p-2">
					<div v-if="!me && context.status !== 'error'" class="animate-pulse space-y-2 p-2" role="status">
						<div class="h-4 w-32 rounded bg-surface-gray-3" />
						<div class="h-4 w-24 rounded bg-surface-gray-3" />
						<p class="pt-1 text-xs text-ink-gray-5">Fetching your screens</p>
					</div>
					<p v-else-if="!me" class="p-2 text-sm text-ink-gray-6">
						Your screens are unknown until the period loads.
					</p>

					<ul v-else class="space-y-0.5">
						<li v-for="item in nav" :key="item.screen || 'no-role'">
							<p v-if="item.screen == null" class="rounded px-2.5 py-2 text-sm font-medium text-ink-red-3">
								{{ item.label }}
							</p>
							<component
								:is="current ? RouterLink : 'span'"
								v-else
								v-bind="current ? { to: routerPath(current.year, current.period, item.screen) } : { 'aria-disabled': 'true', title: 'Choose a period first' }"
								class="flex items-center justify-between rounded px-2.5 py-2 text-sm"
								:class="[
									item.screen === screen ? 'bg-surface-white font-medium text-ink-gray-9 shadow-sm' : 'text-ink-gray-7',
									current ? 'hover:bg-surface-gray-2' : 'cursor-not-allowed opacity-60',
								]"
							>
								<span>{{ item.label }}</span>
								<span
									v-if="myWork.status === 'loading'"
									class="h-4 w-5 animate-pulse rounded bg-surface-gray-3"
									title="Counting"
								><span class="sr-only">count loading</span></span>
								<span
									v-else-if="item.count == null"
									class="rounded bg-surface-gray-2 px-1.5 text-xs text-ink-gray-5"
									title="Count unknown"
								>?<span class="sr-only"> count unknown</span></span>
								<span
									v-else
									class="rounded px-1.5 text-xs font-medium"
									:class="item.blocking ? 'bg-surface-red-2 text-ink-red-3' : 'bg-surface-gray-2 text-ink-gray-7'"
									:title="item.blocking ? `${item.count} items, some blocking` : `${item.count} items`"
								>{{ item.count }}</span>
							</component>
						</li>
					</ul>

					<div v-if="myWork.status === 'error'" role="alert" class="mt-2 rounded px-2.5 py-2 text-xs text-ink-red-3">
						Counts unknown: {{ myWork.error }}
						<button type="button" class="ml-1 underline" @click="loadMyWork">Retry</button>
					</div>

					<button
						type="button"
						class="mt-3 flex w-full items-center justify-between rounded border border-outline-gray-2 bg-surface-white px-2.5 py-1.5 text-sm text-ink-gray-5 hover:text-ink-gray-8"
						aria-haspopup="dialog"
						@click="paletteOpen = true"
					>
						<span class="flex items-center gap-2"><FeatherIcon name="search" class="h-3.5 w-3.5" />Search</span>
						<kbd class="text-xs">⌘K</kbd>
					</button>
				</nav>
			</aside>

			<main class="min-w-0 flex-1">
				<LoadState v-if="view === 'booting'" state="loading" what="your landing period" :source="CONTEXT" />

				<template v-else-if="view === 'landing'">
					<template v-if="landingState.error">
						<LoadState
							state="error"
							what="your landing period"
							:source="CONTEXT"
							:error="landingState.error"
							:busy="landingBusy"
							@retry="retryLanding"
						/>
						<p v-if="isRefused(landingState.error)" class="mx-auto -mt-6 max-w-3xl px-6 text-sm text-ink-gray-7">
							The server refused the request: you may hold no close role. Ask the System Manager for one of
							EPM User (Viewer), Entity Accountant, EPM Analyst or EPM Admin.
						</p>
					</template>
					<div v-else-if="landingState.reason" class="mx-auto max-w-3xl px-6 py-10">
						<div class="rounded border border-outline-gray-2 bg-surface-gray-1 px-5 py-5">
							<h1 class="text-lg font-semibold text-ink-gray-9">No period to open yet</h1>
							<p v-for="(line, i) in messageLines(landingState.reason)" :key="i" class="mt-2 text-base text-ink-gray-7">{{ line }}</p>

							<LoadState
								v-if="context.status !== 'ready'"
								compact
								:state="context.status"
								what="the provisional period"
								:source="CONTEXT"
								:error="context.error"
								@retry="loadContext"
							/>
							<template v-else-if="provisional">
								<Button
									v-if="provisional.period"
									class="mt-4"
									theme="gray"
									variant="solid"
									@click="router.push(routerPath(provisional.period[0], provisional.period[1], firstScreen))"
								>
									Switch to the provisional period {{ periodName(provisional.period) }}
								</Button>
								<p v-else class="mt-4 text-sm text-ink-gray-6">
									No provisional period either: {{ provisional.reason || "the server gave no reason" }}
								</p>
							</template>
							<p class="mt-4 text-sm text-ink-gray-5">You can also choose any period in the header.</p>
						</div>
					</div>
					<LoadState v-else state="loading" what="your landing period" :source="CONTEXT" />
				</template>

				<LoadState v-else-if="view === 'bad-url'" state="error" what="this address" :error="parsed.error">
					<template #error>
						<div class="rounded border border-outline-red-1 bg-surface-red-1 px-4 py-4">
							<p class="text-base font-medium text-ink-gray-9">This address is not a close page</p>
							<p class="mt-1 text-base text-ink-gray-7">
								<code>/close{{ route.path }}</code>: {{ parsed.error }}. Addresses look like <code>/close/2025/7/my-work</code>.
							</p>
							<Button class="mt-3" theme="gray" variant="solid" @click="router.push('/')">Go to your landing period</Button>
						</div>
					</template>
				</LoadState>

				<LoadState
					v-else-if="view === 'loading'"
					state="loading"
					:what="current ? `the period ${periodName([current.year, current.period])}` : 'the period'"
					:source="CONTEXT"
				/>
				<template v-else-if="view === 'error'">
					<LoadState
						state="error"
						:what="current ? `the period ${periodName([current.year, current.period])}` : 'the period'"
						:source="CONTEXT"
						:error="context.error"
						@retry="loadContext"
					/>
					<p v-if="isRefused(context.error)" class="mx-auto -mt-6 max-w-3xl px-6 text-sm text-ink-gray-7">
						The server refused the request: you may hold no close role. Ask the System Manager for one of
						EPM User (Viewer), Entity Accountant, EPM Analyst or EPM Admin.
					</p>
				</template>

				<div v-else-if="view === 'no-role'" class="mx-auto max-w-3xl px-6 py-10">
					<div class="rounded border border-outline-amber-1 bg-surface-amber-1 px-5 py-5 text-ink-amber-3">
						<h1 class="text-lg font-semibold">You have no close role</h1>
						<p class="mt-2 text-base">
							Ask the System Manager for one of EPM User (Viewer), Entity Accountant, EPM Analyst or EPM Admin.
						</p>
					</div>
				</div>

				<LoadState
					v-else-if="view === 'no-state'"
					state="empty"
					:what="`the period ${periodName([current.year, current.period])}`"
					:empty-text="`${periodName([current.year, current.period])} has no close state. Choose a Regular period in the header.`"
				/>

				<div v-else-if="view === 'not-your-screen'" class="mx-auto max-w-3xl px-6 py-10">
					<div class="rounded border border-outline-gray-2 bg-surface-gray-1 px-5 py-5">
						<h1 class="text-lg font-semibold text-ink-gray-9">Not one of your screens</h1>
						<p class="mt-2 text-base text-ink-gray-7">
							The {{ PERSONA_LABELS[me.persona] || me.persona }} does not see
							<code>{{ route.params.screen }}</code>. Your screens for {{ periodName([current.year, current.period]) }}:
						</p>
						<ul class="mt-3 flex flex-wrap gap-2">
							<li v-for="item in screens" :key="item.screen">
								<RouterLink
									:to="routerPath(current.year, current.period, item.screen)"
									class="rounded border border-outline-gray-2 bg-surface-white px-3 py-1.5 text-sm text-ink-blue-3 hover:bg-surface-gray-2"
								>{{ item.label }}</RouterLink>
							</li>
						</ul>
					</div>
				</div>

				<template v-else>
					<div
						v-if="showProvisionalBar"
						class="flex flex-wrap items-center gap-2 border-b border-outline-blue-1 bg-surface-blue-1 px-4 py-2 text-sm text-ink-blue-3"
					>
						You are viewing a signed-off period.
						<button type="button" class="font-medium underline" @click="goProvisional">
							Switch to the provisional period {{ periodName(provisional.period) }}
						</button>
					</div>
					<router-view />
				</template>
			</main>
		</div>

		<div
			v-if="paletteOpen"
			class="fixed inset-0 z-50 flex items-start justify-center bg-black/30 pt-24"
			@click.self="paletteOpen = false"
		>
			<div role="dialog" aria-modal="true" aria-labelledby="palette-title" class="w-full max-w-md rounded-lg bg-surface-white p-5 shadow-xl">
				<h2 id="palette-title" class="text-base font-semibold text-ink-gray-9">Search is not built yet</h2>
				<p class="mt-2 text-sm text-ink-gray-7">
					The command palette comes in a later delivery. Use the screens on the left and the period picker in the header.
				</p>
				<Button class="mt-4" theme="gray" variant="subtle" @click="paletteOpen = false">Close</Button>
			</div>
		</div>
	</div>
</template>
