<script setup>
/**
 * konsol#305 B17: the header — which period, what state it is in, and
 * whether the numbers are current.
 *
 * - Period picker: every Regular period A15 returned. Choosing one changes
 *   the URL only (route.js `format`); nothing is remembered (D5).
 * - Period state: Open / Closed / Locked (effective status), the sign-off
 *   state, the latest checks status, the catch-up label (#303) and whether
 *   the period is history (before the first close period).
 * - Other open periods (A04 `other_open`), each a link to the same screen.
 * - Configuration gaps A15 names (e.g. `first_close_undeclared`), verbatim.
 * - Freshness: the text B09's `freshnessView` produced, passed in by AppShell.
 * - B31: when a screen's quiet context reload fails, `refreshError` says the
 *   period state shown may be out of date, rather than showing it as current.
 *
 * Every piece has a visible state when its data is missing: nothing is blank.
 */
import { computed } from "vue";
import { useRouter } from "vue-router";
import { FeatherIcon } from "frappe-ui";
import { format } from "../route.js";

const props = defineProps({
	/** A15 `get_context` result, or null while it loads or failed. */
	context: { type: Object, default: null },
	/** `{year, period}` from the URL, or null on /close. */
	current: { type: Object, default: null },
	/** The screen the picker keeps when the period changes. */
	screen: { type: String, required: true },
	/** `{tone, text, detail}` from freshnessView, or null before the first answer. */
	freshness: { type: Object, default: null },
	freshnessBusy: { type: Boolean, default: false },
	/** B31: a screen's quiet context reload failed; the period state shown may be old. */
	refreshError: { type: String, default: null },
});

const router = useRouter();

const PERSONA_LABELS = {
	close_lead: "Close Lead",
	group_accountant: "Group Accountant",
	entity_accountant: "Entity Accountant",
	viewer: "Viewer",
};

function periodName(key) {
	return `FY${key[0]} P${String(key[1]).padStart(2, "0")}`;
}

/** A URL under the router's `/close` base, from route.js's full path. */
function routerPath(year, period, screen) {
	return format({ year, period, screen }).replace(/^\/close/, "");
}

const selected = computed(() => (props.context && props.context.selected) || null);
const me = computed(() => (props.context && props.context.me) || null);
const gaps = computed(() => (props.context && props.context.config_gaps) || []);
/** A04 `other_open` is [] when the first close period is undeclared: unknown, not none. */
const firstCloseUndeclared = computed(() => gaps.value.some((g) => g.code === "first_close_undeclared"));

const periodsByYear = computed(() => {
	const groups = new Map();
	for (const state of (props.context && props.context.periods) || []) {
		const year = state.key[0];
		if (!groups.has(year)) groups.set(year, []);
		groups.get(year).push(state);
	}
	// Newest year first; periods within a year in calendar order.
	return [...groups.entries()].sort((a, b) => b[0] - a[0]);
});

const pickerValue = computed(() =>
	props.current && props.current.year != null ? `${props.current.year}/${props.current.period}` : "",
);

function choose(event) {
	const [year, period] = String(event.target.value).split("/").map(Number);
	if (!Number.isInteger(year) || !Number.isInteger(period)) return;
	router.push(routerPath(year, period, props.screen));
}

const STATUS_TONE = {
	Open: "bg-surface-green-2 text-ink-green-3",
	Closed: "bg-surface-gray-3 text-ink-gray-8",
	Locked: "bg-surface-gray-7 text-ink-white",
};
const SIGNOFF_TONE = {
	"Signed Off": "text-ink-green-3",
	Acknowledged: "text-ink-green-3",
	Overridden: "text-ink-amber-3",
	"Re-sign Needed": "text-ink-red-3",
};
const FRESHNESS_TONE = {
	neutral: "border-outline-gray-2 bg-surface-gray-1 text-ink-gray-7",
	blue: "border-outline-blue-1 bg-surface-blue-1 text-ink-blue-3",
	amber: "border-outline-amber-1 bg-surface-amber-1 text-ink-amber-3",
	red: "border-outline-red-1 bg-surface-red-1 text-ink-red-3",
};
</script>

<template>
	<header class="border-b border-outline-gray-2 bg-surface-white">
		<div class="flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2.5">
			<a href="/close" class="flex items-center gap-2 font-semibold tracking-tight text-ink-gray-9">
				<span class="grid h-6 w-6 place-items-center rounded bg-surface-gray-7 text-xs font-bold text-ink-white">K</span>
				Close
			</a>

			<label class="flex items-center gap-2 text-sm text-ink-gray-6">
				Period
				<select
					class="form-select rounded border-outline-gray-2 py-1 pl-2 pr-8 text-sm text-ink-gray-9"
					:value="pickerValue"
					:disabled="!context"
					:aria-label="context ? 'Choose a period' : 'Periods are not loaded yet'"
					@change="choose"
				>
					<option v-if="!pickerValue" value="" disabled>
						{{ context ? "Choose a period" : "Periods not loaded" }}
					</option>
					<optgroup v-for="[year, states] in periodsByYear" :key="year" :label="`FY${year}`">
						<option v-for="s in states" :key="s.key.join('/')" :value="s.key.join('/')">
							{{ periodName(s.key) }}{{ s.label && s.label !== s.code ? ` · ${s.label}` : "" }} — {{ s.status }}{{ s.is_history ? " (history)" : "" }}
						</option>
					</optgroup>
				</select>
			</label>

			<div v-if="selected" class="flex flex-wrap items-center gap-2 text-sm">
				<span
					class="rounded px-2 py-0.5 text-xs font-medium"
					:class="STATUS_TONE[selected.status] || 'bg-surface-gray-2 text-ink-gray-7'"
					:title="`Period status: ${selected.status}`"
				>{{ selected.status || "Status unknown" }}</span>
				<span :class="SIGNOFF_TONE[selected.signoff] || 'text-ink-gray-6'">{{ selected.signoff }}</span>
				<span class="text-ink-gray-5">·</span>
				<span class="text-ink-gray-6">Checks: {{ selected.checks }}</span>
				<span
					v-if="refreshError"
					role="status"
					class="rounded bg-surface-amber-1 px-2 py-0.5 text-xs text-ink-amber-3"
					:title="refreshError"
				>This period state may be out of date: {{ refreshError }}</span>
				<span v-if="selected.catch_up" class="rounded bg-surface-blue-1 px-2 py-0.5 text-xs text-ink-blue-3">{{ selected.catch_up }}</span>
				<span
					v-if="selected.is_history"
					class="rounded bg-surface-gray-2 px-2 py-0.5 text-xs text-ink-gray-7"
					title="Before the first close period: history, not close work"
				>History</span>
			</div>
			<span v-else-if="current && current.year != null && context" class="text-sm text-ink-gray-6">
				No state for {{ periodName([current.year, current.period]) }}
			</span>

			<div class="ml-auto flex flex-wrap items-center gap-3">
				<div
					class="flex items-center gap-2 rounded border px-2.5 py-1 text-sm"
					:class="freshness ? FRESHNESS_TONE[freshness.tone] || FRESHNESS_TONE.red : FRESHNESS_TONE.neutral"
					role="status"
					aria-live="polite"
				>
					<FeatherIcon
						:name="freshness && freshness.tone !== 'neutral' ? 'alert-circle' : 'database'"
						class="h-3.5 w-3.5 shrink-0"
						:class="freshnessBusy ? 'animate-pulse' : ''"
					/>
					<span v-if="freshness">
						{{ freshness.text }}
						<span v-if="freshness.detail" class="ml-1 text-xs opacity-80">{{ freshness.detail }}</span>
					</span>
					<span v-else>Checking whether the numbers are current</span>
				</div>

				<a href="/app" class="flex items-center gap-1 rounded px-2 py-1 text-sm text-ink-gray-6 hover:bg-surface-gray-2">
					Desk
					<FeatherIcon name="external-link" class="h-3.5 w-3.5" />
				</a>

				<div v-if="me" class="text-right text-sm leading-tight">
					<span class="text-ink-gray-9">{{ me.full_name || me.user }}</span>
					<span class="block text-xs text-ink-gray-5">{{ PERSONA_LABELS[me.persona] || "No close role" }}</span>
				</div>
			</div>
		</div>

		<div
			v-if="selected"
			class="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-outline-gray-1 bg-surface-gray-1 px-4 py-1.5 text-sm"
		>
			<span class="text-ink-gray-6">Other open periods:</span>
			<RouterLink
				v-for="s in selected.other_open"
				:key="s.key.join('/')"
				:to="routerPath(s.key[0], s.key[1], screen)"
				class="rounded px-1.5 py-0.5 text-ink-blue-3 underline-offset-2 hover:underline"
			>{{ periodName(s.key) }}</RouterLink>
			<span v-if="firstCloseUndeclared" class="text-ink-gray-5">unknown until the first close period is declared</span>
			<span v-else-if="!selected.other_open || !selected.other_open.length" class="text-ink-gray-5">none</span>
		</div>

		<div
			v-for="gap in gaps"
			:key="gap.code"
			role="alert"
			class="flex items-start gap-2 border-t border-outline-amber-1 bg-surface-amber-1 px-4 py-2 text-sm text-ink-amber-3"
		>
			<FeatherIcon name="alert-triangle" class="mt-0.5 h-3.5 w-3.5 shrink-0" />
			<span>{{ gap.message }}</span>
		</div>
	</header>
</template>
