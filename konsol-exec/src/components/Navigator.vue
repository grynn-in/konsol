<script setup>
/**
 * The fiscal navigator: years as folders holding their fourteen periods
 * (OPN, P01-P12, CLS), each with its close state. It is how people know which
 * year and month they are in (the user's request), so the selected period is
 * always visible and highlighted, and state never depends on colour alone:
 * locked, closed, open and not-started each have their own glyph.
 */
import { computed, ref, watch } from "vue";
import { FeatherIcon } from "frappe-ui";
import { monthPath, defaultExpanded } from "../home.js";

const props = defineProps({
	tree: { type: Object, default: null },
	selected: { type: Object, default: null }, // {year, period}
	mineCount: { type: Number, default: 0 },
	me: { type: Object, default: null },
});

const open = ref(new Set());
watch(
	() => [props.tree, props.selected?.year],
	() => {
		const next = new Set(open.value);
		for (const y of defaultExpanded(props.tree, props.selected)) next.add(y);
		open.value = next;
	},
	{ immediate: true }
);

function toggle(year) {
	const next = new Set(open.value);
	if (next.has(year)) next.delete(year);
	else next.add(year);
	open.value = next;
}

const GLYPH = { locked: "lock", closed: "check-circle", open: "disc", future: "circle" };
const TONE = { locked: "text-ink-gray-4", closed: "text-ink-green-3", open: "text-ink-blue-3", future: "text-ink-gray-3" };
const KIND = { current: "current", planning: "planning", past: "" };

function isSelected(y, p) {
	return props.selected?.year === y && props.selected?.period === p;
}

const links = computed(() => {
	const roles = new Set(props.me?.roles || []);
	const any = (...r) => r.some((x) => roles.has(x));
	const out = [{ label: "Close checklist", to: "/close", icon: "list" }];
	out.push({ label: "Entities", href: "/app/entity", icon: "folder" });
	if (any("EPM Admin", "EPM Analyst", "Entity Accountant", "System Manager")) {
		out.push({ label: "Trial balances", href: "/app/trial-balance-submission", icon: "file-text" });
	}
	if (any("EPM Admin", "EPM Analyst")) {
		out.push({ label: "Adjustments", href: "/app/consolidation-adjustment", icon: "edit-3" });
		out.push({ label: "Reference data", href: "/app/konsolidat", icon: "database" });
	}
	if (props.me?.can?.system) out.push({ label: "Connectors", href: "/app/connector", icon: "zap" });
	return out;
});

const row = "flex items-center gap-2 rounded px-2 py-1 text-ink-gray-7 hover:bg-surface-gray-2";
</script>

<template>
	<nav aria-label="Fiscal calendar" class="flex flex-col gap-4 overflow-y-auto border-b border-outline-gray-1 bg-surface-gray-1 px-2 py-3 text-sm md:border-b-0 md:border-r">
		<div>
			<div class="px-2 pb-1 text-xs font-semibold uppercase tracking-wider text-ink-gray-5">Fiscal calendar</div>
			<ul class="space-y-px">
				<li v-for="y in tree?.years || []" :key="y.fiscal_year">
					<button
						type="button"
						class="flex w-full items-center gap-1.5 rounded px-2 py-1 text-left font-semibold text-ink-gray-9 hover:bg-surface-gray-2"
						:aria-expanded="open.has(y.fiscal_year)"
						@click="toggle(y.fiscal_year)"
					>
						<FeatherIcon :name="open.has(y.fiscal_year) ? 'chevron-down' : 'chevron-right'" class="h-3.5 w-3.5 text-ink-gray-5" />
						<span>{{ y.label }}</span>
						<span class="text-xs font-normal text-ink-gray-5">{{ KIND[y.kind] }}</span>
					</button>
					<ul v-if="open.has(y.fiscal_year)" class="ml-3.5 space-y-px border-l border-outline-gray-2 pl-2">
						<li v-if="y.budget">
							<a :href="`/app/budget-cycle/${encodeURIComponent(y.budget.name)}`" target="_blank" rel="noopener" :class="row">
								<FeatherIcon name="folder" class="h-3.5 w-3.5 text-ink-gray-5" />
								<span class="flex-1 truncate">Budget</span>
								<span class="text-xs text-ink-gray-5">{{ y.budget.status }}</span>
							</a>
						</li>
						<li v-for="p in y.periods" :key="p.fiscal_period">
							<RouterLink
								:to="monthPath(y.fiscal_year, p.fiscal_period)"
								:aria-current="isSelected(y.fiscal_year, p.fiscal_period) ? 'page' : undefined"
								class="flex items-center gap-2 rounded px-2 py-1"
								:class="isSelected(y.fiscal_year, p.fiscal_period)
									? 'bg-surface-white font-medium text-ink-gray-9 shadow-sm'
									: p.state === 'future' ? 'text-ink-gray-5 hover:bg-surface-gray-2' : 'text-ink-gray-7 hover:bg-surface-gray-2'"
							>
								<FeatherIcon :name="GLYPH[p.state]" class="h-3.5 w-3.5 shrink-0" :class="TONE[p.state]" :aria-label="p.state" />
								<span class="tnum w-8 shrink-0 text-xs text-ink-gray-5">{{ p.code }}</span>
								<span class="flex-1 truncate">{{ p.label }}</span>
								<span
									v-if="isSelected(y.fiscal_year, p.fiscal_period) && mineCount"
									class="tnum rounded-full bg-red-500 px-1.5 text-[11px] font-semibold leading-4 text-white"
									:title="`${mineCount} item${mineCount > 1 ? 's' : ''} need you`"
								>{{ mineCount }}</span>
							</RouterLink>
						</li>
					</ul>
				</li>
			</ul>
		</div>

		<div>
			<div class="px-2 pb-1 text-xs font-semibold uppercase tracking-wider text-ink-gray-5">Group</div>
			<ul class="space-y-px">
				<li v-for="l in links" :key="l.label">
					<RouterLink v-if="l.to" :to="l.to" :class="row">
						<FeatherIcon :name="l.icon" class="h-3.5 w-3.5 text-ink-gray-5" />{{ l.label }}
					</RouterLink>
					<a v-else :href="l.href" target="_blank" rel="noopener" :class="row">
						<FeatherIcon :name="l.icon" class="h-3.5 w-3.5 text-ink-gray-5" />{{ l.label }}
					</a>
				</li>
			</ul>
		</div>

		<div class="mt-auto flex flex-wrap gap-x-3 gap-y-1 border-t border-outline-gray-1 px-2 pt-2 text-xs text-ink-gray-5">
			<span v-for="(g, s) in GLYPH" :key="s" class="inline-flex items-center gap-1">
				<FeatherIcon :name="g" class="h-3 w-3" :class="TONE[s]" />{{ s === 'future' ? 'not started' : s }}
			</span>
		</div>
	</nav>
</template>
