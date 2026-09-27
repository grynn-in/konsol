// konsol#305 B01: guard.test.mjs
//
// This test enforces D4/D6: nothing under close-ui/src may import from
// konsol-exec. Reading konsol-exec to mirror its patterns is fine; importing
// from it is not.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC_DIR = __dirname; // close-ui/src
const PACKAGE_JSON = path.resolve(__dirname, "..", "package.json");
const THIS_FILE = fileURLToPath(import.meta.url);

// Matches the specifier string of `import ... from "spec"`, `import("spec")`
// and `require("spec")`, with either quote style.
const IMPORT_SPECIFIER_RE =
  /(?:from\s+|import\(|require\()\s*['"]([^'"]+)['"]/g;

function forbiddenImportSpecifiers(source) {
  const found = [];
  let m;
  IMPORT_SPECIFIER_RE.lastIndex = 0;
  while ((m = IMPORT_SPECIFIER_RE.exec(source)) !== null) {
    const specifier = m[1];
    if (specifier.includes("konsol-exec") || specifier.includes("konsol_exec")) {
      found.push(specifier);
    }
  }
  return found;
}

function walk(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules") continue;
      out.push(...walk(full));
    } else {
      out.push(full);
    }
  }
  return out;
}

test("no file under close-ui/src imports from konsol-exec", () => {
  const allFiles = walk(SRC_DIR);
  assert.ok(allFiles.length > 0, "expected at least one file under close-ui/src");
  // Excludes this file itself: its own failure-path test below deliberately
  // contains the forbidden substring as fixture data, not as a real import.
  const files = allFiles.filter((f) => f !== THIS_FILE);
  const offenders = [];
  for (const file of files) {
    const source = fs.readFileSync(file, "utf8");
    const hits = forbiddenImportSpecifiers(source);
    if (hits.length > 0) {
      offenders.push(`${path.relative(SRC_DIR, file)}: ${hits.join(", ")}`);
    }
  }
  assert.deepEqual(offenders, []);
});

test("package.json declares xstate and vue-router", () => {
  const pkg = JSON.parse(fs.readFileSync(PACKAGE_JSON, "utf8"));
  assert.ok(pkg.dependencies && pkg.dependencies.xstate, "xstate must be a dependency");
  assert.ok(pkg.dependencies && pkg.dependencies["vue-router"], "vue-router must be a dependency");
});

test("the scanner catches a bad import (failure path)", () => {
  const synthetic = `import x from "../../konsol-exec/src/api.js";\n`;
  const hits = forbiddenImportSpecifiers(synthetic);
  assert.deepEqual(hits, ["../../konsol-exec/src/api.js"]);
});
