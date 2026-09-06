import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import frappeui from "frappe-ui/vite";

const root = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
	plugins: [
		// frappe-ui components import their glyphs as `~icons/lucide/*`, which
		// needs the library's own icon resolver. Its proxy/boot-data/build-config
		// helpers assume a standard Frappe SPA layout, so they stay off — konsol
		// serves this bundle from konsol/public/konsol_exec, not from a
		// frontend/ directory.
		frappeui({ frappeProxy: false, jinjaBootData: false, buildConfig: false }),
		vue(),
	],
	base: "/assets/konsol/konsol_exec/",
	resolve: { alias: { "@": path.resolve(root, "src") } },
	build: {
		outDir: path.resolve(root, "../konsol/public/konsol_exec"),
		emptyOutDir: true,
		rollupOptions: {
			output: {
				entryFileNames: "konsol_exec.js",
				chunkFileNames: "konsol_exec.[name].js",
				assetFileNames: "konsol_exec.[ext]",
			},
		},
	},
});
