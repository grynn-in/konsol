// Runs konsol/public/excel-addin/report.js outside Excel (konsol#548 v1 T13:
// apply a compiled report cell map with the re-apply rule decided by Deepak
// Pai, 27 Nov 2026).
//
// report.js is loaded into a fresh vm context with a fake Office and a fake
// Excel: worksheets with ids, cells, sheet-scoped named items with comments,
// document settings with saveAsync. A property of a loaded object can be read
// only after a ctx.sync() that followed its load(), as in Office.js; reading
// it earlier is recorded in `unloadedReads` and throws. Driven by
// test_excel_addin_report.py; no Excel, no browser.
//
// Input (argv[2], JSON):
//   {"action": "apply" | "inspect",
//    "cellMap": {...}, "options": {"target": "active" | "new"},   // apply
//    "sheet": "<name>",                                         // inspect; default active
//    "sheets": [{"name", "id", "cells": {"A1": value | {"f": "=..."}},
//                "names": [{"name", "area", "comment"}]}],
//    "active": "<sheet name>",
//    "settings": {"konsolReports": {...}, "konsolSnapshots": {...}},
//    "server": "https://pane.example",     // konsolServer.current()
//    "api17": true,                        // ExcelApi 1.7 supported
//    "noLogHook": false}
// Output (stdout, JSON): see `out` below.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(
  path.join(here, "..", "public", "excel-addin", "report.js"), "utf8");
const input = JSON.parse(process.argv[2]);

const out = {
  result: null,
  error: null,
  sheets: [],          // [{name, id, cells: {addr: value|"=formula"}, formats: {addr: {nf, b}}, names: [...]}]
  settings: null,      // document settings after the call
  active: null,
  logs: [],            // [level, message]
  pins: 0,
  saves: 0,
  writes: [],          // [sheet, addr] every cell written or cleared
  unloadedReads: [],   // properties read before load+sync
};

// -- A1 addresses ------------------------------------------------------------

function colIndex(letters) {
  let n = 0;
  for (const ch of letters) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n - 1;
}
function colLetter(index) {
  let s = "";
  let n = index + 1;
  while (n > 0) {
    const m = (n - 1) % 26;
    s = String.fromCharCode(65 + m) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}
function parseCell(a) {
  const m = /^\$?([A-Z]+)\$?(\d+)$/.exec(a);
  if (!m) throw new Error("fake Excel: bad cell " + a);
  return { r: Number(m[2]) - 1, c: colIndex(m[1]) };
}
function parseRange(address) {
  const bare = address.includes("!") ? address.slice(address.lastIndexOf("!") + 1) : address;
  const [a, b] = bare.split(":");
  const p = parseCell(a);
  const q = parseCell(b || a);
  return { r1: Math.min(p.r, q.r), c1: Math.min(p.c, q.c), r2: Math.max(p.r, q.r), c2: Math.max(p.c, q.c) };
}
function addr(r, c) { return colLetter(c) + (r + 1); }
function rangeText(g) {
  const a = addr(g.r1, g.c1);
  const b = addr(g.r2, g.c2);
  return a === b ? a : a + ":" + b;
}
function quoted(name) { return "'" + name.replace(/'/g, "''") + "'"; }

// -- the workbook ------------------------------------------------------------

const book = [];  // ordered sheets
for (const s of input.sheets || []) {
  const cells = {};
  for (const [k, v] of Object.entries(s.cells || {})) {
    cells[k] = v && typeof v === "object" ? { f: v.f, v: v.v === undefined ? "" : v.v } : { v };
  }
  book.push({
    name: s.name,
    id: s.id || "{" + s.name + "}",
    cells,
    names: (s.names || []).map((n) => ({ name: n.name, area: n.area, comment: n.comment || "" })),
  });
}
let active = input.active || (book[0] && book[0].name);
let nextId = 100;
const store = JSON.parse(JSON.stringify(input.settings || {}));

function findSheet(name) {
  return book.find((s) => s.name.toLowerCase() === String(name).toLowerCase()) || null;
}

// Each context keeps the objects whose load() is waiting for a sync.
function makeContext() {
  const pending = [];
  const created = [];

  // An object whose listed properties are readable once a sync followed
  // load(); isNullObject is readable after any sync following its creation.
  function loadable(obj, props, fill) {
    const state = { loaded: false };
    const values = {};
    obj.load = function () {
      pending.push(() => { Object.assign(values, fill()); state.loaded = true; });
      return obj;
    };
    for (const p of props) {
      Object.defineProperty(obj, p, {
        get() {
          if (!state.loaded) {
            out.unloadedReads.push(p);
            throw new Error("PropertyNotLoaded: " + p);
          }
          return values[p];
        },
        configurable: true,
      });
    }
    return { state, values };
  }

  function nullable(obj, isNull) {
    let synced = false;
    created.push(() => { synced = true; });
    Object.defineProperty(obj, "isNullObject", {
      get() {
        if (!synced) { out.unloadedReads.push("isNullObject"); throw new Error("PropertyNotLoaded: isNullObject"); }
        return isNull();
      },
    });
    return obj;
  }

  function rangeProxy(sheetRec, address) {
    const g = parseRange(address);
    const rows = g.r2 - g.r1 + 1;
    const cols = g.c2 - g.c1 + 1;
    function each(fn) {
      for (let r = 0; r < rows; r += 1) for (let c = 0; c < cols; c += 1) fn(r, c, addr(g.r1 + r, g.c1 + c));
    }
    function grid(v) {
      if (!Array.isArray(v) || v.length !== rows || v.some((row) => !Array.isArray(row) || row.length !== cols)) {
        throw new Error("fake Excel: " + rows + "x" + cols + " array required for " + address);
      }
    }
    function cell(a) {
      if (!sheetRec.cells[a]) sheetRec.cells[a] = { v: "" };
      return sheetRec.cells[a];
    }
    function snapshot(key) {
      const grid2 = [];
      for (let r = 0; r < rows; r += 1) {
        grid2.push([]);
        for (let c = 0; c < cols; c += 1) {
          const x = sheetRec.cells[addr(g.r1 + r, g.c1 + c)];
          grid2[r].push(x ? (key === "formulas" ? (x.f || x.v) : x.v) : "");
        }
      }
      return grid2;
    }
    const range = {};
    const loaded = {};
    loadable(range, ["address"], () => ({ address: quoted(sheetRec.name) + "!" + rangeText(g) }));
    const baseLoad = range.load;
    range.load = function () {
      baseLoad.call(range);
      pending.push(() => { loaded.formulas = snapshot("formulas"); loaded.values = snapshot("values"); });
      return range;
    };
    for (const key of ["formulas", "values"]) {
      Object.defineProperty(range, key, {
        get() {
          if (!loaded[key]) { out.unloadedReads.push(key); throw new Error("PropertyNotLoaded: " + key); }
          return loaded[key];
        },
        set(v) {
          grid(v);
          each((r, c, a) => {
            const x = cell(a);
            if (key === "formulas") { x.f = v[r][c]; x.v = 0; } else { x.v = v[r][c]; delete x.f; }
            out.writes.push([sheetRec.name, a]);
          });
        },
      });
    }
    Object.defineProperty(range, "numberFormat", {
      set(v) { grid(v); each((r, c, a) => { cell(a).nf = v[r][c]; }); },
    });
    range.format = {
      font: {
        set bold(b) { each((r, c, a) => { cell(a).b = !!b; }); },
      },
    };
    range.clear = function () {
      each((r, c, a) => { if (sheetRec.cells[a]) out.writes.push([sheetRec.name, a]); delete sheetRec.cells[a]; });
    };
    return range;
  }

  function namedItemProxy(sheetRec, nameRec) {
    const item = {};
    nullable(item, () => !nameRec);
    loadable(item, ["name", "comment", "type"], () => (nameRec
      ? { name: nameRec.name, comment: nameRec.comment, type: "Range" } : {}));
    item.delete = function () {
      const i = sheetRec.names.indexOf(nameRec);
      if (i >= 0) sheetRec.names.splice(i, 1);
    };
    item.getRangeOrNullObject = function () {
      if (!nameRec) return nullable({ load() { return this; } }, () => true);
      const r = rangeProxy(sheetRec, nameRec.area);
      nullable(r, () => false);
      return r;
    };
    return item;
  }

  function sheetProxy(rec) {
    const sheet = {};
    nullable(sheet, () => !rec);
    loadable(sheet, ["name", "id"], () => (rec ? { name: rec.name, id: rec.id } : {}));
    if (!rec) return sheet;
    sheet.activate = () => { active = rec.name; };
    sheet.getRange = (address) => { const r = rangeProxy(rec, address); r._area = address; return r; };
    sheet.names = {
      getItemOrNullObject(name) {
        return namedItemProxy(rec, rec.names.find((n) => n.name.toLowerCase() === name.toLowerCase()) || null);
      },
      add(name, reference, comment) {
        if (rec.names.some((n) => n.name.toLowerCase() === name.toLowerCase())) {
          throw new Error("fake Excel: the name " + name + " already exists on " + rec.name);
        }
        let area = reference;
        if (reference && typeof reference === "object") area = reference._area;
        if (typeof area !== "string") throw new Error("fake Excel: names.add needs a range");
        area = area.replace(/^=/, "");
        if (area.includes("!")) area = area.slice(area.lastIndexOf("!") + 1);
        const nameRec = { name, area: rangeText(parseRange(area)), comment: comment || "" };
        rec.names.push(nameRec);
        return namedItemProxy(rec, nameRec);
      },
    };
    return sheet;
  }

  const worksheets = {
    load() { pending.push(() => { worksheets._items = book.map((s) => ({ name: s.name, id: s.id })); }); return worksheets; },
    getActiveWorksheet: () => sheetProxy(findSheet(active)),
    getItemOrNullObject: (name) => sheetProxy(findSheet(name)),
    getItem: (name) => {
      const rec = findSheet(name);
      if (!rec) throw new Error("ItemNotFound " + name);
      return sheetProxy(rec);
    },
    add(name) {
      if (findSheet(name)) throw new Error("fake Excel: a sheet named " + name + " already exists");
      if (name.length > 31) throw new Error("fake Excel: sheet name longer than 31 characters");
      const rec = { name, id: "{ws-" + (nextId++) + "}", cells: {}, names: [] };
      book.push(rec);
      return sheetProxy(rec);
    },
  };
  // items of a loaded collection carry their name and id.
  Object.defineProperty(worksheets, "items", {
    get() {
      if (!worksheets._items) { out.unloadedReads.push("items"); throw new Error("PropertyNotLoaded: items"); }
      return worksheets._items;
    },
  });

  return {
    workbook: { worksheets },
    sync: () => {
      const run = pending.splice(0);
      run.forEach((fn) => fn());
      created.splice(0).forEach((fn) => fn());
      return Promise.resolve();
    },
  };
}

// -- the globals report.js sees ----------------------------------------------

const server = input.server || "https://pane.example";
const context = {
  Promise, JSON, Number, String, Object, Array, Date, Math, Error, RegExp,
  // stdout carries the JSON result; report.js's console fallback goes to stderr.
  console: { log: (...a) => { out.consoleLines = (out.consoleLines || 0) + 1; process.stderr.write(a.join(" ") + "\n"); } },
  isFinite, setTimeout, clearTimeout,
  Excel: {
    run(cb) { return Promise.resolve().then(() => cb(makeContext())); },
  },
  Office: {
    AsyncResultStatus: { Succeeded: "succeeded", Failed: "failed" },
    context: {
      requirements: {
        isSetSupported(name, version) {
          if (name === "ExcelApi" && version === "1.7") return input.api17 !== false;
          return true;
        },
      },
      document: {
        settings: {
          get: (k) => (k in store ? JSON.parse(JSON.stringify(store[k])) : null),
          set: (k, v) => { store[k] = JSON.parse(JSON.stringify(v)); },
          remove: (k) => { delete store[k]; },
          saveAsync(cb) {
            out.saves += 1;
            if (cb) cb(input.saveFails ? { status: "failed", error: { message: "disk full (stub)" } } : { status: "succeeded" });
          },
        },
      },
    },
  },
  konsolServer: {
    current: () => server,
    pin: () => { out.pins += 1; return Promise.resolve({ origin: server }); },
  },
};
if (!input.noLogHook) context.konsolLog = (level, message) => { out.logs.push([level, message]); };
if (input.cryptoRandom !== false) {
  context.crypto = { getRandomValues(a) { for (let i = 0; i < a.length; i += 1) a[i] = Math.floor(Math.random() * 256); return a; } };
}
context.window = context;
vm.createContext(context);
vm.runInContext(source, context);

try {
  const api = context.konsolReport;
  if (!api) throw new Error("report.js did not define konsolReport");
  if (input.action === "apply") {
    out.result = await api.apply(input.cellMap, input.options || {});
  } else if (input.action === "inspect") {
    out.result = await api.inspect(input.sheet);
  } else {
    throw new Error("unknown action " + input.action);
  }
} catch (err) {
  out.error = String(err && err.message || err);
}

out.sheets = book.map((s) => {
  const cells = {};
  const formats = {};
  for (const [a, x] of Object.entries(s.cells)) {
    if (x.f) cells[a] = x.f;
    else if (x.v !== "" && x.v !== undefined && x.v !== null) cells[a] = x.v;
    if (x.nf !== undefined || x.b !== undefined) formats[a] = { nf: x.nf, b: x.b };
  }
  return { name: s.name, id: s.id, cells, formats, names: s.names };
});
out.settings = store;
out.active = active;
process.stdout.write(JSON.stringify(out));
