/* global CustomFunctions, fetch, Office, Excel, konsolServer, localStorage, console */

// ============================================================
// Open EPM — Excel Custom Functions  (namespace "K")
// ============================================================
//
// Served by Frappe at /assets/konsol/excel-addin/.
// Loaded synchronously from index.html (shared runtime) — same pattern
// as Microsoft's excel-shared-runtime-scenario sample.
//
// Every URL and token comes from server.js (konsolServer, konsol#605):
// relative paths to the pane's own origin by default, or the workbook's
// chosen site's address with that site's own token.
// ============================================================

var MAX_BATCH = 2000;

// An index.html cached from before konsol#605 loads this file but not
// server.js. Its pane talks to its own origin and keeps one token under the
// pre-#605 key, so the cells do the same, and the console says why rather
// than every cell failing with a ReferenceError (konsol#618 review).
function sameOriginServer() {
  if (typeof console !== "undefined" && console.warn) {
    console.warn("[konsol] server.js is not loaded (the task pane page is an older cached copy): " +
      "K.* functions read this pane's own site. Reload the add-in to choose a server.");
  }
  function token() {
    try { return localStorage.getItem("konsol_token") || ""; } catch (e) { return ""; }
  }
  return {
    url: function (path) { return path; },
    getToken: token,
    isCrossOrigin: function () { return false; },
    unreachableMessage: function () {
      return "Could not reach the Konsolidat server. Check the connection, then reload the add-in.";
    }
  };
}

var fallbackServer = null;

function server() {
  if (typeof konsolServer !== "undefined") return konsolServer;
  if (!fallbackServer) fallbackServer = sameOriginServer();
  return fallbackServer;
}

function authHeaders(extra) {
  var headers = extra || {};
  var token = server().getToken();
  if (token) headers["X-Konsolidat-Token"] = token;
  return headers;
}

function hasToken() {
  return !!server().getToken();
}

function officeReady() {
  if (typeof Office !== "undefined" && typeof Office.onReady === "function") {
    try { return Promise.resolve(Office.onReady()); } catch (e) { /* fall through */ }
  }
  return Promise.resolve();
}

function postJson(path, body) {
  // With the add-in's own token, send no cookie: a Desk session in the same
  // browser carries a CSRF token, and Frappe refuses a POST without it (400).
  // Another site never gets a cookie either: it sends no Allow-Credentials.
  // The workbook's server is in its document settings, which Office fills
  // in when it has initialised: wait for that, or a cell computed as the
  // workbook opens could read the pane's own site instead of the workbook's.
  return officeReady().then(function () {
    // The first call of a workbook with no stored server keeps this pane's
    // origin with it (server.js pin). A failed save is not a failed call.
    var s = server();
    return typeof s.pin === "function" ? s.pin() : null;
  }).then(function () {
    return fetch(server().url(path), {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      credentials: hasToken() || server().isCrossOrigin() ? "omit" : "include",
      body: JSON.stringify(body)
    });
  }).then(null, function () {
    // The request got no answer: the site is down, or refused this add-in's
    // origin at the CORS check. Name the fix.
    throw new CustomFunctions.Error(CustomFunctions.ErrorCode.notAvailable, server().unreachableMessage());
  }).then(function (res) {
    if (res.status === 401 || res.status === 403) {
      // A 403 is also an access denial (a budget write the user may not
      // make, S2-X3): name it when the server says why, and still say how
      // to sign in, since the session may be gone instead.
      return res.json().catch(function () { return {}; }).then(function (body) {
        var why = res.status === 403 ? serverMessage(body) : "";
        throw new CustomFunctions.Error(
          CustomFunctions.ErrorCode.notAvailable,
          why ? why + " (If you are not signed in, open the Konsolidat task pane and sign in.)"
            : "Not logged in — open the Konsolidat task pane and sign in."
        );
      });
    }
    if (!res.ok) {
      return res.json().catch(function () { return {}; }).then(function (body) {
        throw new CustomFunctions.Error(
          CustomFunctions.ErrorCode.notAvailable,
          serverMessage(body) || "Request failed (HTTP " + res.status + ")"
        );
      });
    }
    return res.json();
  });
}

// The message a Frappe refusal carries (frappe.throw → _server_messages, a
// JSON list of JSON objects), so a refused write shows why in the cell.
function serverMessage(body) {
  try {
    var msgs = JSON.parse(body._server_messages || "[]");
    if (msgs.length) {
      var first = JSON.parse(msgs[0]);
      if (first && first.message) return String(first.message);
    }
  } catch (e) { /* not a Frappe message list */ }
  return body && body.exception ? String(body.exception) : "";
}

// ------------------------------------------------------------
// Dimensions (konsol#287): one argument, never fixed names.
//
// Excel passes the `dimensions` parameter (dimensionality "matrix") as rows:
//   - one cell of text:  [["name1=value1;name2=value2"]]
//   - a 2-column range:  [["name1","value1"],["name2","value2"]]
// Returns {name: value}, or null when there are none. Anything malformed is
// refused, never dropped: a dropped filter would answer with the total over
// every value of that dimension. Whether a name is declared is the server's
// to say; it refuses an undeclared one.
// ------------------------------------------------------------
function dimensionsError(message) {
  return new CustomFunctions.Error(
    CustomFunctions.ErrorCode.invalidValue,
    "dimensions: " + message +
      ' (use one text cell like "name1=value1;name2=value2", or a 2-column range of name/value pairs)'
  );
}

function cellText(v) {
  return v === null || v === undefined ? "" : String(v).trim();
}

function parseDimensions(arg) {
  if (arg === null || arg === undefined) return null;
  var rows = Array.isArray(arg) ? arg : [[arg]];
  var out = {};
  var count = 0;

  function add(name, value) {
    if (!name) throw dimensionsError('a value "' + value + '" has no dimension name');
    if (!value) throw dimensionsError('"' + name + '" has no value');
    if (Object.prototype.hasOwnProperty.call(out, name)) {
      throw dimensionsError('"' + name + '" is given twice');
    }
    out[name] = value;
    count += 1;
  }

  var width = rows.length ? (Array.isArray(rows[0]) ? rows[0].length : 1) : 0;
  if (width === 1 && rows.length === 1) {
    var text = cellText(Array.isArray(rows[0]) ? rows[0][0] : rows[0]);
    if (!text) return null;
    text.split(";").forEach(function (part) {
      if (!part.trim()) return;
      var eq = part.indexOf("=");
      if (eq < 0) throw dimensionsError('"' + part.trim() + '" is not name=value');
      add(part.slice(0, eq).trim(), part.slice(eq + 1).trim());
    });
  } else if (width === 2) {
    rows.forEach(function (row) {
      var name = cellText(row[0]);
      var value = cellText(row[1]);
      if (!name && !value) return;  // a blank row in the range
      add(name, value);
    });
  } else {
    throw dimensionsError("a range must have exactly 2 columns");
  }
  return count ? out : null;
}

// Stable text for a dimensions object, for EPMSAVE's unchanged-value cache.
function dimensionsKey(dims) {
  if (!dims) return "";
  return Object.keys(dims).sort().map(function (k) {
    return k + "=" + dims[k];
  }).join(";");
}

var pending = [];
var flushScheduled = false;

function enqueue(req) {
  return new Promise(function (resolve, reject) {
    if (!isFinite(req.year)) {
      reject(new CustomFunctions.Error(
        CustomFunctions.ErrorCode.invalidValue, "Invalid year"
      ));
      return;
    }
    pending.push({ req: req, resolve: resolve, reject: reject });
    if (!flushScheduled) {
      flushScheduled = true;
      Promise.resolve().then(flush);
    }
  });
}

function flush() {
  flushScheduled = false;
  var batch = pending;
  pending = [];
  if (batch.length === 0) return;
  for (var i = 0; i < batch.length; i += MAX_BATCH) {
    sendChunk(batch.slice(i, i + MAX_BATCH));
  }
}

function sendChunk(chunk) {
  var body = chunk.map(function (e) { return e.req; });

  postJson("/api/method/konsol.api.epm_batch", body).then(function (data) {
    var payload = (data && data.message) || {};
    var values = payload.values || [];
    var errors = payload.errors || [];
    var codes = payload.error_codes || [];

    chunk.forEach(function (e, idx) {
      var err = errors[idx];
      if (err) {
        // "no_data": nothing is loaded for that cell, e.g. no trial balance
        // for the period (konsol#401). #N/A, not a #VALUE! the formula caused.
        e.reject(new CustomFunctions.Error(
          codes[idx] === "no_data" ? CustomFunctions.ErrorCode.notAvailable
            : CustomFunctions.ErrorCode.invalidValue,
          String(err)
        ));
        return;
      }
      var v = values[idx];
      e.resolve(v === null || v === undefined ? 0 : v);
    });
  }).catch(function (err) {
    chunk.forEach(function (e) {
      e.reject(err instanceof CustomFunctions.Error ? err :
        new CustomFunctions.Error(CustomFunctions.ErrorCode.notAvailable, String(err && err.message || err)));
    });
  });
}

function makeReq(entity, year, period, account, measure, scenario, dimensions, scenarioId, hierarchy, node, layer) {
  var req = {
    entity: String(entity),
    year: Number(year),
    period: period,
    account: String(account)
  };
  var dims = parseDimensions(dimensions);
  if (measure) req.measure = String(measure);
  if (scenario) req.scenario = String(scenario);
  if (dims) req.dimensions = dims;
  if (scenarioId) req.scenario_id = String(scenarioId);
  if (hierarchy) req.hierarchy = String(hierarchy);
  if (node) req.hierarchy_node = String(node);
  if (layer) req.layer = String(layer);
  return req;
}

// One argument more than the signature is an old formula (before konsol#287
// there were two dimension slots): dropping its last argument would lose a
// filter, so it is refused. `args` is the caller's `arguments`; `fn` is the
// caller, whose .length is its declared parameter count.
//
// This holds only while no function in functions.json sets
// requiresInvocation, stream, cancelable, requiresAddress or
// requiresParameterAddresses: with any of those, Excel appends an invocation
// argument and this guard would refuse every call
// (test_no_function_makes_excel_append_an_invocation_argument pins it).
//
// Only a slot that carries a value counts. Excel passes every parameter its
// cached functions.json declares, an omitted one as null: an Excel still
// holding the 12-argument metadata from before konsol#287 sends 12 arguments
// for K.EPM("DE_ECL", 2024, 12, "4000"). Empty extra slots lose nothing.
//
// An object that is not a range is Excel's invocation context, which Excel
// Online appends after the declared parameters even without the options
// above (seen on demo.konsolidat.com, 6 Nov 2026). It is not the formula's.
function checkArity(args, fn, name) {
  var extra = null;
  for (var i = fn.length; i < args.length; i++) {
    var v = args[i];
    // Excel's invocation context: the last argument, an object, not a range.
    if (i === args.length - 1 && v !== null && typeof v === "object" && !Array.isArray(v)) continue;
    if (Array.isArray(v)) v = v.length === 1 && Array.isArray(v[0]) && v[0].length === 1 ? v[0][0] : v;
    if (v === null || v === undefined || v === "") continue;
    extra = { index: i + 1, value: v };
    break;
  }
  if (extra) {
    throw new CustomFunctions.Error(
      CustomFunctions.ErrorCode.invalidValue,
      "K." + name + " takes at most " + fn.length + " arguments, but argument " + extra.index +
        " is " + String(JSON.stringify(extra.value)).slice(0, 80) +
        ". A formula from before konsol#287 must be rewritten: the two dimension arguments are now one."
    );
  }
}

// Build the request and queue it; a refused argument becomes the cell's error.
function read(args, fn, name, build) {
  try {
    checkArity(args, fn, name);
    return enqueue(build());
  } catch (err) {
    return Promise.reject(err);
  }
}

function ping() {
  return 1;
}

//   =K.EPM("E1", 2026, 3, "4000", , , "name1=value1;name2=value2")
function epm(entity, year, period, account, measure, scenario, dimensions, scenarioId, hierarchy, node, layer) {
  return read(arguments, epm, "EPM", function () {
    return makeReq(entity, year, period, account, measure, scenario, dimensions, scenarioId, hierarchy, node, layer);
  });
}

function epmBudget(entity, year, period, account, dimensions, scenarioId, hierarchy, node, layer) {
  return read(arguments, epmBudget, "EPM_BUDGET", function () {
    return makeReq(entity, year, period, account, "period_amount", "budget", dimensions, scenarioId, hierarchy, node, layer);
  });
}

function epmVariance(entity, year, period, account, dimensions, scenarioId, hierarchy, node) {
  return read(arguments, epmVariance, "EPM_VARIANCE", function () {
    return makeReq(entity, year, period, account, "variance_abs", "variance", dimensions, scenarioId, hierarchy, node);
  });
}

function epmDebit(entity, year, period, account, dimensions, hierarchy, node) {
  return read(arguments, epmDebit, "EPM_DEBIT", function () {
    return makeReq(entity, year, period, account, "period_debit", "actuals", dimensions, "", hierarchy, node);
  });
}

function epmCredit(entity, year, period, account, dimensions, hierarchy, node) {
  return read(arguments, epmCredit, "EPM_CREDIT", function () {
    return makeReq(entity, year, period, account, "period_credit", "actuals", dimensions, "", hierarchy, node);
  });
}

// Consolidated cash-flow line read. Same epm_batch backend as K.EPM, pointed at
// the `cashflow` Dataset (gold_cashflow_fact): the group goes in the entity
// slot and the cash-flow line name in the account slot. Self-documenting args.
//   =K.CF("GROUP_CORP", 2024, 6, "Change in Inventory")
function cf(group, year, period, line) {
  return enqueue({
    entity: String(group),
    year: Number(year),
    period: period,
    account: String(line),
    measure: "cash_flow_amount",
    fact: "cashflow",
  });
}

var saveCache = {};

// EPMSAVE's currency and amount basis (konsol#348 S2-X3): the server requires
// both and defaults neither, so a blank one is refused here, naming its
// argument, before anything is sent. They come after `node` so that no
// existing argument moves; Excel needs optional parameters last, so
// functions.json declares them optional and this check makes them required.
function requiredSaveArgs(currency, amountBasis) {
  var missing = [];
  if (!cellText(Array.isArray(currency) ? currency[0] && currency[0][0] : currency)) {
    missing.push("currency (argument 11, the ISO code the amount is in, e.g. EUR)");
  }
  if (!cellText(Array.isArray(amountBasis) ? amountBasis[0] && amountBasis[0][0] : amountBasis)) {
    missing.push("amountBasis (argument 12, Movement or Year-to-date)");
  }
  if (missing.length) {
    throw new CustomFunctions.Error(
      CustomFunctions.ErrorCode.invalidValue,
      "K.EPMSAVE needs " + missing.join(" and ") + ": a budget figure states both, and neither is defaulted."
    );
  }
}

// EPMSAVE's asOfDate (konsol#348 S3 K5, QB5 a): the forecast round the cell
// is for, as YYYY-MM-DD. The server requires it for a forecast scenario and
// refuses it for any other, so it is sent as given and never filled in here.
// A date cell reaches a string parameter as its Excel serial number; that is
// turned into the ISO date, since "a round as of 46568" names nothing.
function asOfDateText(value) {
  var text = cellText(Array.isArray(value) ? value[0] && value[0][0] : value);
  if (!text) return "";
  if (/^\d+(\.\d+)?$/.test(text)) {
    var serial = Math.floor(Number(text));
    var ms = Date.UTC(1899, 11, 30) + serial * 86400000;
    return new Date(ms).toISOString().slice(0, 10);
  }
  return text;
}

function epmSave(amount, entity, year, period, account, scenarioId, layer, dimensions, hierarchy, node, currency, amountBasis, asOfDate) {
  checkArity(arguments, epmSave, "EPMSAVE");
  requiredSaveArgs(currency, amountBasis);
  var asOf = asOfDateText(asOfDate);
  var amt = Number(amount);

  if (!isFinite(amt) || !isFinite(Number(year)) || !isFinite(Number(period))) {
    throw new CustomFunctions.Error(
      CustomFunctions.ErrorCode.invalidValue, "Invalid EPMSAVE arguments"
    );
  }

  var dims = parseDimensions(dimensions);
  var key = [scenarioId, entity, year, period, account, layer,
             dimensionsKey(dims), hierarchy || "", node || "",
             String(currency), String(amountBasis), asOf].join("|");
  if (saveCache[key] === amt) {
    return amt;
  }

  var data = {
    scenario_id: String(scenarioId),
    data_area_id: String(entity),
    fiscal_year: Number(year),
    main_account: String(account),
    fiscal_period: Number(period),
    amount: amt,
    layer: String(layer),
    currency: String(currency),
    amount_basis: String(amountBasis)
  };
  if (dims) data.dimensions = dims;
  if (hierarchy) data.hierarchy = String(hierarchy);
  if (node) data.hierarchy_node = String(node);
  if (asOf) data.as_of_date = asOf;

  // A refused write shows the server's reason in the cell. It used to return
  // the amount whatever happened, so a refusal looked like a save.
  return postJson("/api/method/konsol.api.budget_cell_save", data).then(function () {
    saveCache[key] = amt;
    return amt;
  });
}

function markAssociated() {
  if (typeof window !== "undefined") {
    window.konsolFunctionsAssociated = true;
    if (typeof window.onKonsolFunctionsAssociated === "function") {
      window.onKonsolFunctionsAssociated();
    }
  }
}

// Bind JS implementations. #NAME? is a functions.json metadata issue, not associate.
if (typeof CustomFunctions !== "undefined") {
  CustomFunctions.associate("PING", ping);
  CustomFunctions.associate("EPM", epm);
  CustomFunctions.associate("EPM_BUDGET", epmBudget);
  CustomFunctions.associate("EPM_VARIANCE", epmVariance);
  CustomFunctions.associate("EPM_DEBIT", epmDebit);
  CustomFunctions.associate("EPM_CREDIT", epmCredit);
  CustomFunctions.associate("CF", cf);
  CustomFunctions.associate("EPMSAVE", epmSave);
  markAssociated();
} else if (typeof window !== "undefined") {
  window.konsolFunctionsAssociated = false;
}