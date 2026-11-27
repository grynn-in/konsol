// Runs one Excel add-in custom function outside Excel (konsol#287, #605).
//
// konsol/public/excel-addin/server.js and functions.js are loaded, in the
// order index.html loads them, into a fresh vm context with a stub
// CustomFunctions, a stub fetch, a stub localStorage and a stub location.
// Optional konsolServer calls run first, then the function named in the
// input is called, and what it returned and what it sent are printed as
// JSON. Driven by test_excel_addin_dimensions.py and
// test_excel_addin_server.py; no Excel, no browser.
//
// Input (argv[2], JSON):
//   {"fn": "EPM", "args": [...],          // optional: no fn, no call
//    "reply": {...}, "status": 200,       // what the stub server answers
//    "fetchFails": false,                 // fetch rejects, as a refused CORS call does
//    "origin": "https://pane.example",    // location.origin of the pane
//    "storage": {"key": "value"},         // localStorage before loading
//    "ops": [["choose", "https://b.example"], ["getToken"], ...]}
// Output (stdout, JSON):
//   {"result": <value or null>, "error": {"code", "message"} or null,
//    "requests": [{"url", "body", "headers", "credentials"}],
//    "ops": [<result of each op>], "storage": {...}}
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const addin = path.join(here, "..", "public", "excel-addin");
const input = JSON.parse(process.argv[2]);

class CFError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

const storage = Object.assign({}, input.storage || {});
const localStorage = {
  getItem: (k) => (Object.prototype.hasOwnProperty.call(storage, k) ? storage[k] : null),
  setItem: (k, v) => { storage[k] = String(v); },
  removeItem: (k) => { delete storage[k]; },
};

const requests = [];
const registered = {};
const context = {
  CustomFunctions: {
    Error: CFError,
    ErrorCode: { invalidValue: "#VALUE!", notAvailable: "#N/A" },
    associate: (name, fn) => { registered[name] = fn; },
  },
  localStorage,
  location: { origin: input.origin || "https://pane.example" },
  fetch: (url, opts) => {
    requests.push({ url, body: JSON.parse(opts.body), headers: opts.headers || {},
                    credentials: opts.credentials || null });
    if (input.fetchFails) return Promise.reject(new TypeError("Failed to fetch"));
    const status = input.status || 200;
    const reply = input.reply || { message: { values: [42] } };
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(reply),
    });
  },
  Promise,
  JSON,
  Number,
  String,
  Object,
  Array,
  RegExp,
  isFinite,
};
vm.createContext(context);
for (const name of ["server.js", "functions.js"]) {
  vm.runInContext(fs.readFileSync(path.join(addin, name), "utf8"), context, { filename: name });
}

const out = { result: null, error: null, requests, ops: [], storage };
try {
  for (const [method, ...args] of input.ops || []) {
    const v = context.konsolServer[method](...args);
    out.ops.push(v === undefined ? null : v);
  }
  if (input.fn) {
    const fn = registered[input.fn];
    if (!fn) throw new Error("not associated: " + input.fn);
    out.result = await fn(...input.args);
  }
} catch (err) {
  out.error = { code: err.code || null, message: String(err.message) };
}
process.stdout.write(JSON.stringify(out));
