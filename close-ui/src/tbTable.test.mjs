// konsol#305 B12: tbTable.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { checkRows, entityRows, compareRows } from "./tbTable.js";

// Convention used throughout this file and tbTable.js: an amount is debit
// minus credit. A negative amount (a credit balance, or a check-row credit
// that exceeds its debit) renders in brackets, e.g. -1234.5 -> "(1,234.50)".
// A non-negative amount never carries a "+".

test("checkRows: problem rows come first, with the suggestion text kept", () => {
  const result = {
    ok: false,
    rows: [
      { line: 2, main_account: "1000", partner: "", debit: 100, credit: 0, problems: [] },
      {
        line: 3,
        main_account: "4001",
        partner: "",
        debit: 50,
        credit: 0,
        problems: [
          { code: "UNKNOWN_ACCOUNT", message: "Account 4001 is not in the group chart", suggestion: "Did you mean 4010?" },
        ],
      },
      { line: 4, main_account: "2000", partner: "", debit: 0, credit: 150, problems: [] },
    ],
    file_problems: [],
    totals: { debit: 150, credit: 150, difference: 0 },
  };
  const view = checkRows(result);
  assert.deepEqual(view.rows.map((r) => r.line), [3, 2, 4], "the problem row (line 3) moves to the front; clean rows keep their order");
  assert.equal(view.rows[0].problems[0].suggestion, "Did you mean 4010?");
  assert.equal(view.rows[0].hasProblems, true);
  assert.equal(view.rows[1].hasProblems, false);
});

test("checkRows: the totals line shows the difference", () => {
  const result = {
    ok: false,
    rows: [],
    file_problems: ["Debits (100.00) do not equal credits (150.00); difference -50.00 exceeds the 0.01 tolerance"],
    totals: { debit: 100, credit: 150, difference: -50 },
  };
  const view = checkRows(result);
  assert.equal(view.totals.debit, "100.00");
  assert.equal(view.totals.credit, "150.00");
  assert.equal(view.totals.difference, "(50.00)", "a negative difference (credit-heavy) renders in brackets, not with a minus sign");
});

test("checkRows: a balanced file's difference has no brackets", () => {
  const result = { ok: true, rows: [], file_problems: [], totals: { debit: 100, credit: 100, difference: 0 } };
  const view = checkRows(result);
  assert.equal(view.totals.difference, "0.00");
});

test("entityRows: the on-behalf label is kept verbatim", () => {
  const myTbs = {
    period_open: true,
    can_upload: true,
    entities: [
      {
        entity: "ZZE",
        name: "ZZ Entity",
        status: "Received",
        tb: { name: "TBSUB-0001", owner: "admin@example.com", on_behalf_label: "by admin@example.com for ZZE", creation: "2026-09-20T10:00:00Z" },
        exception: null,
      },
    ],
  };
  const view = entityRows(myTbs);
  assert.equal(view[0].tb.on_behalf_label, "by admin@example.com for ZZE");
});

test("entityRows: a TB-less entity keeps tb as null, never an empty object", () => {
  const myTbs = {
    period_open: true,
    can_upload: true,
    entities: [{ entity: "ZZM", name: "ZZ Missing", status: "Missing", tb: null, exception: null }],
  };
  const view = entityRows(myTbs);
  assert.equal(view[0].tb, null);
  assert.equal(view[0].status, "Missing");
});

test("entityRows: failure path — an unknown status throws, never renders blank", () => {
  const myTbs = {
    period_open: true,
    can_upload: true,
    entities: [{ entity: "ZZX", name: "ZZ X", status: "Somehow Pending", tb: null, exception: null }],
  };
  assert.throws(() => entityRows(myTbs), /unknown.*status/i);
});

test("compareRows: a change of None renders as —, and basis_note passes through", () => {
  const cmp = {
    rows: [
      { account: "4001", partner: "", is_ic: false, current: 100, previous: null, change: null },
    ],
    basis_note: "This period is Actual and P08 is Budget: the change is not comparable.",
    previous_note: null,
    previous_code: "P08",
  };
  const view = compareRows(cmp);
  assert.equal(view.rows[0].change, "—");
  assert.equal(view.rows[0].previous, "—", "unknown is never zero: previous is blank, not 0.00");
  assert.equal(view.rows[0].current, "100.00");
  assert.equal(view.basis_note, cmp.basis_note);
});

test("compareRows: no previous trial balance — previous_note passes through and every previous/change is the dash", () => {
  const cmp = {
    rows: [{ account: "1000", partner: "", is_ic: false, current: 200, previous: null, change: null }],
    basis_note: null,
    previous_note: "No trial balance for P08",
    previous_code: "P08",
  };
  const view = compareRows(cmp);
  assert.equal(view.previous_note, "No trial balance for P08");
  assert.equal(view.rows[0].previous, "—");
  assert.equal(view.rows[0].change, "—");
});

test("compareRows: a negative change (a swing toward credit) renders in brackets", () => {
  const cmp = {
    rows: [{ account: "4001", partner: "", is_ic: false, current: -50, previous: 100, change: -150 }],
    basis_note: null,
    previous_note: null,
    previous_code: "P08",
  };
  const view = compareRows(cmp);
  assert.equal(view.rows[0].current, "(50.00)");
  assert.equal(view.rows[0].change, "(150.00)");
});

test("compareRows: an intercompany row keeps its partner and is_ic flag", () => {
  const cmp = {
    rows: [{ account: "1500", partner: "ZZP", is_ic: true, current: 10, previous: 10, change: 0 }],
    basis_note: null,
    previous_note: null,
    previous_code: "P08",
  };
  const view = compareRows(cmp);
  assert.equal(view.rows[0].partner, "ZZP");
  assert.equal(view.rows[0].is_ic, true);
  assert.equal(view.rows[0].change, "0.00");
});
