// Runs konsol/public/excel-addin/report_form.js outside Excel (konsol#548 v1 T14:
// the Reports panel's template list and the form generated from the catalog).
//
// report_form.js is loaded into a fresh vm context with a small fake DOM (just
// the element calls the form makes), a stub Excel whose worksheets.onActivated
// handler the scenario can fire, a stub catalog and a stub choices endpoint.
// The ops in the input run in order; "snap" records what the panel shows.
// Driven by test_excel_addin_report_form.py; no Excel, no browser.
//
// Input (argv[2], JSON):
//   {"catalog": {...} | null,            // report_catalog's message; null: the call fails
//    "catalogError": "text",             // the failure's message
//    "choices": [[kind, args, payload]], // report_choices replies, matched on kind + args
//    "inspect": [{...}, ...],            // konsolReport.inspect() answers, in turn (last repeats)
//    "hold": [kind, ...],                // these kinds' replies wait for a "release"
//    "ops": [["load"], ["select", id], ["set", param, value], ["setMany", param, [values]],
//            ["setKind", param, kind], ["activate"], ["snap"], ["values"], ["missing"],
//            ["normalise", arg], ["release", n], ["api", name], ["advance", ms]]}
//   "fakeTimers": true makes the page's setTimeout a fake clock that only the
//   "advance" op moves; "aborted" counts the requests whose signal fired.
//   "release" n answers the n-th held request (0-based, in request order), so a
//   test can deliver an old reply after a newer one. "api" records
//   konsolReportForm[name].
// Output (stdout, JSON):
//   {"snaps": [...], "results": [...], "requests": [{kind, args}], "inspects": n,
//    "logs": [[level, message]], "watched": bool, "error": text|null}
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(
  path.join(here, "..", "public", "excel-addin", "report_form.js"), "utf8");
const input = JSON.parse(process.argv[2]);

// -- a fake DOM: only what report_form.js uses ---------------------------------

class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attrs = {};
    this.listeners = {};
    this.hidden = false;
    this.disabled = false;
    this.multiple = false;
    this.selected = false;
    this.className = "";
    this.type = "";
    this.title = "";
    this.placeholder = "";
    this._text = "";
    this._value = "";
    this.parentNode = null;
  }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); c.parentNode = null; return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "id") this.id = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  fire(t) { (this.listeners[t] || []).forEach((fn) => fn({ type: t, target: this })); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this._text = String(v); this.children.forEach((c) => { c.parentNode = null; }); this.children = []; }
  get options() { return this.children.filter((c) => c.tagName === "OPTION"); }
  get value() {
    if (this.tagName === "SELECT") {
      const opts = this.options;
      const sel = opts.find((o) => o.selected);
      if (sel) return sel.value;
      return this.multiple || !opts.length || this._none ? "" : opts[0].value;
    }
    return this._value;
  }
  set value(v) {
    v = String(v);
    if (this.tagName === "SELECT") {
      let hit = false;
      this.options.forEach((o) => { o.selected = !hit && o.value === v; if (o.selected) hit = true; });
      this._none = !hit;
      return;
    }
    this._value = v;
  }
}

const document = { createElement: (t) => new El(t) };

function walk(el, fn) {
  fn(el);
  el.children.forEach((c) => walk(c, fn));
}
function find(root, pred) {
  let hit = null;
  walk(root, (e) => { if (!hit && pred(e)) hit = e; });
  return hit;
}

const els = {};
for (const k of ["select", "fields", "templateWhy", "hint", "blocked", "error", "applyButton", "snapshotButton"]) {
  els[k] = new El(k.endsWith("Button") ? "button" : k === "select" ? "select" : "div");
}
els.applyButton.textContent = "Apply report";
const root = new El("div");
Object.values(els).forEach((e) => root.appendChild(e));

// -- stubs ---------------------------------------------------------------------

const out = { snaps: [], results: [], requests: [], inspects: 0, logs: [], watched: false, error: null };
let activated = null;

function sameArgs(a, b) {
  const ka = Object.keys(a || {}).sort();
  const kb = Object.keys(b || {}).sort();
  return JSON.stringify(ka) === JSON.stringify(kb) && ka.every((k) => String(a[k]) === String(b[k]));
}

const held = [];
out.aborted = 0;

// The page's clock: fake when the scenario says so, so a 30 s timeout is a test op.
const fake = { now: 0, timers: [], next: 1 };
function fakeSetTimeout(fn, ms) {
  const id = fake.next++;
  fake.timers.push({ id, at: fake.now + (ms || 0), fn });
  return id;
}
function fakeClearTimeout(id) { fake.timers = fake.timers.filter((t) => t.id !== id); }
function advance(ms) {
  fake.now += ms;
  const due = fake.timers.filter((t) => t.at <= fake.now);
  fake.timers = fake.timers.filter((t) => t.at > fake.now);
  due.forEach((t) => t.fn());
}

function fetchChoices(kind, args, opts) {
  out.requests.push({ kind, args });
  if (opts && opts.signal) opts.signal.addEventListener("abort", () => { out.aborted += 1; });
  const hit = (input.choices || []).find(([k, a]) => k === kind && sameArgs(a, args));
  if (!hit) return Promise.reject(new Error("no stub for " + kind + " " + JSON.stringify(args)));
  if ((input.hold || []).includes(kind)) {
    return new Promise((resolve) => { held.push(() => resolve({ kind, choices: hit[2] })); });
  }
  return Promise.resolve({ kind, choices: hit[2] });
}

function fetchCatalog() {
  if (input.catalog === null) return Promise.reject(new Error(input.catalogError || "HTTP 500"));
  return Promise.resolve(input.catalog);
}

function inspect() {
  const list = input.inspect || [{ own: false, entry: null, reason: "no stub" }];
  const answer = list[Math.min(out.inspects, list.length - 1)];
  out.inspects += 1;
  return Promise.resolve(answer);
}

const context = {
  Promise, JSON, Number, String, Object, Array, Math, Error, RegExp, isFinite, parseInt,
  AbortController,
  setTimeout: input.fakeTimers ? fakeSetTimeout : setTimeout,
  clearTimeout: input.fakeTimers ? fakeClearTimeout : clearTimeout,
  console: { log: (...a) => process.stderr.write(a.join(" ") + "\n") },
  document,
  Excel: {
    run(cb) {
      const ctx = {
        workbook: { worksheets: { onActivated: { add(h) { activated = h; out.watched = true; } } } },
        sync: () => Promise.resolve(),
      };
      return Promise.resolve().then(() => cb(ctx));
    },
  },
};
context.window = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: "report_form.js" });

// -- reading the panel -----------------------------------------------------------

function optionIds(sel) { return sel.options.map((o) => o.value); }

function fieldView(name) {
  const input_ = find(els.fields, (e) => e.getAttribute("data-param") === name);
  const why = find(els.fields, (e) => e.getAttribute("data-why") === name);
  const label = find(els.fields, (e) => e.getAttribute("data-label") === name);
  const kindSel = find(els.fields, (e) => e.getAttribute("data-scope-kind") === name);
  if (!input_) return null;
  const v = {
    tag: input_.tagName.toLowerCase(),
    type: input_.type || null,
    multiple: !!input_.multiple,
    list: input_.getAttribute("list"),
    value: input_.tagName === "SELECT" && input_.multiple
      ? input_.options.filter((o) => o.selected).map((o) => o.value) : input_.value,
    disabled: !!input_.disabled,
    why: why && !why.hidden ? why.textContent : "",
    label: label ? label.textContent : null,
  };
  if (input_.tagName === "SELECT") v.options = optionIds(input_);
  if (v.list) {
    const dl = find(els.fields, (e) => e.tagName === "DATALIST" && e.id === v.list);
    v.options = dl ? optionIds(dl) : null;
  }
  if (kindSel) v.kinds = { options: optionIds(kindSel), value: kindSel.value };
  return v;
}

function snap(ctl) {
  const names = [];
  walk(els.fields, (e) => { const n = e.getAttribute("data-param"); if (n) names.push(n); });
  const fields = {};
  names.forEach((n) => { fields[n] = fieldView(n); });
  const kindsWhy = {};
  walk(els.fields, (e) => {
    const n = e.getAttribute("data-kinds-why");
    if (n) kindsWhy[n] = e.hidden ? "" : e.textContent;
  });
  return {
    kindsWhy,
    template: els.select.value,
    templates: els.select.options.map((o) => ({ id: o.value, text: o.textContent, disabled: !!o.disabled })),
    templateWhy: els.templateWhy.hidden ? "" : els.templateWhy.textContent,
    hint: els.hint.textContent,
    error: els.error.hidden ? "" : els.error.textContent,
    blocked: els.blocked.hidden ? "" : els.blocked.textContent,
    apply: { text: els.applyButton.textContent, disabled: !!els.applyButton.disabled, hidden: !!els.applyButton.hidden },
    snapshot: { disabled: !!els.snapshotButton.disabled, hidden: !!els.snapshotButton.hidden },
    fields,
    order: names,
    values: ctl.values(),
  };
}

const settle = async () => { for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0)); };

try {
  const api = context.konsolReportForm;
  if (!api) throw new Error("report_form.js did not define konsolReportForm");
  const ctl = api.mount(Object.assign({ doc: document, fetchCatalog, fetchChoices, inspect,
    log: (level, message) => out.logs.push([level, message]) }, els));
  await ctl.watchActivation();
  for (const [op, ...args] of input.ops || []) {
    if (op === "load") ctl.load();
    else if (op === "select") { els.select.value = args[0]; els.select.fire("change"); }
    else if (op === "set" || op === "setMany") {
      const el = find(els.fields, (e) => e.getAttribute("data-param") === args[0]);
      if (!el) throw new Error("no field " + args[0]);
      if (op === "setMany") el.options.forEach((o) => { o.selected = args[1].includes(o.value); });
      else el.value = args[1];
      el.fire("input");
      el.fire("change");
    } else if (op === "setKind") {
      const el = find(els.fields, (e) => e.getAttribute("data-scope-kind") === args[0]);
      if (!el) throw new Error("no kind toggle " + args[0]);
      el.value = args[1];
      el.fire("change");
    } else if (op === "activate") {
      if (!activated) throw new Error("no onActivated handler");
      activated({ type: "WorksheetActivated" });
    } else if (op === "snap") { await settle(); out.snaps.push(snap(ctl)); continue; }
    else if (op === "values") out.results.push(ctl.values());
    else if (op === "missing") out.results.push(ctl.missing());
    else if (op === "normalise") out.results.push(api.normaliseRequest(args[0]));
    else if (op === "advance") advance(args[0]);
    else if (op === "release") {
      if (!held[args[0]]) throw new Error("no held request " + args[0]);
      held[args[0]]();
    } else if (op === "api") out.results.push(JSON.parse(JSON.stringify(api[args[0]])));
    else throw new Error("unknown op " + op);
    await settle();
  }
} catch (err) {
  out.error = String(err && err.stack || err);
}
process.stdout.write(JSON.stringify(out));
