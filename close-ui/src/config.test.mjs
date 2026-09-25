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

// konsol#305 B26: the entry and its stylesheet are content-hashed, and so
// are the route chunks. A fixed "close.js" loaded by www/close.html with a
// `?v=` query differed from the "./close.js" the lazy chunks import, so the
// browser ran the module twice and a period screen rendered blank (C1 run 1).
// With hashed names the page reads the name from build-manifest.json and
// needs no query: page and chunks import one identical URL.
test("vite.config.js content-hashes the entry and chunk file names", () => {
  const source = read(VITE_CONFIG);
  assert.match(source, /entryFileNames:\s*["']close\.\[hash\]\.js["']/);
  assert.match(source, /chunkFileNames:\s*["']close\.\[name\]\.\[hash\]\.js["']/);
  assert.doesNotMatch(source, /entryFileNames:\s*["']close\.js["']/);
});

test("vite.config.js content-hashes the entry stylesheet and every other asset", () => {
  const source = read(VITE_CONFIG);
  assert.match(source, /assetFileNames:\s*\(assetInfo\)\s*=>/);
  assert.match(source, /original\s*===\s*["']index\.css["']/);
  assert.match(source, /return\s*["']close\.\[hash\]\.css["']/);
  assert.doesNotMatch(source, /return\s*["']close\.css["']/);
  assert.match(source, /return\s*["']close\.\[name\]\.\[hash\]\[extname\]["']/);
});

test("vite.config.js writes the entry and its stylesheet into build-manifest.json", () => {
  const source = read(VITE_CONFIG);
  assert.match(source, /build-manifest\.json/);
  assert.match(source, /entry:\s*\w/);
  assert.match(source, /css:\s*\w/);
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
