// konsol#305 B24: signOffPeriodActions.section.test.mjs
//
// Source-level checks on sections/SignOffPeriodActions.vue (declare a TB
// exception, close, reopen; stories 9.1, 9.3, 9.5) and on its mount in
// screens/SignOff.vue. A .vue file only compiles inside the Vite build, which
// the row's gate runs separately (Problems found 17: C1 is the real check for
// screens). The machine's own behaviour is B14's test; this checks the section
// provides its close/reopen actors, sends CLOSE / REOPEN / REFRESH only when
// the machine takes them, and posts each endpoint from exactly one place.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SECTION = path.join(__dirname, "SignOffPeriodActions.vue");
const SCREEN = path.join(__dirname, "..", "screens", "SignOff.vue");

const CLOSE_PERIOD = "konsol.close.signoff_api.close_period";
const REOPEN_PERIOD = "konsol.close.signoff_api.reopen_period";
const DECLARE = "konsol.close.signoff_api.declare_tb_exception";

function read(file = SECTION) {
  return fs.readFileSync(file, "utf8");
}

/** Every <script> block's body, joined (a plain <script> plus <script setup>). */
function scripts(source) {
  const bodies = [...source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
  assert.ok(bodies.length, "the file has a <script>");
  return bodies.join("\n");
}

/** The plain (non-setup) <script>: where named exports live. */
function plainScript(source) {
  const m = source.match(/<script(?![^>]*\bsetup\b)[^>]*>([\s\S]*?)<\/script>/);
  assert.ok(m, "the file has a plain <script> for its named export");
  return m[1];
}

function template(source) {
  const start = source.indexOf("<template>");
  const end = source.lastIndexOf("</template>");
  assert.ok(start >= 0 && end > start, "the file has a <template>");
  return source.slice(start, end);
}

/** The opening tag that contains index `i` of `tpl`. */
function tagAt(tpl, i) {
  const start = tpl.lastIndexOf("<", i);
  return tpl.slice(start, tpl.indexOf(">", i) + 1);
}

/** Every opening tag carrying `attr`. */
function tagsWith(tpl, attr) {
  const out = [];
  let i = tpl.indexOf(attr);
  while (i >= 0) {
    out.push(tagAt(tpl, i));
    i = tpl.indexOf(attr, i + attr.length);
  }
  return out;
}

/** The <textarea>/<input> whose id is `id`, and a <label for> naming it. */
function labelled(tpl, id) {
  assert.match(tpl, new RegExp(`<label[^>]*\\bfor="${id}"`), `a <label for="${id}">`);
  const m = tpl.match(new RegExp(`<(textarea|input)\\b[^>]*\\bid="${id}"[^>]*>`));
  assert.ok(m, `a <textarea> or <input> with id="${id}"`);
  return m[0];
}

test("the section file exists and SignOff.vue mounts it", () => {
  assert.ok(fs.existsSync(SECTION), "sections/SignOffPeriodActions.vue exists");
  const screen = read(SCREEN);
  assert.match(
    screen,
    /import\s+SignOffPeriodActions\s*,\s*\{[^}]*\bperiodActors\b[^}]*\}\s*from\s*["']\.\.\/sections\/SignOffPeriodActions\.vue["']/,
  );
  assert.match(template(screen), /<SignOffPeriodActions\b/);
});

test("close and reopen are the machine's actors: exported by the section, provided by the screen", () => {
  const source = read();
  const plain = plainScript(source);
  assert.match(plain, /export\s+function\s+periodActors\s*\(/);
  assert.match(plain, /\bclose\s*:\s*fromPromise\(/, "the close actor");
  assert.match(plain, /\breopen\s*:\s*fromPromise\(/, "the reopen actor");
  assert.match(plain, /\bnote\s*:\s*input\.note\b/, "close sends the machine's note (optional)");
  assert.match(plain, /\breason\s*:\s*input\.reason\b/, "reopen sends the machine's reason");
  const screen = scripts(read(SCREEN));
  assert.match(screen, /signoffMachine\.provide\(\s*\{\s*actors\s*:\s*\{[\s\S]*\.\.\.periodActors\(\s*key\s*\)/,
    "the screen spreads periodActors(key) into provide({actors})");
});

test("one post( call site per endpoint: close_period, reopen_period, declare_tb_exception", () => {
  const source = read();
  const js = scripts(source);
  for (const endpoint of [CLOSE_PERIOD, REOPEN_PERIOD, DECLARE]) {
    assert.equal(source.split(endpoint).length - 1, 1, `${endpoint} is named once`);
  }
  assert.equal((js.match(/\bpost\(/g) || []).length, 3, "three post( call sites, one per endpoint");
  assert.match(js, /import\s*\{[^}]*\bpost\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
  assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js");
  // The screen still has only its own post (sign).
  assert.equal((scripts(read(SCREEN)).match(/\bpost\(/g) || []).length, 1, "SignOff.vue keeps one post( call site");
});

test("every event the section sends is gated by the machine (snapshot.can)", () => {
  const source = read();
  const tpl = template(source);
  const tags = tagsWith(tpl, '@click="');
  assert.ok(tags.length >= 3, "Close, Reopen, Confirm reopen buttons");
  for (const tag of tagsWith(tpl, "send(")) {
    assert.match(tag, /(v-if|:disabled)="[^"]*\baccepts\(/, `gated by the machine: ${tag}`);
  }
  const js = scripts(source);
  assert.match(js, /\.can\(/, "accepts() asks the machine");
  for (const event of ["CLOSE", "REOPEN", "REFRESH"]) {
    assert.match(source, new RegExp(`type:\\s*["']${event}["']`), `${event} is sent`);
  }
  assert.doesNotMatch(js, /\.trim\(/, "blank-text checks are the machine's and the server's");
  assert.doesNotMatch(js, /["']Locked["']/, "whether a Locked period reopens is the machine's call");
});

test("Close: shown only when the machine accepts CLOSE, with a labelled optional note in the event", () => {
  const source = read();
  const tpl = template(source);
  assert.match(tpl, /v-if="[^"]*accepts\(\s*\{\s*type:\s*'CLOSE'/, "Close is shown only when CLOSE is accepted");
  const note = labelled(tpl, "signoff-close-note");
  assert.match(note, /^<textarea/);
  assert.doesNotMatch(note, /\brequired\b/, "the note is optional");
  assert.match(source, /type:\s*'CLOSE'\s*,\s*note:/, "the note travels in CLOSE");
});

test("Reopen: a frappe-ui Dialog with a required, labelled reason, and the A31 warning", () => {
  const source = read();
  const tpl = template(source);
  assert.match(scripts(source), /import\s*\{[^}]*\bDialog\b[^}]*\}\s*from\s*["']frappe-ui["']/);
  assert.match(tpl, /<Dialog\b/);
  assert.match(tpl, /v-if="[^"]*accepts\(\s*\{\s*type:\s*'REOPEN'/, "the Reopen opener is shown only when REOPEN is accepted");
  const reason = labelled(tpl, "signoff-reopen-reason");
  assert.match(reason, /^<textarea/);
  assert.match(reason, /\brequired\b/);
  assert.match(source, /type:\s*'REOPEN'\s*,\s*reason:/, "the reason travels in REOPEN");
  assert.match(tpl, /Later signed periods will need re-signing/);
});

test("Declare TB exception: labelled entity and required reason, only for the Close Lead while TBs are missing", () => {
  const source = read();
  const tpl = template(source);
  const entity = labelled(tpl, "signoff-exception-entity");
  assert.match(entity, /^<input/);
  assert.match(entity, /\brequired\b/);
  const reason = labelled(tpl, "signoff-exception-reason");
  assert.match(reason, /^<textarea/);
  assert.match(reason, /\brequired\b/);
  const js = scripts(source);
  assert.match(js, /completeness/, "reads the summary's completeness gate");
  assert.match(js, /\.missing\b/, "the missing TBs the summary names");
  assert.match(js, /can_override\s*===\s*true/, "Close Lead = the server's own role answer (OVERRIDE_ROLES)");
  assert.match(tpl, /v-if="[^"]*\bshowDeclare\b/, "the form is gated");
  assert.match(js, /showDeclare\s*=\s*computed\([^;]*isCloseLead[^;]*missing/);
});

test("Declare TB exception: REFRESH after success, the server's refusal shown verbatim", () => {
  const source = read();
  const js = scripts(source);
  assert.match(js, /await\s+post\(\s*DECLARE_TB_EXCEPTION/, "the declaration is awaited");
  assert.match(js, /catch\s*\(\s*(\w+)\s*\)\s*\{[^}]*\1\.message/, "the refusal is the server's own message");
  assert.match(js, /messageLines\(/, "split on <br> into plain lines, never v-html");
  assert.match(js, /type:\s*["']REFRESH["']/);
  // REFRESH comes after the awaited post, not before it.
  const postAt = js.search(/await\s+post\(\s*DECLARE_TB_EXCEPTION/);
  const refreshAt = js.search(/type:\s*["']REFRESH["']/);
  assert.ok(refreshAt > postAt, "REFRESH is sent after the declaration returns");
  assert.match(template(source), /role="alert"/);
});

test("Failure path: no browser dialogs, no v-html, no generic error, nothing stored in the browser", () => {
  const source = read();
  assert.doesNotMatch(source, /window\.(prompt|confirm|alert)|\bprompt\(|\bconfirm\(|\balert\(/);
  assert.doesNotMatch(source, /v-html/);
  assert.doesNotMatch(source, /Something went wrong/i);
  for (const store of ["local" + "Storage", "session" + "Storage", "indexed" + "DB"]) {
    assert.ok(!source.includes(store), `no ${store}`);
  }
});
