import frappeUIPreset from "frappe-ui/tailwind";

/** frappe-ui owns the palette, type scale and spacing. We add the close
 *  app's IBM Plex typography on top of it. */
export default {
	presets: [frappeUIPreset],
	content: [
		"./index.html",
		"./src/**/*.{vue,js}",
		"./node_modules/frappe-ui/src/components/**/*.{vue,js,ts}",
	],
	theme: {
		extend: {
			fontFamily: {
				sans: ["IBM Plex Sans", "sans-serif"],
				mono: ["IBM Plex Mono", "monospace"],
			},
		},
	},
};
