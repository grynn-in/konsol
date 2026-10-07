<script setup>
/**
 * konsol#305 O60: the "Change ownership" form on the Rates & ownership
 * screen's Ownership tab (story 4.2; #305-4.2-1; wireframe-4.2.md section 1,
 * confirmed as drawn by Deepak Pai 7 Oct; C-O2, C-O3, C-O4).
 *
 * Rates.vue mounts it only when `get_ownership` says `can_record`, and
 * passes that payload's `change` (O63): the entities that already have a
 * submitted ownership period, each with its own node's group, and the Open
 * Regular periods. Every choice the form offers comes from there:
 * - Entity: one option per node. An entity on two nodes names its group in
 *   the option, never a guessed node. The group is shown read-only from the
 *   chosen node.
 * - First period affected: Open Regular periods only (C-O2: a period, never
 *   a free date). Live has 192 (FY2010–FY2025), so the picker groups them by
 *   fiscal year, newest year first, P01..P12 in calendar order inside each.
 * - Ownership % and Method. METHODS mirrors ownership_change_model.METHODS
 *   (the test pins the two equal); the server still refuses any other.
 * Nothing is preselected: a change states every value it sets.
 *
 * As the inputs change, the form asks `rates_api.preview_ownership_change`
 * (O55) for the refusals and the structural effect: debounced, one request
 * in flight, and a response for an older input is never shown over a newer
 * one (makePreviewer). The effect panel is rates.js ownershipEffectView
 * (O59): the screen computes no pct, method, date or period. Refusals are
 * the server's sentences as plain text. An incomplete form asks nothing.
 *
 * "Save draft" posts `rates_api.save_ownership_change` (O56) through the ONE
 * function saveDraft, its body from rates.js ownershipChangeBody. It is
 * disabled while any refusal or error shows, while the shown preview is
 * older than the inputs, and while a save is in flight. After a save the
 * wireframe's line names the draft, the form clears, and `saved` tells the
 * screen to reload its pending list.
 *
 * There is no acquisition or disposal date or price here (#305-4.2-1): those
 * are Business Combination and Business Disposal documents in Desk, and a
 * first ownership stays the gaps list's "Record ownership" Desk link (C-O3).
 */
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { Button, FeatherIcon } from "frappe-ui";
import { get, post } from "../api.js";
import { ownershipChangeBody, ownershipEffectView } from "../rates.js";
import { dueDateText } from "../dueDate.js";

const PREVIEW = "konsol.close.rates_api.preview_ownership_change";
const SAVE_OWNERSHIP_CHANGE = "konsol.close.rates_api.save_ownership_change";
const METHODS = ["full", "proportional", "equity", "none"];
const PREVIEW_DELAY_MS = 300;
const INCOMPLETE = "Choose an entity, the first period affected, the ownership % and the method to see the effect.";

const props = defineProps({
	/** `get_ownership(...).change` (O63): `{entities, periods}`. */
	change: { type: Object, required: true },
});
const emit = defineEmits(["saved"]);

/** Throws naming the first of `keys` that `obj` lacks: never a guessed value. */
function need(obj, keys, where) {
	for (const key of keys) {
		if (!obj || typeof obj !== "object" || !(key in obj)) {
			throw new Error(`OwnershipChange: ${where} has no ${key}`);
		}
	}
}

/** `change.entities` -> one option per node, `{key, entity, group, label}`. */
function entityOptions(entities) {
	if (!Array.isArray(entities)) {
		throw new Error("OwnershipChange: get_ownership's change has no entities list");
	}
	const nodes = {};
	for (const e of entities) {
		need(e, ["entity", "entity_name", "consolidation_group"], "an entity choice");
		nodes[e.entity] = (nodes[e.entity] || 0) + 1;
	}
	return entities.map((e) => ({
		key: `${e.entity}|${e.consolidation_group}`,
		entity: e.entity,
		group: e.consolidation_group,
		label: `${e.entity} — ${e.entity_name}${nodes[e.entity] > 1 ? ` (in ${e.consolidation_group})` : ""}`,
	}));
}

/** `change.periods` (calendar order) -> `[{year, label, options}]`, newest
 * fiscal year first; each option `{key, fiscal_year, fiscal_period, label}`
 * with the server's own period label. */
function periodGroups(periods) {
	if (!Array.isArray(periods)) {
		throw new Error("OwnershipChange: get_ownership's change has no periods list");
	}
	const groups = [];
	for (const p of periods) {
		need(p, ["fiscal_year", "fiscal_period", "label", "start_date"], "a period choice");
		let group = groups.find((g) => g.year === p.fiscal_year);
		if (!group) {
			group = { year: p.fiscal_year, label: `Fiscal year ${p.fiscal_year}`, options: [] };
			groups.push(group);
		}
		group.options.push({
			key: `${p.fiscal_year}/${p.fiscal_period}`,
			fiscal_year: p.fiscal_year,
			fiscal_period: p.fiscal_period,
			label: `${p.label} (from ${p.start_date})`,
		});
	}
	return groups.sort((a, b) => b.year - a.year);
}

/** The preview's parameters, or null while the form is incomplete. A bad
 * pct is still sent: its refusal is the server's sentence. */
function previewParams(entity, period, pct, method) {
	const text = pct === null || pct === undefined ? "" : String(pct).trim();
	if (!entity || !period || !method || !text) return null;
	return {
		fiscal_year: period.fiscal_year,
		fiscal_period: period.fiscal_period,
		consolidation_group: entity.group,
		entity: entity.entity,
		ownership_pct: text,
		consolidation_method: method,
	};
}

/** The preview's `current` -> the wireframe's "Currently" line. */
function currentText(current) {
	need(current, ["name", "effective_date", "end_date", "ownership_pct", "consolidation_method"], "the current period");
	const to = current.end_date === null ? "open-ended" : `to ${dueDateText(current.end_date, "OwnershipChange")}`;
	return `${current.ownership_pct} % · ${current.consolidation_method} · from ${dueDateText(current.effective_date, "OwnershipChange")} · ${to} (${current.name})`;
}

/** A preview result (`{payload}` or `{error}`) -> `{problems, view,
 * current, error}`. Never throws: a malformed payload is its error. */
function effectPanel(result) {
	if ("error" in result) return { problems: [], view: null, current: null, error: result.error };
	try {
		const payload = result.payload;
		need(payload, ["problems", "effect", "current"], "the preview");
		if (!Array.isArray(payload.problems)) {
			throw new Error("OwnershipChange: the preview's problems is not a list");
		}
		const view = payload.effect === null ? null : ownershipEffectView(payload.effect);
		if (!payload.problems.length && !view) {
			throw new Error("OwnershipChange: the preview gave neither an effect nor a refusal");
		}
		return { problems: payload.problems, view, current: payload.current, error: null };
	} catch (e) {
		return { problems: [], view: null, current: null, error: e.message };
	}
}

/** True while Save draft must stay disabled. */
function saveBlocked(panel, fresh, saving) {
	return Boolean(saving || !fresh || !panel || panel.error || panel.problems.length || !panel.view);
}

/** The save's result -> the wireframe's line after "Save draft". */
function savedLine(result) {
	need(result, ["name"], "the save");
	return `Draft ${result.name} saved — awaiting the Close Lead's approval (Historical equity rates tab, and Approvals).`;
}

/** Debounced previews, one in flight; `show` gets only the newest input's
 * result. A newer `ask` (or a `cancel`) makes every older response stale. */
function makePreviewer({ fetch, show, delay, setTimer, clearTimer }) {
	let timer = null;
	let wanted = null;
	let seq = 0;
	let inFlight = false;
	async function run() {
		if (inFlight || wanted === null) return;
		const mine = seq;
		const params = wanted;
		wanted = null;
		inFlight = true;
		let result;
		try {
			result = { payload: await fetch(params) };
		} catch (e) {
			result = { error: e.message };
		}
		inFlight = false;
		if (mine === seq) show(result);
		if (timer === null) run();
	}
	return {
		ask(params) {
			seq += 1;
			wanted = params;
			if (timer !== null) clearTimer(timer);
			timer = setTimer(() => {
				timer = null;
				run();
			}, delay);
		},
		cancel() {
			seq += 1;
			wanted = null;
			if (timer !== null) clearTimer(timer);
			timer = null;
		},
	};
}

const form = reactive({ entityKey: "", periodKey: "", pct: "", method: "" });
const preview = reactive({ panel: null, fresh: false, busy: false });
const saving = ref(false);
const saveError = ref(null);
const saved = ref(null);

const choices = computed(() => {
	try {
		need(props.change, ["entities", "periods"], "get_ownership's change");
		return { entities: entityOptions(props.change.entities), groups: periodGroups(props.change.periods), error: null };
	} catch (e) {
		return { entities: [], groups: [], error: e.message };
	}
});
const selectedEntity = computed(() => choices.value.entities.find((o) => o.key === form.entityKey) || null);
const selectedPeriod = computed(() => {
	for (const g of choices.value.groups) {
		const hit = g.options.find((o) => o.key === form.periodKey);
		if (hit) return hit;
	}
	return null;
});
const current = computed(() => {
	const panel = preview.panel;
	if (!panel || !panel.current) return null;
	try {
		return { text: currentText(panel.current) };
	} catch (e) {
		return { error: e.message };
	}
});

const previewer = makePreviewer({
	fetch: (params) => get(PREVIEW, params),
	show(result) {
		preview.panel = effectPanel(result);
		preview.fresh = true;
		preview.busy = false;
	},
	delay: PREVIEW_DELAY_MS,
	setTimer: (fn, ms) => setTimeout(fn, ms),
	clearTimer: (t) => clearTimeout(t),
});

watch(
	() => previewParams(selectedEntity.value, selectedPeriod.value, form.pct, form.method),
	(params) => {
		preview.fresh = false;
		saveError.value = null;
		if (!params) {
			previewer.cancel();
			preview.panel = null;
			preview.busy = false;
			return;
		}
		preview.busy = true;
		previewer.ask(params);
	},
);

onBeforeUnmount(() => previewer.cancel());

async function saveDraft() {
	if (saveBlocked(preview.panel, preview.fresh, saving.value)) return;
	const built = ownershipChangeBody(
		{ fiscal_year: selectedPeriod.value.fiscal_year, fiscal_period: selectedPeriod.value.fiscal_period },
		{
			consolidationGroup: selectedEntity.value.group,
			entity: selectedEntity.value.entity,
			ownershipPct: form.pct,
			consolidationMethod: form.method,
		},
	);
	if (built.error) {
		saveError.value = built.error;
		return;
	}
	saving.value = true;
	saveError.value = null;
	saved.value = null;
	try {
		const result = await post(SAVE_OWNERSHIP_CHANGE, built.body);
		saved.value = savedLine(result);
		Object.assign(form, { entityKey: "", periodKey: "", pct: "", method: "" });
		emit("saved", result.name);
	} catch (e) {
		saveError.value = e.message;
	} finally {
		saving.value = false;
	}
}
</script>

<template>
	<section aria-label="Change ownership" class="mt-6">
		<h2 class="mb-2 text-base font-semibold text-ink-gray-9">Change ownership</h2>
		<p
			v-if="choices.error"
			role="alert"
			class="rounded border border-outline-red-1 bg-surface-red-1 px-4 py-3 text-sm text-ink-gray-8"
		>{{ choices.error }}</p>
		<p
			v-else-if="!choices.entities.length"
			class="rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-sm text-ink-gray-7"
		>No entity has an approved ownership period to change. A first ownership is recorded in Desk.</p>
		<p
			v-else-if="!choices.groups.length"
			class="rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-sm text-ink-gray-7"
		>No Open Regular period to start a change in.</p>
		<template v-else>
			<div class="grid max-w-3xl grid-cols-[10rem_1fr] items-center gap-x-4 gap-y-3 rounded border border-outline-gray-2 px-4 py-4 text-sm">
				<label for="oc-entity" class="text-ink-gray-6">Entity</label>
				<select
					id="oc-entity"
					v-model="form.entityKey"
					class="rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-ink-gray-8"
				>
					<option value="" disabled>Choose an entity</option>
					<option v-for="o in choices.entities" :key="o.key" :value="o.key">{{ o.label }}</option>
				</select>

				<span class="text-ink-gray-6">Group</span>
				<span class="text-ink-gray-8">
					<template v-if="selectedEntity">{{ selectedEntity.group }}</template><template v-else>—</template>
					<span class="ml-2 text-xs text-ink-gray-5">(from the entity's node; read-only)</span>
				</span>

				<template v-if="current">
					<span class="text-ink-gray-6">Currently</span>
					<span :class="current.error ? 'text-ink-red-4' : 'text-ink-gray-8'">{{ current.error || current.text }}</span>
				</template>

				<label for="oc-period" class="text-ink-gray-6">First period affected</label>
				<span class="flex flex-wrap items-center gap-2">
					<select
						id="oc-period"
						v-model="form.periodKey"
						class="rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-ink-gray-8"
					>
						<option value="" disabled>Choose a period</option>
						<optgroup v-for="g in choices.groups" :key="g.year" :label="g.label">
							<option v-for="o in g.options" :key="o.key" :value="o.key">{{ o.label }}</option>
						</optgroup>
					</select>
					<span class="text-xs text-ink-gray-5">Open Regular periods only, newest year first</span>
				</span>

				<label for="oc-pct" class="text-ink-gray-6">Ownership %</label>
				<input
					id="oc-pct"
					v-model="form.pct"
					type="text"
					inputmode="decimal"
					class="w-28 rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-right font-mono text-ink-gray-8"
				/>

				<label for="oc-method" class="text-ink-gray-6">Method</label>
				<select
					id="oc-method"
					v-model="form.method"
					class="w-40 rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-ink-gray-8"
				>
					<option value="" disabled>Choose a method</option>
					<option v-for="m in METHODS" :key="m" :value="m">{{ m }}</option>
				</select>
			</div>

			<div class="mt-4 max-w-3xl rounded border border-outline-gray-2 px-4 py-3 text-sm" aria-live="polite">
				<p class="text-xs font-medium uppercase tracking-wide text-ink-gray-6">
					Effect <span class="normal-case tracking-normal text-ink-gray-5">(structural; updates as you type; nothing is saved yet)</span>
				</p>
				<p v-if="!preview.panel && !preview.busy" class="mt-2 text-ink-gray-6">{{ INCOMPLETE }}</p>
				<p v-else-if="preview.busy" class="mt-2 text-ink-gray-5">Checking…</p>
				<template v-if="preview.panel">
					<p v-if="preview.panel.error" role="alert" class="mt-2 text-ink-red-4">{{ preview.panel.error }}</p>
					<table v-if="preview.panel.view" class="mt-2 w-full text-left">
						<thead class="text-xs text-ink-gray-5">
							<tr>
								<th class="py-1 pr-3 font-medium"></th>
								<th class="py-1 pr-3 font-medium">Before</th>
								<th class="py-1 pr-3 font-medium"></th>
								<th class="py-1 font-medium">After</th>
							</tr>
						</thead>
						<tbody>
							<tr v-for="row in preview.panel.view.rows" :key="row.label" class="align-top">
								<td class="py-1 pr-3 text-ink-gray-6">{{ row.label }}</td>
								<td class="py-1 pr-3 text-ink-gray-8">
									{{ row.before }}
									<div v-if="row.note" class="text-xs text-ink-gray-5">{{ row.note }}</div>
								</td>
								<td class="py-1 pr-3 text-ink-gray-5">→</td>
								<td class="py-1 text-ink-gray-9">
									{{ row.after }}<span v-if="row.unchanged" class="ml-1 text-xs text-ink-gray-5">(unchanged)</span>
								</td>
							</tr>
						</tbody>
					</table>
					<dl v-if="preview.panel.view" class="mt-2 grid grid-cols-[12rem_1fr] gap-x-3 gap-y-1">
						<dt class="text-ink-gray-6">Periods affected</dt>
						<dd class="text-ink-gray-8">{{ preview.panel.view.periods }}</dd>
						<dt class="text-ink-gray-6">Will need re-signing when approved</dt>
						<dd class="text-ink-gray-8">
							<template v-if="preview.panel.view.resignNone">{{ preview.panel.view.resignNone }}</template>
							<ul v-else>
								<li v-for="p in preview.panel.view.resign" :key="p">{{ p }}</li>
							</ul>
						</dd>
					</dl>
					<p v-if="preview.panel.view" class="mt-2 flex items-start gap-1 text-xs text-ink-gray-5">
						<FeatherIcon name="info" class="mt-0.5 h-3 w-3 shrink-0" />{{ preview.panel.view.notShown }}
					</p>
					<ul v-if="preview.panel.problems.length" role="alert" class="mt-2 flex flex-col gap-1">
						<li
							v-for="(problem, i) in preview.panel.problems"
							:key="i"
							class="flex items-start gap-2 rounded border border-outline-red-1 bg-surface-red-1 px-3 py-2 text-ink-gray-8"
						>
							<FeatherIcon name="x" class="mt-0.5 h-4 w-4 shrink-0" /><span>{{ problem }}</span>
						</li>
					</ul>
				</template>
			</div>
		</template>

		<div v-if="!choices.error && choices.entities.length && choices.groups.length" class="mt-4 flex max-w-3xl flex-col items-end gap-2">
			<Button
				theme="gray"
				variant="solid"
				:loading="saving"
				:disabled="saveBlocked(preview.panel, preview.fresh, saving)"
				@click="saveDraft"
			>
				Save draft
			</Button>
			<p v-if="saveError" role="alert" class="text-sm text-ink-red-4">{{ saveError }}</p>
		</div>
		<p v-if="saved" role="status" class="mt-2 text-sm text-ink-green-4">{{ saved }}</p>
	</section>
</template>
