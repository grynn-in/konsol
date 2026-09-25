import { createHash } from "node:crypto";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import frappeui from "frappe-ui/vite";

const root = path.dirname(fileURLToPath(import.meta.url));

// konsol#305 B25: the committed bundle in konsol/public/close must be
// reproducible from close-ui/src. This hashes every file under src/
// (sorted by relative path, so the result does not depend on directory
// read order) and writes it to build-manifest.json in the outDir.
// konsol/tests/test_close_bundle.py recomputes the SAME hash from the
// checked-out src/ tree and fails if it does not match: a bundle that
// was not rebuilt after a source change is caught by the host suite
// instead of shipping unnoticed. The algorithm here and in the Python
// test must stay byte-for-byte identical.
function hashSrcDir(srcDir) {
	const files = [];
	const walk = (dir) => {
		for (const entry of readdirSync(dir, { withFileTypes: true })) {
			const full = path.join(dir, entry.name);
			if (entry.isDirectory()) walk(full);
			else files.push(full);
		}
	};
	walk(srcDir);
	files.sort();
	const hash = createHash("sha256");
	for (const file of files) {
		const rel = path.relative(srcDir, file).split(path.sep).join("/");
		hash.update(rel, "utf8");
		hash.update("\u0000");
		hash.update(readFileSync(file));
		hash.update("\u0000");
	}
	return hash.digest("hex");
}

function sourceManifestPlugin() {
	let outDir;
	return {
		name: "konsol-close-source-manifest",
		configResolved(config) {
			// Honour --outDir overrides (other rows' gate builds to a scratch
			// directory so the committed bundle stays untouched until B25), so
			// the manifest always lands next to whatever bundle was actually
			// written, never forced into konsol/public/close.
			outDir = config.build.outDir;
		},
		closeBundle() {
			const srcHash = hashSrcDir(path.resolve(root, "src"));
			writeFileSync(
				path.resolve(outDir, "build-manifest.json"),
				JSON.stringify({ srcHash }, null, 2) + "\n",
			);
		},
	};
}

export default defineConfig({
	plugins: [
		// frappe-ui components import their glyphs as `~icons/lucide/*`, which
		// needs the library's own icon resolver. Its proxy/boot-data/build-config
		// helpers assume a standard Frappe SPA layout, so they stay off — konsol
		// serves this bundle from konsol/public/close, not from a frontend/
		// directory.
		frappeui({ frappeProxy: false, jinjaBootData: false, buildConfig: false }),
		vue(),
		sourceManifestPlugin(),
	],
	base: "/assets/konsol/close/",
	resolve: { alias: { "@": path.resolve(root, "src") } },
	build: {
		outDir: path.resolve(root, "../konsol/public/close"),
		emptyOutDir: true,
		rollupOptions: {
			output: {
				entryFileNames: "close.js",
				chunkFileNames: "close.[name].js",
				assetFileNames: "close.[ext]",
			},
		},
	},
});
