<script setup>
/**
 * One month's close: the eight-stage lane, the viewer's work, and a detail
 * panel for the selected row.
 *
 * The lane is the same for every role (shared context); stages you own are
 * tinted and marked "You". The queue only holds what the server says is yours,
 * and every button carries whether you may press it: a refused action shows
 * disabled with the reason, never as a button that fails after the click. An
 * allowed button can carry a note: the form opens, but what it leads to can't
 * be completed yet (an approval in a closed period).
 *
 * Each row is two controls side by side, never one inside the other: a
 * button that selects the row (aria-pressed), and the row's action.
 */
import { computed, inject, ref, watch } from "vue";
import { Badge, Button } from "frappe-ui";
import StatusBadge from "./StatusBadge.vue";
import { isMine, stageTarget, firstOpenItem, openCount, actionHint, PERIOD_THEME } from "../home.js";

const props = defineProps({
	year: { type: String, required: true },
	period: { type: String, required: true },
});

const home = inject("home");
const y = computed(() => Number(props.year));
const p = computed(() => Number(props.period));

const month = computed(() => {
	const m = home.month.value;
	return m && m.period.fiscal_year === y.value && m.period.fiscal_period === p.value ? m : null;
});
const roles = computed(() => home.me.value?.roles || []);

const selectedId = ref(null);
watch(() => [props.year, props.period], () => { selectedId.value = null; });
const items = computed(() => (month.value ? [...month.value.mine, ...month.value.waiting] : []));
const selected = computed(() => items.value.find((i) => i.id === selectedId.value) || firstOpenItem(month.value));
const needsYou = computed(() => openCount(month.value?.mine));

const subtitle = computed(() => {
	const m = month.value;
	if (!m) return "";
	const parts = ["Month-end close", `${m.period.entities_in_close} entities in the close`];
	if (m.period.closed_by) parts.push(`signed off by ${m.period.closed_by}`);
	return parts.join(" · ");
});

const sections = computed(() => {
	const m = month.value;
	if (!m) return [];
	const out = [{ id: "mine", title: "Needs you", count: needsYou.value, items: m.mine, empty: `Nothing needs you in ${m.period.label}.` }];
	if (m.waiting.length) out.push({ id: "waiting", title: "Waiting on others", count: m.waiting.length, items: m.waiting });
	return out;
});

function stageName(n) {
	return month.value?.stages.find((s) => s.n === n)?.label || "";
}

function act(item) {
	const a = item?.action;
	if (!a || !a.allowed) return;
	if (a.step) home.router.push(`/close/${a.step}`);
	else window.open(a.href, "_blank", "noopener");
}

function openStage(stage) {
	const t = stageTarget(stage, y.value, p.value);
	if (t.step) home.router.push(`/close/${t.step}`);
	else window.open(t.href, "_blank", "noopener");
}
</script>

<template>
	<div class="px-6 py-5">
		<div v-if="!month" class="py-10 text-base text-ink-gray-5">
			<template v-if="home.error.value">Could not load this period: {{ home.error.value.message || home.error.value }}</template>
			<template v-else>Loading…</template>
		</div>

		<template v-else>
			<header class="mb-4 flex flex-wrap items-end justify-between gap-3">
				<div>
					<h1 class="text-2xl font-semibold tracking-tight text-ink-gray-9">{{ month.period.label }}</h1>
					<p class="mt-1 text-base text-ink-gray-6">{{ subtitle }}</p>
				</div>
				<div class="flex flex-wrap items-center gap-2">
					<Button v-if="home.me.value?.can?.approve" variant="subtle" @click="home.router.push('/uploads')">Upload trial balances</Button>
					<Badge :theme="PERIOD_THEME[month.period.status] || 'gray'" size="lg" variant="subtle">{{ month.period.status }}</Badge>
					<Badge theme="gray" size="lg" variant="subtle">{{ month.period.code }}</Badge>
				</div>
			</header>

			<ol class="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-outline-gray-2 bg-surface-gray-3 sm:grid-cols-4 xl:grid-cols-8" aria-label="Close stages">
				<li v-for="s in month.stages" :key="s.id" class="flex">
					<button
						type="button"
						class="flex w-full flex-col gap-1 px-3 py-2.5 text-left hover:bg-surface-gray-2"
						:class="isMine(s, roles) ? 'bg-surface-gray-1' : 'bg-surface-white'"
						@click="openStage(s)"
					>
						<span class="flex items-center justify-between text-xs text-ink-gray-5">
							<span class="tnum">{{ s.n }}</span>
							<span v-if="isMine(s, roles)" class="font-semibold uppercase tracking-wider text-ink-blue-3">You</span>
						</span>
						<span class="truncate text-base font-medium text-ink-gray-9">{{ s.label }}</span>
						<StatusBadge :state="s.state" :label="s.summary" size="sm" class="self-start" />
					</button>
				</li>
			</ol>

			<div class="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
				<div class="space-y-6">
					<section v-for="sec in sections" :key="sec.id">
						<h2 class="mb-2 flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-ink-gray-5">
							{{ sec.title }}
							<span class="tnum rounded-full bg-surface-gray-2 px-2 text-xs text-ink-gray-6">{{ sec.count }}</span>
						</h2>
						<ul v-if="sec.items.length" class="divide-y divide-outline-gray-1 overflow-hidden rounded-lg border border-outline-gray-2">
							<li
								v-for="item in sec.items"
								:key="item.id"
								class="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3"
								:class="selected?.id === item.id ? 'bg-surface-gray-2' : 'hover:bg-surface-gray-1'"
							>
								<button
									type="button"
									class="flex min-w-[12rem] flex-1 items-center gap-3 rounded text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-outline-gray-3"
									:aria-pressed="selected?.id === item.id"
									@click="selectedId = item.id"
								>
									<StatusBadge :state="item.state" />
									<span class="min-w-0 flex-1">
										<span class="block text-base font-medium text-ink-gray-9">{{ item.title }}</span>
										<span class="block text-sm text-ink-gray-5">{{ item.detail }}</span>
									</span>
								</button>
								<span v-if="sec.id === 'waiting' && item.who" class="text-sm text-ink-gray-5">{{ item.who }}</span>
								<span v-else-if="item.stage" class="text-xs text-ink-gray-5">Stage {{ item.stage }}</span>
								<span v-if="item.action && sec.id === 'mine' && item.action.allowed && item.action.note" class="text-xs text-ink-gray-5">{{ item.action.note }}</span>
								<Button
									v-if="item.action && sec.id === 'mine'"
									size="sm"
									:variant="item.state === 'done' ? 'ghost' : 'subtle'"
									:disabled="!item.action.allowed"
									:title="actionHint(item.action)"
									@click="act(item)"
								>{{ item.action.label }}</Button>
							</li>
						</ul>
						<p v-else class="rounded-lg border border-dashed border-outline-gray-2 px-4 py-6 text-center text-base text-ink-gray-5">
							{{ sec.empty }}
						</p>
					</section>
				</div>

				<aside class="h-fit rounded-lg border border-outline-gray-2" aria-label="Selected item">
					<template v-if="selected">
						<div class="border-b border-outline-gray-1 px-4 py-3">
							<div class="text-xs font-semibold uppercase tracking-wider text-ink-gray-4">
								{{ selected.stage ? `Stage ${selected.stage} · ${stageName(selected.stage)}` : "Item" }}
							</div>
							<h3 class="mt-1 text-lg font-semibold text-ink-gray-9">{{ selected.title }}</h3>
							<StatusBadge class="mt-2" :state="selected.state" />
						</div>
						<dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 px-4 py-3 text-sm">
							<template v-if="selected.detail"><dt class="text-ink-gray-5">Detail</dt><dd class="text-ink-gray-9">{{ selected.detail }}</dd></template>
							<template v-if="selected.entity"><dt class="text-ink-gray-5">Entity</dt><dd class="text-ink-gray-9">{{ selected.entity }}</dd></template>
							<template v-if="selected.who"><dt class="text-ink-gray-5">Who</dt><dd class="text-ink-gray-9">{{ selected.who }}</dd></template>
							<dt class="text-ink-gray-5">Period</dt><dd class="text-ink-gray-9">{{ month.period.code }} · {{ month.period.label }}</dd>
						</dl>
						<div v-if="selected.action" class="space-y-2 border-t border-outline-gray-1 px-4 py-3">
							<Button variant="solid" :disabled="!selected.action.allowed" @click="act(selected)">{{ selected.action.label }}</Button>
							<p v-if="actionHint(selected.action)" class="text-sm text-ink-gray-5">{{ actionHint(selected.action) }}</p>
						</div>
					</template>
					<p v-else class="px-4 py-6 text-sm text-ink-gray-5">Nothing selected.</p>
				</aside>
			</div>
		</template>
	</div>
</template>
