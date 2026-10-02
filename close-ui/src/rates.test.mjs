// konsol#305 E407: rates.test.mjs
//
// Exercises rates.js against the real `rates_api.py` payload shapes
// (get_rates, get_pending, get_ownership), not the row's original sketch —
// see rates.js's header comment for where they differ (previousLabel
// carries the record name; a raw `status` field rides beside `statusLabel`;
// ownershipView also surfaces `hiddenCount`, added by #305-W2-10/W2-14
// after this row was written).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  STATUS_LABELS,
  gridView,
  saveBody,
  approveAction,
  approveBody,
  pendingView,
  ownershipView,
} from "./rates.js";

function cell(overrides = {}) {
  return {
    status: "missing",
    name: null,
    owner: null,
    quote: null,
    quoted_per: null,
    rate: null,
    previous: null,
    delta: null,
    flag: null,
    change_reason: null,
    source: null,
    extra_drafts: [],
    approver: null,
    approve: null,
    edited_by: null,
    ...overrides,
  };
}

function row(overrides = {}) {
  return {
    from_currency: "JPY",
    to_currency: "USD",
    required: true,
    closing: cell(),
    average: cell(),
    ...overrides,
  };
}

function payload(overrides = {}) {
  return {
    period: { fiscal_year: 2026, fiscal_period: 8, period_code: "FY2026-P08", status: "Open" },
    rows: [],
    unrequired: [],
    summary: { missing: 0, awaiting_approval: 0, approved: 0 },
    pairs_error: null,
    blockers: [],
    threshold_pct: 30,
    policy_gaps: [],
    self_approval: "Blocked",
    can_enter: true,
    can_approve: false,
    ...overrides,
  };
}

// -- labels -----------------------------------------------------------------

test("STATUS_LABELS names the three server statuses", () => {
  assert.deepEqual(STATUS_LABELS, {
    missing: "Missing",
    awaiting_approval: "Awaiting approval",
    approved: "Approved",
  });
});

test("an unknown cell status throws", () => {
  const view = payload({ rows: [row({ closing: cell({ status: "weird" }) })] });
  assert.throws(() => gridView(view), /Unknown rate status: weird/);
});

// -- deltaText ----------------------------------------------------------------

test("deltaText: positive delta gets a plus sign", () => {
  const view = gridView(payload({
    rows: [row({ closing: cell({
      status: "approved", name: "GER-1", quote: 1, quoted_per: 1, rate: 1,
      previous: { rate: 0.973615, quoted_per: 1, fiscal_year: 2026, fiscal_period: 7, name: "GER-0", label: "FY2026 P07 (GER-0)" },
      delta: 0.027,
    }) })],
  }));
  assert.equal(view.rows[0].closing.deltaText, "+2.7%");
});

test("deltaText: negative delta uses U+2212, not a hyphen", () => {
  const view = gridView(payload({
    rows: [row({ closing: cell({
      status: "approved", name: "GER-1", quote: 1, quoted_per: 1, rate: 1,
      previous: { rate: 1.01626, quoted_per: 1, fiscal_year: 2026, fiscal_period: 7, name: "GER-0", label: "FY2026 P07 (GER-0)" },
      delta: -0.016,
    }) })],
  }));
  assert.equal(view.rows[0].closing.deltaText, "−1.6%");
});

test("deltaText: null delta is an em dash", () => {
  const view = gridView(payload({
    rows: [row({ closing: cell({ status: "approved", name: "GER-1", quote: 1, quoted_per: 1, rate: 1, delta: null }) })],
  }));
  assert.equal(view.rows[0].closing.deltaText, "—");
});

// -- missing previous rate ---------------------------------------------------

test("a cell with no previous approved rate: label and delta both say so", () => {
  const view = gridView(payload({
    rows: [row({ closing: cell({ status: "approved", name: "GER-1", quote: 1, quoted_per: 1, rate: 1, previous: null, delta: null }) })],
  }));
  assert.equal(view.rows[0].closing.previousLabel, "No previous approved rate");
  assert.equal(view.rows[0].closing.deltaText, "—");
});

// -- undeclared threshold -----------------------------------------------------

test("undeclared threshold: thresholdText is the gap message, no digit in it", () => {
  const gap = { code: "rate_move_undeclared", message: "Declare the rate move threshold in Close Settings." };
  const view = gridView(payload({ threshold_pct: null, policy_gaps: [gap] }));
  assert.equal(view.thresholdText, gap.message);
  assert.ok(!/\d/.test(view.thresholdText));
});

test("declared threshold: thresholdText names the percent", () => {
  const view = gridView(payload({ threshold_pct: 30 }));
  assert.equal(view.thresholdText, "Moves over 30% need a Reason for Change");
});

test("a declared threshold with no gap given still throws nothing (sanity)", () => {
  assert.doesNotThrow(() => gridView(payload({ threshold_pct: 12.5 })));
});

test("undeclared threshold with no matching gap throws rather than guessing", () => {
  assert.throws(() => gridView(payload({ threshold_pct: null, policy_gaps: [] })),
    /rate_move_undeclared/);
});

// -- banner order -------------------------------------------------------------

test("banner: pairs_error, then each blocker, then each policy gap message, in that order", () => {
  const view = gridView(payload({
    pairs_error: "RuntimeError",
    blockers: ["Consolidation Group G1 has no reporting currency"],
    policy_gaps: [
      { code: "self_approval_undeclared", message: "Declare the self-approval policy." },
      { code: "rate_move_undeclared", message: "Declare the rate move threshold." },
    ],
    threshold_pct: null,
  }));
  assert.deepEqual(view.banner, [
    "The warehouse could not say which currencies this period translates: RuntimeError",
    "Consolidation Group G1 has no reporting currency",
    "Declare the self-approval policy.",
    "Declare the rate move threshold.",
  ]);
});

test("banner: no pairs_error, no blockers, no gaps is an empty list", () => {
  const view = gridView(payload());
  assert.deepEqual(view.banner, []);
});

// -- saveBody -------------------------------------------------------------------

function period() {
  return { fiscal_year: 2026, fiscal_period: 8, period_code: "FY2026-P08", status: "Open" };
}

test("saveBody: a new cell carries exactly the 8 keys, no name", () => {
  const r = row({ closing: cell({ name: null }) });
  const { body, error } = saveBody(period(), r, "Closing", { quote: "1.05", quotedPer: 1, changeReason: null });
  assert.equal(error, undefined);
  assert.deepEqual(Object.keys(body).sort(), [
    "change_reason", "fiscal_period", "fiscal_year", "from_currency",
    "quote", "quoted_per", "rate_type", "to_currency",
  ]);
  assert.equal(body.quote, 1.05);
  assert.equal(body.rate_type, "Closing");
  assert.equal(body.from_currency, "JPY");
  assert.equal(body.to_currency, "USD");
});

test("saveBody: an edited cell adds name, and never docstatus/source/owner/erp_quote/source_note", () => {
  const r = row({ average: cell({ name: "GER-77" }) });
  const { body } = saveBody(period(), r, "Average", { quote: "1.1", quotedPer: 1, changeReason: "Market move" });
  assert.deepEqual(Object.keys(body).sort(), [
    "change_reason", "fiscal_period", "fiscal_year", "from_currency",
    "name", "quote", "quoted_per", "rate_type", "to_currency",
  ]);
  assert.equal(body.name, "GER-77");
  for (const forged of ["docstatus", "source", "owner", "erp_quote", "source_note"]) {
    assert.ok(!(forged in body), `${forged} must never be in the save body`);
  }
});

test("saveBody: an empty quote gives an error and no body", () => {
  const r = row();
  const result = saveBody(period(), r, "Closing", { quote: "", quotedPer: 1, changeReason: null });
  assert.deepEqual(result, { error: "Enter a quote." });
});

test("saveBody: a non-numeric quote gives an error", () => {
  const r = row();
  const result = saveBody(period(), r, "Closing", { quote: "abc", quotedPer: 1, changeReason: null });
  assert.deepEqual(result, { error: "Enter a quote." });
});

test("saveBody: a blank Quoted Per gives an error", () => {
  const r = row();
  const result = saveBody(period(), r, "Closing", { quote: "1.05", quotedPer: "", changeReason: null });
  assert.deepEqual(result, { error: "Choose Quoted Per." });
});

// -- approveAction ----------------------------------------------------------

test("approveAction: direct becomes button", () => {
  assert.deepEqual(approveAction({ mode: "direct", message: null }), { kind: "button", message: null });
});

test("approveAction: reason stays reason, carrying the no-reason message", () => {
  const msg = "Give a reason: Close Settings allows self-approval only with a reason.";
  assert.deepEqual(approveAction({ mode: "reason", message: msg }), { kind: "reason", message: msg });
});

test("approveAction: refused stays refused", () => {
  const msg = "Self-approval is Blocked by Close Settings.";
  assert.deepEqual(approveAction({ mode: "refused", message: msg }), { kind: "refused", message: msg });
});

test("approveAction: not_approver becomes none", () => {
  const msg = "The Close Lead approves (R2).";
  assert.deepEqual(approveAction({ mode: "not_approver", message: msg }), { kind: "none", message: msg });
});

test("approveAction: an unknown mode throws", () => {
  assert.throws(() => approveAction({ mode: "mystery", message: null }), /Unknown approve mode: mystery/);
});

// -- approveBody --------------------------------------------------------------

test("approveBody: none (not_approver) gives an error and never builds a body", () => {
  const action = approveAction({ mode: "not_approver", message: "The Close Lead approves (R2)." });
  const result = approveBody("Group Exchange Rate", "GER-1", action, null);
  assert.deepEqual(result, { error: "The Close Lead approves (R2)." });
});

test("approveBody: refused (self-approval under Blocked) carries the server's sentence", () => {
  const serverMessage = "Self-approval is Blocked by Close Settings (the Close Lead or System Manager can allow it with a reason).";
  const action = approveAction({ mode: "refused", message: serverMessage });
  const result = approveBody("Ownership Period", "OP-1", action, null);
  assert.deepEqual(result, { error: serverMessage });
});

test("approveBody: reason with only whitespace gives the reason error", () => {
  const action = approveAction({ mode: "reason", message: "no-reason refusal text" });
  const result = approveBody("Group Exchange Rate", "GER-1", action, "   ");
  assert.deepEqual(result, { error: "Give a reason: Close Settings allows self-approval only with a reason." });
});

test("approveBody: reason with a real reason gives a body carrying it", () => {
  const action = approveAction({ mode: "reason", message: "no-reason refusal text" });
  const result = approveBody("Group Exchange Rate", "GER-1", action, "ok");
  assert.deepEqual(result, { body: { doctype: "Group Exchange Rate", name: "GER-1", reason: "ok" } });
});

test("approveBody: button builds a body with no reason key", () => {
  const action = approveAction({ mode: "direct", message: null });
  const result = approveBody("Group Exchange Rate", "GER-1", action, null);
  assert.deepEqual(result, { body: { doctype: "Group Exchange Rate", name: "GER-1" } });
});

// -- unrequired rows ------------------------------------------------------------

test("a currency with no required pair: unrequired rows are passed through, separate from rows", () => {
  const view = gridView(payload({
    rows: [row({ from_currency: "JPY", to_currency: "USD" })],
    unrequired: [row({ from_currency: "GBP", to_currency: "USD", required: false })],
  }));
  assert.equal(view.rows.length, 1);
  assert.equal(view.unrequired.length, 1);
  assert.equal(view.unrequired[0].toCurrency, "USD");
  assert.equal(view.unrequired[0].fromCurrency, "GBP");
});

// -- pendingView (supplementary: not itemised in the row's test-first list,
// added so the exported function has at least one test per
// "tests that could not fail") --------------------------------------------------

test("pendingView: each item's approve mode is run through approveAction", () => {
  const view = pendingView({
    items: [
      { doctype: "Historical Equity Rate", name: "HER-1", title: "t", detail: "d",
        preparer: "analyst@example.com", edited_by: [], created: "2026-09-01T00:00:00",
        approve: { mode: "direct", message: null } },
    ],
    counts: { "Historical Equity Rate": 1, "Ownership Period": 0, hidden: 0 },
    self_approval: "Blocked",
    can_approve: true,
  });
  assert.equal(view.items.length, 1);
  assert.deepEqual(view.items[0].approve, { kind: "button", message: null });
  assert.equal(view.canApprove, true);
  assert.deepEqual(view.counts, { "Historical Equity Rate": 1, "Ownership Period": 0, hidden: 0 });
});

// -- ownershipView (supplementary, same reason as pendingView) ------------------

test("ownershipView: counts out-of-scope and surfaces the hidden count", () => {
  const view = ownershipView({
    period: { fiscal_year: 2026, fiscal_period: 8 },
    start_date: "2026-08-01",
    blocking: [{ entity: "ZZB", message: "m", desk: "/app/ownership-period/new?data_area_id=ZZB" }],
    out_of_scope: ["ZZC", "ZZD"],
    in_scope_count: 306,
    can_record: true,
    hidden: 3,
  });
  assert.deepEqual(view.blocking, [{ entity: "ZZB", message: "m", desk: "/app/ownership-period/new?data_area_id=ZZB" }]);
  assert.equal(view.outOfScopeCount, 2);
  assert.equal(view.inScopeCount, 306);
  assert.equal(view.canRecord, true);
  assert.equal(view.hiddenCount, 3);
});

// -- can_enter / can_approve -------------------------------------------------

test("gridView passes can_enter and can_approve through as booleans", () => {
  const view = gridView(payload({ can_enter: false, can_approve: true }));
  assert.equal(view.canEnter, false);
  assert.equal(view.canApprove, true);
});
