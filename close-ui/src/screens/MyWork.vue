<script setup>
/**
 * konsol#305 B18: the My work screen (stories 1.1-1.4, 0.4).
 *
 * Every item the caller owns across every open period (A29 `get_my_work`),
 * in three groups: blocking, to do, waiting on others. The pure modules do
 * the thinking:
 * - myWork.js (B10) `sections` groups the items and `itemRoute` gives each
 *   one its action. The server ranks the items (A20 `rank`); this screen
 *   never re-sorts them, and it always draws all three groups, an empty one
 *   with its own text.
 * - LoadState (B17) draws the loading and error states; api.js (B06) makes
 *   the calls; route.js (B07) reads the period from the URL.
 *
 * Actions (A29 / story 0.4): a period item opens an in-app screen for ITS
 * period (not the one in the URL). Only a configuration gap opens the Desk,
 * in a new tab, marked as Desk.
 *
 * An Entity Accountant with no entity assigned (A25 note): `my_tbs` for the
 * URL period returns `entities: []`, and the screen says so rather than
 * showing three empty groups that read as "all done".
 *
 * Age: A29's items carry no date, so no age is shown; guessing one from the
 * period would be a number the server did not send.
 */
import { computed, reactive, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { Badge, FeatherIcon } from "frappe-ui";
import LoadState from "../components/LoadState.vue";
import { get } from "../api.js";
import { itemRoute, sections } from "../myWork.js";
import { parse } from "../route.js";

const MY_WORK = "konsol.close.mywork_api.get_my_work";
const CONTEXT = "konsol.close.period_api.get_context";
const MY_TBS = "konsol.close.tb_read_api.my_tbs";

/** Owner roles (A20 `OWNERS`, plus System Manager for the accountants gap). */
const OWNER_LABELS = {
	"EPM Admin": "Close Lead",
	"EPM Analyst": "Group Accountant",
	"Entity Accountant": "Entity Accountant",
	"System Manager": "System Manager",
};

const SCREEN_ACTIONS = {
	"my-work": "Open My work",
	"trial-balances": "Open trial balances",
	checks: "Open checks",
	"sign-off": "Open sign-off",
};

const KIND_STYLES = {
	blocking: { icon: "alert-octagon", tone: "text-ink-red-3", badge: "red" },
	todo: { icon: "check-square", tone: "text-ink-gray-7", badge: "blue" },
	waiting: { icon: "clock", tone: "text-ink-gray-5", badge: "gray" },
};

const route = useRoute();

const current = computed(() => {
	const p = parse(`/close${route.path}`);
	return p.error || p.year == null ? null : { year: p.year, period: p.period };
});

function periodName(c) {
	return `FY${c.year} P${String(c.period).padStart(2, "0")}`;
}

// --- data -------------------------------------------------------------------

const work = reactive({ status: "loading", items: [], error: null, busy: false });
/** Only for an Entity Accountant: are any entities assigned? (A25 note) */
const scope = reactive({ status: "idle", persona: null, noEntities: false, error: null });
let seq = 0;

async function loadScope(n, c) {
	scope.status = "loading";
	scope.error = null;
	scope.noEntities = false;
	try {
		const params = c ? { fiscal_year: c.year, fiscal_period: c.period } : null;
		const ctx = await get(CONTEXT, params);
		if (n !== seq) return;
		scope.persona = (ctx && ctx.me && ctx.me.persona) || null;
		if (scope.persona !== "entity_accountant" || !c) {
			scope.status = "ready";
			return;
		}
		const tbs = await get(MY_TBS, params);
		if (n !== seq) return;
		scope.noEntities = Array.isArray(tbs && tbs.entities) && tbs.entities.length === 0;
		scope.status = "ready";
	} catch (e) {
		if (n !== seq) return;
		scope.error = e.message;
		scope.status = "error";
	}
}

async function load() {
	const n = ++seq;
	work.busy = work.status === "error";
	work.status = "loading";
	work.error = null;
	loadScope(n, current.value);
	try {
		const data = await get(MY_WORK);
		if (n !== seq) return;
		if (!data || !Array.isArray(data.items)) {
			throw new Error("get_my_work sent no item list.");
		}
		work.items = data.items;
		work.status = "ready";
	} catch (e) {
		if (n !== seq) return;
		work.items = [];
		work.error = e.message;
		work.status = "error";
	} finally {
		work.busy = false;
	}
}

watch(() => (current.value ? `${current.value.year}/${current.value.period}` : "none"), load, { immediate: true });

/** B10 groups, in the server's order. An unknown kind is an error, not a dropped item. */
const grouped = computed(() => {
	if (work.status !== "ready") return { groups: [], error: null };
	try {
		return { groups: sections(work.items), error: null };
	} catch (e) {
		return { groups: [], error: e.message };
	}
});
const groups = computed(() => grouped.value.groups);

const loadState = computed(() => {
	if (work.status === "ready" && grouped.value.error) return "error";
	return work.status;
});
const loadError = computed(() => work.error || grouped.value.error);

/** An item's action: `{to}` in-app, `{external}` for a gap, or `{error}`. */
function routeOf(item) {
	try {
		const r = itemRoute(item);
		if (r && typeof r === "object" && r.external) return { external: r.external };
		return { to: String(r).replace(/^\/close/, "") };
	} catch (e) {
		return { error: `This item has no action the screen can open (${e.message}).` };
	}
}

function actionLabel(item) {
	const screen = (item.action || {}).screen;
	return SCREEN_ACTIONS[screen] || `Open ${screen}`;
}

function ownerLabel(owner) {
	if (!owner) return "No owner named";
	return OWNER_LABELS[owner] ? `${OWNER_LABELS[owner]} (${owner})` : owner;
}

const total = computed(() => work.items.length);
</script>

<template>
	<div class="mx-auto max-w-4xl px-6 py-6">
		<header class="mb-5 flex flex-wrap items-baseline justify-between gap-2">
			<div>
				<h1 class="text-xl font-semibold text-ink-gray-9">My work</h1>
				<p class="mt-1 text-sm text-ink-gray-6">
					Everything waiting on you across every open period. Each item names its own period.
				</p>
			</div>
			<p v-if="work.status === 'ready' && !grouped.error" class="text-sm text-ink-gray-6">
				{{ total }} {{ total === 1 ? "item" : "items" }}
			</p>
		</header>

		<div
			v-if="scope.status === 'ready' && scope.noEntities"
			role="alert"
			class="mb-5 flex items-start gap-3 rounded border border-outline-amber-1 bg-surface-amber-1 px-4 py-4 text-ink-amber-3"
		>
			<FeatherIcon name="user-x" class="mt-0.5 h-4 w-4 shrink-0" />
			<div>
				<p class="text-base font-medium">No entities are assigned to you. Ask the System Manager.</p>
				<p class="mt-1 text-sm">
					Until an entity is assigned there is nothing for you to upload, so the groups below stay empty.
				</p>
			</div>
		</div>
		<LoadState
			v-else-if="scope.status === 'error'"
			compact
			state="error"
			:what="current ? `your entities for ${periodName(current)}` : 'your entities'"
			:source="MY_TBS"
			:error="scope.error"
			@retry="load"
		/>

		<LoadState
			:state="loadState"
			what="your work across every open period"
			:source="MY_WORK"
			:error="loadError"
			:busy="work.busy"
			@retry="load"
		>
			<div class="space-y-6">
				<section v-for="section in groups" :key="section.kind" :aria-labelledby="`mywork-${section.kind}`">
					<h2
						:id="`mywork-${section.kind}`"
						class="mb-2 flex items-center gap-2 text-sm font-semibold uppercase tracking-wide"
						:class="section.empty ? 'text-ink-gray-5' : KIND_STYLES[section.kind].tone"
					>
						<FeatherIcon :name="KIND_STYLES[section.kind].icon" class="h-4 w-4" />
						{{ section.title }}
						<span v-if="!section.empty" class="font-normal text-ink-gray-5">({{ section.items.length }})</span>
					</h2>

					<p
						v-if="section.empty"
						class="rounded border border-dashed border-outline-gray-2 px-4 py-3 text-sm text-ink-gray-5"
					>
						{{ section.title }}.
					</p>

					<ul v-else class="divide-y divide-outline-gray-1 rounded border border-outline-gray-2">
						<li
							v-for="item in section.items"
							:key="item.id"
							class="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
						>
							<div class="min-w-0">
								<div class="flex flex-wrap items-center gap-2">
									<Badge
										v-if="item.period"
										:theme="KIND_STYLES[section.kind].badge"
										variant="subtle"
										:label="item.period.code"
									/>
									<Badge v-else theme="orange" variant="subtle" label="Setup" />
									<span class="text-base font-medium text-ink-gray-9">{{ item.title }}</span>
								</div>
								<p v-if="item.detail" class="mt-1 break-words text-sm text-ink-gray-7">{{ item.detail }}</p>
								<p class="mt-1 text-xs text-ink-gray-5">Owner: {{ ownerLabel(item.owner) }}</p>
							</div>

							<div class="shrink-0">
								<!-- gap-item: the only Desk link (story 0.4) -->
								<a
									v-if="routeOf(item).external"
									:href="routeOf(item).external"
									target="_blank"
									rel="noopener noreferrer"
									class="inline-flex items-center gap-1.5 rounded border border-outline-gray-2 bg-surface-white px-3 py-1.5 text-sm text-ink-gray-8 hover:bg-surface-gray-2"
									:title="`Opens ${routeOf(item).external} in the Desk, in a new tab`"
								>
									Fix in Desk
									<FeatherIcon name="external-link" class="h-3.5 w-3.5" />
									<span class="sr-only">(opens the Desk in a new tab)</span>
								</a>
								<!-- /gap-item -->
								<p v-else-if="routeOf(item).error" role="alert" class="max-w-xs text-sm text-ink-red-3">
									{{ routeOf(item).error }}
								</p>
								<RouterLink
									v-else
									:to="routeOf(item).to"
									class="inline-flex items-center gap-1.5 rounded bg-surface-gray-7 px-3 py-1.5 text-sm font-medium text-ink-white hover:bg-surface-gray-6"
								>
									{{ actionLabel(item) }}
									<FeatherIcon name="arrow-right" class="h-3.5 w-3.5" />
								</RouterLink>
							</div>
						</li>
					</ul>
				</section>
			</div>
		</LoadState>
	</div>
</template>
