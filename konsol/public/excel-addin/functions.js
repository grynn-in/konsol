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
    // "omit" — see the api() helper in index.html: a session cookie turns this
    // into a cookie-authenticated POST that Frappe rejects with CSRFTokenError
    // (400). The X-Konsolidat-Token header above is the intended auth path.
    credentials: "omit",
    body: JSON.stringify(body)
  }).then(function (res) {
    if (res.status === 401 || res.status === 403) {
      throw new CustomFunctions.Error(
        CustomFunctions.ErrorCode.notAvailable,
        "Not logged in — open the Konsolidat task pane and sign in."
      );
    }
    if (!res.ok) {
      throw new CustomFunctions.Error(
        CustomFunctions.ErrorCode.notAvailable,
        "Request failed (HTTP " + res.status + ")"
      );
    }
    return res.json();
  });
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

function makeReq(entity, year, period, account, measure, scenario, costCenter, department, scenarioId, hierarchy, node, layer) {
  var req = {
    entity: String(entity),
    year: Number(year),
    period: period,
    account: String(account)
  };
  if (measure) req.measure = String(measure);
  if (scenario) req.scenario = String(scenario);
  if (costCenter) req.cost_center = String(costCenter);
  if (department) req.department = String(department);
  if (scenarioId) req.scenario_id = String(scenarioId);
  if (hierarchy) req.hierarchy = String(hierarchy);
  if (node) req.hierarchy_node = String(node);
  if (layer) req.layer = String(layer);
  return req;
}

function ping() {
  return 1;
}

function epm(entity, year, period, account, measure, scenario, costCenter, department, scenarioId, hierarchy, node, layer) {
  return enqueue(makeReq(entity, year, period, account, measure, scenario, costCenter, department, scenarioId, hierarchy, node, layer));
}

function epmBudget(entity, year, period, account, costCenter, department, scenarioId, hierarchy, node, layer) {
  return enqueue(makeReq(entity, year, period, account, "period_amount", "budget", costCenter, department, scenarioId, hierarchy, node, layer));
}

function epmVariance(entity, year, period, account, costCenter, department, scenarioId, hierarchy, node) {
  return enqueue(makeReq(entity, year, period, account, "variance_abs", "variance", costCenter, department, scenarioId, hierarchy, node));
}

function epmDebit(entity, year, period, account, costCenter, department, hierarchy, node) {
  return enqueue(makeReq(entity, year, period, account, "period_debit", "actuals", costCenter, department, "", hierarchy, node));
}

function epmCredit(entity, year, period, account, costCenter, department, hierarchy, node) {
  return enqueue(makeReq(entity, year, period, account, "period_credit", "actuals", costCenter, department, "", hierarchy, node));
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

function epmSave(amount, entity, year, period, account, scenarioId, layer, costCenter, department, hierarchy, node) {
  var amt = Number(amount);

  if (!isFinite(amt) || !isFinite(Number(year)) || !isFinite(Number(period))) {
    throw new CustomFunctions.Error(
      CustomFunctions.ErrorCode.invalidValue, "Invalid EPMSAVE arguments"
    );
  }

  var key = [scenarioId, entity, year, period, account, layer,
             costCenter || "", department || "", hierarchy || "", node || ""].join("|");
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
  if (costCenter) data.dim_cost_center = String(costCenter);
  if (department) data.dim_department = String(department);
  if (hierarchy) data.hierarchy = String(hierarchy);
  if (node) data.hierarchy_node = String(node);

  return postJson("/api/method/konsol.api.budget_cell_save", data).then(function () {
    saveCache[key] = amt;
    return amt;
  }).catch(function () {
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