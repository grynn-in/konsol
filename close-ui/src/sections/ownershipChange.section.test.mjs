// konsol#305 O60: ownershipChange.section.test.mjs
//
// sections/OwnershipChange.vue is the "Change ownership" form on the Rates &
// ownership screen's Ownership tab (story 4.2; #305-4.2-1; wireframe-4.2.md
// section 1, confirmed as drawn by Deepak Pai 7 Oct). A .vue file only
// compiles inside the Vite build, which the row's gate runs apart, so the
// wiring is read as text and the behaviour is tested by building the
// section's OWN functions from its <script> source (the O61 precedent,
// ratesPending.section.test.mjs) and feeding them the REAL producers'
// output: the O63 golden get_ownership payload, the O55 golden previews and
// the real rates.js ownershipEffectView (O59).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ownershipEffectView, ownershipChangeBody, pendingView } from "../rates.js";
import * as ratesModule from "../rates.js";
import { dueDateText } from "../dueDate.js";
import { periodName } from "../periodName.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SECTION = path.join(__dirname, "OwnershipChange.vue");
const REPO = path.join(__dirname, "..", "..", "..");

function fixture(name) {
  return JSON.parse(fs.readFileSync(path.join(REPO, "konsol", "tests", "fixtures", name), "utf8"));
}
/** The one date wording (dueDate.js); the ICU month abbreviation varies by Node build. */
const d = (iso) => dueDateText(iso, "test");
const OWNERSHIP = fixture("close_ownership_payload.json");
const PREVIEW_OK = fixture("close_ownership_preview_payload.json");
const PREVIEW_REFUSED = fixture("close_ownership_preview_refused.json");
const PENDING = fixture("close_rates_pending_payload.json");

function read() {
  return fs.readFileSync(SECTION, "utf8");
}

function script(source) {
  const m = source.match(/<script[^>]*>([\s\S]*?)<\/script>/);
  assert.ok(m, "the section has a <script>");
  return m[1];
}

function template(source) {
  const start = source.indexOf("<template>");
  const end = source.lastIndexOf("</template>");
  assert.ok(start >= 0 && end > start, "the section has a <template>");
  return source.slice(start, end);
}

/** A top-level `function NAME(` or `const NAME =` declaration of the section's script. */
function decl(js, name) {
  const fn = js.match(new RegExp(`\\n(?:async )?function ${name}\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n\\}\\n`));
  if (fn) return fn[0];
  const c = js.match(new RegExp(`\\nconst ${name} = [\\s\\S]*?;\\n`));
  assert.ok(c, `the section declares ${name}`);
  return c[0];
}

/** The section's own declarations, with the real imports injected. */
function load(names, inject = {}) {
  const js = script(read());
  const src = names.map((n) => decl(js, n)).join("");
  return new Function(...Object.keys(inject), `${src}return { ${names.join(", ")} };`)(...Object.values(inject));
}

function inputTags(tpl) {
  return tpl.match(/<(input|select|textarea)\b[^>]*>/g) || [];
}

test("the section file exists", () => {
  assert.ok(fs.existsSync(SECTION), "sections/OwnershipChange.vue exists");
});

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

test("Wiring: one get(PREVIEW and one post(SAVE_OWNERSHIP_CHANGE, both through api.js", () => {
  const source = read();
  assert.match(source, /import\s*\{[^}]*\bget\b[^}]*\bpost\b[^}]*\}\s*from\s*["']\.\.\/api\.js["']/);
  const js = script(source);
  assert.match(js, /const PREVIEW = "konsol\.close\.rates_api\.preview_ownership_change";/);
  assert.match(js, /const SAVE_OWNERSHIP_CHANGE = "konsol\.close\.rates_api\.save_ownership_change";/);
  assert.equal((js.match(/\bget\(PREVIEW\b/g) || []).length, 1, "exactly one get(PREVIEW");
  assert.equal((js.match(/\bpost\(SAVE_OWNERSHIP_CHANGE\b/g) || []).length, 1, "exactly one post(SAVE_OWNERSHIP_CHANGE");
  assert.equal((js.match(/\bpost\(/g) || []).length, 1, "no other POST");
  assert.equal((js.match(/\bget\(/g) || []).length, 1, "no other GET");
});

test("The ONE save function builds its body with rates.js ownershipChangeBody (O59) and posts built.body", () => {
  const source = read();
  assert.match(source, /import\s*\{[^}]*\bownershipChangeBody\b[^}]*\}\s*from\s*["']\.\.\/rates\.js["']/);
  const fn = decl(script(source), "saveDraft");
  assert.match(fn, /ownershipChangeBody\(/);
  assert.match(fn, /post\(SAVE_OWNERSHIP_CHANGE,\s*built\.body\)/);
  assert.match(fn, /saveBlocked\(/, "saveDraft re-checks the block before posting");
});

test("Save draft is disabled through saveBlocked(…), and the save emits `saved` for the screen", () => {
  const source = read();
  const tpl = template(source);
  const i = tpl.indexOf("Save draft");
  assert.ok(i > 0, "a Save draft control");
  const tag = tpl.slice(tpl.lastIndexOf("<Button", i), i);
  assert.match(tag, /:disabled="[^"]*\bsaveBlocked\(/, "Save draft's :disabled is saveBlocked(…)");
  assert.match(script(source), /defineEmits\(\s*\[\s*["']saved["']\s*,\s*["']edit-opened["']\s*\]\s*\)/);
  assert.match(decl(script(source), "saveDraft"), /emit\(\s*["']saved["']/);
});

test("The preview is driven by the inputs (watch → previewer.ask / cancel) and cancelled on unmount", () => {
  const js = script(read());
  assert.match(js, /makePreviewer\(\{[\s\S]*fetch:\s*\([^)]*\)\s*=>\s*get\(PREVIEW,/);
  assert.match(js, /watch\(/);
  assert.match(js, /previewer\.ask\(/);
  assert.match(js, /previewer\.cancel\(/);
  assert.match(js, /onBeforeUnmount\(\(\)\s*=>\s*previewer\.cancel\(\)\)/);
});

test("Server sentences only: the refusals are the preview's problems as text, no v-html, no dialogs, no storage", () => {
  const source = read();
  const tpl = template(source);
  assert.match(tpl, /v-for="\(problem, i\) in preview\.panel\.problems"/);
  assert.match(tpl, /\{\{\s*problem\s*\}\}/);
  assert.doesNotMatch(source, /v-html/);
  assert.doesNotMatch(source, /window\.(prompt|confirm|alert)|\bprompt\(|\bconfirm\(|\balert\(/);
  for (const store of ["local" + "Storage", "session" + "Storage", "indexed" + "DB"]) {
    assert.ok(!source.includes(store), `no ${store}`);
  }
});

test("Group is read-only from the chosen entity's node: no input is bound to the group", () => {
  const tpl = template(read());
  for (const tag of inputTags(tpl)) {
    assert.doesNotMatch(tag, /group/i, `no group input: ${tag}`);
  }
  assert.match(tpl, /selectedEntity\.group/);
  assert.match(tpl, /from the entity's node; read-only/);
});

test("Failure path: no input for an acquisition or disposal date or price exists", () => {
  const tpl = template(read());
  const tags = inputTags(tpl);
  assert.ok(tags.length >= 4, "entity, period, pct and method inputs exist");
  for (const tag of tags) {
    assert.doesNotMatch(tag, /acquisition|disposal|price/i, tag);
  }
});

test("The method choices are ownership_change_model.METHODS, in its order (no second list drifting)", () => {
  const py = fs.readFileSync(path.join(REPO, "konsol", "close", "ownership_change_model.py"), "utf8");
  const m = py.match(/^METHODS = \(([^)]*)\)/m);
  assert.ok(m, "the server declares METHODS");
  const server = [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
  const { METHODS } = load(["METHODS"]);
  assert.deepEqual(METHODS, server);
});

// ---------------------------------------------------------------------------
// Choices (the O63 golden get_ownership().change)
// ---------------------------------------------------------------------------

function helpers() {
  return load(["need", "entityOptions", "periodGroups", "previewParams", "currentText", "effectPanel", "saveBlocked", "savedLine", "makePreviewer"], {
    ownershipEffectView,
    dueDateText,
  });
}

test("Entity choices: one per node from the golden; an entity on two nodes names its group, never a guessed one", () => {
  const { entityOptions } = helpers();
  const opts = entityOptions(OWNERSHIP.change.entities);
  assert.deepEqual(
    opts.map((o) => [o.key, o.entity, o.group, o.label]),
    [
      ["ZZ5B1|ECL_GROUP", "ZZ5B1", "ECL_GROUP", "ZZ5B1 — ZZ Five B One (in ECL_GROUP)"],
      ["ZZ5B1|ZZ_SUBGROUP", "ZZ5B1", "ZZ_SUBGROUP", "ZZ5B1 — ZZ Five B One (in ZZ_SUBGROUP)"],
      ["ZZ5B3|ECL_GROUP", "ZZ5B3", "ECL_GROUP", "ZZ5B3 — ZZ Five B Three"],
    ],
  );
});

test("Failure path: an entity choice without its group, or no entities list, throws", () => {
  const { entityOptions } = helpers();
  assert.throws(() => entityOptions([{ entity: "ZZ5B1", entity_name: "ZZ" }]), /consolidation_group/);
  assert.throws(() => entityOptions(undefined), /entities/);
});

test("Period choices: grouped by fiscal year, newest year first, calendar order inside a year, label from the server", () => {
  const { periodGroups } = helpers();
  const groups = periodGroups(OWNERSHIP.change.periods);
  assert.deepEqual(
    groups.map((g) => [g.year, g.label, g.options.map((o) => [o.key, o.fiscal_year, o.fiscal_period, o.label])]),
    [
      [2026, "Fiscal year 2026", [["2026/1", 2026, 1, "FY2026 P01 (from 2026-01-01)"]]],
      [2025, "Fiscal year 2025", [["2025/10", 2025, 10, "FY2025 P10 (from 2025-10-01)"]]],
    ],
  );
});

test("192 Open Regular periods (live, FY2010–FY2025) give 16 year groups, FY2025 first, P01..P12 in each", () => {
  const { periodGroups } = helpers();
  const periods = [];
  for (let y = 2010; y <= 2025; y++) {
    for (let p = 1; p <= 12; p++) {
      const mm = String(p).padStart(2, "0");
      periods.push({ fiscal_year: y, fiscal_period: p, label: `FY${y} P${mm}`, start_date: `${y}-${mm}-01` });
    }
  }
  const groups = periodGroups(periods);
  assert.equal(groups.length, 16);
  assert.equal(groups[0].year, 2025);
  assert.equal(groups[15].year, 2010);
  for (const g of groups) {
    assert.deepEqual(g.options.map((o) => o.fiscal_period), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
  }
  assert.equal(groups.reduce((n, g) => n + g.options.length, 0), 192);
});

test("Failure path: a period choice without its start date throws", () => {
  const { periodGroups } = helpers();
  assert.throws(() => periodGroups([{ fiscal_year: 2025, fiscal_period: 10, label: "FY2025 P10" }]), /start_date/);
  assert.throws(() => periodGroups(null), /periods/);
});

// ---------------------------------------------------------------------------
// The preview request
// ---------------------------------------------------------------------------

/** The endpoint's parameters as `[name, optional]`, read from rates_api.py. */
function endpointSignature(name) {
  const py = fs.readFileSync(path.join(REPO, "konsol", "close", "rates_api.py"), "utf8");
  const m = py.match(new RegExp(`def ${name}\\(([^)]*)\\)`));
  assert.ok(m, `rates_api declares ${name}`);
  return m[1].split(",").map((s) => s.trim()).filter(Boolean).map((s) => [s.split("=")[0].trim(), s.includes("=")]);
}

test("previewParams carries exactly preview_ownership_change's parameters, from the chosen node and period", () => {
  const { entityOptions, periodGroups, previewParams } = helpers();
  const signature = endpointSignature("preview_ownership_change");
  const all = signature.map(([n]) => n).sort();
  const required = signature.filter(([, optional]) => !optional).map(([n]) => n).sort();
  assert.deepEqual(signature.filter(([, optional]) => optional).map(([n]) => n), ["name"], "only `name` is optional (O66)");
  const entity = entityOptions(OWNERSHIP.change.entities)[1];
  const period = periodGroups(OWNERSHIP.change.periods)[1].options[0];
  const params = previewParams(entity, period, " 80 ", "full", null);
  assert.deepEqual(params, {
    fiscal_year: 2025,
    fiscal_period: 10,
    consolidation_group: "ZZ_SUBGROUP",
    entity: "ZZ5B1",
    ownership_pct: "80",
    consolidation_method: "full",
  });
  assert.deepEqual(Object.keys(params).sort(), required, "a new change sends every required parameter and no name");
  const edited = previewParams(entity, period, " 80 ", "full", "OP-ZZ5B1-2025-10-01");
  assert.equal(edited.name, "OP-ZZ5B1-2025-10-01", "O67: editing a draft previews it by name");
  assert.deepEqual(Object.keys(edited).sort(), all, "an edit sends exactly every parameter");
  assert.equal("name" in previewParams(entity, period, "80", "full", ""), false, "a blank name is no edit");
});

test("Failure path: an incomplete form asks for no preview (null), so no refusal is guessed for an untouched field", () => {
  const { entityOptions, periodGroups, previewParams } = helpers();
  const entity = entityOptions(OWNERSHIP.change.entities)[0];
  const period = periodGroups(OWNERSHIP.change.periods)[1].options[0];
  assert.equal(previewParams(null, period, "80", "full"), null);
  assert.equal(previewParams(entity, null, "80", "full"), null);
  assert.equal(previewParams(entity, period, "  ", "full"), null);
  assert.equal(previewParams(entity, period, "80", ""), null);
  // A bad pct is still previewed: the refusal is the server's sentence.
  assert.equal(previewParams(entity, period, "120", "full").ownership_pct, "120");
});

// ---------------------------------------------------------------------------
// The effect panel and the Save gate (the O55 goldens)
// ---------------------------------------------------------------------------

test("The golden preview gives the effect panel through the real ownershipEffectView, and Save is enabled", () => {
  const { effectPanel, saveBlocked } = helpers();
  const panel = effectPanel({ payload: PREVIEW_OK });
  assert.equal(panel.error, null);
  assert.deepEqual(panel.problems, []);
  assert.deepEqual(panel.view, ownershipEffectView(PREVIEW_OK.effect));
  assert.deepEqual(panel.current, PREVIEW_OK.current);
  assert.deepEqual(
    panel.view.rows.map((r) => [r.label, r.before, r.after]),
    [
      ["Ownership", "100 %", "80 %"],
      ["Method", "full", "full"],
      ["Covers", `${d("2025-01-01")} → ${d("2025-09-30")}`, `${d("2025-10-01")} → open-ended`],
    ],
  );
  assert.equal(saveBlocked(panel, true, false), false);
});

test("Failure path: the refused golden shows the server's sentences exactly, no panel, and Save is disabled", () => {
  const { effectPanel, saveBlocked } = helpers();
  const panel = effectPanel({ payload: PREVIEW_REFUSED });
  assert.deepEqual(panel.problems, PREVIEW_REFUSED.problems);
  assert.equal(panel.view, null);
  assert.equal(saveBlocked(panel, true, false), true);
});

test("Failure path: a preview without `problems`, a request error, or no effect and no refusal is an error and blocks Save", () => {
  const { effectPanel, saveBlocked } = helpers();
  const { problems, ...noProblems } = PREVIEW_OK;
  const missing = effectPanel({ payload: noProblems });
  assert.match(missing.error, /problems/);
  assert.equal(saveBlocked(missing, true, false), true);
  const thrown = effectPanel({ error: "You are not permitted to see ZZ5B9." });
  assert.equal(thrown.error, "You are not permitted to see ZZ5B9.");
  assert.equal(saveBlocked(thrown, true, false), true);
  const empty = effectPanel({ payload: { problems: [], effect: null, current: null } });
  assert.ok(empty.error, "neither an effect nor a refusal is an error");
  const { resign, ...noResign } = PREVIEW_OK.effect;
  const bad = effectPanel({ payload: { ...PREVIEW_OK, effect: noResign } });
  assert.match(bad.error, /resign/);
  assert.equal(saveBlocked(bad, true, false), true);
});

test("Failure path: Save is disabled while the shown preview is stale, while saving, and before any preview", () => {
  const { effectPanel, saveBlocked } = helpers();
  const panel = effectPanel({ payload: PREVIEW_OK });
  assert.equal(saveBlocked(panel, false, false), true, "a newer input is not previewed yet");
  assert.equal(saveBlocked(panel, true, true), true, "a save is in flight");
  assert.equal(saveBlocked(null, true, false), true, "nothing previewed");
});

test("Currently: the golden current period as the wireframe's line; a missing key throws", () => {
  const { currentText } = helpers();
  assert.equal(currentText(PREVIEW_OK.current), `100 % · full · from ${d("2025-01-01")} · open-ended (OP-ZZ5B1-1)`);
  assert.equal(
    currentText({ ...PREVIEW_OK.current, end_date: "2025-09-30" }),
    `100 % · full · from ${d("2025-01-01")} · to ${d("2025-09-30")} (OP-ZZ5B1-1)`,
  );
  const { name, ...noName } = PREVIEW_OK.current;
  assert.throws(() => currentText(noName), /name/);
});

test("After save: the wireframe's line names the draft; a save result without a name throws", () => {
  const { savedLine } = helpers();
  assert.equal(
    savedLine({ name: "OP-ECL_GROUP-ZZ5B1-2025-10-01", docstatus: 0 }),
    "Draft OP-ECL_GROUP-ZZ5B1-2025-10-01 saved — awaiting the Close Lead's approval (Historical equity rates tab, and Approvals).",
  );
  assert.throws(() => savedLine({ docstatus: 0 }), /name/);
});

// ---------------------------------------------------------------------------
// The previewer: debounced, one in flight, a stale response never shown
// ---------------------------------------------------------------------------

function harness() {
  const { makePreviewer } = helpers();
  const timers = [];
  const calls = [];
  const shown = [];
  const previewer = makePreviewer({
    fetch: (params) => new Promise((resolve, reject) => calls.push({ params, resolve, reject })),
    show: (result) => shown.push(result),
    delay: 300,
    setTimer: (fn, ms) => {
      const t = { fn, ms, live: true };
      timers.push(t);
      return t;
    },
    clearTimer: (t) => {
      t.live = false;
    },
  });
  const fire = () => {
    for (const t of timers.splice(0)) if (t.live) t.fn();
  };
  return { previewer, calls, shown, fire, timers };
}
const settle = () => new Promise((resolve) => setImmediate(resolve));

test("Debounced: two quick inputs make one request, for the newest input, after the delay", async () => {
  const h = harness();
  h.previewer.ask({ v: "A" });
  h.previewer.ask({ v: "B" });
  assert.equal(h.calls.length, 0, "nothing sent before the delay");
  assert.equal(h.timers.filter((t) => t.live).length, 1);
  assert.equal(h.timers[1].ms, 300);
  h.fire();
  assert.deepEqual(h.calls.map((c) => c.params), [{ v: "B" }]);
  h.calls[0].resolve(PREVIEW_OK);
  await settle();
  assert.deepEqual(h.shown, [{ payload: PREVIEW_OK }]);
});

test("Failure path: one in flight, and the stale response is never shown over the newer input", async () => {
  const h = harness();
  h.previewer.ask({ v: "A" });
  h.fire();
  h.previewer.ask({ v: "B" });
  h.fire();
  assert.equal(h.calls.length, 1, "B waits while A is in flight");
  h.calls[0].resolve(PREVIEW_OK);
  await settle();
  assert.deepEqual(h.shown, [], "A's response is stale: not shown");
  assert.deepEqual(h.calls.map((c) => c.params), [{ v: "A" }, { v: "B" }], "B is sent once A is back");
  h.calls[1].resolve(PREVIEW_REFUSED);
  await settle();
  assert.deepEqual(h.shown, [{ payload: PREVIEW_REFUSED }]);
});

test("Failure path: a response landing while a newer input waits for its delay is dropped; the newer one follows the delay", async () => {
  const h = harness();
  h.previewer.ask({ v: "A" });
  h.fire();
  h.previewer.ask({ v: "B" });
  h.calls[0].resolve(PREVIEW_OK);
  await settle();
  assert.deepEqual(h.shown, []);
  assert.equal(h.calls.length, 1, "B still waits for its delay");
  h.fire();
  assert.deepEqual(h.calls[1].params, { v: "B" });
});

test("A request error for the newest input is shown as its message; cancel drops an in-flight response", async () => {
  const h = harness();
  h.previewer.ask({ v: "A" });
  h.fire();
  h.calls[0].reject(new Error("FY2024 P12 is Closed: an ownership change must start in an Open period."));
  await settle();
  assert.deepEqual(h.shown, [{ error: "FY2024 P12 is Closed: an ownership change must start in an Open period." }]);
  h.previewer.ask({ v: "B" });
  h.fire();
  h.previewer.cancel();
  h.calls[1].resolve(PREVIEW_OK);
  await settle();
  assert.equal(h.shown.length, 1, "a cancelled request is never shown");
  assert.equal(h.calls.length, 2);
});

// ---------------------------------------------------------------------------
// konsol#305 O67 (wireframe-4.2.md section 1, "The Analyst can edit it until
// it is approved", confirmed by Deepak Pai 7 Oct): a saved draft is edited
// in this form. What may be edited is the server's: get_pending's `edit`
// (O69) through rates.js ownershipDraftEdits, and the preview's `pending`
// names. No title or sentence is parsed. Editing sends `name` in both the
// preview and the save, through the one send path each.
// ---------------------------------------------------------------------------

const EDITABLE = () => ratesModule.ownershipDraftEdits(pendingView(PENDING).items);

function editHelpers() {
  return load(["need", "entityOptions", "periodGroups", "previewParams", "effectPanel", "editForm", "pendingEdits"], {
    ownershipEffectView,
    dueDateText,
    periodName,
  });
}

test("O67 editForm: the golden pending draft's server edit loads its node, first period, pct and method", () => {
  const { entityOptions, periodGroups, previewParams, editForm } = editHelpers();
  const entities = entityOptions(OWNERSHIP.change.entities);
  const groups = periodGroups(OWNERSHIP.change.periods);
  const name = "OP-ZZ5B1-2025-10-01";
  const edit = EDITABLE()[name];
  assert.ok(edit, "the golden draft is editable");
  const loaded = editForm(name, edit, entities, groups);
  assert.deepEqual(loaded, { entityKey: "ZZ5B1|ECL_GROUP", periodKey: "2025/10", pct: "80", method: "full" });
  const entity = entities.find((o) => o.key === loaded.entityKey);
  const period = groups.flatMap((g) => g.options).find((o) => o.key === loaded.periodKey);
  assert.deepEqual(previewParams(entity, period, loaded.pct, loaded.method, name), {
    fiscal_year: 2025,
    fiscal_period: 10,
    consolidation_group: "ECL_GROUP",
    entity: "ZZ5B1",
    ownership_pct: "80",
    consolidation_method: "full",
    name,
  });
});

test("O67 failure path: an edit whose node or period is not a choice here, or that lacks a key, throws naming the draft", () => {
  const { entityOptions, periodGroups, editForm } = editHelpers();
  const entities = entityOptions(OWNERSHIP.change.entities);
  const groups = periodGroups(OWNERSHIP.change.periods);
  const name = "OP-ZZ5B1-2025-10-01";
  const edit = EDITABLE()[name];
  assert.throws(() => editForm(name, { ...edit, entity: null }, entities, groups), /OP-ZZ5B1-2025-10-01[\s\S]*ECL_GROUP/, "the group node is not an entity choice");
  assert.throws(() => editForm(name, { ...edit, entity: "ZZ5B2" }, entities, groups), /OP-ZZ5B1-2025-10-01[\s\S]*ZZ5B2/, "an entity not on this form");
  assert.throws(() => editForm(name, { ...edit, fiscal_period: 11 }, entities, groups), /OP-ZZ5B1-2025-10-01[\s\S]*FY2025 P11/, "a period not offered");
  for (const key of ["consolidation_group", "entity", "fiscal_year", "fiscal_period", "ownership_pct", "consolidation_method"]) {
    const partial = { ...edit };
    delete partial[key];
    assert.throws(() => editForm(name, partial, entities, groups), new RegExp(key), key);
  }
});

test("O67 pendingEdits: the preview's awaiting drafts offer Edit only where the server made them editable", () => {
  const { pendingEdits } = editHelpers();
  const editable = EDITABLE();
  assert.deepEqual(pendingEdits(PREVIEW_REFUSED.pending, editable), [], "the refused golden's draft is not in the pending list's edits");
  assert.deepEqual(
    pendingEdits(["OP-ZZ5B1-2025-10-01", "OP-ZZ5B2-2025-10-01"], editable),
    ["OP-ZZ5B1-2025-10-01"],
    "the Desk draft (edit null) offers no Edit",
  );
  assert.deepEqual(pendingEdits([], editable), []);
  assert.throws(() => pendingEdits(undefined, editable), /pending/);
});

test("O67: the preview's `pending` reaches the panel; a preview without it is an error and blocks Save", () => {
  const { effectPanel } = editHelpers();
  assert.deepEqual(effectPanel({ payload: PREVIEW_REFUSED }).pending, PREVIEW_REFUSED.pending);
  assert.deepEqual(effectPanel({ payload: PREVIEW_OK }).pending, []);
  const { pending, ...noPending } = PREVIEW_OK;
  const panel = effectPanel({ payload: noPending });
  assert.match(panel.error, /pending/);
  const { saveBlocked } = helpers();
  assert.equal(saveBlocked(panel, true, false), true);
});

/** The section's own saveDraft, with the real ownershipChangeBody and a recording post. */
function saveHarness(editingName) {
  const { savedLine, saveBlocked } = helpers();
  const posts = [];
  const emitted = [];
  const refs = {
    preview: { panel: { problems: [], view: {}, error: null }, fresh: true },
    saving: { value: false },
    selectedPeriod: { value: { fiscal_year: 2025, fiscal_period: 10 } },
    selectedEntity: { value: { group: "ECL_GROUP", entity: "ZZ5B1" } },
    form: { entityKey: "ZZ5B1|ECL_GROUP", periodKey: "2025/10", pct: "75", method: "full" },
    editing: { value: editingName },
    saveError: { value: null },
    saved: { value: null },
  };
  const js = script(read());
  const inject = {
    ...refs,
    saveBlocked,
    savedLine,
    ownershipChangeBody,
    SAVE_OWNERSHIP_CHANGE: "konsol.close.rates_api.save_ownership_change",
    post: async (method, body) => {
      posts.push([method, body]);
      return { name: body.name || "OP-ECL_GROUP-ZZ5B1-2025-10-01", docstatus: 0 };
    },
    emit: (...args) => emitted.push(args),
  };
  const saveDraft = new Function(...Object.keys(inject), `${decl(js, "saveDraft")}return saveDraft;`)(...Object.values(inject));
  return { saveDraft, posts, emitted, refs };
}

test("O67: saving an edit posts the draft's name through the one save path; the same saved line shows and editing ends", async () => {
  const h = saveHarness("OP-ZZ5B1-2025-10-01");
  await h.saveDraft();
  assert.equal(h.posts.length, 1);
  assert.equal(h.posts[0][0], "konsol.close.rates_api.save_ownership_change");
  assert.deepEqual(h.posts[0][1], {
    fiscal_year: 2025,
    fiscal_period: 10,
    consolidation_group: "ECL_GROUP",
    entity: "ZZ5B1",
    ownership_pct: 75,
    consolidation_method: "full",
    name: "OP-ZZ5B1-2025-10-01",
  });
  assert.equal(
    h.refs.saved.value,
    "Draft OP-ZZ5B1-2025-10-01 saved — awaiting the Close Lead's approval (Historical equity rates tab, and Approvals).",
  );
  assert.equal(h.refs.editing.value, null, "the edit is done");
  assert.deepEqual(h.emitted, [["saved", "OP-ZZ5B1-2025-10-01"]]);
});

test("O67 failure path: a new change (not editing) posts no name", async () => {
  const h = saveHarness(null);
  await h.saveDraft();
  assert.equal(h.posts.length, 1);
  assert.equal("name" in h.posts[0][1], false);
});

/** The section's own openDraft, with the real editForm and the golden choices. */
function openHarness(editable) {
  const { entityOptions, periodGroups, editForm } = editHelpers();
  const refs = {
    props: { editable },
    choices: { value: { entities: entityOptions(OWNERSHIP.change.entities), groups: periodGroups(OWNERSHIP.change.periods), error: null } },
    form: { entityKey: "", periodKey: "", pct: "", method: "" },
    editing: { value: null },
    editError: { value: null },
    saved: { value: "an earlier line" },
  };
  const js = script(read());
  const inject = { ...refs, editForm };
  const openDraft = new Function(...Object.keys(inject), `${decl(js, "openDraft")}return openDraft;`)(...Object.values(inject));
  return { openDraft, refs };
}

test("O67 openDraft: loads the server's edit into the form and marks the draft being edited", () => {
  const h = openHarness(EDITABLE());
  h.openDraft("OP-ZZ5B1-2025-10-01");
  assert.deepEqual({ ...h.refs.form }, { entityKey: "ZZ5B1|ECL_GROUP", periodKey: "2025/10", pct: "80", method: "full" });
  assert.equal(h.refs.editing.value, "OP-ZZ5B1-2025-10-01");
  assert.equal(h.refs.editError.value, null);
  assert.equal(h.refs.saved.value, null, "an older saved line is cleared");
});

test("O67 failure path: opening a draft the server did not make editable shows an error and loads nothing", () => {
  const h = openHarness(EDITABLE());
  h.openDraft("OP-ZZ5B2-2025-10-01");
  assert.match(h.refs.editError.value, /OP-ZZ5B2-2025-10-01/);
  assert.equal(h.refs.editing.value, null);
  assert.deepEqual({ ...h.refs.form }, { entityKey: "", periodKey: "", pct: "", method: "" });
});

test("O67 wiring: props editable + openEdit, opened once on mount, the preview watch sends the edited name, Edit buttons from pendingEdits", () => {
  const source = read();
  const js = script(source);
  assert.match(js, /editable:\s*\{\s*type:\s*Object/);
  assert.match(js, /openEdit:\s*\{\s*type:\s*String/);
  assert.match(js, /watch\(\s*\(\)\s*=>\s*props\.openEdit,[\s\S]*?openDraft\([\s\S]*?emit\(\s*["']edit-opened["'][\s\S]*?\{\s*immediate:\s*true\s*\}/);
  assert.match(js, /previewParams\(selectedEntity\.value,\s*selectedPeriod\.value,\s*form\.pct,\s*form\.method,\s*editing\.value\)/);
  assert.match(decl(js, "saveDraft"), /name:\s*editing\.value/);
  const tpl = template(source);
  assert.match(tpl, /v-for="n in pendingEdits\(preview\.panel\.pending, editable\)"/);
  assert.match(tpl, /@click="openDraft\(n\)"/);
  assert.match(tpl, /Editing draft \{\{\s*editing\s*\}\}/);
  assert.match(tpl, /@click="stopEditing"/);
  assert.match(tpl, /\{\{\s*editError\s*\}\}/);
  assert.doesNotMatch(js, /\.title\b|\.detail\b|problems\[[^\]]*\]\.(match|split|includes)/, "no title or sentence parsing");
});
