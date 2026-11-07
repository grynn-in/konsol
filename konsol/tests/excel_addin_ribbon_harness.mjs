// Runs one Excel add-in ribbon command outside Excel (Refresh sheet /
// Refresh workbook, decided 7 Nov 2026).
//
// konsol/public/excel-addin/ribbon.js is loaded into a fresh vm context with a
// stub Office, a stub Excel and stub pane hooks (whoami, log, snapshot), the
// command named in the input is called with a stub ribbon event, and what it
// did is printed as JSON. Driven by test_excel_addin_ribbon.py; no Excel, no
// browser.
//
// Input (argv[2], JSON):
//   {"action": "refreshSheet" | "refreshWorkbook" | "rememberSnapshot",
//    "user": "someone" | null,            // what whoami answers
//    "sheets": [{"name", "row1": [...], "formulas": [[...]]}],
//    "active": "<sheet name>",
//    "snapshots": {"<sheet>": {...params}},  // saved in document settings
//    "snapshotFails": {"<sheet>": "message"},
//    "sheetCalcSupported": true,          // ExcelApi 1.6
//    "excelRunThrows": false,
//    "params": {...}}                     // for rememberSnapshot
// Output (stdout, JSON): see `out` below.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(
  path.join(here, "..", "public", "excel-addin", "ribbon.js"), "utf8");
const input = JSON.parse(process.argv[2]);

const out = {
  completed: 0,       // event.completed() calls
  calcs: [],          // {sheet, markAllDirty} or {workbook: type}
  stamps: [],         // {sheet, row, col, value}
  logs: [],           // [level, message]
  snapshots: [],      // sheet names re-snapshotted
  panes: 0,           // Office.addin.showAsTaskpane() calls
  settings: null,     // document settings after the command
  saves: 0,           // settings.saveAsync calls
  active: null,       // active sheet after the command
  registered: [],     // Office.actions.associate names
  usedRangeValuesOnly: [],  // the valuesOnly argument of each getUsedRangeOrNullObject
  associatedFlag: null,     // window.konsolRibbonAssociated
  result: null,
  error: null,
};

const sheets = {};
for (const s of input.sheets || []) {
  sheets[s.name] = { name: s.name, row1: (s.row1 || []).slice(), formulas: s.formulas || [], protected: !!s.protected };
}
let active = input.active || (input.sheets && input.sheets[0] && input.sheets[0].name);
const store = {};
if (input.snapshots) store.konsolSnapshots = JSON.parse(JSON.stringify(input.snapshots));

function row1Range(sheet) {
  const r = sheet.row1;
  let first = -1;
  let last = -1;
  r.forEach((v, i) => {
    if (v !== null && v !== undefined && v !== "") {
      if (first < 0) first = i;
      last = i;
    }
  });
  return {
    isNullObject: first < 0,
    columnIndex: first,
    values: first < 0 ? [[]] : [r.slice(first, last + 1).map((v) => (v === null || v === undefined ? "" : v))],
    load() { return this; },
  };
}

function sheetProxy(name) {
  const sheet = sheets[name];
  return {
    name,
    isNullObject: !sheet,
    load() { return this; },
    activate() { active = name; },
    calculate(markAllDirty) { out.calcs.push({ sheet: name, markAllDirty }); },
    protection: { protected: !!(sheet && sheet.protected) },
    getUsedRangeOrNullObject(valuesOnly) {
      out.usedRangeValuesOnly.push(valuesOnly);
      const f = sheet.formulas;
      return { isNullObject: !f.length, formulas: f, load() { return this; } };
    },
    getRange(address) {
      if (address !== "1:1") throw new Error("stub knows only row 1, not " + address);
      return { getUsedRangeOrNullObject: () => row1Range(sheet) };
    },
    getCell(row, col) {
      return {
        set values(v) {
          out.stamps.push({ sheet: name, row, col, value: v[0][0] });
          if (row === 0) {
            while (sheet.row1.length <= col) sheet.row1.push("");
            sheet.row1[col] = v[0][0];
          }
        },
      };
    },
  };
}

function makeContext() {
  const worksheets = {
    items: [],
    load() {
      this.items = Object.keys(sheets).map(sheetProxy);
      return this;
    },
    getActiveWorksheet: () => sheetProxy(active),
    getItemOrNullObject: (name) => sheetProxy(name),
    getItem: (name) => {
      if (!sheets[name]) throw new Error("ItemNotFound " + name);
      return sheetProxy(name);
    },
  };
  return {
    workbook: {
      worksheets,
      application: { calculate(type) { out.calcs.push({ workbook: type }); } },
    },
    sync: () => Promise.resolve(),
  };
}

const registered = {};
let excelRunFailures = input.excelRunFailCount || 0;
const context = {
  Promise,
  JSON,
  Number,
  String,
  Object,
  Array,
  Date,
  Math,
  console,
  Error,
  isFinite,
  setTimeout,
  clearTimeout,
  Excel: {
    CalculationType: { full: "Full", recalculate: "Recalculate" },
    run(cb) {
      if (input.excelRunThrows) return Promise.reject(new Error("Excel.run failed (stub)"));
      if (excelRunFailures > 0) {
        excelRunFailures -= 1;
        return Promise.reject(new Error("Excel.run failed (stub)"));
      }
      return Promise.resolve().then(() => cb(makeContext()));
    },
  },
  Office: {
    AsyncResultStatus: { Succeeded: "succeeded", Failed: "failed" },
    actions: { associate(name, fn) { registered[name] = fn; out.registered.push(name); } },
    addin: { showAsTaskpane() { out.panes += 1; return Promise.resolve(); } },
    context: {
      requirements: {
        isSetSupported(name, version) {
          if (name === "ExcelApi" && version === "1.6") return input.sheetCalcSupported !== false;
          return true;
        },
      },
      document: {
        settings: {
          get: (k) => (k in store ? JSON.parse(JSON.stringify(store[k])) : null),
          set: (k, v) => { store[k] = JSON.parse(JSON.stringify(v)); },
          remove: (k) => { delete store[k]; },
          saveAsync(cb) { out.saves += 1; if (cb) cb({ status: "succeeded" }); },
        },
      },
    },
  },
  konsolWhoami: () => {
    if (input.whoami === "throw") throw new Error("whoami threw (stub)");
    if (input.whoami === "reject") return Promise.reject(new Error("HTTP 502 (stub)"));
    return Promise.resolve(input.user === undefined ? "zz@example.com" : input.user);
  },
  konsolLog: (level, message) => { out.logs.push([level, message]); },
  konsolidatSnapshotReport(params) {
    const name = params.sheet_name;
    out.snapshots.push(name);
    const fail = (input.snapshotFails || {})[name];
    if (fail) return Promise.reject(new Error(fail));
    // What applyCellMap does: create the sheet if missing, clear it, paint
    // the template's row 1 and data, and activate it.
    sheets[name] = {
      name,
      row1: (input.snapshotRow1 || ["Trial Balance (snapshot)"]).slice(),
      formulas: [["Trial Balance (snapshot)"], ["x"]],
    };
    active = name;
    return Promise.resolve({ sheet_name: name, row_count: 1 });
  },
};
context.window = context;
vm.createContext(context);
vm.runInContext(source, context);

async function settle() {
  for (let i = 0; i < 200; i += 1) await new Promise((r) => setTimeout(r, 0));
}

try {
  if (input.action === "rememberSnapshot") {
    out.result = await context.konsolRibbon.rememberSnapshot(input.params);
  } else {
    const fn = registered[input.action];
    if (!fn) throw new Error("not associated: " + input.action);
    for (let i = 0; i < (input.clicks || 1); i += 1) {
      fn({ completed() { out.completed += 1; } });
      if (input.settleBetweenClicks) await settle();
    }
    await settle();
  }
} catch (err) {
  out.error = String(err && err.message || err);
}
out.settings = store.konsolSnapshots === undefined ? null : store.konsolSnapshots;
out.active = active;
out.associatedFlag = context.konsolRibbonAssociated === undefined ? null : context.konsolRibbonAssociated;
process.stdout.write(JSON.stringify(out));
