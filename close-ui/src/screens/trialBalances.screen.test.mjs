// konsol#305 B19: trialBalances.screen.test.mjs
//
// Source-level checks on screens/TrialBalances.vue (my entities for the
// period, stories 3.1 and 3.7). The component is read as text: a .vue file
// only compiles inside the Vite build, which the row's gate runs separately
// (Problems found 17: C1 is the real check for screens).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCREEN = path.join(__dirname, "TrialBalances.vue");

function read() {
  return fs.readFileSync(SCREEN, "utf8");
}

function template(source) {
  const start = source.indexOf("<template>");
  const end = source.lastIndexOf("</template>");
  assert.ok(start >= 0 && end > start, "the screen has a <template>");
  return source.slice(start, end);
}

function script(source) {
  const match = source.match(/<script setup>([\s\S]*?)<\/script>/);
  assert.ok(match, "the screen has a <script setup>");
  return match[1];
}

test("it reads A25 my_tbs through api.js (B06), not its own fetch", () => {
  const source = read();
  assert.match(source, /import\s*\{[^}]*\bget\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
  assert.match(source, /konsol\.close\.tb_read_api\.my_tbs/);
  assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js");
});

test("the period comes from the URL through route.js (B07), never remembered", () => {
  const source = read();
  assert.match(source, /import\s*\{[^}]*\bparse\b[^}]*\}\s*from\s*["']\.\.\/route\.js["']/);
  assert.match(source, /useRoute\(/);
  // D5 "nothing is remembered" is enforced for every file by route.test.mjs (B07).
});

test("it builds the rows with entityRows (B12), not its own mapping", () => {
  const source = read();
  assert.match(source, /import\s*\{[^}]*\bentityRows\b[^}]*\}\s*from\s*["']\.\.\/tbTable\.js["']/);
  assert.match(script(source), /entityRows\(/);
});

test("it shows the on-behalf label verbatim (R4), and 'not recorded' when there is none", () => {
  const source = read();
  const tpl = template(source);
  assert.match(tpl, /on_behalf_label/);
  assert.match(source, /not recorded/);
  // Verbatim: the label is never rebuilt from owner/entity in the screen.
  assert.doesNotMatch(source, /`by \$\{/, "the screen never rebuilds the label");
});

test("it renders LoadState (B17) for loading, empty and error", () => {
  const source = read();
  assert.match(source, /import\s+LoadState\s+from\s*["']\.\.\/components\/LoadState\.vue["']/);
  const tpl = template(source);
  assert.match(tpl, /<LoadState\b/);
  assert.match(tpl, /@retry=/, "an error offers the Retry");
});

test("an Entity Accountant with no entities gets the explicit message (A25 note)", () => {
  const source = read();
  assert.match(source, /No entities are assigned to you\. Ask the System Manager\./);
  assert.match(source, /entity_accountant/, "the message is for the Entity Accountant");
  assert.match(source, /konsol\.close\.period_api\.get_context/, "the persona comes from A15");
});

test("the screen handles all seven A25 statuses via KNOWN_STATUSES, not its own list", () => {
  const source = read();
  // Styling per status may be declared, but every status must be present so
  // none renders unstyled or blank.
  for (const status of [
    "Missing",
    "Frequency not declared",
    "Quarter not declared",
    "Received",
    "Exception declared",
    "Not expected this period",
    "Not consolidated: no ownership for this period",
  ]) {
    assert.ok(source.includes(`"${status}"`), `status "${status}" is handled`);
  }
});

// E209b: STATUS_TONE has a key for every KNOWN_STATUSES value, parsed from
// its own source (not a hardcoded list), so a status with no tone cannot
// silently render unstyled.
test("STATUS_TONE has a key for every KNOWN_STATUSES value, with the #289 status in red", async () => {
  const source = read();
  const block = source.match(/const STATUS_TONE = \{([\s\S]*?)\};/);
  assert.ok(block, "the screen declares STATUS_TONE");
  const tones = new Map(
    [...block[1].matchAll(/"([^"]+)":\s*"([^"]+)"/g)].map((m) => [m[1], m[2]]),
  );
  const { KNOWN_STATUSES } = await import("../tbTable.js");
  for (const status of KNOWN_STATUSES) {
    assert.ok(tones.has(status), `STATUS_TONE has a key for "${status}" — otherwise it renders unstyled`);
  }
  assert.equal(
    tones.get("Not consolidated: no ownership for this period"),
    "bg-surface-red-1 text-ink-red-3",
    "someone must act, like Missing",
  );
});

test("a null count reads 'unknown', never 0 or blank", () => {
  const source = read();
  assert.match(source, /["']unknown["']/);
  assert.match(source, /==\s*null/);
});

// E209c: the summary line agrees in number ("1 entity:", "2 entities:"),
// never a hardcoded plural.
test("(E209c) the summary line's noun agrees in number, via tbTable's entityWord", () => {
  const source = read();
  assert.match(source, /import\s*\{[^}]*\bentityWord\b[^}]*\}\s*from\s*["']\.\.\/tbTable\.js["']/);
  const tpl = template(source);
  assert.match(tpl, /entityWord\(total\)/, "the summary line builds its noun from entityWord(total)");
  assert.doesNotMatch(tpl, />\s*entities:/, "the plural is never hardcoded in the template");
});

test("selecting an entity opens its detail area (filled by B20 and B21)", () => {
  const tpl = template(read());
  assert.match(tpl, /@click=/);
  assert.match(tpl, /selected/);
  assert.match(tpl, /data-detail|aria-label="Detail/);
});

test("failure path: no row-edit control (D1)", () => {
  const source = read();
  const tpl = template(source);
  assert.doesNotMatch(tpl, /contenteditable/i, "no editable cells");
  assert.doesNotMatch(tpl, /<input\b[^>]*v-model/i, "no input bound to a value");
  assert.doesNotMatch(tpl, /<(FormControl|TextInput|Input)\b[^>]*v-model/i, "no frappe-ui input bound to a value");
  assert.doesNotMatch(tpl, /v-model=["'][^"']*(amount|debit|credit|balance)/i, "no input bound to a row amount");
});

test("failure path: no v-html, window.open or bare Loading… text", () => {
  const source = read();
  assert.doesNotMatch(source, /v-html/);
  assert.doesNotMatch(source, /window\.open\(|window\.prompt\(/);
  assert.doesNotMatch(template(source), />\s*Loading…?\s*</);
});

// --- B18b: `?entity=` pre-selects the detail area -------------------------

test("(B18b) a ?entity= in the URL opens that entity's detail area", () => {
  const source = read();
  assert.match(source, /route\.query\.entity/, "the query is read from the URL, not remembered client state");
  assert.match(source, /selectedCode\.value\s*=\s*match\.entity/);
});

test("(B18b) failure path: an unknown ?entity= is ignored with a note, never guessed", () => {
  const source = read();
  assert.match(source, /entityNote/);
  const t = template(source);
  assert.match(t, /entityNote/, "the note is rendered");
  // Never silently falls back to the first row or any other row when the
  // requested entity is not found.
  assert.doesNotMatch(source, /rows\[0\]|rows\.find\([^)]*\)\s*\|\|\s*rows\[/,
    "an unknown ?entity= is never replaced by a guessed row");
});

// --- B27: no literal "None"; times formatted in the user's zone -----------

test("(B27) the template never shows the literal 'None'", () => {
  const tpl = template(read());
  assert.doesNotMatch(tpl, />\s*None\s*</, "a missing TB shows the dash from tbTable, not 'None'");
});

test("(B27) the TB and uploaded cells use tbTable's tbText / uploaded, not raw creation", () => {
  const source = read();
  const tpl = template(source);
  assert.match(tpl, /row\.tbText/);
  assert.match(tpl, /row\.uploaded/);
  assert.match(tpl, /selected\.uploaded/);
  assert.doesNotMatch(tpl, /tb\.creation/, "the raw server timestamp is never shown");
});

test("(B27) entityRows gets the user's zone and now; a missing zone is shown, not guessed", () => {
  const source = read();
  const s = script(source);
  assert.match(s, /entityRows\([^)]*,\s*[^)]*,\s*timeZone\s*\)/);
  // B29: the zone comes from timefmt.js, imported, never a local copy.
  // Comments are stripped so a mention in prose cannot satisfy the check.
  const code = s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
  assert.match(code, /import\s*\{[^}]*\buserTimeZone\b[^}]*\}\s*from\s*["']\.\.\/timefmt\.js["']/);
  assert.doesNotMatch(code, /function\s+userTimeZone\b|\buserTimeZone\s*=/, "no local copy");
  assert.doesNotMatch(code, /time_zone|resolvedOptions\(\)/, "no local copy of the lookup");
  assert.match(s, /if\s*\(!timeZone\)/, "no zone is an explicit error, never a default");
});

// --- Y62: Remind on a Missing row, and the reminded text (story 1.5) --------

function code(source) {
  return script(source).replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

test("(Y62) Remind posts through remind.js's REMIND and remindBody, via api.js's post", () => {
  const source = read();
  const s = code(source);
  assert.match(s, /import\s*\{[^}]*\bpost\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
  assert.match(s, /import\s*\{[^}]*\bREMIND\b[^}]*\bremindBody\b[^}]*\}\s*from\s*["']\.\.\/remind\.js["']|import\s*\{[^}]*\bremindBody\b[^}]*\bREMIND\b[^}]*\}\s*from\s*["']\.\.\/remind\.js["']/);
  assert.doesNotMatch(s, /remind_api/, "the endpoint name lives in remind.js only");
  assert.match(s, /remindBody\([^)]*,\s*[^)]*,\s*["']tb["']\s*\)/, "the topic is tb");
});

test("(Y62) exactly one post(REMIND — one function sends a reminder", () => {
  const s = code(read());
  const posts = s.match(/post\(\s*REMIND\b/g) || [];
  assert.equal(posts.length, 1);
});

test("(Y62) the Remind button is v-if on row.canRemind and the reminded text is row.reminded", () => {
  const tpl = template(read());
  const button = tpl.match(/<button\b[^>]*v-if="row\.canRemind"[^>]*>[\s\S]*?<\/button>/);
  assert.ok(button, "a button gated by v-if=\"row.canRemind\"");
  assert.match(button[0], /Remind/);
  assert.match(button[0], /@click\.stop=/, "clicking Remind does not toggle the row's detail area");
  assert.match(tpl, /v-if="row\.reminded"[^>]*>\s*\{\{\s*row\.reminded\s*\}\}/, "the text is shown as entityRows built it");
  assert.doesNotMatch(tpl, /can_remind/, "the template never reads the raw payload flag");
});

test("(Y62) failure path: a refusal shows the server's sentence through messageLines, never invented text", () => {
  const source = read();
  const s = code(source);
  assert.match(s, /import\s*\{[^}]*\bmessageLines\b[^}]*\}\s*from\s*["']\.\.\/signoff\.js["']/);
  assert.match(s, /messageLines\(\s*e\.message\s*\)/);
  const tpl = template(source);
  assert.match(tpl, /remindError/, "the refusal is rendered");
  assert.match(tpl, /role="alert"[^>]*>[\s\S]*?remindError/);
});

test("(Y62) success reloads my_tbs once; a refusal does not reload or change the row", () => {
  const s = code(read());
  const fn = s.match(/async function remind\(row\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(fn, "one async function remind(row)");
  const body = fn[1];
  const tryBlock = body.match(/try\s*\{([\s\S]*?)\}\s*catch/);
  assert.ok(tryBlock, "remind posts inside try/catch");
  assert.equal((tryBlock[1].match(/reloadTbs\(\)/g) || []).length, 1, "one reload, after a successful post");
  const catchBlock = body.slice(body.indexOf("catch"));
  assert.doesNotMatch(catchBlock.split("finally")[0], /reloadTbs\(|load\.data\s*=/, "a refusal leaves the row as it was");
});

// --- D60: the TB due header and the overdue chip (stories 2.4, 3.1) --------
// Behaviour (texts, tone, throws) is tested on tbTable.js with the golden
// payload; these check the wiring only.

test("(D60) the header shows tbTable's tbDue text, built in the script, not its own date wording", () => {
  const source = read();
  const s = code(source);
  assert.match(s, /import\s*\{[^}]*\btbDue\b[^}]*\}\s*from\s*["']\.\.\/tbTable\.js["']/);
  assert.match(s, /tbDue\(/);
  const tpl = template(source);
  const header = tpl.match(/<header\b[\s\S]*?<\/header>/);
  assert.ok(header, "the screen has a header");
  assert.match(header[0], /\{\{\s*due\.text\s*\}\}/, "the header renders the due text");
  assert.doesNotMatch(source, /No due date is shown/, "the old 'no due date' note is gone");
  assert.doesNotMatch(tpl, /deadline\./, "the template never reads the raw deadline");
});

test("(D60) the Overdue chip is v-if on row.overdueChip with its tone, and never blocks a control", () => {
  const tpl = template(read());
  const chip = tpl.match(/<span\b[^>]*v-if="row\.overdueChip"[^>]*>[\s\S]*?<\/span>/);
  assert.ok(chip, "a chip gated by v-if=\"row.overdueChip\"");
  assert.match(chip[0], /:class="row\.overdueChip\.tone"/);
  assert.match(chip[0], /\{\{\s*row\.overdueChip\.text\s*\}\}/);
  assert.doesNotMatch(tpl, /:disabled="[^"]*overdue/i, "overdue never disables anything (#305-2.4-1)");
});
