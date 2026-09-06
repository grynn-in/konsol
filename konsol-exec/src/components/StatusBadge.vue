<script setup>
/**
 * The single status component (U5).
 *
 * The old app had two: a 7px colour-only dot in the nav and cards, and a
 * glyph+colour row in the Setup checklist. The dot failed twice over — red
 * against green is the commonest colour-vision deficiency, and seven pixels of
 * hue carries almost nothing at a glance. Everything now uses this, and the
 * glyph is what carries the meaning; colour agrees with it rather than being
 * asked to do the job alone.
 */
import { computed } from "vue";
import { Badge, FeatherIcon, Spinner } from "frappe-ui";
import { STATUS, SETUP } from "../constants.js";

const props = defineProps({
	state: { type: String, required: true },
	kind: { type: String, default: "run" }, // "run" | "setup"
	size: { type: String, default: "md" },
	label: { type: String, default: "" },
});

const meta = computed(() => {
	const table = props.kind === "setup" ? SETUP : STATUS;
	return table[props.state] || table.idle || table.missing;
});
</script>

<template>
	<Badge :theme="meta.theme" :size="size" variant="subtle">
		<template #prefix>
			<Spinner v-if="meta.glyph === 'loader'" class="h-3 w-3" />
			<FeatherIcon v-else :name="meta.glyph" class="h-3 w-3" />
		</template>
		{{ label || meta.label }}
	</Badge>
</template>
