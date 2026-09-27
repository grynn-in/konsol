// konsol#305 B23: signOff.screen.test.mjs
//
// Source-level checks on screens/SignOff.vue (E9; stories 9.1, 9.2). The
// component is read as text: a .vue file only compiles inside the Vite build,
// which the row's gate runs separately (Problems found 17: C1 is the real
// check for screens). The machine itself is tested in
// machines/signoffMachine.test.mjs; here we check the screen drives it and
// never decides anything the machine decides.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SIGN_OFF = path.join(__dirname, "SignOff.vue");

function read() {
  return fs.readFileSync(SIGN_OFF, "utf8");
}

function script(source) {
  const m = source.match(/<script[^>]*>([\s\S]*?)<\/script>/);
  assert.ok(m, "the component has a <script>");
  return m[1];
}

function template(source) {
  const start = source.indexOf("<template>");
  const end = source.lastIndexOf("</template>");
  assert.ok(start >= 0 && end > start, "the component has a <template>");
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

test("SignOff.vue drives signoffMachine (B14) with services injected through provide({actors})", () => {
  const source = read();
  const src = script(source);
  assert.match(source, /import\s*\{[^}]*\bsignoffMachine\b[^}]*\}\s*from\s*["']\.\.\/machines\/signoffMachine\.js["']/);
  assert.match(src, /signoffMachine\.provide\(\s*\{\s*actors\s*:/, "services come in through provide({actors})");
  assert.match(src, /\bload\s*:/, "the load service is provided");
  assert.match(src, /\bsign\s*:/, "the sign service is provided");
});

test("load is A30 get_signoff over GET, sign is A32 sign over POST, and nothing else is posted", () => {
  const source = read();
  const src = script(source);
  assert.match(source, /konsol\.close\.signoff_api\.get_signoff/);
  assert.match(source, /konsol\.close\.signoff_api\.sign\b/);
  assert.match(source, /import\s*\{[^}]*\bget\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
  assert.match(source, /import\s*\{[^}]*\bpost\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
  assert.equal((src.match(/\bpost\(/g) || []).length, 1, "one post( call site: sign");
  assert.doesNotMatch(source, /\bfetch\(/, "all server calls go through api.js");
  // Close and reopen belong to B24.
  assert.doesNotMatch(source, /close_period|reopen_period|declare_tb_exception/);
});

test("It renders through summaryView (B15), and the period comes from the URL (route.js)", () => {
  const source = read();
  assert.match(source, /import\s*\{[^}]*\bsummaryView\b[^}]*\}\s*from\s*["']\.\.\/signoff\.js["']/);
  assert.match(script(source), /summaryView\(/);
  assert.match(source, /from\s*["']\.\.\/route\.js["']/);
  const tpl = template(source);
  for (const section of ["gates", "checks", "acknowledgements", "onBehalf", "exceptions", "covers", "previous"]) {
    assert.match(source, new RegExp(`\\b${section}\\b`), `the ${section} section is rendered`);
  }
  assert.match(tpl, /\.rows\b/, "each section's rows (\"None\" when empty) come from summaryView");
});

test("The action button's label comes from the summary's label", () => {
  const tpl = template(read());
  assert.match(tpl, /\{\{\s*view\.label\s*\}\}/);
});

test("Every button that sends an event is shown only when the machine accepts that event", () => {
  const tpl = template(read());
  const tags = tagsWith(tpl, "@click=\"send");
  assert.ok(tags.length >= 5, "SIGN, ACKNOWLEDGE, OVERRIDE, CONFIRM_ACK, CONFIRM_OVERRIDE, CANCEL buttons send events");
  for (const tag of tags) {
    assert.match(tag, /v-if="[^"]*\baccepts\(/, `gated by the machine: ${tag}`);
  }
  const src = script(read());
  assert.match(src, /\.can\(/, "accepts() asks the machine (snapshot.can)");
  for (const event of ["SIGN", "ACKNOWLEDGE", "OVERRIDE", "CONFIRM_ACK", "CONFIRM_OVERRIDE", "CANCEL"]) {
    assert.match(read(), new RegExp(`["']${event}["']`), `${event} is sent`);
  }
});

test("The Amber acknowledgement and the Red override reason are labelled <textarea>s whose text travels in the CONFIRM event", () => {
  const source = read();
  const tpl = template(source);
  const areas = tpl.match(/<textarea[\s\S]*?>/g) || [];
  assert.equal(areas.length, 2, "two textareas");
  for (const area of areas) {
    const id = (area.match(/\bid="([^"]+)"/) || [])[1];
    assert.ok(id, `the textarea has an id: ${area}`);
    assert.match(tpl, new RegExp(`<label[^>]*\\bfor="${id}"`), `a <label for="${id}">`);
  }
  assert.match(source, /type:\s*["']CONFIRM_ACK["'][^}]*\btext:/);
  assert.match(source, /type:\s*["']CONFIRM_OVERRIDE["'][^}]*\btext:/);
});

test("No machine guard is re-implemented in the component", () => {
  const source = read();
  assert.doesNotMatch(script(source), /\.trim\(/, "blank-text checks are the machine's (hasText)");
  assert.doesNotMatch(source, /can_override/, "the override gate is the machine's (canOverride)");
  assert.doesNotMatch(source, /can_sign/, "the sign gate is the machine's (canSign)");
});

test("Failure path: no browser dialogs; the machine's error is shown verbatim in plain lines", () => {
  const source = read();
  assert.doesNotMatch(source, /window\.(prompt|confirm|alert)|\bprompt\(|\bconfirm\(/);
  assert.match(source, /context\.error/, "context.error is rendered");
  assert.match(source, /messageLines\(/, "split on <br> into plain lines");
  assert.match(template(source), /role="alert"/);
  assert.doesNotMatch(source, /Something went wrong/i);
  assert.doesNotMatch(source, /v-html/);
});

test("Failure path: \"Signed\" is shown only in the signed and closed states", () => {
  const source = read();
  assert.match(
    script(source),
    /signedOrClosed\s*=\s*computed\(\s*\(\)\s*=>[^;]*matches\(\s*["']signed["']\s*\)[^;]*matches\(\s*["']closed["']\s*\)/,
  );
  const tpl = template(source);
  const open = tpl.indexOf('v-if="signedOrClosed"');
  assert.ok(open >= 0, "a region gated by signedOrClosed");
  const endMark = "<!-- end signed region -->";
  const close = tpl.indexOf(endMark, open);
  assert.ok(close > open, "the region ends with the marker comment");
  const outside = tpl.slice(0, tpl.lastIndexOf("<", open)) + tpl.slice(close + endMark.length);
  assert.doesNotMatch(outside, /\bSigned\b/, "no \"Signed\" outside the signed/closed region");
});

test("Failure path: loading and load errors go through LoadState (B17), and Retry sends RETRY", () => {
  const source = read();
  assert.match(source, /import\s+LoadState\s+from\s*["']\.\.\/components\/LoadState\.vue["']/);
  assert.match(template(source), /<LoadState[\s\S]*?@retry=/);
  assert.match(source, /["']RETRY["']/);
});

test("Nothing is stored in the browser (D5)", () => {
  const source = read();
  for (const store of ["local" + "Storage", "session" + "Storage", "indexed" + "DB"]) {
    assert.ok(!source.includes(store), `no ${store}`);
  }
});

// --- konsol#305 B32: the header follows sign, close and reopen -------------

test("B32: SignOff.vue injects CONTEXT_RELOAD from the shell, with no silent default", () => {
  const js = script(read());
  assert.match(js, /import\s*\{[^}]*\bCONTEXT_RELOAD\b[^}]*\}\s*from\s*["']\.\.\/contextRefresh\.js["']/);
  assert.match(js, /\bconst\s+reloadContext\s*=\s*inject\(\s*CONTEXT_RELOAD\s*\)/, "inject with no default");
  assert.match(js, /import\s*\{[^}]*\binject\b[^}]*\}\s*from\s*["']vue["']/);
});

test("B32: SignOff.vue calls the reload from the machine's PERIOD_CHANGED event, and nowhere else", () => {
  const js = script(read());
  assert.match(js, /import\s*\{[^}]*\bPERIOD_CHANGED\b[^}]*\}\s*from\s*["']\.\.\/machines\/signoffMachine\.js["']/);
  assert.match(js, /actor\.on\(\s*PERIOD_CHANGED\s*,\s*\(\)\s*=>\s*reloadContext\(\)\s*\)/, "the machine decides; the screen calls");
  assert.equal((js.match(/reloadContext\(\)/g) || []).length, 1, "one call site: the event");
});

test("B32: reloadContext is declared before the immediate period watcher (B30)", () => {
  const js = script(read());
  const decl = js.search(/\bconst\s+reloadContext\s*=/);
  const watcher = js.indexOf("{ immediate: true }");
  assert.ok(decl >= 0 && watcher > decl, "declared before the watcher that subscribes to it");
});

// --- konsol#305 B33: "closed on" is a time, not a raw ISO string ------------

test("B33: the template never renders closed_on directly", () => {
  const tpl = template(read());
  assert.doesNotMatch(tpl, /closed_on/, "closed_on goes through closedOnText, not the template");
});

test("B33: SignOff.vue formats closed_on with signoff.js's closedOnText in the user's zone (B29)", () => {
  const js = script(read());
  assert.match(js, /import\s*\{[^}]*\bclosedOnText\b[^}]*\}\s*from\s*["']\.\.\/signoff\.js["']/);
  assert.match(js, /import\s*\{[^}]*\buserTimeZone\b[^}]*\}\s*from\s*["']\.\.\/timefmt\.js["']/);
  assert.doesNotMatch(js, /function\s+userTimeZone\b|\buserTimeZone\s*=/, "no local copy");
  assert.match(js, /closedOnText\(\s*closedInfo\.value\.closed_on\b/);
});

test("B33: failure path — a refused closed_on is shown, not swallowed", () => {
  const source = read();
  const js = script(source);
  assert.match(js, /catch\s*\(/, "the refusal is caught so the page still renders");
  assert.match(template(source), /closedOn\.error/, "and its message is shown");
});
