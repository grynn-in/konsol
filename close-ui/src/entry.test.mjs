// konsol#305 B03: entry.test.mjs
//
// Source-level checks on the app entry: index.html, main.js, App.vue and
// index.css. These are read as text rather than imported, because main.js
// and App.vue import "vue" and "frappe-ui", neither of which is installed
// until yarn install runs (B01 established the no-node_modules convention
// for the plain source tests). The real proof that the entry actually
// builds is the vite build step in the row's gate, run separately.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CLOSE_UI_DIR = path.resolve(__dirname, "..");
const INDEX_HTML = path.join(CLOSE_UI_DIR, "index.html");
const MAIN_JS = path.join(__dirname, "main.js");
const APP_VUE = path.join(__dirname, "App.vue");
const INDEX_CSS = path.join(__dirname, "index.css");

function read(p) {
  return fs.readFileSync(p, "utf8");
}

test("index.html points to /src/main.js and mounts #root", () => {
  const source = read(INDEX_HTML);
  assert.match(source, /<div id="root">/);
  assert.match(source, /<script type="module" src="\/src\/main\.js">/);
});

test("main.js mounts #root", () => {
  const source = read(MAIN_JS);
  assert.match(source, /\.mount\(\s*["']#root["']\s*\)/);
});

test("main.js imports ./index.css", () => {
  const source = read(MAIN_JS);
  assert.match(source, /import\s+["']\.\/index\.css["']/);
});

test("main.js creates the app from App.vue and mounts FrappeUIProvider", () => {
  const source = read(MAIN_JS);
  assert.match(source, /import\s+App\s+from\s+["']\.\/App\.vue["']/);
  assert.match(source, /createApp/);
});

test("App.vue uses FrappeUIProvider", () => {
  const source = read(APP_VUE);
  assert.match(source, /<FrappeUIProvider>/);
  assert.match(source, /<\/FrappeUIProvider>/);
});

test("B17: App.vue mounts the router through AppShell, not a static placeholder", () => {
  // B16 installed the router (main.js); B17 renders it. App.vue wraps
  // AppShell, and AppShell holds the <router-view/>.
  const source = read(APP_VUE);
  assert.match(source, /import\s+AppShell\s+from\s+["']\.\/components\/AppShell\.vue["']/);
  assert.match(source, /<AppShell\s*\/>/);
  assert.doesNotMatch(source, /konsol close/, "the B03 placeholder is gone");
  const shell = read(path.join(__dirname, "components", "AppShell.vue"));
  assert.match(shell, /<router-view|<RouterView/);
});

test("B17: main.js installs the router that App.vue renders", () => {
  const source = read(MAIN_JS);
  assert.match(source, /app\.use\(\s*createCloseRouter\(\)\s*\)/);
});

test("index.css declares the tailwind directives and bundles IBM Plex Sans", () => {
  const source = read(INDEX_CSS);
  assert.match(source, /@tailwind base;/);
  assert.match(source, /@tailwind components;/);
  assert.match(source, /@tailwind utilities;/);
  assert.match(source, /@import\s+["']@fontsource\/ibm-plex-sans["'];/);
});
