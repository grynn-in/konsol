// Runs one Excel add-in custom function outside Excel (konsol#287).
//
// konsol/public/excel-addin/functions.js is loaded into a fresh vm context
// with a stub CustomFunctions and a stub fetch, the function named in the
// input is called, and what it returned and what it POSTed are printed as
// JSON. Driven by test_excel_addin_dimensions.py; no Excel, no browser.
//
// Input (argv[2], JSON):
//   {"fn": "EPM", "args": [...],
//    "reply": {...}, "status": 200}   // what the stub server answers
// Output (stdout, JSON):
//   {"result": <value or null>, "error": {"code", "message"} or null,
//    "requests": [{"url", "body"}]}
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(
  path.join(here, "..", "public", "excel-addin", "functions.js"), "utf8");
const input = JSON.parse(process.argv[2]);

class CFError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

const requests = [];
const registered = {};
const context = {
  CustomFunctions: {
    Error: CFError,
    ErrorCode: { invalidValue: "#VALUE!", notAvailable: "#N/A" },
    associate: (name, fn) => { registered[name] = fn; },
  },
  localStorage: { getItem: () => null },
  fetch: (url, opts) => {
    requests.push({ url, body: JSON.parse(opts.body) });
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
  isFinite,
};
vm.createContext(context);
vm.runInContext(source, context);

const out = { result: null, error: null, requests };
try {
  const fn = registered[input.fn];
  if (!fn) throw new Error("not associated: " + input.fn);
  out.result = await fn(...input.args);
} catch (err) {
  out.error = { code: err.code || null, message: String(err.message) };
}
process.stdout.write(JSON.stringify(out));
