import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const root = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
	plugins: [react()],
	base: "/assets/konsol/konsol_exec/",
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