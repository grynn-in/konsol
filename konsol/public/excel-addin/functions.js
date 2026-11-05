/* global CustomFunctions, fetch, Office, Excel */

// ============================================================
// Open EPM — Excel Custom Functions  (namespace "K")
// ============================================================
//
// Served same-origin by Frappe at /assets/konsol/excel-addin/.
// Loaded synchronously from index.html (shared runtime) — same pattern
// as Microsoft's excel-shared-runtime-scenario sample.
// ============================================================

var MAX_BATCH = 2000;

function authHeaders(extra) {
  var headers = extra || {};
  try {
    var token = localStorage.getItem("konsol_token");
    if (token) headers["X-Konsolidat-Token"] = token;
  } catch (e) { /* localStorage blocked */ }
  return headers;
}

function postJson(path, body) {
  return fetch(path, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    credentials: "include",
    body: JSON.stringify(body)
  }).then(function (res) {
    if (res.status === 401 || res.status === 403) {
      throw new CustomFunctions.Error(
        CustomFunctions.ErrorCode.notAvailable,
        "Not logged in — open the Konsolidat task pane and sign in."
      );
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

    chunk.forEach(function (e, idx) {
      var err = errors[idx];
      if (err) {
        e.reject(new CustomFunctions.Error(
          CustomFunctions.ErrorCode.invalidValue, String(err)
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
function checkArity(args, fn, name) {
  if (args.length > fn.length) {
    throw new CustomFunctions.Error(
      CustomFunctions.ErrorCode.invalidValue,
      "K." + name + " takes at most " + fn.length + " arguments, not " + args.length +
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

function epmSave(amount, entity, year, period, account, scenarioId, layer, dimensions, hierarchy, node) {
  checkArity(arguments, epmSave, "EPMSAVE");
  var amt = Number(amount);

  if (!isFinite(amt) || !isFinite(Number(year)) || !isFinite(Number(period))) {
    throw new CustomFunctions.Error(
      CustomFunctions.ErrorCode.invalidValue, "Invalid EPMSAVE arguments"
    );
  }

  var dims = parseDimensions(dimensions);
  var key = [scenarioId, entity, year, period, account, layer,
             dimensionsKey(dims), hierarchy || "", node || ""].join("|");
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
    layer: String(layer)
  };
  if (dims) data.dimensions = dims;
  if (hierarchy) data.hierarchy = String(hierarchy);
  if (node) data.hierarchy_node = String(node);

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