// konsol#305 B20: tbUpload.section.test.mjs
//
// Source-level checks on sections/TbUpload.vue (check, re-upload, submit;
// stories 3.2, 3.3, 3.5, 3.7) and on its mount in screens/TrialBalances.vue.
// A .vue file only compiles inside the Vite build, which the row's gate runs
// separately (Problems found 17: C1 is the real check for screens). The
// machine's own behaviour is B11's test; this checks the section wires it
// through machine.provide({actors}) and shows every state it can be in.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SECTION = path.join(__dirname, "TbUpload.vue");
const SCREEN = path.join(__dirname, "..", "screens", "TrialBalances.vue");

function read(file = SECTION) {
  return fs.readFileSync(file, "utf8");
}

function template(source) {
  const start = source.indexOf("<template>");
  const end = source.lastIndexOf("</template>");
  assert.ok(start >= 0 && end > start, "the file has a <template>");
  return source.slice(start, end);
}

function script(source) {
  const match = source.match(/<script setup>([\s\S]*?)<\/script>/);
  assert.ok(match, "the file has a <script setup>");
  return match[1];
}

/** The template block guarded by `v-if`/`v-else-if` naming `state`. */
function block(tpl, state) {
  const re = new RegExp(`<(\\w+)[^>]*v-(?:else-)?if="[^"]*${state.replace(".", "\\.")}[^"]*"[^>]*>`);
  const m = tpl.match(re);
  assert.ok(m, `a block is shown for the ${state} state`);
  const tag = m[1];
  const rest = tpl.slice(m.index);
  const close = rest.indexOf(`</${tag}>`);
  assert.ok(close > 0, `the ${state} block closes`);
  return rest.slice(0, close);
}

test("it drives B11's tbUploadMachine through useMachine, with actors provided, not copied", () => {
  const source = read();
  const js = script(source);
  assert.match(js, /import\s*\{[^}]*\btbUploadMachine\b[^}]*\}\s*from\s*["']\.\.\/machines\/tbUploadMachine\.js["']/);
  assert.match(js, /import\s*\{[^}]*\buseMachine\b[^}]*\}\s*from\s*["']@xstate\/vue["']/);
  assert.match(js, /tbUploadMachine\.provide\(\s*\{\s*actors\s*:/);
  for (const actor of ["readFile", "check", "submit"]) {
    assert.match(js, new RegExp(`\\b${actor}\\s*:\\s*fromPromise\\(`), `the ${actor} actor is provided`);
  }
  // The logic stays in the machine: no second machine is built here.
  assert.doesNotMatch(js, /createMachine\(|setup\(\s*\{/, "no machine logic copied into the section");
});

test("readFile uses FileReader.readAsText", () => {
  const js = script(read());
  assert.match(js, /new FileReader\(/);
  assert.match(js, /readAsText\(/);
});

test("check POSTs A19 check_tb with the content in the body; submit POSTs A24 submit_tb with replaces", () => {
  const source = read();
  const js = script(source);
  assert.match(js, /import\s*\{[^}]*\bpost\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
  assert.match(js, /konsol\.close\.tb_api\.check_tb/);
  assert.match(js, /konsol\.close\.tb_api\.submit_tb/);
  assert.match(js, /post\(\s*CHECK\s*,\s*\{[^}]*\bcontent\b[^}]*\}/, "check sends the content in the POST body");
  assert.match(js, /post\(\s*SUBMIT\s*,\s*\{[^}]*\breplaces\b[^}]*\}/, "submit passes replaces");
  assert.doesNotMatch(source, /\bget\(\s*CHECK/, "check is never a GET");
  assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js");
});

test("the check table is built with checkRows (B12) and shows the real CSV line", () => {
  const source = read();
  assert.match(script(source), /import\s*\{[^}]*\bcheckRows\b[^}]*\}\s*from\s*["']\.\.\/tbTable\.js["']/);
  assert.match(script(source), /checkRows\(/);
  const tpl = template(source);
  assert.match(tpl, /\.line\b/, "each row shows its line number from the server");
  assert.doesNotMatch(source, /index\s*\+\s*2|i\s*\+\s*2/, "the line is never re-derived from the row index");
  assert.match(tpl, /\.suggestion\b/, "each problem shows its suggestion");
  assert.match(tpl, /file_problems/, "file-level problems are listed");
  assert.match(tpl, /totals\.difference/, "the totals line shows the difference");
});

test("every machine state has something shown", () => {
  const tpl = template(read());
  for (const state of ["idle", "reading", "readFailed", "checking", "checkFailed", "checked.ok", "checked.problems", "submitting", "received"]) {
    assert.ok(tpl.includes(`'${state}'`) || tpl.includes(`"${state}"`), `the ${state} state is shown`);
  }
});

test("the Submit button is disabled unless the state is checked.ok", () => {
  const tpl = template(read());
  const buttons = [...tpl.matchAll(/<(?:button|Button)\b[^>]*>[\s\S]*?<\/(?:button|Button)>/g)].map((m) => m[0]);
  const submit = buttons.filter((b) => /Submit/.test(b.replace(/<[^>]*>/g, "")));
  assert.ok(submit.length >= 1, "there is a Submit button");
  for (const b of submit) {
    assert.match(b, /:disabled="[^"]*!\s*[^"]*checked\.ok/, "disabled when not in checked.ok");
    assert.match(b, /send\(\s*\{\s*type:\s*'SUBMIT'/, "it sends SUBMIT");
  }
});

test("period_problem is shown and blocks submit", () => {
  const source = read();
  const tpl = template(source);
  assert.match(tpl, /period_problem/);
  // The machine already routes a period_problem to checked.problems; the
  // section's own guard says so too, so a result with one never enables Submit.
  assert.match(script(source), /period_problem/);
});

test("when replaces is set, 'This replaces <TB name>' is shown before submit", () => {
  const tpl = template(read());
  assert.match(tpl, /This replaces/);
  assert.match(tpl, /context\.replaces/);
});

test("received shows the name and, for an on-behalf upload, the R4 label", () => {
  const tpl = template(read());
  const received = block(tpl, "received");
  assert.match(received, /Received/);
  assert.match(received, /received\.name/);
  assert.match(received, /on_behalf/);
  assert.match(received, /received\.replaced/);
});

test("failure path: checkFailed and readFailed render context.error, split into plain lines", () => {
  const source = read();
  const tpl = template(source);
  for (const state of ["checkFailed", "readFailed"]) {
    assert.match(block(tpl, state), /errorLines|context\.error/, `${state} shows the error`);
  }
  assert.match(block(tpl, "checkFailed"), /RETRY/, "a failed check can be retried");
  assert.match(script(source), /messageLines\(/, "server text with <br> becomes separate lines");
  assert.doesNotMatch(source, /v-html/);
});

test("failure path: a failed submit's error is shown in checked.ok", () => {
  const tpl = template(read());
  assert.match(block(tpl, "checked.ok"), /errorLines|context\.error/);
});

test("failure path: no row editing (D1); the only fix is choosing a corrected file", () => {
  const source = read();
  const tpl = template(source);
  assert.doesNotMatch(tpl, /contenteditable/i, "no editable cells");
  assert.doesNotMatch(tpl, /v-model=["'][^"']*(amount|debit|credit|balance|row)/i, "no input bound to a row value");
  assert.doesNotMatch(source, /EDIT_ROW/, "there is no edit event");
  assert.match(tpl, /type="file"/, "a file input exists");
  assert.match(source, /FILE_CHOSEN/, "a new file is the way out of problems");
  assert.match(block(tpl, "checked.problems"), /corrected file/i, "problems say to choose a corrected file");
});

test("the amount basis is chosen by the user from the three declared values, with no default", () => {
  const source = read();
  for (const basis of ["Period movement", "Year-to-date movement", "Period-end balance"]) {
    assert.ok(source.includes(`"${basis}"`), `${basis} is offered`);
  }
  assert.match(script(source), /const\s+basis\s*=\s*ref\(\s*(null|"")\s*\)/, "no basis is preselected");
});

test("failure path: no v-html, no window.open, no bare Loading… text", () => {
  const source = read();
  assert.doesNotMatch(source, /v-html/);
  assert.doesNotMatch(source, /window\.open\(|window\.prompt\(/);
  assert.doesNotMatch(template(source), />\s*Loading…?\s*</);
});

test("TrialBalances.vue mounts the section in the detail area, only for personas who upload", () => {
  const source = read(SCREEN);
  assert.match(script(source), /import\s+TbUpload\s+from\s*["']\.\.\/sections\/TbUpload\.vue["']/);
  const tpl = template(source);
  const detail = tpl.slice(tpl.indexOf("data-detail"));
  assert.match(detail, /<TbUpload\b/, "mounted inside the detail area");
  const tag = detail.match(/<TbUpload\b[^>]*>/)[0];
  assert.match(tag, /v-if="canUpload"/, "shown only when the persona can upload");
  assert.match(tag, /:key=/, "a new entity gets a fresh machine");
  const js = script(source);
  assert.match(js, /canUpload\s*=\s*computed\(/);
  const guard = js.slice(js.indexOf("canUpload"));
  assert.match(guard, /can_upload/, "the server's can_upload is respected");
  assert.match(guard, /entity_accountant/, "the Entity Accountant uploads");
  assert.match(guard, /close_lead/, "the Close Lead uploads");
  assert.doesNotMatch(guard.slice(0, guard.indexOf(";")), /group_accountant|viewer/, "the Analyst and the Viewer do not");
});
