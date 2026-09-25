// konsol#305 B02: config.test.mjs
//
// Source-level checks on vite.config.js, tailwind.config.js and
// postcss.config.js. These are read as text rather than imported, because
// vite.config.js imports "vite", "@vitejs/plugin-vue" and "frappe-ui/vite",
// none of which are installed until yarn install runs (B01 established the
// no-node_modules convention for the plain source tests).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CLOSE_UI_DIR = path.resolve(__dirname, "..");
const VITE_CONFIG = path.join(CLOSE_UI_DIR, "vite.config.js");
const TAILWIND_CONFIG = path.join(CLOSE_UI_DIR, "tailwind.config.js");
const POSTCSS_CONFIG = path.join(CLOSE_UI_DIR, "postcss.config.js");

function read(p) {
  return fs.readFileSync(p, "utf8");
}

test("vite.config.js sets the close asset base", () => {
  const source = read(VITE_CONFIG);
  assert.match(source, /base:\s*["']\/assets\/konsol\/close\/["']/);
});

test("vite.config.js builds into konsol/public/close", () => {
  const source = read(VITE_CONFIG);
  assert.match(
    source,
    /outDir:\s*path\.resolve\(root,\s*["']\.\.\/konsol\/public\/close["']\)/,
  );
});

test("vite.config.js fixes the entry, chunk and asset file names to close.*", () => {
  const source = read(VITE_CONFIG);
  assert.match(source, /entryFileNames:\s*["']close\.js["']/);
  assert.match(source, /chunkFileNames:\s*["']close\.\[name\]\.js["']/);
  assert.match(source, /assetFileNames:\s*["']close\.\[ext\]["']/);
});

test("vite.config.js keeps the frappeui plugin off proxy/boot/build-config, and the vue plugin", () => {
  const source = read(VITE_CONFIG);
  assert.match(
    source,
    /frappeui\(\{\s*frappeProxy:\s*false,\s*jinjaBootData:\s*false,\s*buildConfig:\s*false\s*\}\)/,
  );
  assert.match(source, /vue\(\)/);
});

test("failure path: outDir must not contain konsol_exec", () => {
  const source = read(VITE_CONFIG);
  assert.ok(
    !source.includes("konsol_exec"),
    "vite.config.js must not build into the konsol_exec output directory",
  );
});

test("tailwind.config.js uses the frappe-ui preset", () => {
  const source = read(TAILWIND_CONFIG);
  assert.match(source, /from\s+["']frappe-ui\/tailwind["']/);
  assert.match(source, /presets:\s*\[\s*frappeUIPreset\s*\]/);
});

test("tailwind.config.js declares the IBM Plex font family under theme.extend.fontFamily", () => {
  const source = read(TAILWIND_CONFIG);
  assert.match(source, /theme:\s*\{\s*extend:\s*\{\s*fontFamily:/);
  assert.match(source, /sans:\s*\[\s*["']IBM Plex Sans["']/);
  assert.match(source, /mono:\s*\[\s*["']IBM Plex Mono["']/);
});

test("postcss.config.js declares tailwindcss and autoprefixer", () => {
  const source = read(POSTCSS_CONFIG);
  assert.match(source, /tailwindcss:\s*\{\}/);
  assert.match(source, /autoprefixer:\s*\{\}/);
});
