// konsol#305 story 9.4 (#157), #305-W5-1: signOffReject.section.test.mjs
//
// Source-level checks on sections/SignOffReject.vue and its mount in
// screens/SignOff.vue. A .vue file only compiles inside the Vite build, which
// the row's gate runs separately. The machine's REJECT / rejecting behaviour
// is signoffMachine.test.mjs's; the dialog's keep/refused/reset rule is
// signoff.js's rejectDialogAfter (signoff.test.mjs). Here: the section
// provides the reject service, posts the endpoint from one place, offers
// Reject only when the machine takes REJECT, and its reason is a labelled,
// required <textarea> whose text travels in the event.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SECTION = path.join(__dirname, "SignOffReject.vue");
const SCREEN = path.join(__dirname, "..", "screens", "SignOff.vue");
const REJECT = "konsol.close.signoff_api.reject";

const read = (file = SECTION) => fs.readFileSync(file, "utf8");

function scripts(source) {
  const bodies = [...source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
  assert.ok(bodies.length, "the file has a <script>");
  return bodies.join("\n");
}

function template(source) {
  const start = source.indexOf("<template>");
  const end = source.lastIndexOf("</template>");
  assert.ok(start >= 0 && end > start, "the file has a <template>");
  return source.slice(start, end);
}

test("the section exists and SignOff.vue mounts it and spreads its reject service", () => {
  assert.ok(fs.existsSync(SECTION), "sections/SignOffReject.vue exists");
  const screen = read(SCREEN);
  assert.match(
    screen,
    /import\s+SignOffReject\s*,\s*\{[^}]*\brejectActors\b[^}]*\}\s*from\s*["']\.\.\/sections\/SignOffReject\.vue["']/,
  );
  assert.match(screen, /\.\.\.rejectActors\(key\)/, "provide({actors}) gets the reject service");
  assert.match(template(screen), /<SignOffReject\b[\s\S]*?:snapshot="snap"[\s\S]*?@send="send"/);
});

test("the reject endpoint is posted from one place, with the run and the reason the machine passes", () => {
  const src = scripts(read());
  assert.equal(src.split(REJECT).length - 1, 1, "the endpoint is named once");
  assert.equal((src.match(/\bpost\(/g) || []).length, 1, "one post( call site");
  assert.match(src, /export function rejectActors\(key\)/);
  assert.match(src, /post\(\s*REJECT\s*,\s*\{\s*\.\.\.key\s*,\s*run:\s*input\.run\s*,\s*reason:\s*input\.reason\s*\}\s*\)/);
});

test("Reject is offered only when the machine accepts REJECT, and sends the typed reason", () => {
  const source = read();
  const tpl = template(source);
  assert.match(tpl, /v-if="accepts\(\{ type: 'REJECT', reason: ANY_REASON \}\)"/);
  assert.match(tpl, /:disabled="[^"]*!accepts\(\{ type: 'REJECT', reason: dialog\.reason \}\)[^"]*"/);
  assert.match(source, /send\(\{ type: ["']REJECT["'], reason: dialog\.value\.reason \}\)/);
});

test("the reason is a labelled, required <textarea>", () => {
  const tpl = template(read());
  assert.match(tpl, /<label[^>]*\bfor="signoff-reject-reason"/);
  const area = tpl.match(/<textarea\b[^>]*\bid="signoff-reject-reason"[^>]*>/);
  assert.ok(area, "a <textarea id=\"signoff-reject-reason\">");
  assert.match(area[0], /\brequired\b/);
  assert.match(area[0], /:value="dialog\.reason"/);
  assert.match(area[0], /@input="apply\(\{ type: 'TYPE', text: \$event\.target\.value \}\)"/);
});

// U3/U10: what the dialog does is rejectDialogNext's, driven against the real
// machine in signoff.test.mjs. Here: the section applies it to every change.
test("the dialog state is rejectDialogNext's alone: machine changes, typing, Esc/overlay and Cancel all go through it", () => {
  const source = read();
  const src = scripts(source);
  const tpl = template(source);
  assert.match(src, /import\s*\{[^}]*\brejectDialogNext\b[^}]*\}\s*from\s*["']\.\.\/signoff\.js["']/);
  assert.match(src, /dialog\.value\s*=\s*rejectDialogNext\(\s*dialog\.value\s*,/);
  assert.match(src, /type:\s*["']MACHINE["'][\s\S]*?error:\s*props\.snapshot\s*\?\s*props\.snapshot\.context\.error/);
  // The Dialog's own close (Esc, overlay, its X) is an update:modelValue: it
  // must CLOSE through the reducer, never just flip `open` (U10).
  assert.match(tpl, /<Dialog\b[^>]*:model-value="dialog\.open"[^>]*@update:model-value="onDialogModel"/);
  assert.doesNotMatch(tpl, /<Dialog\b[^>]*v-model=/);
  assert.match(src, /function onDialogModel\(value\)\s*\{\s*apply\(\{\s*type:\s*value\s*\?\s*["']OPEN["']\s*:\s*["']CLOSE["']\s*\}\)/);
  assert.match(tpl, /@click="apply\(\{ type: 'CLOSE' \}\)"[^>]*>|>\s*Cancel/);
  assert.match(tpl, /v-for="\(line, i\) in dialog\.refused"/);
  assert.match(tpl, /role="alert"/);
});

test("no machine guard is re-implemented, no browser dialog, nothing stored", () => {
  const source = read();
  assert.doesNotMatch(scripts(source), /\.trim\(/, "blank reasons are the machine's (hasReason)");
  assert.doesNotMatch(source, /can_reject/, "the role gate is the machine's (canReject)");
  assert.doesNotMatch(source, /window\.(prompt|confirm|alert)|\bprompt\(|\bconfirm\(/);
  for (const store of ["local" + "Storage", "session" + "Storage", "indexed" + "DB"]) {
    assert.ok(!source.includes(store), `no ${store}`);
  }
});
