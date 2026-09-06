import frappeUIPreset from "frappe-ui/tailwind";

/** frappe-ui owns the palette, type scale and spacing. We add nothing to it —
 *  that is the point of adopting the design system rather than copying it. */
export default {
	presets: [frappeUIPreset],
	content: [
		"./index.html",
		"./src/**/*.{vue,js}",
		"./node_modules/frappe-ui/src/components/**/*.{vue,js,ts}",
	],
};
