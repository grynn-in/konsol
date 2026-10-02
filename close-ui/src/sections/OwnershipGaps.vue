<script setup>
/**
 * konsol#305 E411: the Rates & ownership screen's "Ownership" tab —
 * entities with a submitted trial balance but no ownership covering the
 * period's start (#305-W2-2: a valid TB with no ownership is blocking),
 * each with a "Record ownership" link; plus the out-of-scope entities, for
 * information, and the in-scope count (#305-W2-3).
 *
 * Presentational only: it takes `ownershipView(payload)` (rates.js, E407)
 * as its `view` prop and never calls api.js. `ownershipView` collapses the
 * out-of-scope entities to a count (`outOfScopeCount`); the raw names come
 * in separately as the `outOfScope` prop, read straight from the payload
 * `get_ownership` returns, because Rates.vue (E409) already holds that
 * payload to build `view` and there is no reason to lose the names along
 * the way.
 *
 * The Record ownership link's `href` is exactly the server's own `desk`
 * field (E406): the section never builds that URL itself, so there is one
 * place that knows the Desk route. It opens in a new tab and says so, so
 * the one Desk hop stays visible (0.4 is excepted here until story 4.2).
 * It renders only when `view.canRecord` (E406: a Viewer, or anyone with no
 * create permission on Ownership Period, sees no link).
 */
defineProps({
	/** `ownershipView(payload)` (rates.js): `{blocking, outOfScopeCount,
	 * inScopeCount, canRecord, hiddenCount}`, or null while loading. */
	view: { type: Object, default: null },
	/** The period's out-of-scope entity codes, from the raw `get_ownership`
	 * payload (`out_of_scope`): the names `view` does not carry. */
	outOfScope: { type: Array, default: () => [] },
});
</script>

<template>
	<div>
		<p
			v-if="view && view.hiddenCount > 0"
			class="mb-3 text-sm text-ink-gray-6"
		>
			{{ view.hiddenCount }} {{ view.hiddenCount === 1 ? "entity" : "entities" }} outside your scope are not shown.
		</p>

		<p
			v-if="!view || !view.blocking.length"
			class="rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3 text-sm text-ink-gray-7"
		>
			No ownership gaps for this period.
		</p>
		<ul v-else class="divide-y divide-outline-gray-2 rounded border border-outline-gray-2">
			<li v-for="item in view.blocking" :key="item.entity" class="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
				<div class="min-w-0">
					<p class="text-sm font-medium text-ink-gray-9">{{ item.entity }}</p>
					<p class="mt-1 text-sm text-ink-gray-7">{{ item.message }}</p>
				</div>
				<a
					v-if="view.canRecord"
					:href="item.desk"
					target="_blank"
					rel="noopener"
					class="shrink-0 rounded border border-outline-gray-3 px-3 py-1.5 text-sm font-medium text-ink-gray-8 hover:bg-surface-gray-1"
				>
					Record ownership (opens Desk)
				</a>
			</li>
		</ul>

		<div v-if="view" class="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm text-ink-gray-7">
			<details>
				<summary class="cursor-pointer select-none">
					{{ view.outOfScopeCount }} {{ view.outOfScopeCount === 1 ? "entity" : "entities" }} out of scope
				</summary>
				<ul v-if="outOfScope.length" class="mt-2 flex flex-wrap gap-x-4 gap-y-1">
					<li v-for="name in outOfScope" :key="name">{{ name }}</li>
				</ul>
			</details>
			<p>{{ view.inScopeCount }} {{ view.inScopeCount === 1 ? "entity" : "entities" }} in scope for this period</p>
		</div>
	</div>
</template>
