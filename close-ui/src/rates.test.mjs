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
import fs from "node:fs";
import { parseZoned } from "./timefmt.js";
import {
  STATUS_LABELS,
  gridView,
  saveBody,
  approveAction,
  approveBody,
  pendingView,
  pendingCount,
  pendingEmptyMessage,
  pendingCreatedText,
  ownershipView,
  ownershipEmptyMessage,
  ownershipGapsCount,
  mergeDrafts,
  moveFlagView,
  ownershipChangeBody,
  ownershipEffectView,
} from "./rates.js";
import * as ratesModule from "./rates.js";

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
  assert.equal(view.rows[0].closing.previousValue, null);
  assert.equal(view.rows[0].closing.previousQuotedPer, null);
  assert.equal(view.rows[0].closing.deltaText, "—");
});

// -- previous value shown as a quote, not a bare true rate (R01k, SPA must-fix 2) --

test("previous value is shown in its own quoted unit, not the bare true rate (per 1)", () => {
  const view = gridView(payload({
    rows: [row({ closing: cell({
      status: "approved", name: "GER-2", quote: 0.6673, quoted_per: "100", rate: 0.006673,
      previous: {
        // the true rate a plain float division of 0.6607/100 gives, carrying
        // the float noise group_rates.py's own true_rate() comment warns
        // about; quoted_per is a Select field, so it arrives as a string.
        rate: 0.006606999999999999, quoted_per: "100",
        fiscal_year: 2026, fiscal_period: 7, name: "GER-1", label: "FY2026 P07 (GER-1)",
      },
      delta: 0.01,
    }) })],
  }));
  const closing = view.rows[0].closing;
  assert.equal(closing.previousValue, 0.6607);
  assert.equal(closing.previousQuotedPer, 100);
  // the delta is untouched: it is the server's own figure, never recomputed here.
  assert.equal(closing.deltaText, "+1.0%");
});

test("previous value uses its own quoted_per even when the current quote uses a different one", () => {
  const view = gridView(payload({
    rows: [row({ closing: cell({
      status: "approved", name: "GER-3", quote: 0.0066, quoted_per: "1", rate: 0.0066,
      previous: {
        rate: 0.0067, quoted_per: "100",
        fiscal_year: 2026, fiscal_period: 7, name: "GER-2", label: "FY2026 P07 (GER-2)",
      },
      delta: -0.015,
    }) })],
  }));
  const closing = view.rows[0].closing;
  assert.equal(closing.previousValue, 0.67);
  assert.equal(closing.previousQuotedPer, 100);
});

// -- cell carries what the approver needs (R01o, SPA should-fix 6) ------------

test("gridView carries change_reason, edited_by, extra drafts and source through to the cell view", () => {
  const view = gridView(payload({
    rows: [row({ closing: cell({
      status: "awaiting_approval", name: "GER-9", quote: 1.05, quoted_per: "1", rate: 1.05,
      change_reason: "Market move", edited_by: ["analyst2@example.com"], source: "ERP pre-fill",
      extra_drafts: ["GER-10"],
    }) })],
  }));
  const closing = view.rows[0].closing;
  assert.equal(closing.changeReason, "Market move");
  assert.deepEqual(closing.editedBy, ["analyst2@example.com"]);
  assert.equal(closing.source, "ERP pre-fill");
  assert.equal(closing.extraDraftsText, "and 1 more draft");
});

test("a cell with no extra drafts, no change reason and no source carries them as null", () => {
  const view = gridView(payload({
    rows: [row({ closing: cell({ status: "approved", name: "GER-9", quote: 1.05, quoted_per: "1", rate: 1.05 }) })],
  }));
  const closing = view.rows[0].closing;
  assert.equal(closing.changeReason, null);
  assert.equal(closing.editedBy, null);
  assert.equal(closing.source, null);
  assert.equal(closing.extraDraftsText, null);
});

test("extraDraftsText pluralises for more than one extra draft", () => {
  const view = gridView(payload({
    rows: [row({ closing: cell({
      status: "awaiting_approval", name: "GER-9", quote: 1.05, quoted_per: "1", rate: 1.05,
      extra_drafts: ["GER-10", "GER-11"],
    }) })],
  }));
  assert.equal(view.rows[0].closing.extraDraftsText, "and 2 more drafts");
});

// -- undeclared threshold -----------------------------------------------------

test("R01v goal 2: undeclared threshold — thresholdText is null (the gap already shows once, in the banner), never the gap message again", () => {
  const gap = { code: "rate_move_undeclared", message: "Declare the rate move threshold in Close Settings." };
  const view = gridView(payload({ threshold_pct: null, policy_gaps: [gap] }));
  assert.equal(view.thresholdText, null);
  // The gap still reaches the screen — once, in the banner.
  assert.deepEqual(view.banner, [gap.message]);
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

// -- R01v: moveFlagView — a move is judged only where a rate can still be entered ---
//
// group_rates.move_problem sets cell.flag to the SAME "declare the threshold"
// sentence on every cell with something to compare against whenever the
// threshold is undeclared, Approved cells included (facts: live, 28/28
// approved cells flagged). moveFlagView is the pure decision over where that
// shows: never on an Approved cell (goal 1), and only as the short
// "no threshold declared" cue — never the sentence again, never "flagged" —
// when the threshold itself is undeclared (goals 2, 3).

test("moveFlagView: an Approved cell never shows a flag, threshold declared", () => {
  const cell = { status: "approved", flag: "This rate moves +40% from the previous approved rate." };
  assert.deepEqual(moveFlagView(cell, true), { tag: null, message: null });
});

test("moveFlagView: an Approved cell never shows a flag, threshold undeclared", () => {
  const cell = { status: "approved", flag: "Declare the rate move threshold in Close Settings." };
  assert.deepEqual(moveFlagView(cell, false), { tag: null, message: null });
});

test("moveFlagView: an editable cell with no flag shows nothing", () => {
  assert.deepEqual(moveFlagView({ status: "missing", flag: null }, true), { tag: null, message: null });
  assert.deepEqual(moveFlagView({ status: "awaiting_approval", flag: null }, false), { tag: null, message: null });
});

test("moveFlagView: an editable cell with a real flag and a declared threshold shows 'flagged' and the sentence", () => {
  const cell = { status: "awaiting_approval", flag: "This rate moves +40% from the previous approved rate." };
  assert.deepEqual(moveFlagView(cell, true), { tag: "flagged", message: cell.flag });
});

test("moveFlagView: an editable cell with a flag and an undeclared threshold shows the short cue, not the sentence (goals 2, 3)", () => {
  const cell = { status: "missing", flag: "Declare the rate move threshold in Close Settings." };
  assert.deepEqual(moveFlagView(cell, false), { tag: "no threshold declared", message: null });
});

// -- R01v: gridView carries the decision through cellView (flagTag/flagMessage) ----

test("gridView goal 1: an Approved cell with a flag renders no flagTag and no flagMessage, threshold declared", () => {
  const view = gridView(payload({
    threshold_pct: 30,
    rows: [row({ closing: cell({
      status: "approved", name: "GER-1", quote: 1.4, quoted_per: "1", rate: 1.4,
      previous: { rate: 1, quoted_per: 1, fiscal_year: 2026, fiscal_period: 7, name: "GER-0", label: "FY2026 P07 (GER-0)" },
      delta: 0.4,
      flag: "This rate (1.4) moves +40% from the previous approved rate (1 USD per JPY). A move over 30% can be real, but say why (Reason for Change) before it is saved.",
    }) })],
  }));
  assert.equal(view.rows[0].closing.flagTag, null);
  assert.equal(view.rows[0].closing.flagMessage, null);
});

test("gridView goals 2/3: a draft cell flagged only because the threshold is undeclared shows 'no threshold declared', not the gap sentence again", () => {
  const gap = { code: "rate_move_undeclared", message: "Declare the rate move threshold in Close Settings." };
  const view = gridView(payload({
    threshold_pct: null,
    policy_gaps: [gap],
    rows: [row({ closing: cell({
      status: "awaiting_approval", name: "GER-2", quote: 1, quoted_per: "1", rate: 1,
      previous: { rate: 1, quoted_per: 1, fiscal_year: 2026, fiscal_period: 7, name: "GER-0", label: "FY2026 P07 (GER-0)" },
      delta: 0,
      flag: gap.message,
    }) })],
  }));
  assert.equal(view.rows[0].closing.deltaText, "+0.0%");
  assert.equal(view.rows[0].closing.flagTag, "no threshold declared");
  assert.equal(view.rows[0].closing.flagMessage, null);
  // The sentence itself still reaches the screen exactly once — the banner.
  assert.deepEqual(view.banner, [gap.message]);
});

test("gridView: a draft cell with a real flag and a declared threshold still shows the warning (unchanged path)", () => {
  const view = gridView(payload({
    threshold_pct: 30,
    rows: [row({ average: cell({
      status: "missing", flag: "This rate moves +40% from the previous approved rate.",
    }) })],
  }));
  assert.equal(view.rows[0].average.flagTag, "flagged");
  assert.equal(view.rows[0].average.flagMessage, "This rate moves +40% from the previous approved rate.");
});

// -- R01v goal 4 (folded in from R01q's gate): ownership tab counts hidden gaps too --

test("ownershipGapsCount: visible + hidden blocking gaps, so a scoped user never reads zero while gaps exist outside their scope", () => {
  assert.equal(ownershipGapsCount({ blocking: [{ entity: "E1" }, { entity: "E2" }], blockingHidden: 3 }), 5);
});

test("ownershipGapsCount: no hidden gaps is just the visible count", () => {
  assert.equal(ownershipGapsCount({ blocking: [{ entity: "E1" }], blockingHidden: 0 }), 1);
});

test("ownershipGapsCount: a view not yet loaded (null) is null, not zero", () => {
  assert.equal(ownershipGapsCount(null), null);
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
//
// saveBody takes a row as gridView hands it back out (camelCase
// fromCurrency/toCurrency, closing/average cell views), not the raw
// snake_case payload row() builds above.

function period() {
  return { fiscal_year: 2026, fiscal_period: 8, period_code: "FY2026-P08", status: "Open" };
}

function viewRow(overrides = {}) {
  return {
    fromCurrency: "JPY",
    toCurrency: "USD",
    required: true,
    closing: { name: null },
    average: { name: null },
    ...overrides,
  };
}

test("saveBody: a new cell carries exactly the 8 keys, no name", () => {
  const r = viewRow({ closing: { name: null } });
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
  const r = viewRow({ average: { name: "GER-77" } });
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
  const r = viewRow();
  const result = saveBody(period(), r, "Closing", { quote: "", quotedPer: 1, changeReason: null });
  assert.deepEqual(result, { error: "Enter a quote." });
});

test("saveBody: a non-numeric quote gives an error", () => {
  const r = viewRow();
  const result = saveBody(period(), r, "Closing", { quote: "abc", quotedPer: 1, changeReason: null });
  assert.deepEqual(result, { error: "Enter a quote." });
});

test("saveBody: a blank Quoted Per gives an error", () => {
  const r = viewRow();
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

// -- pendingCount / pendingEmptyMessage (#305-R01p: SPA should-fix 7, 8) --------

test("pendingCount: HER and OP together, the tab label counts every pending item", () => {
  assert.equal(pendingCount({ "Historical Equity Rate": 2, "Ownership Period": 1, hidden: 0 }), 3);
});

test("pendingCount: zero when both doctypes are empty", () => {
  assert.equal(pendingCount({ "Historical Equity Rate": 0, "Ownership Period": 0, hidden: 5 }), 0);
});

test("pendingEmptyMessage: a scoped user with hidden drafts sees the outside-scope note, not 'none awaiting'", () => {
  const view = pendingView({
    items: [],
    counts: { "Historical Equity Rate": 0, "Ownership Period": 0, hidden: 2 },
    self_approval: "Blocked",
    can_approve: false,
  });
  assert.equal(pendingEmptyMessage(view), "2 awaiting outside your scope");
});

test("pendingEmptyMessage: no items and nothing hidden says none awaiting", () => {
  const view = pendingView({
    items: [],
    counts: { "Historical Equity Rate": 0, "Ownership Period": 0, hidden: 0 },
    self_approval: "Blocked",
    can_approve: false,
  });
  assert.equal(
    pendingEmptyMessage(view),
    "No historical equity rates or ownership periods are awaiting approval.",
  );
});

test("pendingEmptyMessage: null when there are visible items, regardless of hidden", () => {
  const view = pendingView({
    items: [
      { doctype: "Historical Equity Rate", name: "HER-1", title: "t", detail: "d",
        preparer: "analyst@example.com", edited_by: [], created: "2026-09-01T00:00:00",
        approve: { mode: "direct", message: null } },
    ],
    counts: { "Historical Equity Rate": 1, "Ownership Period": 0, hidden: 2 },
    self_approval: "Blocked",
    can_approve: true,
  });
  assert.equal(pendingEmptyMessage(view), null);
});

// -- pendingCreatedText (L01f: rates_api.get_pending's `created` now
// arrives zoned, L01e. L01d's naive-time fallback -- the private
// `naiveInZone` re-zoner and this function's `systemZone` parameter -- is
// deleted along with it: a `created` with no time zone can now only mean a
// server regression, and must show up as the exact error `parseZoned`
// gives the TB screen for the same defect (tbTable.js's `timestampText`),
// never silently re-zoned and never a message invented here) ------------

const NOW = new Date("2026-09-25T12:00:00Z");

test("pendingCreatedText: today shows HH:MM, in the viewer's own zone", () => {
  assert.equal(pendingCreatedText("2026-09-25T09:30:00+00:00", NOW, "Europe/London"), "10:30");
});

test("pendingCreatedText: an earlier day renders as 'Sep 20, 10:42', the same text as the TB list", () => {
  assert.equal(pendingCreatedText("2026-09-20T09:42:00+00:00", NOW, "Europe/London"), "Sep 20, 10:42");
});

test("pendingCreatedText: null/undefined `created` reads 'not recorded', never a guessed time", () => {
  assert.equal(pendingCreatedText(null, NOW, "Europe/London"), "not recorded");
  assert.equal(pendingCreatedText(undefined, NOW, "Europe/London"), "not recorded");
});

test("pendingCreatedText: failure path — no viewer zone is refused, never defaulted", () => {
  assert.throws(() => pendingCreatedText("2026-09-25T09:30:00+00:00", NOW, ""), /time zone/i);
  assert.throws(() => pendingCreatedText("2026-09-25T09:30:00+00:00", NOW, null), /time zone/i);
});

test("pendingCreatedText: failure path — no valid `now` is refused, never defaulted", () => {
  assert.throws(() => pendingCreatedText("2026-09-25T09:30:00+00:00", undefined, "Europe/London"), /now/i);
  assert.throws(() => pendingCreatedText("2026-09-25T09:30:00+00:00", new Date("x"), "Europe/London"), /now/i);
});

test("pendingCreatedText: failure path — a naive `created` (a server regression, the live defect's own raw value, now that L01e sends it zoned) throws exactly parseZoned's own error for the same string, not a message invented here", () => {
  const naive = "2026-09-16T18:13:05.213749";
  let fromParseZoned;
  try {
    parseZoned(naive);
    assert.fail("parseZoned was expected to throw on a naive string");
  } catch (e) {
    fromParseZoned = e.message;
  }
  assert.throws(
    () => pendingCreatedText(naive, NOW, "Europe/London"),
    (e) => e.message === fromParseZoned,
  );
});

test("pendingCreatedText: failure path — garbage `created` throws the same way as the naive case, consistent with the zoned case parseZoned already refuses", () => {
  assert.throws(() => pendingCreatedText("not-a-timestamp", NOW, "Europe/London"), /time zone/i);
});

test("rates.js: no naiveInZone -- the naive-time fallback is gone now that rates_api sends zoned times (L01e), and so is its systemZone parameter", () => {
  const source = fs.readFileSync(new URL("./rates.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /naiveInZone/);
  assert.doesNotMatch(source, /systemZone/);
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

test("ownershipView: blocking_hidden is surfaced separately from the combined hidden count", () => {
  const view = ownershipView({
    blocking: [],
    out_of_scope: ["ZZC"],
    in_scope_count: 10,
    can_record: true,
    hidden: 3,
    blocking_hidden: 2,
  });
  assert.equal(view.hiddenCount, 3);
  assert.equal(view.blockingHidden, 2);
});

test("ownershipView: blocking_hidden defaults to 0 when the payload omits it", () => {
  const view = ownershipView({
    blocking: [],
    out_of_scope: [],
    in_scope_count: 1,
    can_record: true,
    hidden: 0,
  });
  assert.equal(view.blockingHidden, 0);
});

// -- ownershipEmptyMessage (#305-R01q: SPA should-fix 9) ---------------------

test("ownershipEmptyMessage: a hidden blocking gap is never read as 'no gaps' -- it names the hidden count instead", () => {
  const view = ownershipView({
    blocking: [],
    out_of_scope: [],
    in_scope_count: 10,
    can_record: true,
    hidden: 2,
    blocking_hidden: 2,
  });
  assert.equal(ownershipEmptyMessage(view), "2 gaps outside your scope");
});

test("ownershipEmptyMessage: singular 'gap' for exactly one hidden blocking entity", () => {
  const view = ownershipView({
    blocking: [],
    out_of_scope: [],
    in_scope_count: 5,
    can_record: true,
    hidden: 1,
    blocking_hidden: 1,
  });
  assert.equal(ownershipEmptyMessage(view), "1 gap outside your scope");
});

test("ownershipEmptyMessage: no blocking and nothing hidden says there are no gaps", () => {
  const view = ownershipView({
    blocking: [],
    out_of_scope: [],
    in_scope_count: 10,
    can_record: true,
    hidden: 0,
    blocking_hidden: 0,
  });
  assert.equal(ownershipEmptyMessage(view), "No ownership gaps for this period.");
});

test("ownershipEmptyMessage: null when there are visible blocking entries, regardless of blocking_hidden", () => {
  const view = ownershipView({
    blocking: [{ entity: "ZZB", message: "m", desk: "/app/ownership-period/new?data_area_id=ZZB" }],
    out_of_scope: [],
    in_scope_count: 1,
    can_record: true,
    hidden: 2,
    blocking_hidden: 2,
  });
  assert.equal(ownershipEmptyMessage(view), null);
});

// -- can_enter / can_approve -------------------------------------------------

test("gridView passes can_enter and can_approve through as booleans", () => {
  const view = gridView(payload({ can_enter: false, can_approve: true }));
  assert.equal(view.canEnter, false);
  assert.equal(view.canApprove, true);
});

// -- quoted_per_options (E409c): the server's own Select options, not a hand-copied list ----

test("gridView passes quoted_per_options through as quotedPerOptions", () => {
  const view = gridView(payload({ quoted_per_options: ["1", "10", "100", "1000", "10000"] }));
  assert.deepEqual(view.quotedPerOptions, ["1", "10", "100", "1000", "10000"]);
});

test("gridView reflects a changed quoted_per_options payload: no constant array of its own", () => {
  const view = gridView(payload({ quoted_per_options: ["5", "50"] }));
  assert.deepEqual(view.quotedPerOptions, ["5", "50"]);
});

test("gridView defaults quotedPerOptions to an empty list, never a guessed one", () => {
  const view = gridView(payload());
  assert.deepEqual(view.quotedPerOptions, []);
});

// -- mergeDrafts (R01n): keeps a dirty or refused edit across a reload ------

function viewCell(value, quotedPer) {
  return { value, quotedPer };
}

test("mergeDrafts with no existing draft builds a clean baseline from the fresh cell", () => {
  const merged = mergeDrafts(undefined, viewCell(1.25, "100"), false);
  assert.deepEqual(merged, { quote: "1.25", quotedPer: "100", orig: { quote: "1.25", quotedPer: "100" } });
});

test("mergeDrafts with no existing draft and a null fresh value/quotedPer baselines to empty strings", () => {
  const merged = mergeDrafts(undefined, viewCell(null, null), false);
  assert.deepEqual(merged, { quote: "", quotedPer: "", orig: { quote: "", quotedPer: "" } });
});

test("mergeDrafts replaces a clean, un-refused draft with the fresh cell's current value", () => {
  // The old draft equals its own orig (never edited) but the server's value
  // has since moved (e.g. this cell was approved elsewhere and reloaded).
  const old = { quote: "1.25", quotedPer: "100", orig: { quote: "1.25", quotedPer: "100" } };
  const merged = mergeDrafts(old, viewCell(1.3, "100"), false);
  assert.deepEqual(merged, { quote: "1.3", quotedPer: "100", orig: { quote: "1.3", quotedPer: "100" } });
});

test("mergeDrafts keeps a dirty draft across the reload (R01n: approve must not wipe other unsaved edits)", () => {
  const old = { quote: "1.99", quotedPer: "100", orig: { quote: "1.25", quotedPer: "100" } };
  const merged = mergeDrafts(old, viewCell(1.25, "100"), false);
  assert.equal(merged, old, "the exact same draft object survives, untouched");
});

test("mergeDrafts keeps a dirty draft even when the fresh cell's value has also changed", () => {
  const old = { quote: "1.99", quotedPer: "100", orig: { quote: "1.25", quotedPer: "100" } };
  const merged = mergeDrafts(old, viewCell(1.4, "100"), false);
  assert.equal(merged, old);
});

test("mergeDrafts keeps a refused draft even though it is clean (equals its own orig)", () => {
  const old = { quote: "1.25", quotedPer: "100", orig: { quote: "1.25", quotedPer: "100" } };
  const merged = mergeDrafts(old, viewCell(1.25, "100"), true);
  assert.equal(merged, old, "hasError alone keeps the draft");
});

test("mergeDrafts: a quotedPer-only edit (quote unchanged) still counts as dirty", () => {
  const old = { quote: "1.25", quotedPer: "1", orig: { quote: "1.25", quotedPer: "100" } };
  const merged = mergeDrafts(old, viewCell(1.25, "100"), false);
  assert.equal(merged, old);
});

test("mergeDrafts: a clean draft with no error is rebuilt as a new object (not the same reference)", () => {
  const old = { quote: "1.25", quotedPer: "100", orig: { quote: "1.25", quotedPer: "100" } };
  const merged = mergeDrafts(old, viewCell(1.25, "100"), false);
  assert.notEqual(merged, old, "a clean, un-refused draft is replaced, not mutated in place");
  assert.deepEqual(merged, old);
});

// ---------------------------------------------------------------------------
// konsol#305 O59 (story 4.2; wireframe-4.2.md, confirmed as drawn by Deepak
// Pai 7 Oct): the ownership change body and the effect panel's view. Fed the
// REAL producers' output: O55's preview golden files and O57's pending golden
// file, each asserted equal to the stub-site call by its own host test.

function fixture(name) {
  return JSON.parse(
    fs.readFileSync(new URL(`../../konsol/tests/fixtures/${name}`, import.meta.url), "utf8"),
  );
}

const PREVIEW = fixture("close_ownership_preview_payload.json");
const REFUSED = fixture("close_ownership_preview_refused.json");
const PENDING = fixture("close_rates_pending_payload.json");

// The O56 signature, read from the endpoint itself, so the body cannot drift
// from what save_ownership_change accepts.
function saveOwnershipChangeParams() {
  const src = fs.readFileSync(new URL("../../konsol/close/rates_api.py", import.meta.url), "utf8");
  const m = /def save_ownership_change\(([^)]*)\)/.exec(src);
  assert.ok(m, "save_ownership_change is not defined in rates_api.py");
  return m[1].split(",").map((p) => p.trim().split("=")[0].trim()).filter(Boolean);
}

const PERIOD = { fiscal_year: 2025, fiscal_period: 10 };
// Dates are written as literal strings in dueDate.js's pinned wording
// (R52l): an expectation never comes from the function under test.
const FORM = {
  consolidationGroup: "ECL_GROUP",
  entity: "ZZ5B1",
  ownershipPct: "80",
  consolidationMethod: "full",
};

test("O59 ownershipChangeBody: exactly the save_ownership_change keys, pct as a number", () => {
  const { body, error } = ownershipChangeBody(PERIOD, FORM);
  assert.equal(error, undefined);
  assert.deepEqual(body, {
    fiscal_year: 2025,
    fiscal_period: 10,
    consolidation_group: "ECL_GROUP",
    entity: "ZZ5B1",
    ownership_pct: 80,
    consolidation_method: "full",
  });
  const params = saveOwnershipChangeParams();
  assert.deepEqual(Object.keys(body).sort(), params.filter((p) => p !== "name").sort());
});

test("O59 ownershipChangeBody: editing a draft carries its name, and only then", () => {
  const { body } = ownershipChangeBody(PERIOD, { ...FORM, name: "OP-ZZ5B1-2025-10-01" });
  assert.equal(body.name, "OP-ZZ5B1-2025-10-01");
  assert.deepEqual(Object.keys(body).sort(), saveOwnershipChangeParams().sort());
  assert.equal("name" in ownershipChangeBody(PERIOD, { ...FORM, name: "" }).body, false);
});

test("O59 ownershipChangeBody: forged keys never reach the body", () => {
  const { body } = ownershipChangeBody(PERIOD, {
    ...FORM,
    docstatus: 1,
    supersedes: "OP-X",
    end_date: "2025-12-31",
    superseded_end_date: "2025-12-31",
    acquisition_date: "2025-10-01",
  });
  for (const key of ["docstatus", "supersedes", "end_date", "superseded_end_date", "acquisition_date"]) {
    assert.equal(key in body, false, key);
  }
});

test("O59 ownershipChangeBody: a blank entity is refused, no body", () => {
  for (const entity of ["", null, undefined, "   "]) {
    const out = ownershipChangeBody(PERIOD, { ...FORM, entity });
    assert.equal(out.body, undefined, String(entity));
    assert.equal(out.error, "Choose an entity.");
  }
});

test("O59 ownershipChangeBody: a bad pct is refused with the server's own sentence", () => {
  // The server's sentence, as the REAL preview returns it (O55 refused golden).
  const serverSentence = REFUSED.problems.find((p) => p.startsWith("Ownership %"));
  assert.ok(serverSentence);
  for (const pct of ["", null, undefined, "abc", "80abc", "NaN", "Infinity", "-1", "100.01", -0.5, 101, true]) {
    const out = ownershipChangeBody(PERIOD, { ...FORM, ownershipPct: pct });
    assert.equal(out.body, undefined, String(pct));
    assert.equal(out.error, serverSentence, String(pct));
  }
  for (const [pct, n] of [["0", 0], ["100", 100], [" 80.5 ", 80.5], [60, 60]]) {
    assert.equal(ownershipChangeBody(PERIOD, { ...FORM, ownershipPct: pct }).body.ownership_pct, n);
  }
});

test("O59 ownershipEffectView: the wireframe panel from the REAL preview", () => {
  const view = ownershipEffectView(PREVIEW.effect);
  assert.deepEqual(view.rows, [
    { label: "Ownership", before: "100 %", after: "80 %", unchanged: false },
    { label: "Method", before: "full", after: "full", unchanged: true },
    {
      label: "Covers",
      before: `Wed 1 Jan 2025 → Tue 30 Sep 2025`,
      after: `Wed 1 Oct 2025 → open-ended`,
      unchanged: false,
      note: "(now open-ended; ends on approval)",
    },
  ]);
  assert.equal(view.currentEnds, "Tue 30 Sep 2025");
  // wireframe-4.2.md §3: "Ends  <current_name> on <date>" (O65).
  assert.equal(view.endsLine, `OP-ZZ5B1-1 on Tue 30 Sep 2025`);
  assert.equal(view.firstPeriod, "FY2025 P10");
  assert.equal(view.periods, "FY2025 P10 onward (open-ended)");
  // wireframe-4.2.md §1: "FY2025 P11 (signed <date> by <name>)" (O65).
  // R52f: exactly one entry; the signed Closing P13 is never marked by the
  // approval, so the REAL preview no longer lists it.
  assert.deepEqual(view.resign, [
    `FY2025 P11 (signed Sat 4 Oct 2025 by Zz Lead)`,
  ]);
  assert.equal(view.resignNone, null);
  assert.equal(view.notShown, "Goodwill, NCI and results are not previewed; they change at the next build.");
});

test("O59 ownershipEffectView: the pending item's effect (O57 golden) gives the same panel", () => {
  const op = PENDING.items.find((i) => i.doctype === "Ownership Period" && i.effect);
  assert.deepEqual(ownershipEffectView(op.effect), ownershipEffectView(PREVIEW.effect));
});

test("O59 ownershipEffectView: no signed period reads the wireframe sentence", () => {
  const view = ownershipEffectView({ ...PREVIEW.effect, resign: [], resign_detail: [] });
  assert.deepEqual(view.resign, []);
  assert.equal(view.resignNone, "No signed period is affected.");
});

test("O59 ownershipEffectView: a current period with an end date keeps it in the note and after", () => {
  const effect = {
    ...PREVIEW.effect,
    before: { ...PREVIEW.effect.before, to: "2026-03-31" },
    after: { ...PREVIEW.effect.after, to: "2026-03-31" },
  };
  const covers = ownershipEffectView(effect).rows[2];
  assert.equal(covers.before, `Wed 1 Jan 2025 → Tue 30 Sep 2025`);
  assert.equal(covers.after, `Wed 1 Oct 2025 → Tue 31 Mar 2026`);
  assert.equal(covers.note, `(now to Tue 31 Mar 2026; ends on approval)`);
});

test("O59 ownershipEffectView: failure path — an effect without resign throws", () => {
  const { resign, ...rest } = PREVIEW.effect;
  assert.throws(() => ownershipEffectView(rest), /resign/);
});

test("O65 ownershipEffectView: the approvals golden names its own predecessor and signer", () => {
  const ops = fixture("close_approvals_op_queue_payload.json").items.filter((i) => i.effect);
  assert.ok(ops.length, "the approvals golden carries an effect");
  const view = ownershipEffectView(ops[0].effect);
  assert.equal(view.endsLine, `OP-ZZ58-1 on Tue 30 Jun 2026`);
  assert.deepEqual(view.resign, [`FY2026 P07 (signed Tue 4 Aug 2026 by Zz Lead)`]);
});

test("O65 ownershipEffectView: failure path — a missing current_name or resign_detail throws", () => {
  for (const key of ["current_name", "resign_detail"]) {
    const broken = { ...PREVIEW.effect };
    delete broken[key];
    assert.throws(() => ownershipEffectView(broken), new RegExp(key), key);
  }
  for (const bad of [null, "", "  "]) {
    assert.throws(() => ownershipEffectView({ ...PREVIEW.effect, current_name: bad }), /current_name/, String(bad));
  }
  assert.throws(() => ownershipEffectView({ ...PREVIEW.effect, resign_detail: null }), /resign_detail/);
});

test("O65 ownershipEffectView: failure path — a re-sign entry without its date or signer throws, never a blank", () => {
  const detail = PREVIEW.effect.resign_detail;
  for (const key of ["period", "signed_on", "signed_by_name"]) {
    const entry = { ...detail[0] };
    delete entry[key];
    const broken = { ...PREVIEW.effect, resign_detail: [entry] };
    assert.throws(() => ownershipEffectView(broken), new RegExp(`resign_detail.${key}`), key);
  }
  for (const name of [null, "", "  "]) {
    const broken = { ...PREVIEW.effect, resign_detail: [{ ...detail[0], signed_by_name: name }] };
    assert.throws(() => ownershipEffectView(broken), /signed_by_name/, String(name));
  }
  const badDate = { ...PREVIEW.effect, resign_detail: [{ ...detail[0], signed_on: "04/10/2025" }] };
  assert.throws(() => ownershipEffectView(badDate), /ISO/);
});

test("O65 ownershipEffectView: failure path — resign_detail out of step with resign throws", () => {
  const detail = PREVIEW.effect.resign_detail;
  // R52f: the REAL preview lists exactly one re-sign period (P11).
  assert.deepEqual(PREVIEW.effect.resign, ["FY2025 P11"]);
  assert.equal(detail.length, 1);
  // Fewer entries than resign, more entries than resign, and an entry naming
  // another period all throw.
  assert.throws(() => ownershipEffectView({ ...PREVIEW.effect, resign_detail: [] }), /resign_detail/);
  assert.throws(
    () => ownershipEffectView({ ...PREVIEW.effect, resign_detail: [detail[0], detail[0]] }),
    /resign_detail/,
  );
  assert.throws(
    () => ownershipEffectView({ ...PREVIEW.effect, resign_detail: [{ ...detail[0], period: "FY2025 P13" }] }),
    /resign_detail FY2025 P13 does not match resign FY2025 P11/,
  );
});

test("O59 ownershipEffectView: every other missing key throws, never a guessed panel", () => {
  for (const key of Object.keys(PREVIEW.effect)) {
    const broken = { ...PREVIEW.effect };
    delete broken[key];
    assert.throws(() => ownershipEffectView(broken), new RegExp(key), key);
  }
  for (const side of ["before", "after"]) {
    for (const key of ["pct", "method", "from", "to"]) {
      const broken = { ...PREVIEW.effect, [side]: { ...PREVIEW.effect[side] } };
      delete broken[side][key];
      assert.throws(() => ownershipEffectView(broken), new RegExp(`${side}.${key}`), `${side}.${key}`);
    }
  }
  assert.throws(() => ownershipEffectView(null), /effect/);
  assert.throws(() => ownershipEffectView({ ...PREVIEW.effect, current_ends: "30/09/2025" }), /ISO/);
});

// ---------------------------------------------------------------------------
// konsol#305 O67 (wireframe-4.2.md section 1, "The Analyst can edit it until
// it is approved", confirmed by Deepak Pai 7 Oct): get_pending's Ownership
// Period items carry the server-decided `edit` (O69). pendingView keeps it,
// checked, and ownershipDraftEdits maps each editable draft's name to it.
// Fed the REAL O69 golden (close_rates_pending_payload.json).
// ---------------------------------------------------------------------------

const EDIT_KEYS = ["consolidation_group", "entity", "fiscal_year", "fiscal_period", "ownership_pct", "consolidation_method"];

test("O67 pendingView keeps each OP item's server `edit` as sent; an HER item gets no edit", () => {
  const view = pendingView(PENDING);
  const byName = Object.fromEntries(view.items.map((i) => [i.name, i]));
  assert.deepEqual(byName["OP-ZZ5B1-2025-10-01"].edit, PENDING.items[1].edit);
  assert.deepEqual(Object.keys(byName["OP-ZZ5B1-2025-10-01"].edit).sort(), [...EDIT_KEYS].sort());
  assert.equal(byName["OP-ZZ5B2-2025-10-01"].edit, null, "the Desk draft is not editable here");
  assert.equal("edit" in byName["HER-ZZ5B1-1"], false);
});

test("O67 failure path: an OP item without `edit`, or an edit without one of its keys, throws naming it", () => {
  const op = PENDING.items[1];
  const { edit, ...noEdit } = op;
  assert.throws(() => pendingView({ ...PENDING, items: [noEdit] }), /OP-ZZ5B1-2025-10-01.*edit/);
  for (const key of EDIT_KEYS) {
    const partial = { ...op.edit };
    delete partial[key];
    assert.throws(() => pendingView({ ...PENDING, items: [{ ...op, edit: partial }] }), new RegExp(key), key);
  }
});

test("O67 ownershipDraftEdits: only the drafts the server made editable, keyed by name", () => {
  assert.equal(typeof ratesModule.ownershipDraftEdits, "function", "rates.js exports ownershipDraftEdits");
  const edits = ratesModule.ownershipDraftEdits(pendingView(PENDING).items);
  assert.deepEqual(edits, { "OP-ZZ5B1-2025-10-01": PENDING.items[1].edit });
  assert.deepEqual(ratesModule.ownershipDraftEdits([]), {});
});

// ---------------------------------------------------------------------------
// konsol#305 R52o (review U6 part 1, the S2 consumer): one ownership-effect
// view, `opEffectView(item)`, shared by the pending list and (R52p) the
// Approvals detail. Fed the REAL goldens (close_rates_pending_payload.json,
// close_approvals_op_queue_payload.json; both carry `effect_error` from
// R52i). Expected strings are literals.
// ---------------------------------------------------------------------------

const OP_QUEUE = fixture("close_approvals_op_queue_payload.json");

// The real server sentence: rates_api.get_pending on test_close_rates_api.py's
// R52i site (a draft whose `supersedes` names a cancelled period).
const R52O_SERVER_ERROR =
  "The pending ownership change OP-ZZ5B3-2025-10-01 cannot be shown: OP-ZZ5B3-2025-10-01 supersedes " +
  "OP-ZZ5B3-1, which is not an approved Ownership Period. Correct or delete the draft in Desk.";

function r52oItem(payload, name) {
  const item = payload.items.find((i) => i.name === name);
  assert.ok(item, `the golden has ${name}`);
  return item;
}

test("R52o opEffectView: the golden change draft gives the pending panel (Ownership, Method, Ends; no Covers row)", () => {
  assert.equal(typeof ratesModule.opEffectView, "function", "rates.js exports opEffectView");
  const item = r52oItem(pendingView(PENDING), "OP-ZZ5B1-2025-10-01");
  assert.equal(item.effect_error, null, "R52i: the golden carries effect_error null");
  assert.deepEqual(ratesModule.opEffectView(item), {
    view: {
      rows: [
        { label: "Ownership", before: "100 %", after: "80 %", unchanged: false },
        { label: "Method", before: "full", after: "full", unchanged: true },
      ],
      currentEnds: "Tue 30 Sep 2025",
      endsLine: "OP-ZZ5B1-1 on Tue 30 Sep 2025",
      firstPeriod: "FY2025 P10",
      periods: "FY2025 P10 onward (open-ended)",
      resign: ["FY2025 P11 (signed Sat 4 Oct 2025 by Zz Lead)"],
      resignNone: null,
      notShown: "Goodwill, NCI and results are not previewed; they change at the next build.",
    },
  });
});

test("R52o opEffectView: the golden Desk draft (effect null, effect_error null) gives the Desk sentence", () => {
  assert.equal(ratesModule.DESK_DRAFT, "Drafted in Desk: effect not previewed.");
  const item = r52oItem(pendingView(PENDING), "OP-ZZ5B2-2025-10-01");
  assert.deepEqual(ratesModule.opEffectView(item), { desk: "Drafted in Desk: effect not previewed." });
});

test("R52o opEffectView failure path: every golden OP item with the server's effect_error gives that sentence, never the Desk line", () => {
  const items = [
    ...pendingView(PENDING).items,
    ...OP_QUEUE.items,
  ].filter((i) => i.doctype === "Ownership Period");
  assert.equal(items.length, 4, "two OP items in each golden");
  for (const item of items) {
    const broken = { ...item, effect: null, effect_error: R52O_SERVER_ERROR };
    assert.deepEqual(ratesModule.opEffectView(broken), { error: R52O_SERVER_ERROR }, item.name);
  }
});

test("R52o opEffectView failure path: a missing effect or effect_error key throws", () => {
  const item = r52oItem(pendingView(PENDING), "OP-ZZ5B1-2025-10-01");
  const noEffect = { ...item };
  delete noEffect.effect;
  assert.throws(() => ratesModule.opEffectView(noEffect), /OP-ZZ5B1-2025-10-01 has no effect/);
  const noError = { ...item };
  delete noError.effect_error;
  assert.throws(() => ratesModule.opEffectView(noError), /OP-ZZ5B1-2025-10-01 has no effect_error/);
  const { resign, ...noResign } = item.effect;
  assert.throws(() => ratesModule.opEffectView({ ...item, effect: noResign }), /resign/);
});

// ---------------------------------------------------------------------------
// konsol#305 R52q (review S18, the O62 watch): an Ownership Period item's
// effect arrives as `ownership_effect` / `ownership_effect_error`, never the
// journal's `effect`. Fed the REAL regenerated goldens; expected strings are
// literals.
// ---------------------------------------------------------------------------

test("R52q: both goldens' OP items carry ownership_effect and ownership_effect_error, never effect", () => {
  const items = [...PENDING.items, ...OP_QUEUE.items].filter((i) => i.doctype === "Ownership Period");
  assert.equal(items.length, 4, "two OP items in each golden");
  for (const item of items) {
    assert.ok("ownership_effect" in item, `${item.name} carries ownership_effect`);
    assert.equal(item.ownership_effect_error, null, item.name);
    assert.equal("effect" in item, false, `${item.name} carries no effect`);
    assert.equal("effect_error" in item, false, `${item.name} carries no effect_error`);
  }
});

test("R52q: the approvals golden's change draft gives the pending panel through opEffectView", () => {
  const item = r52oItem(OP_QUEUE, "OP-ZZ58-2026-07-01");
  assert.deepEqual(ratesModule.opEffectView(item).view.rows, [
    { label: "Ownership", before: "100 %", after: "80 %", unchanged: false },
    { label: "Method", before: "full", after: "full", unchanged: true },
  ]);
  assert.deepEqual(ratesModule.opEffectView(r52oItem(OP_QUEUE, "OP-ZZ58B-2026-07-01")), {
    desk: "Drafted in Desk: effect not previewed.",
  });
});

test("R52q failure path: an item carrying the old effect key but no ownership_effect throws", () => {
  const item = r52oItem(pendingView(PENDING), "OP-ZZ5B1-2025-10-01");
  const old = { ...item, effect: item.ownership_effect, effect_error: null };
  delete old.ownership_effect;
  delete old.ownership_effect_error;
  assert.throws(() => ratesModule.opEffectView(old), /OP-ZZ5B1-2025-10-01 has no ownership_effect/);
});
