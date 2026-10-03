// konsol#305 A13: adjustments.test.mjs
//
// Exercises adjustments.js against journal_api.get_journals's real payload
// shape (A05: konsol/close/journal_api.py, konsol/close/journal_model.py).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
	journalsView,
	durationOptions,
	NO_REVERSAL_NOTE,
	draftTotals,
	saveJournalBody,
	effectView,
	editable,
} from "./adjustments.js";

const NOW = new Date("2026-10-03T12:00:00Z");
const TZ = "UTC";

function payload(overrides = {}) {
	return {
		period: { fiscal_year: 2026, fiscal_period: 9, code: "FY26 P09", status: "Open", period_type: "Regular" },
		journals: [],
		groups: [{ consolidation_group: "Demo Group", reporting_currency: "USD", entities: ["ZZ-A", "ZZ-B"] }],
		accounts: {},
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

// --- durationOptions ----------------------------------------------------

test("durationOptions: with no reversal choices, only 'none' is offered", () => {
	const options = durationOptions(payload({ reversal_choices: [] }));
	assert.equal(options.length, 1);
	assert.equal(options[0].kind, "none");
	assert.equal(options[0].label, "This period only, no reversal");
	assert.equal(NO_REVERSAL_NOTE, "No open Regular period after this one to reverse into");
});

test("durationOptions: a reversal choice is offered as 'reverses'", () => {
	const options = durationOptions(
		payload({ reversal_choices: [{ fiscal_year: 2026, fiscal_period: 10, code: "FY26 P10" }] })
	);
	assert.equal(options.length, 2);
	assert.deepEqual(options[1], { kind: "reverses", fiscal_year: 2026, fiscal_period: 10, label: "Reverses in FY26 P10" });
});

test("durationOptions: failure path, never a 'stays until reversed' option", () => {
	const options = durationOptions(
		payload({ reversal_choices: [{ fiscal_year: 2026, fiscal_period: 10, code: "FY26 P10" }] })
	);
	for (const option of options) {
		assert.doesNotMatch(option.label, /until reversed/i);
	}
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

test("editable: true for a Draft journal, the first state, an open period, when drafting is allowed", () => {
	const p = payload({ first_state: "Draft", can_edit_period: true, can_draft: true });
	assert.equal(editable(journal({ status: "Draft", docstatus: 0 }), p, "someone@example.com"), true);
});

test("editable: failure path, false for 'Pending Approval'", () => {
	const p = payload({ first_state: "Draft", can_edit_period: true, can_draft: true });
	assert.equal(editable(journal({ status: "Pending Approval", docstatus: 0 }), p, "someone@example.com"), false);
});

test("editable: failure path, false for docstatus 1", () => {
	const p = payload({ first_state: "Draft", can_edit_period: true, can_draft: true });
	assert.equal(editable(journal({ status: "Draft", docstatus: 1 }), p, "someone@example.com"), false);
});

test("editable: failure path, false for a Closed period", () => {
	const p = payload({ first_state: "Draft", can_edit_period: false, can_draft: true });
	assert.equal(editable(journal({ status: "Draft", docstatus: 0 }), p, "someone@example.com"), false);
});

test("editable: when can_draft is false, only the journal's own preparer may edit it", () => {
	const p = payload({ first_state: "Draft", can_edit_period: true, can_draft: false });
	assert.equal(editable(journal({ status: "Draft", docstatus: 0, preparer: "owner@example.com" }), p, "owner@example.com"), true);
	assert.equal(editable(journal({ status: "Draft", docstatus: 0, preparer: "owner@example.com" }), p, "other@example.com"), false);
});
