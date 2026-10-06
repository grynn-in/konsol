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
  assert.match(tpl, /:disabled="[^"]*!accepts\(\{ type: 'REJECT', reason: reason \}\)[^"]*"/);
  assert.match(source, /send\(\{ type: ["']REJECT["'], reason: reason\.value \}\)/);
});

test("the reason is a labelled, required <textarea>", () => {
  const tpl = template(read());
  assert.match(tpl, /<label[^>]*\bfor="signoff-reject-reason"/);
  const area = tpl.match(/<textarea\b[^>]*\bid="signoff-reject-reason"[^>]*>/);
  assert.ok(area, "a <textarea id=\"signoff-reject-reason\">");
  assert.match(area[0], /\brequired\b/);
  assert.match(area[0], /v-model="reason"/);
});

test("refusal keeps the text: the dialog follows rejectDialogAfter, and shows the server's message", () => {
  const src = scripts(read());
  assert.match(src, /import\s*\{[^}]*\brejectDialogAfter\b[^}]*\}\s*from\s*["']\.\.\/signoff\.js["']/);
  assert.match(src, /rejectDialogAfter\(/);
  assert.match(src, /messageLines\(/);
  assert.match(template(read()), /role="alert"/);
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
