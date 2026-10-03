// konsol#305 A14: approvals.test.mjs
//
// Exercises approvals.js against approvals_api.get_queue's real payload
// shape (A10: konsol/close/approvals_api.py, konsol/close/approvals_model.py).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { queueView, rejectBody, approveAction, approveBody, REJECT_REASON_ERROR } from "./approvals.js";

const NOW = new Date("2026-09-25T12:00:00Z");
const TZ = "UTC";

function gerItem(overrides = {}) {
	return {
		doctype: "Group Exchange Rate",
		name: "GER-10",
		kind_label: "Group rate · USD→EUR Closing",
		title: "0.9123 per 1",
		detail: "FY2026 P09",
		preparer: "alice@example.com",
		edited_by: [],
		created: "2026-09-20T08:00:00+00:00",
		approve: { mode: "direct", message: null },
		inline: true,
		desk: null,
		entity: null,
		sent_back: false,
		rejection: null,
		...overrides,
	};
}

function journalItem(overrides = {}) {
	return {
		doctype: "Consolidation Journal",
		name: "CJ-1",
		kind_label: "Adjustment · CJ-1",
		title: "Intercompany reclass",
		detail: "Elimination · FY2026 P09 · This period only",
		preparer: "carol@example.com",
		edited_by: [],
		created: "2026-09-19T08:00:00+00:00",
		approve: { mode: "reason", message: "no-reason refusal text" },
		inline: true,
		desk: null,
		entity: null,
		sent_back: false,
		rejection: null,
		lines: [{ idx: 1, data_area_id: "ZZ-A", main_account: "4000", account_name: "Sales",
			debit_amount: 0, credit_amount: 18500, description: null }],
		effect: {
			headings: [{ section: "profit_and_loss", heading: "4000", heading_name: null, net_debit: -18500 }],
			sections: [{ section: "profit_and_loss", net_debit: -18500 }],
			no_heading: 1,
		},
		total_debit: 18500,
		currency: "USD",
		...overrides,
	};
}

function bcItem(overrides = {}) {
	return {
		doctype: "Business Combination",
		name: "BC-1",
		kind_label: "Business combination · ZZ-ACQ",
		title: "Business combination · ZZ-ACQ",
		detail: "Demo Group · 60% from 2026-01-01",
		preparer: "bob@example.com",
		edited_by: [],
		created: "2026-09-18T08:00:00+00:00",
		approve: { mode: "direct", message: null },
		inline: false,
		desk: "/app/business-combination/BC-1",
		entity: "ZZ-ACQ",
		sent_back: false,
		rejection: null,
		...overrides,
	};
}

function payload(overrides = {}) {
	return {
		items: [],
		sent_back: [],
		counts: {},
		hidden: 0,
		self_approval: "Blocked",
		can_approve: true,
		waiting: { count: 0, oldest: null },
		...overrides,
	};
}

// --- queueView: header / hiddenNote / pass-through --------------------------

test("queueView: header names the waiting count and the oldest's age", () => {
	const view = queueView(payload({ waiting: { count: 3, oldest: "2026-09-20T08:00:00+00:00" } }), NOW, TZ);
	assert.equal(view.header, "3 waiting for you · oldest 5 days");
});

test("queueView: a zero count reads \"Nothing waiting for you\"", () => {
	const view = queueView(payload({ waiting: { count: 0, oldest: null } }), NOW, TZ);
	assert.equal(view.header, "Nothing waiting for you");
});

test("queueView: hiddenNote names the hidden count, or null when nothing is hidden", () => {
	assert.equal(queueView(payload({ hidden: 2 }), NOW, TZ).hiddenNote, "2 awaiting outside your scope");
	assert.equal(queueView(payload({ hidden: 0 }), NOW, TZ).hiddenNote, null);
});

test("queueView: canApprove and selfApproval pass through", () => {
	const view = queueView(payload({ can_approve: false, self_approval: null }), NOW, TZ);
	assert.equal(view.canApprove, false);
	assert.equal(view.selfApproval, null);
});

test("queueView requires a time zone and a valid now", () => {
	assert.throws(() => queueView(payload(), NOW, null), /time zone/);
	assert.throws(() => queueView(payload(), new Date("not a date"), TZ), /valid `now`/);
});

// --- U12: no invented fallback for a missing `waiting` or `hidden` ----------

test("Failure path, U12: a payload with no `waiting` throws, never reading as nothing waiting", () => {
	assert.throws(() => queueView(payload({ waiting: undefined }), NOW, TZ), /payload\.waiting/);
	assert.throws(() => queueView(payload({ waiting: {} }), NOW, TZ), /payload\.waiting/);
	assert.throws(() => queueView(payload({ waiting: null }), NOW, TZ), /payload\.waiting/);
});

test("Failure path, U12: a payload with no `hidden` throws, never reading as zero", () => {
	assert.throws(() => queueView(payload({ hidden: undefined }), NOW, TZ), /payload\.hidden/);
	assert.throws(() => queueView(payload({ hidden: null }), NOW, TZ), /payload\.hidden/);
	assert.throws(() => queueView(payload({ hidden: "0" }), NOW, TZ), /payload\.hidden/);
});

// --- inline items: approve mode, effect --------------------------------------

test("queueView: a direct item becomes an approve button, inline true", () => {
	const view = queueView(payload({ items: [gerItem()] }), NOW, TZ);
	assert.deepEqual(view.items[0].approve, { kind: "button", message: null });
	assert.equal(view.items[0].inline, true);
	assert.equal(view.items[0].deskLink, null);
});

test("Failure path, R2: a not_approver item has approve.kind \"none\"", () => {
	const message = "The Close Lead approves (R2).";
	const view = queueView(
		payload({ items: [gerItem({ approve: { mode: "not_approver", message } })] }), NOW, TZ);
	assert.equal(view.items[0].approve.kind, "none");
	assert.equal(view.items[0].approve.message, message);
});

test("Failure path, R5: a refused item carries the server's message as text", () => {
	const message = "Self-approval is Blocked by Close Settings.";
	const view = queueView(
		payload({ items: [gerItem({ approve: { mode: "refused", message } })] }), NOW, TZ);
	assert.equal(view.items[0].approve.kind, "refused");
	assert.equal(view.items[0].approve.message, message);
});

test("queueView: an inline journal's effect runs through effectView; a non-journal item carries no effect key", () => {
	const view = queueView(payload({ items: [journalItem(), gerItem()] }), NOW, TZ);
	assert.deepEqual(view.items[0].effect.headings[0], { section: "profit_and_loss", heading: "4000", label: "no heading", amountText: "Cr 18,500.00" });
	assert.equal("effect" in view.items[1], false);
});

test("Under \"Allowed with reason\", a reason item's approveBody with a blank reason gives the reused function's error", () => {
	const view = queueView(payload({ items: [journalItem()] }), NOW, TZ);
	const result = approveBody(view.items[0].doctype, view.items[0].name, view.items[0].approve, "   ");
	assert.deepEqual(result, { error: "Give a reason: Close Settings allows self-approval only with a reason." });
});

test("Failure path, U12: a \"reason\" item with no server message throws, never an invented label", () => {
	assert.throws(
		() => queueView(payload({ items: [journalItem({ approve: { mode: "reason", message: null } })] }), NOW, TZ),
		/needs the server's own message/,
	);
	assert.throws(
		() => queueView(payload({ items: [journalItem({ approve: { mode: "reason", message: "" } })] }), NOW, TZ),
		/needs the server's own message/,
	);
});

// --- U9: a pure test feeding queueView's own output the way Approvals.vue --
// uses it, rather than grepping the component's source (approvals.screen.
// test.mjs still checks the wiring; this exercises the real decisions).

test("U9: Approvals.vue's own gate (canAct) and reason label, fed queueView's real output", () => {
	// Mirrors Approvals.vue's `canAct(item)` exactly (screens/Approvals.vue):
	// only an inline item whose approve mode is one the caller may act on.
	function canAct(item) {
		return item.inline && (item.approve.kind === "button" || item.approve.kind === "reason");
	}

	const reasonMessage = "Give a reason: Close Settings allows self-approval only with a reason.";
	const view = queueView(
		payload({
			items: [
				journalItem({ approve: { mode: "reason", message: reasonMessage } }),
				gerItem({ approve: { mode: "direct", message: null } }),
				bcItem(),
			],
		}),
		NOW, TZ,
	);

	const [reasonItem, directItem, deskItem] = view.items;

	// A "reason" item: canAct is true, and the label the screen renders
	// (`item.approve.message`, with no `||` fallback) is the server's own
	// sentence, exactly.
	assert.equal(canAct(reasonItem), true);
	assert.equal(reasonItem.approve.message, reasonMessage);

	// A "button" (direct) item: canAct is true, no reason label needed.
	assert.equal(canAct(directItem), true);

	// A Desk-only (BC/BD) item: never inline, so canAct's own `item.inline`
	// check short-circuits false without ever reading `item.approve.kind` —
	// consistent with `approve` being null for a desk item.
	assert.equal(deskItem.inline, false);
	assert.equal(deskItem.approve, null);
});

// --- Desk-only items (BC/BD) --------------------------------------------------

test("Business Combination / Disposal items have a deskLink and inline false, with no approve action", () => {
	const view = queueView(payload({ items: [bcItem()] }), NOW, TZ);
	assert.equal(view.items[0].inline, false);
	assert.equal(view.items[0].deskLink, "/app/business-combination/BC-1");
	assert.equal(view.items[0].approve, null);
});

// --- sent-back items -----------------------------------------------------------

test("Failure path: a sent-back item has no approve action", () => {
	const view = queueView(payload({
		sent_back: [gerItem({
			sent_back: true,
			rejection: { reason: "Wrong quote", actor: "dave@example.com", at: "2026-09-21T09:30:00+00:00" },
		})],
	}), NOW, TZ);
	assert.equal(view.sentBack.length, 1);
	assert.equal("approve" in view.sentBack[0], false);
});

test("a sent-back item carries the rejection's reason, actor and formatted time", () => {
	const view = queueView(payload({
		sent_back: [gerItem({
			sent_back: true,
			rejection: { reason: "Wrong quote", actor: "dave@example.com", at: "2026-09-21T09:30:00+00:00" },
		})],
	}), NOW, TZ);
	assert.deepEqual(view.sentBack[0].rejection, { reason: "Wrong quote", actor: "dave@example.com", at: "Sep 21, 09:30" });
});

test("a sent-back Desk item (BC/BD) still carries its deskLink", () => {
	const view = queueView(payload({
		sent_back: [bcItem({
			sent_back: true,
			rejection: { reason: "Wrong entity", actor: "dave@example.com", at: "2026-09-21T09:30:00+00:00" },
		})],
	}), NOW, TZ);
	assert.equal(view.sentBack[0].deskLink, "/app/business-combination/BC-1");
	assert.equal("approve" in view.sentBack[0], false);
});

// --- rejectBody ----------------------------------------------------------------

test("Failure path: rejectBody with a blank reason gives the server's error and no body", () => {
	assert.deepEqual(rejectBody("Group Exchange Rate", "GER-1", ""), { error: REJECT_REASON_ERROR });
	assert.deepEqual(rejectBody("Group Exchange Rate", "GER-1", "   "), { error: REJECT_REASON_ERROR });
	assert.equal(REJECT_REASON_ERROR, "A rejection needs a reason.");
});

test("rejectBody with a real reason gives a trimmed body", () => {
	assert.deepEqual(
		rejectBody("Group Exchange Rate", "GER-1", "  Wrong quote  "),
		{ body: { doctype: "Group Exchange Rate", name: "GER-1", reason: "Wrong quote" } },
	);
});

// --- module hygiene --------------------------------------------------------

test("the source imports approveAction and approveBody from ./rates.js, not a copy", () => {
	const path = fileURLToPath(new URL("./approvals.js", import.meta.url));
	const source = readFileSync(path, "utf8");
	assert.match(source, /import\s*\{[^}]*approveAction[^}]*\}\s*from\s*["']\.\/rates\.js["']/);
	assert.match(source, /import\s*\{[^}]*approveBody[^}]*\}\s*from\s*["']\.\/rates\.js["']/);
});

test("the source imports no vue, frappe or xstate", () => {
	const path = fileURLToPath(new URL("./approvals.js", import.meta.url));
	const source = readFileSync(path, "utf8");
	for (const term of ["vue", "frappe", "xstate"]) {
		assert.ok(!source.includes(`"${term}`) && !source.includes(`'${term}`), `unexpected import of ${term}`);
	}
});

test("approveAction is exported and the imported one (re-export round trip)", () => {
	assert.deepEqual(approveAction({ mode: "direct", message: null }), { kind: "button", message: null });
});
