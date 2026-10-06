// konsol#305 A13: adjustments.test.mjs
//
// Exercises adjustments.js against journal_api.get_journals's real payload
// shape (A05: konsol/close/journal_api.py, konsol/close/journal_model.py).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import {
	journalsView,
	durationOptions,
	durationIndex,
	NO_REVERSAL_NOTE,
	draftTotals,
	saveJournalBody,
	effectView,
	editable,
	snapshotDraft,
	snapshotLines,
	draftDirty,
	canOpenNew,
	canSaveDraft,
	canSendDraft,
	formatAmount,
	dimKeysOf,
	dimValueText,
} from "./adjustments.js";

const NOW = new Date("2026-10-03T12:00:00Z");
const TZ = "UTC";

function payload(overrides = {}) {
	return {
		period: { fiscal_year: 2026, fiscal_period: 9, code: "FY26 P09", status: "Open", period_type: "Regular" },
		journals: [],
		groups: [{ consolidation_group: "Demo Group", reporting_currency: "USD", entities: ["ZZ-A", "ZZ-B"] }],
		accounts: {},
		dimensions: [],
		reversing_in: [],
		reversal_choices: [],
		workflow_installed: true,
		first_state: "Draft",
		can_draft: true,
		can_send: true,
		can_edit_period: true,
		...overrides,
	};
}

function journal(overrides = {}) {
	return {
		name: "CJ-00001",
		title: "Intercompany reclass",
		description: "Intercompany reclass",
		adjustment_type: "topside",
		status: "Draft",
		docstatus: 0,
		consolidation_group: "Demo Group",
		currency: "USD",
		total_debit: 185.0,
		total_credit: 185.0,
		duration: "Reverses in FY26 P10",
		reverse: { fiscal_year: 2026, fiscal_period: 10 },
		preparer: "analyst@example.com",
		created: "2026-10-01T09:00:00+00:00",
		modified: "2026-10-01T09:00:00+00:00",
		approved_by: null,
		approved_at: null,
		lines: [],
		effect: { headings: [], sections: [], no_heading: 0 },
		last_rejection: null,
		...overrides,
	};
}

// --- journalsView -----------------------------------------------------

test("journalsView passes groups, accounts and flags through", () => {
	const view = journalsView(payload({ can_send: false }), NOW, TZ);
	assert.deepEqual(view.groups, [{ consolidation_group: "Demo Group", reporting_currency: "USD", entities: ["ZZ-A", "ZZ-B"] }]);
	assert.equal(view.firstState, "Draft");
	assert.equal(view.canDraft, true);
	assert.equal(view.canSend, false);
	assert.equal(view.canEditPeriod, true);
});

test("journalsView carries reversal_choices through as reversalChoices (U1)", () => {
	const choices = [{ fiscal_year: 2026, fiscal_period: 10, code: "FY26 P10" }];
	const view = journalsView(payload({ reversal_choices: choices }), NOW, TZ);
	assert.deepEqual(view.reversalChoices, choices);
});

test("journalsView keeps journal order and formats last_rejection's time", () => {
	const j1 = journal({ name: "CJ-00001" });
	const j2 = journal({
		name: "CJ-00002",
		status: "Draft",
		last_rejection: { reason: "Wrong account", actor: "lead@example.com", at: "2026-10-01T10:30:00+00:00" },
	});
	const view = journalsView(payload({ journals: [j1, j2] }), NOW, TZ);
	assert.deepEqual(view.journals.map((j) => j.name), ["CJ-00001", "CJ-00002"]);
	assert.equal(view.journals[0].lastRejection, null);
	assert.equal(view.journals[1].lastRejection.reason, "Wrong account");
	assert.equal(view.journals[1].lastRejection.actor, "lead@example.com");
	assert.notEqual(view.journals[1].lastRejection.at, "2026-10-01T10:30:00+00:00");
	assert.equal(view.journals[1].lastRejection.at, "Oct 1, 10:30");
});

test("journalsView: a null approved_at reads 'not recorded'", () => {
	const view = journalsView(payload({ journals: [journal({ approved_at: null })] }), NOW, TZ);
	assert.equal(view.journals[0].approvedAtText, "not recorded");
});

test("journalsView requires a time zone and a valid now", () => {
	assert.throws(() => journalsView(payload(), NOW, null));
	assert.throws(() => journalsView(payload(), new Date("not a date"), TZ));
});

// D04 correction to D03: get_journals (D02) always sends `dimensions`, even
// as `[]`; a missing key is a bug in the caller (or a stale/forged
// payload), never "zero dimensions" (no silent fallback).
test("journalsView: failure path, a missing `dimensions` key throws rather than defaulting to []", () => {
	const p = payload();
	delete p.dimensions;
	assert.throws(() => journalsView(p, NOW, TZ), /dimensions/);
});

// --- durationOptions, durationIndex --------------------------------------
//
// U1/U9: fed exactly what the screen passes — `journalsView`'s output, not
// the raw `get_journals` payload.

test("durationOptions: with no reversal choices, only 'none' is offered", () => {
	const options = durationOptions(journalsView(payload({ reversal_choices: [] }), NOW, TZ));
	assert.equal(options.length, 1);
	assert.equal(options[0].kind, "none");
	assert.equal(options[0].label, "This period only, no reversal");
	assert.equal(NO_REVERSAL_NOTE, "No open Regular period after this one to reverse into");
});

test("durationOptions: a reversal choice is offered as 'reverses' (U1, U9 — fed journalsView's output)", () => {
	const view = journalsView(
		payload({ reversal_choices: [{ fiscal_year: 2026, fiscal_period: 10, code: "FY26 P10" }] }),
		NOW,
		TZ,
	);
	const options = durationOptions(view);
	assert.equal(options.length, 2);
	assert.deepEqual(options[1], { kind: "reverses", fiscal_year: 2026, fiscal_period: 10, label: "Reverses in FY26 P10" });
});

test("durationOptions: failure path, never a 'stays until reversed' option", () => {
	const view = journalsView(
		payload({ reversal_choices: [{ fiscal_year: 2026, fiscal_period: 10, code: "FY26 P10" }] }),
		NOW,
		TZ,
	);
	const options = durationOptions(view);
	for (const option of options) {
		assert.doesNotMatch(option.label, /until reversed/i);
	}
});

test("durationIndex: a reversing journal's duration index is its option, not 0 (U1)", () => {
	const view = journalsView(
		payload({
			reversal_choices: [
				{ fiscal_year: 2026, fiscal_period: 10, code: "FY26 P10" },
				{ fiscal_year: 2026, fiscal_period: 11, code: "FY26 P11" },
			],
		}),
		NOW,
		TZ,
	);
	const options = durationOptions(view);
	const idx = durationIndex(options, { kind: "reverses", fiscal_year: 2026, fiscal_period: 11 });
	assert.equal(idx, 2);
	assert.notEqual(idx, 0);
});

test("durationIndex: failure path, throws on an unknown duration rather than falling back to 0 (U1)", () => {
	const view = journalsView(payload({ reversal_choices: [] }), NOW, TZ);
	const options = durationOptions(view);
	assert.throws(() => durationIndex(options, { kind: "reverses", fiscal_year: 2026, fiscal_period: 10 }));
});

// --- draftTotals ----------------------------------------------------------

test("draftTotals: 0.10 + 0.20 against 0.30 is balanced", () => {
	const totals = draftTotals([
		{ debit_amount: "0.10", credit_amount: 0 },
		{ debit_amount: "0.20", credit_amount: 0 },
		{ debit_amount: 0, credit_amount: "0.30" },
	]);
	assert.equal(totals.debit, 0.3);
	assert.equal(totals.credit, 0.3);
	assert.equal(totals.difference, 0);
	assert.equal(totals.balanced, true);
	assert.deepEqual(totals.invalid, []);
});

test("draftTotals: 18500 against 18499.99 shows a difference of 0.01", () => {
	const totals = draftTotals([
		{ debit_amount: 18500, credit_amount: 0 },
		{ debit_amount: 0, credit_amount: 18499.99 },
	]);
	assert.equal(totals.difference, 0.01);
	assert.equal(totals.balanced, false);
});

test("draftTotals: a non-number ('abc') counts as 0 and is flagged invalid", () => {
	const totals = draftTotals([
		{ debit_amount: "abc", credit_amount: 0 },
		{ debit_amount: 10, credit_amount: 0 },
	]);
	assert.equal(totals.debit, 10);
	assert.deepEqual(totals.invalid, [1]);
});

test("draftTotals: failure path, 'Infinity' is invalid, not a huge valid amount (U12)", () => {
	const totals = draftTotals([
		{ debit_amount: "Infinity", credit_amount: 0 },
		{ debit_amount: 10, credit_amount: 0 },
	]);
	assert.equal(totals.debit, 10);
	assert.deepEqual(totals.invalid, [1]);
});

test("draftTotals: failure path, '0x10' is invalid, not hex 16 (U12)", () => {
	const totals = draftTotals([
		{ debit_amount: "0x10", credit_amount: 0 },
		{ debit_amount: 10, credit_amount: 0 },
	]);
	assert.equal(totals.debit, 10);
	assert.deepEqual(totals.invalid, [1]);
});

test("draftTotals: never a float sum (0.1 repeated 3 times still balances)", () => {
	const totals = draftTotals([
		{ debit_amount: 0.1, credit_amount: 0 },
		{ debit_amount: 0.1, credit_amount: 0 },
		{ debit_amount: 0.1, credit_amount: 0 },
		{ debit_amount: 0, credit_amount: 0.3 },
	]);
	assert.equal(totals.balanced, true);
});

// --- saveJournalBody --------------------------------------------------

const PERIOD = { fiscal_year: 2026, fiscal_period: 9 };

test("saveJournalBody: a new draft carries exactly the A06 keys, no more", () => {
	const draft = {
		consolidation_group: "Demo Group",
		adjustment_type: "topside",
		description: "Reclass",
		duration: { kind: "none" },
		lines: [{ data_area_id: "ZZ-A", main_account: "6100", debit_amount: 1, credit_amount: 0, description: "x" }],
	};
	const body = saveJournalBody(PERIOD, draft);
	assert.deepEqual(
		Object.keys(body).sort(),
		["adjustment_type", "consolidation_group", "description", "fiscal_period", "fiscal_year", "lines", "reverse_fiscal_period", "reverse_fiscal_year"].sort()
	);
	assert.equal(body.reverse_fiscal_year, 0);
	assert.equal(body.reverse_fiscal_period, 0);
	const lines = JSON.parse(body.lines);
	assert.deepEqual(Object.keys(lines[0]).sort(), ["credit_amount", "data_area_id", "debit_amount", "description", "main_account"].sort());
});

test("saveJournalBody: a reversal duration carries its year and period", () => {
	const draft = {
		consolidation_group: "Demo Group",
		adjustment_type: "topside",
		description: "Reclass",
		duration: { kind: "reverses", fiscal_year: 2026, fiscal_period: 10 },
		lines: [],
	};
	const body = saveJournalBody(PERIOD, draft);
	assert.equal(body.reverse_fiscal_year, 2026);
	assert.equal(body.reverse_fiscal_period, 10);
});

test("saveJournalBody: editing an existing draft adds 'name'", () => {
	const draft = {
		name: "CJ-00001",
		consolidation_group: "Demo Group",
		adjustment_type: "topside",
		description: "Reclass",
		duration: { kind: "none" },
		lines: [],
	};
	const body = saveJournalBody(PERIOD, draft);
	assert.equal(body.name, "CJ-00001");
});

test("saveJournalBody: forged header and line keys never reach the body", () => {
	const draft = {
		consolidation_group: "Demo Group",
		adjustment_type: "topside",
		description: "Reclass",
		duration: { kind: "none" },
		status: "Approved",
		docstatus: 1,
		approved_by: "Administrator",
		lines: [
			{
				data_area_id: "ZZ-A",
				main_account: "6100",
				debit_amount: 1,
				credit_amount: 0,
				description: "x",
				docstatus: 1,
				name: "forged",
				parent: "forged",
			},
		],
	};
	const body = saveJournalBody(PERIOD, draft);
	assert.equal(body.status, undefined);
	assert.equal(body.docstatus, undefined);
	assert.equal(body.approved_by, undefined);
	const line = JSON.parse(body.lines)[0];
	assert.equal(line.docstatus, undefined);
	assert.equal(line.name, undefined);
	assert.equal(line.parent, undefined);
});

// --- effectView -------------------------------------------------------

test("effectView(null): an unsaved edit gives the note", () => {
	assert.deepEqual(effectView(null), { note: "Save the draft to see its effect" });
});

test("effectView: Dr for a positive net_debit, 2dp with grouping", () => {
	const view = effectView({ headings: [{ section: "Profit and Loss", heading: "6000", heading_name: "Expenses", net_debit: 18500 }], sections: [], no_heading: 0 });
	assert.equal(view.headings[0].amountText, "Dr 18,500.00");
	assert.equal(view.headings[0].label, "Expenses");
});

test("effectView: Cr for a negative net_debit", () => {
	const view = effectView({ headings: [{ section: "Balance Sheet", heading: "2310", heading_name: "Payables", net_debit: -18500 }], sections: [], no_heading: 0 });
	assert.equal(view.headings[0].amountText, "Cr 18,500.00");
});

test("effectView: 'no change' for a zero net_debit", () => {
	const view = effectView({ headings: [{ section: null, heading: null, heading_name: null, net_debit: 0 }], sections: [], no_heading: 1 });
	assert.equal(view.headings[0].amountText, "no change");
});

test("effectView: 'no heading' for a null heading, never dropped", () => {
	const view = effectView({ headings: [{ section: null, heading: null, heading_name: null, net_debit: 5 }], sections: [], no_heading: 1 });
	assert.equal(view.headings.length, 1);
	assert.equal(view.headings[0].label, "no heading");
	assert.equal(view.noHeading, 1);
});

// --- editable -----------------------------------------------------------
//
// U2/U9: fed `journalsView`'s output (camelCase keys), the same object the
// screen itself passes — never the raw snake_case payload.

test("editable: true for a Draft journal, the first state, an open period, when drafting is allowed (U9)", () => {
	const view = journalsView(payload({ first_state: "Draft", can_edit_period: true, can_draft: true }), NOW, TZ);
	assert.equal(editable(journal({ status: "Draft", docstatus: 0 }), view), true);
});

test("editable: failure path, false for 'Pending Approval'", () => {
	const view = journalsView(payload({ first_state: "Draft", can_edit_period: true, can_draft: true }), NOW, TZ);
	assert.equal(editable(journal({ status: "Pending Approval", docstatus: 0 }), view), false);
});

test("editable: failure path, false for docstatus 1", () => {
	const view = journalsView(payload({ first_state: "Draft", can_edit_period: true, can_draft: true }), NOW, TZ);
	assert.equal(editable(journal({ status: "Draft", docstatus: 1 }), view), false);
});

test("editable: failure path, false for a Closed period", () => {
	const view = journalsView(payload({ first_state: "Draft", can_edit_period: false, can_draft: true }), NOW, TZ);
	assert.equal(editable(journal({ status: "Draft", docstatus: 0 }), view), false);
});

test("editable: failure path, false when can_draft is false even for the journal's own preparer (U2 — no preparer-owns-it branch)", () => {
	const view = journalsView(payload({ first_state: "Draft", can_edit_period: true, can_draft: false }), NOW, TZ);
	assert.equal(editable(journal({ status: "Draft", docstatus: 0, preparer: "owner@example.com" }), view), false);
});

// --- journalsView: list totals (U6) ----------------------------------

test("journalsView: list totals are formatted 2dp with grouping through the module, not a raw float (U6)", () => {
	const view = journalsView(payload({ journals: [journal({ total_debit: 1234.5, total_credit: 1234.5 })] }), NOW, TZ);
	assert.equal(view.journals[0].totalsText, "1,234.50 / 1,234.50");
	assert.equal(formatAmount(1234.5), "1,234.50");
});

// --- snapshotDraft / draftDirty (U3, U9 — fed the editor's own draft shape) --

function blankDraft(overrides = {}) {
	return {
		consolidation_group: "Demo Group",
		adjustment_type: "topside",
		description: "Reclass",
		duration: { kind: "none" },
		lines: [{ data_area_id: "ZZ-A", main_account: "6100", debit_amount: 100, credit_amount: 0, description: "x" }],
		...overrides,
	};
}

test("draftDirty: an identical draft is never dirty against its own snapshot (U3)", () => {
	const draft = blankDraft();
	const snapshot = snapshotDraft(draft);
	assert.equal(draftDirty(snapshot, draft), false);
});

test("draftDirty: a changed line amount is dirty (U3)", () => {
	const draft = blankDraft();
	const snapshot = snapshotDraft(draft);
	draft.lines[0].debit_amount = 150;
	assert.equal(draftDirty(snapshot, draft), true);
});

test("draftDirty: '100' typed on screen and 100 last saved compare equal in cents, never as strings (U3)", () => {
	const draft = blankDraft({ lines: [{ data_area_id: "ZZ-A", main_account: "6100", debit_amount: 100, credit_amount: 0, description: "x" }] });
	const snapshot = snapshotDraft(draft);
	draft.lines[0].debit_amount = "100";
	draft.lines[0].credit_amount = "0";
	assert.equal(draftDirty(snapshot, draft), false);
});

test("draftDirty: a changed group, type, description or duration is dirty even with unchanged lines (U3)", () => {
	const snapshot = snapshotDraft(blankDraft());
	assert.equal(draftDirty(snapshot, blankDraft({ consolidation_group: "Other Group" })), true);
	assert.equal(draftDirty(snapshot, blankDraft({ adjustment_type: "reclassification" })), true);
	assert.equal(draftDirty(snapshot, blankDraft({ description: "Different" })), true);
	assert.equal(draftDirty(snapshot, blankDraft({ duration: { kind: "reverses", fiscal_year: 2026, fiscal_period: 10 } })), true);
});

test("draftDirty: an added or removed line is dirty (U3)", () => {
	const draft = blankDraft();
	const snapshot = snapshotDraft(draft);
	draft.lines.push({ data_area_id: "ZZ-B", main_account: "6100", debit_amount: 1, credit_amount: 0, description: "" });
	assert.equal(draftDirty(snapshot, draft), true);
});

test("draftDirty: failure path, no snapshot (nothing saved yet) is never dirty", () => {
	assert.equal(draftDirty(null, blankDraft()), false);
});

// --- canOpenNew / canSaveDraft / canSendDraft (U4, U9 — fed journalsView's output) --

test("canOpenNew: requires canDraft AND canEditPeriod — can_draft ignores period status, so the screen must still refuse a locked period (U4)", () => {
	const open = journalsView(payload({ can_draft: true, can_edit_period: true }), NOW, TZ);
	assert.equal(canOpenNew(open), true);
	const locked = journalsView(payload({ can_draft: true, can_edit_period: false }), NOW, TZ);
	assert.equal(canOpenNew(locked), false);
	assert.equal(canOpenNew(null), false);
});

test("canSaveDraft: requires canEditPeriod and no invalid line (U4)", () => {
	const open = journalsView(payload({ can_edit_period: true }), NOW, TZ);
	assert.equal(canSaveDraft(open, { invalid: [] }), true);
	assert.equal(canSaveDraft(open, { invalid: [1] }), false);
	const locked = journalsView(payload({ can_edit_period: false }), NOW, TZ);
	assert.equal(canSaveDraft(locked, { invalid: [] }), false);
});

test("canSendDraft: requires canSend, canEditPeriod, a saved name, and no unsaved change (U3, U4)", () => {
	const view = journalsView(payload({ can_send: true, can_edit_period: true }), NOW, TZ);
	assert.equal(canSendDraft(view, { name: "CJ-00001" }, false), true);
	assert.equal(canSendDraft(view, { name: null }, false), false, "nothing saved yet");
	assert.equal(canSendDraft(view, { name: "CJ-00001" }, true), false, "dirty (U3)");
	const locked = journalsView(payload({ can_send: true, can_edit_period: false }), NOW, TZ);
	assert.equal(canSendDraft(locked, { name: "CJ-00001" }, false), false, "locked period (U4)");
});

// --- konsolidat#245 option D (D03): dimensions in the view, the snapshot
// and the save body ---------------------------------------------------
//
// Fed journal_api.get_journals's real payload shape (A05/D02):
// `dimensions: [{key, label, suggestions}]` on the payload, and each
// declared key present (default '') on every line.

const DIMENSIONS = [
	{ key: "dim_cost_center", label: "Cost Center", suggestions: ["CC-100", "CC-200"] },
];

test("journalsView: carries payload.dimensions through as view.dimensions", () => {
	const view = journalsView(payload({ dimensions: DIMENSIONS }), NOW, TZ);
	assert.deepEqual(view.dimensions, DIMENSIONS);
});

test("journalsView: zero declared dimensions -> view.dimensions is [] (identical to today)", () => {
	const view = journalsView(payload(), NOW, TZ);
	assert.deepEqual(view.dimensions, []);
	// every other key journalsView has always returned is unaffected
	assert.equal(view.canDraft, true);
	assert.equal(view.canEditPeriod, true);
});

test("snapshotLines: a declared dim key is carried, default '' when missing", () => {
	const lines = snapshotLines(
		[{ data_area_id: "ZZ-A", main_account: "6100", debit_amount: 1, credit_amount: 0, description: "x" }],
		["dim_cost_center"],
	);
	assert.equal(lines[0].dim_cost_center, "");
});

test("snapshotLines: a declared dim key's real value is carried, never refused (typed text not in suggestions)", () => {
	const lines = snapshotLines(
		[{ data_area_id: "ZZ-A", main_account: "6100", debit_amount: 1, credit_amount: 0, description: "x", dim_cost_center: "anything typed" }],
		["dim_cost_center"],
	);
	assert.equal(lines[0].dim_cost_center, "anything typed");
});

test("snapshotLines: failure path, zero declared dimensions carries no dim key at all (identical to today)", () => {
	const lines = snapshotLines([{ data_area_id: "ZZ-A", main_account: "6100", debit_amount: 1, credit_amount: 0, description: "x", dim_cost_center: "CC-100" }]);
	assert.deepEqual(Object.keys(lines[0]).sort(), ["credit_amount", "data_area_id", "debit_amount", "description", "main_account"].sort());
});

test("draftDirty: a changed declared dim value is dirty when dimKeys is passed (U3)", () => {
	const draft = blankDraft({
		lines: [{ data_area_id: "ZZ-A", main_account: "6100", debit_amount: 100, credit_amount: 0, description: "x", dim_cost_center: "CC-100" }],
	});
	const snapshot = snapshotDraft(draft, ["dim_cost_center"]);
	draft.lines[0].dim_cost_center = "CC-200";
	assert.equal(draftDirty(snapshot, draft, ["dim_cost_center"]), true);
});

test("draftDirty: an unchanged declared dim value is never dirty against its own snapshot (U3)", () => {
	const draft = blankDraft({
		lines: [{ data_area_id: "ZZ-A", main_account: "6100", debit_amount: 100, credit_amount: 0, description: "x", dim_cost_center: "CC-100" }],
	});
	const snapshot = snapshotDraft(draft, ["dim_cost_center"]);
	assert.equal(draftDirty(snapshot, draft, ["dim_cost_center"]), false);
});

test("draftDirty: failure path, zero declared dimensions ignores a dim-looking key (identical to today)", () => {
	const draft = blankDraft({
		lines: [{ data_area_id: "ZZ-A", main_account: "6100", debit_amount: 100, credit_amount: 0, description: "x", dim_cost_center: "CC-100" }],
	});
	const snapshot = snapshotDraft(draft);
	draft.lines[0].dim_cost_center = "CC-200";
	assert.equal(draftDirty(snapshot, draft), false);
});

test("saveJournalBody: sends exactly LINE_KEYS plus the declared dim keys, default '' when missing", () => {
	const draft = {
		consolidation_group: "Demo Group",
		adjustment_type: "topside",
		description: "Reclass",
		duration: { kind: "none" },
		lines: [{ data_area_id: "ZZ-A", main_account: "6100", debit_amount: 1, credit_amount: 0, description: "x" }],
	};
	const body = saveJournalBody(PERIOD, draft, ["dim_cost_center"]);
	const line = JSON.parse(body.lines)[0];
	assert.deepEqual(
		Object.keys(line).sort(),
		["credit_amount", "data_area_id", "debit_amount", "description", "main_account", "dim_cost_center"].sort(),
	);
	assert.equal(line.dim_cost_center, "");
});

test("saveJournalBody: a typed dim value is sent verbatim, never refused (konsol#247)", () => {
	const draft = {
		consolidation_group: "Demo Group",
		adjustment_type: "topside",
		description: "Reclass",
		duration: { kind: "none" },
		lines: [{ data_area_id: "ZZ-A", main_account: "6100", debit_amount: 1, credit_amount: 0, description: "x", dim_cost_center: "anything typed" }],
	};
	const body = saveJournalBody(PERIOD, draft, ["dim_cost_center"]);
	const line = JSON.parse(body.lines)[0];
	assert.equal(line.dim_cost_center, "anything typed");
});

test("saveJournalBody: failure path, an undeclared dim key on a draft line is dropped", () => {
	const draft = {
		consolidation_group: "Demo Group",
		adjustment_type: "topside",
		description: "Reclass",
		duration: { kind: "none" },
		lines: [{ data_area_id: "ZZ-A", main_account: "6100", debit_amount: 1, credit_amount: 0, description: "x", dim_cost_center: "CC-100", dim_project: "forged" }],
	};
	// dim_project is NOT in the declared dimKeys, so it must never reach the body.
	const body = saveJournalBody(PERIOD, draft, ["dim_cost_center"]);
	const line = JSON.parse(body.lines)[0];
	assert.equal(line.dim_cost_center, "CC-100");
	assert.equal(line.dim_project, undefined);
});

test("saveJournalBody: failure path, zero declared dimensions sends no dim key at all (identical to today)", () => {
	const draft = {
		consolidation_group: "Demo Group",
		adjustment_type: "topside",
		description: "Reclass",
		duration: { kind: "none" },
		lines: [{ data_area_id: "ZZ-A", main_account: "6100", debit_amount: 1, credit_amount: 0, description: "x", dim_cost_center: "CC-100" }],
	};
	const body = saveJournalBody(PERIOD, draft);
	const line = JSON.parse(body.lines)[0];
	assert.deepEqual(Object.keys(line).sort(), ["credit_amount", "data_area_id", "debit_amount", "description", "main_account"].sort());
});

// --- konsolidat#245 option D (D04): dimKeysOf, dimValueText (Adjustments.vue's
// screen-level helpers) --------------------------------------------------
//
// Fed journalsView's real output, not a hand-built dict (coordinator
// instruction: screen-source greps alone are not enough).

test("dimKeysOf: the declared keys, in the server's order, from journalsView's output", () => {
	const view = journalsView(payload({ dimensions: DIMENSIONS }), NOW, TZ);
	assert.deepEqual(dimKeysOf(view), ["dim_cost_center"]);
});

test("dimKeysOf: failure path, zero declared dimensions gives [] (identical to today)", () => {
	const view = journalsView(payload({ dimensions: [] }), NOW, TZ);
	assert.deepEqual(dimKeysOf(view), []);
});

test("dimKeysOf: failure path, no view yet (not loaded) gives []", () => {
	assert.deepEqual(dimKeysOf(null), []);
});

test("dimValueText: a missing, null or blank value reads as the explicit em dash", () => {
	assert.equal(dimValueText({}, "dim_cost_center"), "—");
	assert.equal(dimValueText({ dim_cost_center: null }, "dim_cost_center"), "—");
	assert.equal(dimValueText({ dim_cost_center: "" }, "dim_cost_center"), "—");
});

test("dimValueText: a real typed value is shown verbatim, never refused (konsol#247)", () => {
	assert.equal(dimValueText({ dim_cost_center: "CC-100" }, "dim_cost_center"), "CC-100");
});

// --- #305 story 6.5: journals reversing into this period ---------------

// The golden fixture is the real `journal_api.get_journals` `reversing_in`
// item (test_close_journal_api.py::test_reversing_item_matches_the_golden_fixture).
const REVERSING_FIXTURE_PATH = fileURLToPath(
	new URL("../../konsol/tests/fixtures/close_journals_reversing_in.json", import.meta.url),
);
function goldenReversing() {
	return JSON.parse(fs.readFileSync(REVERSING_FIXTURE_PATH, "utf8"));
}

test("6.5: journalsView throws when `reversing_in` is missing (get_journals always sends it, even as [])", () => {
	const p = payload();
	delete p.reversing_in;
	assert.throws(() => journalsView(p, NOW, TZ), /reversing_in/);
});

test("6.5: journalsView passes the real reversing item through with its label, totals and approval time", () => {
	const golden = goldenReversing();
	const view = journalsView(payload({ reversing_in: [golden] }), NOW, TZ);
	assert.equal(view.reversingIn.length, 1);
	const item = view.reversingIn[0];
	assert.equal(item.name, golden.name);
	assert.equal(item.label, "Reverses here from FY2025 P06");
	assert.deepEqual(item.origin, golden.origin);
	assert.equal(item.totalsText, "1,200.00 / 1,200.00");
	assert.notEqual(item.approvedAtText, "not recorded");
	assert.deepEqual(item.lines, golden.lines);
	assert.deepEqual(item.effect, golden.effect);
	assert.equal(view.journals.length, 0, "a reversing item never joins the period's own journals");
});

test("6.5: the reversing item's effect reads as the original's, signs flipped", () => {
	const view = effectView(goldenReversing().effect);
	assert.deepEqual(
		view.headings.map((h) => [h.label, h.amountText]),
		[
			["Operating expenses", "Cr 1,200.00"],
			["Current liabilities", "Dr 1,200.00"],
		],
	);
});

// --- konsol#305 review-w5 U4 / U12: the reversing item's effect, behaviour --

function reversingWithNoHeading() {
	const golden = goldenReversing();
	golden.effect = {
		headings: [
			...golden.effect.headings,
			{ heading: null, heading_name: null, net_debit: -50.0, section: null },
		],
		sections: golden.effect.sections,
		no_heading: 1,
	};
	return golden;
}

test("U4: effectView names the accounts outside any heading in one shared sentence", () => {
	assert.equal(effectView(reversingWithNoHeading().effect).noHeadingText, "1 account(s) outside any heading.");
	assert.equal(effectView(goldenReversing().effect).noHeadingText, null);
});

test("U4: a reversing item's effect keeps the amount and the note for accounts outside any heading", () => {
	const view = journalsView(payload({ reversing_in: [reversingWithNoHeading()] }), NOW, TZ);
	const effect = view.reversingIn[0].effectView;
	assert.deepEqual(effect.headings.at(-1), {
		section: null,
		heading: null,
		label: "no heading",
		amountText: "Cr 50.00",
	});
	assert.equal(effect.noHeadingText, "1 account(s) outside any heading.");
});

test("U12: the reversing list is built even when the period has no journals of its own", () => {
	const view = journalsView(payload({ journals: [], reversing_in: [goldenReversing()] }), NOW, TZ);
	assert.equal(view.journals.length, 0);
	assert.equal(view.reversingIn.length, 1);
	assert.deepEqual(view.reversingIn[0].effectView, effectView(goldenReversing().effect));
});
