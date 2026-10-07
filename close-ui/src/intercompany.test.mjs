import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { intercompanyView, sendBackBody, panel, bannerToneClass } from "./intercompany.js";
import { remindedText } from "./remind.js";

// --- fixtures --------------------------------------------------------------

const NOT_CONFIGURED = "Intercompany not configured — nothing was checked.";
const SETUP_HELP = (
	NOT_CONFIGURED +
	" No Intercompany Account is Published. Publish each pairing in Intercompany Account." +
	" Each account must first allow intercompany in the group chart" +
	" (Main Account → Allow Intercompany); otherwise publishing is refused:" +
	" \"The group chart has not declared allow_ic on <accounts>." +
	" Set Allow Intercompany on those accounts before publishing this pairing.\""
);
const NOT_BUILT = "The warehouse has not built the intercompany tables yet — nothing was checked.";
const NOT_APPLICABLE = "Intercompany: none in this group (declared in Close Settings) — not applicable.";

const NOW = new Date("2026-10-03T12:00:00Z");
const ZONE = "UTC";

function pairRow(overrides) {
	return {
		consolidation_group: "ROOT",
		entity_a: "UK01",
		account_a: "1810",
		entity_b: "DE01",
		account_b: "2810",
		currency_a: "GBP",
		currency_b: "EUR",
		local_a: 1000,
		local_b: -950,
		balance_a: 1200,
		balance_b: -1200,
		group_balance_a: 1200,
		group_balance_b: -1200,
		share_a: 1200,
		share_b: -1200,
		difference: 0,
		difference_cause: null,
		residual_a: 0,
		residual_b: 0,
		match_status: "matched",
		masked_a: false,
		masked_b: false,
		sent_back: null,
		can_send_back: false,
		reminders_a: null,
		reminders_b: null,
		...overrides,
	};
}

function basePayload(overrides) {
	return {
		period: { fiscal_year: 2025, fiscal_period: 7, period_code: "2025-P07", status: "Open" },
		state: "checked",
		message: null,
		help: null,
		published: 3,
		declared_none: false,
		groups: [],
		unmatched: [],
		counts: { pairs: 0, matched: 0, within_tolerance: 0, fx_difference: 0, over_tolerance: 0, unmatched: 0 },
		hidden: { pairs: 0, unmatched: 0 },
		can_send_back: false,
		can_remind: false,
		...overrides,
	};
}

function walkStrings(value, out) {
	if (typeof value === "string") {
		out.push(value);
	} else if (Array.isArray(value)) {
		for (const v of value) walkStrings(v, out);
	} else if (value && typeof value === "object") {
		for (const v of Object.values(value)) walkStrings(v, out);
	}
	return out;
}

// --- not configured ---------------------------------------------------------

test("not configured: block banner, help lines, no chips, no groups, never reads as reconciled", () => {
	const payload = basePayload({
		state: "not_configured",
		message: NOT_CONFIGURED,
		help: SETUP_HELP,
		published: 0,
		groups: [],
		unmatched: [],
		counts: null,
	});
	const view = intercompanyView(payload, NOW, ZONE);

	assert.equal(view.banner.tone, "block");
	assert.ok(view.banner.lines.includes(NOT_CONFIGURED));
	assert.ok(view.banner.lines.some((line) => line.includes("Allow Intercompany")));
	assert.equal(view.chips, null);
	assert.deepEqual(view.groups, []);

	const strings = walkStrings(view, []);
	for (const s of strings) {
		assert.doesNotMatch(s, /reconciled|within tolerance|matched/i);
	}
});

// --- error / not_built -------------------------------------------------------

test("not_built: no chips, the message shown, block tone", () => {
	const payload = basePayload({
		state: "not_built",
		message: NOT_BUILT,
		help: null,
		groups: [],
		unmatched: [],
		counts: null,
	});
	const view = intercompanyView(payload, NOW, ZONE);
	assert.equal(view.chips, null);
	assert.equal(view.banner.tone, "block");
	assert.ok(view.banner.lines.includes(NOT_BUILT));
});

test("error: no chips, the message shown, block tone", () => {
	const payload = basePayload({
		state: "error",
		message: "Intercompany could not be checked: RuntimeError. Rebuild the consolidation, then open this again.",
		help: null,
		groups: [],
		unmatched: [],
		counts: null,
	});
	const view = intercompanyView(payload, NOW, ZONE);
	assert.equal(view.chips, null);
	assert.equal(view.banner.tone, "block");
	assert.ok(view.banner.lines[0].includes("could not be checked"));
});

// --- checked ------------------------------------------------------------------

test("checked: chips from counts, group order kept, status texts, sent-back pair text", () => {
	const sentBackPair = pairRow({
		account_a: "1811",
		match_status: "over_tolerance",
		sent_back: { by: "analyst@example.com", by_name: "Ana Lyst", at: "2026-09-29T08:15:00Z", reason: "checking" },
	});
	const payload = basePayload({
		state: "checked",
		groups: [
			{
				consolidation_group: "ROOT",
				reporting_currency: "GBP",
				tolerance: 10,
				ic_difference_account: "7999",
				tolerance_declared: true,
				pairs: [sentBackPair, pairRow({ match_status: "within_tolerance" })],
			},
			{
				consolidation_group: "SUB",
				reporting_currency: "EUR",
				tolerance: 5,
				ic_difference_account: null,
				tolerance_declared: true,
				pairs: [pairRow({ consolidation_group: "SUB", match_status: "fx_difference" })],
			},
		],
		unmatched: [],
		counts: { pairs: 3, matched: 0, within_tolerance: 1, fx_difference: 1, over_tolerance: 1, unmatched: 0 },
		hidden: { pairs: 0, unmatched: 0 },
	});

	const view = intercompanyView(payload, NOW, ZONE);

	assert.deepEqual(view.chips, { pairs: 3, within: 1, differences: 1, fx: 1, unmatched: 0 });
	assert.deepEqual(view.groups.map((g) => g.consolidationGroup), ["ROOT", "SUB"]);

	const [root, sub] = view.groups;
	// U5: a sent-back pair keeps its real status (never "Sent to both"),
	// with the sent-back time appended and the status's own tone.
	assert.equal(root.pairs[0].statusText, "Over tolerance · sent back Sep 29, 08:15");
	assert.notEqual(root.pairs[0].statusText, "Sent to both · Sep 29, 08:15");
	assert.equal(root.pairs[0].statusTone, "block");
	assert.equal(root.pairs[1].statusText, "Within tolerance");
	assert.equal(root.pairs[1].statusTone, "ok");
	assert.equal(sub.pairs[0].statusText, "FX difference");
	assert.equal(sub.pairs[0].statusTone, "warn");
});

test("U5: sent-back pairs across every match status keep STATUS_TEXT and append the sent-back time, never 'Sent to both'", () => {
	const sentBack = { by: "a@x.com", by_name: "A", at: "2026-09-29T08:15:00Z", reason: "checking" };
	for (const [status, label] of [
		["matched", "Matched"],
		["within_tolerance", "Within tolerance"],
		["fx_difference", "FX difference"],
		["over_tolerance", "Over tolerance"],
	]) {
		const payload = basePayload({
			state: "checked",
			groups: [{
				consolidation_group: "ROOT",
				reporting_currency: "GBP",
				tolerance: 10,
				ic_difference_account: null,
				tolerance_declared: true,
				pairs: [pairRow({ match_status: status, sent_back: sentBack })],
			}],
			counts: { pairs: 1, matched: 0, within_tolerance: 0, fx_difference: 0, over_tolerance: 0, unmatched: 0 },
		});
		const view = intercompanyView(payload, NOW, ZONE);
		const pair = view.groups[0].pairs[0];
		assert.equal(pair.statusText, `${label} · sent back Sep 29, 08:15`);
		assert.doesNotMatch(pair.statusText, /Sent to both/);
	}
});

// --- W3-2 masking --------------------------------------------------------------

test("W3-2: a masked side never shows an amount, shows the hidden label instead", () => {
	const masked = pairRow({ masked_b: true, balance_b: null, local_b: null, currency_b: null, group_balance_b: null, share_b: null, residual_b: null });
	const payload = basePayload({
		state: "checked",
		groups: [{
			consolidation_group: "ROOT",
			reporting_currency: "GBP",
			tolerance: 10,
			ic_difference_account: "7999",
			tolerance_declared: true,
			pairs: [masked],
		}],
		counts: { pairs: 1, matched: 1, within_tolerance: 0, fx_difference: 0, over_tolerance: 0, unmatched: 0 },
	});

	const view = intercompanyView(payload, NOW, ZONE);
	const pair = view.groups[0].pairs[0];
	assert.equal(pair.balanceBText, "Hidden: outside your entities");
	assert.notEqual(pair.balanceBText, "0.00");

	const sidePanel = panel(pair);
	assert.equal(sidePanel.sideB.amount, "Hidden: outside your entities");
	assert.notEqual(sidePanel.sideB.amount, "0.00");
});

// --- U6: formatted difference ---------------------------------------------------

test("U6: pairView carries a formatted differenceText, mirroring the balance columns", () => {
	const payload = basePayload({
		state: "checked",
		groups: [{
			consolidation_group: "ROOT",
			reporting_currency: "GBP",
			tolerance: 10,
			ic_difference_account: null,
			tolerance_declared: true,
			pairs: [pairRow({ difference: -1234.5 })],
		}],
		counts: { pairs: 1, matched: 1, within_tolerance: 0, fx_difference: 0, over_tolerance: 0, unmatched: 0 },
	});
	const view = intercompanyView(payload, NOW, ZONE);
	const pair = view.groups[0].pairs[0];
	assert.equal(pair.differenceText, "(1,234.50)");
	assert.notEqual(pair.differenceText, -1234.5);
});

// --- U10: an unmasked null never reads as zero -----------------------------------

test("U10: an unmasked null balance reads 'not reported', never '0.00'", () => {
	const payload = basePayload({
		state: "checked",
		groups: [{
			consolidation_group: "ROOT",
			reporting_currency: "GBP",
			tolerance: 10,
			ic_difference_account: null,
			tolerance_declared: true,
			pairs: [pairRow({ balance_a: null, masked_a: false })],
		}],
		counts: { pairs: 1, matched: 1, within_tolerance: 0, fx_difference: 0, over_tolerance: 0, unmatched: 0 },
	});
	const view = intercompanyView(payload, NOW, ZONE);
	const pair = view.groups[0].pairs[0];
	assert.equal(pair.balanceAText, "not reported");
	assert.notEqual(pair.balanceAText, "0.00");

	const sidePanel = panel(pair);
	assert.equal(sidePanel.sideA.amount, "not reported");
	assert.notEqual(sidePanel.sideA.amount, "0.00");
});

test("U10: a null unmatched amount reads 'not reported', never '0.00'", () => {
	const payload = basePayload({
		state: "checked",
		groups: [],
		unmatched: [{
			consolidation_group: "ROOT",
			data_area_id: "UK01",
			main_account: "1810",
			counterpart_account: "2810",
			unmatched_local_amount: null,
			unmatched_amount: null,
		}],
		counts: { pairs: 0, matched: 0, within_tolerance: 0, fx_difference: 0, over_tolerance: 0, unmatched: 1 },
	});
	const view = intercompanyView(payload, NOW, ZONE);
	const row = view.unmatched[0];
	assert.equal(row.amount, "not reported (group view, after ownership)");
	assert.equal(row.local, "not reported (entity currency)");
	assert.doesNotMatch(row.amount, /0\.00/);
	assert.doesNotMatch(row.local, /0\.00/);
});

// --- U12: the not_applicable banner gets its own tone, not amber ------------------

test("U12: bannerToneClass gives 'ok' its own class, distinct from 'block' and from amber/warn styling", () => {
	const okClass = bannerToneClass("ok");
	const blockClass = bannerToneClass("block");
	assert.notEqual(okClass, blockClass);
	assert.doesNotMatch(okClass, /amber/, "the not_applicable/ok banner must not render with warn-amber styling");
});

test("U12: bannerToneClass throws on an unknown tone, naming it", () => {
	assert.throws(() => bannerToneClass("mystery"), /mystery/);
});

test("U12: a not_applicable banner's tone renders its own class via bannerToneClass, not the block class", () => {
	const payload = basePayload({
		state: "not_applicable",
		message: "Intercompany: none in this group (declared in Close Settings) — not applicable.",
		help: null,
		groups: [],
		unmatched: [],
		counts: null,
	});
	const view = intercompanyView(payload, NOW, ZONE);
	assert.equal(view.banner.tone, "ok");
	assert.notEqual(bannerToneClass(view.banner.tone), bannerToneClass("block"));
});

// --- U9: a pure test feeding intercompanyView's output the way Intercompany.vue uses it ---

test("U9: Intercompany.vue's own reads of intercompanyView's output — banner class and the difference cell", () => {
	// The template renders the banner's class via bannerToneClass(view.banner.tone)
	// and the difference cell via {{ pair.differenceText }} — exercise both exactly
	// as the screen does, rather than grepping the .vue source.
	const notApplicable = intercompanyView(
		basePayload({
			state: "not_applicable",
			message: "Intercompany: none in this group (declared in Close Settings) — not applicable.",
			help: null,
			groups: [],
			unmatched: [],
			counts: null,
		}),
		NOW,
		ZONE,
	);
	assert.doesNotThrow(() => bannerToneClass(notApplicable.banner.tone));

	const checked = intercompanyView(
		basePayload({
			state: "checked",
			groups: [{
				consolidation_group: "ROOT",
				reporting_currency: "GBP",
				tolerance: 10,
				ic_difference_account: null,
				tolerance_declared: true,
				pairs: [pairRow({ difference: -1234.5, balance_a: null, masked_a: false })],
			}],
			counts: { pairs: 1, matched: 1, within_tolerance: 0, fx_difference: 0, over_tolerance: 0, unmatched: 0 },
		}),
		NOW,
		ZONE,
	);
	const pair = checked.groups[0].pairs[0];
	// The template reads these fields directly — never the raw pair.difference
	// or balance_a, and never falls back to a masked/blank/"0.00" look.
	assert.equal(pair.differenceText, "(1,234.50)");
	assert.equal(pair.balanceAText, "not reported");
	assert.doesNotThrow(() => bannerToneClass(checked.banner.tone));
});

// --- panel ---------------------------------------------------------------------

test("panel: the difference-account sentence when an account is declared", () => {
	const pair = pairRow({ ic_difference_account: "7999", difference: 50, canRemind: false, remindedAText: null, remindedBText: null });
	const result = panel(pair);
	assert.equal(result.accountSentence, "Booked to 7999 in the group view while it stays open.");
});

test("panel: the difference-account sentence when no account is declared", () => {
	const pair = pairRow({ ic_difference_account: null, difference: 50, canRemind: false, remindedAText: null, remindedBText: null });
	const result = panel(pair);
	assert.equal(
		result.accountSentence,
		"No difference account is declared: the difference stays on the intercompany accounts.",
	);
});

// --- sendBackBody ----------------------------------------------------------------

test("sendBackBody: exactly the seven keys the server accepts", () => {
	const period = { fiscal_year: 2025, fiscal_period: 7 };
	const pair = pairRow({});
	const out = sendBackBody(period, pair, "over tolerance, chasing DE01");
	assert.deepEqual(Object.keys(out.body).sort(), [
		"account_a", "account_b", "entity_a", "entity_b", "fiscal_period", "fiscal_year", "reason",
	].sort());
	assert.equal(out.body.entity_a, "UK01");
	assert.equal(out.body.entity_b, "DE01");
	assert.equal(out.body.fiscal_year, 2025);
	assert.equal(out.body.fiscal_period, 7);
	assert.equal(out.body.reason, "over tolerance, chasing DE01");
	for (const key of ["difference", "actor", "entity", "kind"]) {
		assert.ok(!(key in out.body), `unexpected key ${key}`);
	}
});

test("sendBackBody: failure path — a blank reason is refused", () => {
	const period = { fiscal_year: 2025, fiscal_period: 7 };
	const pair = pairRow({});
	const out = sendBackBody(period, pair, "");
	assert.equal(out.body, undefined);
	assert.equal(out.error, "Say why the difference is sent back: the entities read this reason.");
});

test("sendBackBody: failure path — a whitespace-only reason is refused", () => {
	const period = { fiscal_year: 2025, fiscal_period: 7 };
	const pair = pairRow({});
	const out = sendBackBody(period, pair, "   ");
	assert.equal(out.body, undefined);
	assert.equal(out.error, "Say why the difference is sent back: the entities read this reason.");
});

// --- unknown values throw, naming them ----------------------------------------------

test("failure path: an unknown state throws naming it", () => {
	const payload = basePayload({ state: "mystery", message: "x" });
	assert.throws(() => intercompanyView(payload, NOW, ZONE), /mystery/);
});

test("failure path: an unknown match_status throws naming it", () => {
	const payload = basePayload({
		state: "checked",
		groups: [{
			consolidation_group: "ROOT",
			reporting_currency: "GBP",
			tolerance: 10,
			ic_difference_account: "7999",
			tolerance_declared: true,
			pairs: [pairRow({ match_status: "close_enough" })],
		}],
		counts: { pairs: 1, matched: 0, within_tolerance: 0, fx_difference: 0, over_tolerance: 0, unmatched: 0 },
	});
	assert.throws(() => intercompanyView(payload, NOW, ZONE), /close_enough/);
});

// --- amended 3 Oct: not_applicable --------------------------------------------------

test("not_applicable: ok banner, no chips, no groups, never reads as reconciled or not configured", () => {
	const payload = basePayload({
		state: "not_applicable",
		message: NOT_APPLICABLE,
		help: null,
		published: 0,
		declared_none: true,
		groups: [],
		unmatched: [],
		counts: null,
	});
	const view = intercompanyView(payload, NOW, ZONE);

	assert.equal(view.banner.tone, "ok");
	assert.deepEqual(view.banner.lines, [NOT_APPLICABLE]);
	assert.equal(view.chips, null);
	assert.deepEqual(view.groups, []);

	const strings = walkStrings(view, []);
	for (const s of strings) {
		assert.doesNotMatch(s, /reconciled|not configured|within tolerance|matched/i);
	}
});

// --- amended 3 Oct: tolerance declared / undeclared ---------------------------------

test("W3-6: a group's tolerance header, declared", () => {
	const payload = basePayload({
		state: "checked",
		groups: [{
			consolidation_group: "ROOT",
			reporting_currency: "GBP",
			tolerance: 25,
			ic_difference_account: "7999",
			tolerance_declared: true,
			pairs: [pairRow({ match_status: "over_tolerance" })],
		}],
		counts: { pairs: 1, matched: 0, within_tolerance: 0, fx_difference: 0, over_tolerance: 1, unmatched: 0 },
	});
	const view = intercompanyView(payload, NOW, ZONE);
	assert.equal(view.groups[0].toleranceText, "tolerance 25.00");
	assert.equal(view.groups[0].pairs[0].statusText, "Over tolerance");
});

test("W3-6: a group's tolerance header, undeclared — never '0' and never 'tolerance 0.00'", () => {
	const payload = basePayload({
		state: "checked",
		groups: [{
			consolidation_group: "ROOT",
			reporting_currency: "GBP",
			tolerance: 0,
			ic_difference_account: "7999",
			tolerance_declared: false,
			pairs: [pairRow({ match_status: "over_tolerance" })],
		}],
		counts: { pairs: 1, matched: 0, within_tolerance: 0, fx_difference: 0, over_tolerance: 1, unmatched: 0 },
	});
	const view = intercompanyView(payload, NOW, ZONE);
	assert.equal(view.groups[0].toleranceText, "Tolerance not declared (0 is undeclared)");
	assert.notEqual(view.groups[0].toleranceText, "tolerance 0.00");
	assert.equal(view.groups[0].pairs[0].statusText, "Over tolerance · tolerance not declared");
});

// --- unmatched rows and hidden note -------------------------------------------------

test("unmatched rows: entity, account, group-view and local-currency amounts, and the ask line", () => {
	const payload = basePayload({
		state: "checked",
		groups: [],
		unmatched: [{
			consolidation_group: "ROOT",
			data_area_id: "UK01",
			main_account: "1810",
			counterpart_account: "2810",
			unmatched_local_amount: -500,
			unmatched_amount: -480,
		}],
		counts: { pairs: 0, matched: 0, within_tolerance: 0, fx_difference: 0, over_tolerance: 0, unmatched: 1 },
	});
	const view = intercompanyView(payload, NOW, ZONE);
	assert.equal(view.unmatched.length, 1);
	const row = view.unmatched[0];
	assert.equal(row.entity, "UK01");
	assert.equal(row.account, "1810");
	assert.equal(row.amount, "(480.00) (group view, after ownership)");
	assert.equal(row.local, "(500.00) (entity currency)");
	assert.equal(row.note, "No partner · ask UK01");
});

test("hiddenNote: both counts > 0", () => {
	const payload = basePayload({ hidden: { pairs: 2, unmatched: 3 } });
	const view = intercompanyView(payload, NOW, ZONE);
	assert.equal(view.hiddenNote, "2 pairs and 3 partnerless rows for entities outside your scope are not shown");
});

test("hiddenNote: null when nothing is hidden", () => {
	const payload = basePayload({ hidden: { pairs: 0, unmatched: 0 } });
	const view = intercompanyView(payload, NOW, ZONE);
	assert.equal(view.hiddenNote, null);
});

// --- hygiene ---------------------------------------------------------------------

test("the source imports no vue, frappe or xstate", () => {
	const path = fileURLToPath(new URL("./intercompany.js", import.meta.url));
	const source = readFileSync(path, "utf8");
	for (const term of ["vue", "frappe", "xstate"]) {
		assert.ok(!source.includes(`"${term}`) && !source.includes(`'${term}`), `unexpected import of ${term}`);
	}
});

// --- konsol#305 5.4 (W5-4): IC Balances (unrealised profit) ------------------

import { icBalancesView, icBalanceBody } from "./intercompany.js";

const GAP_MESSAGE =
	"No unrealised-profit IC Elimination Rule (rule type unrealized_profit, margin above 0) " +
	"matches 1 IC Balance pair: FR01 → DE01. Its unrealised profit is not eliminated: declare the " +
	"rule in Desk (IC Elimination Rule) before signing off.";

function balancesPayload(overrides) {
	return {
		period: { fiscal_year: 2025, fiscal_period: 7, period_code: "P07", status: "Open" },
		balances: [
			{
				name: "ICB-FR01-DE01-2025-P7", selling_entity: "FR01", buying_entity: "DE01",
				fiscal_year: 2025, fiscal_period: 7, ic_sales_amount: 1000, ending_inventory_from_ic: 250.5,
				status: "Approved", rules: [], missing_rule: true, pending_rule: false,
			},
			{
				name: "ICB-UK01-DE01-2025-P7", selling_entity: "UK01", buying_entity: "DE01",
				fiscal_year: 2025, fiscal_period: 7, ic_sales_amount: 1234.5, ending_inventory_from_ic: 0,
				status: "Draft", rules: [{ rule_id: "R-UK", rule_name: "UK margin", margin_pct: 12.5 }],
				missing_rule: false, pending_rule: false,
			},
		],
		gap: {
			code: "ic_unrealized_profit_rule_undeclared",
			pairs: [{ selling_entity: "FR01", buying_entity: "DE01" }],
			entities: ["DE01", "FR01"],
			message: GAP_MESSAGE,
		},
		ambiguous_gap: null,
		pending_gap: null,
		hidden: 0,
		entities: ["DE01", "FR01", "UK01"],
		can_draft: true,
		rules_desk: "/app/ic-elimination-rule",
		...overrides,
	};
}

test("icBalancesView: rows carry the pair, status, amounts and the rule's margin read-only", () => {
	const view = icBalancesView(balancesPayload());
	const [fr, uk] = view.rows;
	assert.equal(fr.pair, "FR01 → DE01");
	assert.equal(fr.statusText, "Approved");
	assert.equal(fr.salesText, "1,000.00");
	assert.equal(fr.inventoryText, "250.50");
	assert.equal(fr.marginText, "No unrealised-profit rule: nothing is eliminated");
	assert.equal(fr.missingRule, true);
	assert.equal(fr.editable, false);
	assert.equal(uk.statusText, "Draft");
	assert.equal(uk.marginText, "12.5% (UK margin)");
	assert.equal(uk.missingRule, false);
	assert.equal(uk.editable, true);
});

test("icBalancesView: the gap is shown naming each pair, with the Desk link", () => {
	const view = icBalancesView(balancesPayload());
	assert.deepEqual(view.gap.pairs, ["FR01 → DE01"]);
	assert.deepEqual(view.gap.lines, [GAP_MESSAGE]);
	assert.equal(view.rulesDesk, "/app/ic-elimination-rule");
});

test("icBalancesView: no gap, no balances, hidden note", () => {
	const view = icBalancesView(balancesPayload({ gap: null, balances: [], hidden: 2 }));
	assert.equal(view.gap, null);
	assert.deepEqual(view.rows, []);
	assert.equal(view.hiddenNote, "2 IC Balances for entities outside your scope are not shown");
	assert.equal(icBalancesView(balancesPayload()).hiddenNote, null);
});

test("icBalancesView: a draft is editable only when the caller can draft", () => {
	const view = icBalancesView(balancesPayload({ can_draft: false }));
	assert.equal(view.canDraft, false);
	assert.ok(view.rows.every((r) => r.editable === false));
});

test("icBalancesView: more than one matching rule shows every margin", () => {
	const payload = balancesPayload();
	payload.balances[1].rules.push({ rule_id: "R-ALL", rule_name: "R-ALL", margin_pct: 5 });
	assert.equal(icBalancesView(payload).rows[1].marginText, "12.5% (UK margin), 5% (R-ALL)");
});

test("Failure path — icBalancesView throws on an unknown status, naming it", () => {
	const payload = balancesPayload();
	payload.balances[0].status = "Cancelled";
	assert.throws(() => icBalancesView(payload), /Unknown IC Balance status: Cancelled/);
});

test("icBalanceBody: exactly the endpoint's keys, amounts as typed, name only when editing", () => {
	const period = { fiscal_year: 2025, fiscal_period: 7 };
	const form = { selling_entity: "UK01", buying_entity: "DE01", ic_sales_amount: "1000", ending_inventory_from_ic: "250" };
	assert.deepEqual(icBalanceBody(period, form), {
		body: {
			fiscal_year: 2025, fiscal_period: 7, selling_entity: "UK01", buying_entity: "DE01",
			ic_sales_amount: "1000", ending_inventory_from_ic: "250",
		},
	});
	const edit = icBalanceBody(period, { ...form, name: "ICB-UK01-DE01-2025-P7", docstatus: 1, status: "Approved" });
	assert.deepEqual(Object.keys(edit.body).sort(), [
		"buying_entity", "ending_inventory_from_ic", "fiscal_period", "fiscal_year",
		"ic_sales_amount", "name", "selling_entity",
	]);
	assert.equal(edit.body.name, "ICB-UK01-DE01-2025-P7");
});

test("Failure path — icBalanceBody refuses blank/same entities and bad amounts before any request", () => {
	const period = { fiscal_year: 2025, fiscal_period: 7 };
	const ok = { selling_entity: "UK01", buying_entity: "DE01", ic_sales_amount: "1", ending_inventory_from_ic: "1" };
	assert.match(icBalanceBody(period, { ...ok, selling_entity: "" }).error, /selling entity/);
	assert.match(icBalanceBody(period, { ...ok, buying_entity: "UK01" }).error, /same entity/);
	assert.match(icBalanceBody(period, { ...ok, ic_sales_amount: "abc" }).error, /IC sales amount/);
	assert.match(icBalanceBody(period, { ...ok, ending_inventory_from_ic: "-3" }).error, /negative/);
	assert.match(icBalanceBody(period, { ...ok, ending_inventory_from_ic: "" }).error, /ending inventory/);
});

// F51b / review S2: two rules on one pair are both applied by dbt, so the
// pair is eliminated twice. The server sends that as `ambiguous_gap`.
const AMBIGUOUS_MESSAGE =
	"1 IC Balance pair matches more than one unrealised-profit IC Elimination Rule: " +
	"UK01 → DE01 (R-ALL, R-UK). dbt applies every matching rule, so its unrealised profit " +
	"is eliminated more than once: keep one rule per pair in Desk (IC Elimination Rule) " +
	"before signing off.";

test("icBalancesView: the ambiguous-rule gap names each pair with its rules", () => {
	const view = icBalancesView(
		balancesPayload({
			ambiguous_gap: {
				code: "ic_unrealized_profit_rule_ambiguous",
				pairs: [{ selling_entity: "UK01", buying_entity: "DE01", rule_ids: ["R-ALL", "R-UK"] }],
				entities: ["DE01", "UK01"],
				message: AMBIGUOUS_MESSAGE,
			},
		}),
	);
	assert.deepEqual(view.ambiguousGap.pairs, ["UK01 → DE01 (R-ALL, R-UK)"]);
	assert.deepEqual(view.ambiguousGap.lines, [AMBIGUOUS_MESSAGE]);
});

test("icBalancesView: no ambiguous gap is null; a row two rules match is flagged", () => {
	assert.equal(icBalancesView(balancesPayload()).ambiguousGap, null);
	assert.equal(icBalancesView(balancesPayload({ ambiguous_gap: null })).ambiguousGap, null);
	const payload = balancesPayload();
	payload.balances[1] = { ...payload.balances[1], ambiguous_rule: true };
	const [fr, uk] = icBalancesView(payload).rows;
	assert.equal(uk.ambiguousRule, true);
	assert.equal(fr.ambiguousRule, false);
});

// --- konsol#305 review-w5 U5: no silent fallbacks; the real producer's shape --

// The golden fixture is the real `ic_balance_api.get_ic_balances` payload
// (test_close_ic_balance_api.py::test_get_matches_the_golden_fixture).
const IC_BALANCES_FIXTURE_PATH = fileURLToPath(
	new URL("../../konsol/tests/fixtures/close_ic_balances_payload.json", import.meta.url),
);
function goldenBalances() {
	return JSON.parse(readFileSync(IC_BALANCES_FIXTURE_PATH, "utf8"));
}

test("U5: icBalancesView reads the real get_ic_balances payload", () => {
	const golden = goldenBalances();
	const view = icBalancesView(golden);
	assert.deepEqual(view.rows.map((r) => r.pair), golden.balances.map((b) => `${b.selling_entity} → ${b.buying_entity}`));
	assert.ok(view.rows.length > 0, "the fixture carries at least one balance");
	assert.equal(view.hiddenNote, `${golden.hidden} IC Balances for entities outside your scope are not shown`);
	assert.ok(golden.hidden > 0, "the fixture hides at least one balance");
	assert.deepEqual(view.entities, golden.entities);
	assert.equal(view.canDraft, golden.can_draft);
	assert.equal(view.rulesDesk, golden.rules_desk);
});

for (const key of ["hidden", "balances", "entities", "can_draft"]) {
	test(`U5: icBalancesView throws naming \`${key}\` when it is missing (never guessed)`, () => {
		const payload = goldenBalances();
		delete payload[key];
		assert.throws(() => icBalancesView(payload), new RegExp(key));
	});
}

// F51b: `gap` and `ambiguous_gap` are always sent, null when there is none.
for (const key of ["gap", "ambiguous_gap"]) {
	test(`F51b: icBalancesView throws naming \`${key}\` when it is missing; null is "no gap"`, () => {
		const payload = goldenBalances();
		assert.ok(key in payload, `the golden fixture carries \`${key}\``);
		delete payload[key];
		assert.throws(() => icBalancesView(payload), new RegExp(key));
		assert.doesNotThrow(() => icBalancesView({ ...goldenBalances(), [key]: null }));
	});
}

// --- konsol#305 I54 (S8, #305-S8-1): a draft IC Balance with a rule blocks sign-off --
//
// The server's `pending_gap` (ic_balance_model.pending_gap via get_ic_balances)
// names each draft whose pair has a matching unrealised-profit rule. dbt
// eliminates only approved balances, so the screen shows the gap like the two
// rule gaps, and marks each such draft row. Coordinator call: the row note
// reads "approve, or delete the draft".

const PENDING_NOTE = "Blocks sign-off: approve, or delete the draft";

test("I54: icBalancesView shows the real pending_gap, naming the fixture's draft pair and its drafts", () => {
	const golden = goldenBalances();
	assert.ok(golden.pending_gap, "the golden fixture carries a pending gap");
	const view = icBalancesView(golden);
	assert.deepEqual(
		view.pendingGap.pairs,
		golden.pending_gap.pairs.map((p) => `${p.selling_entity} → ${p.buying_entity} (${p.names.join(", ")})`),
	);
	assert.deepEqual(view.pendingGap.pairs, ["UK01 → DE01 (ICB-UK01-DE01-2025-P7)"]);
	assert.deepEqual(view.pendingGap.lines, [golden.pending_gap.message]);
});

test("I54: no pending gap is null (the declared \"no gap\")", () => {
	assert.equal(icBalancesView({ ...goldenBalances(), pending_gap: null }).pendingGap, null);
	assert.equal(icBalancesView(balancesPayload()).pendingGap, null);
});

test("Failure path — I54: a payload without `pending_gap` throws, naming it", () => {
	const payload = goldenBalances();
	delete payload.pending_gap;
	assert.throws(() => icBalancesView(payload), /pending_gap/);
});

test("I54: the fixture's draft with a matching rule carries the blocks-sign-off note; the one with no rule does not", () => {
	const golden = goldenBalances();
	const view = icBalancesView(golden);
	const byName = Object.fromEntries(view.rows.map((r) => [r.name, r]));
	assert.equal(byName["ICB-UK01-DE01-2025-P7"].pendingNote, PENDING_NOTE);
	// no matching rule: the undeclared gap covers it, not this note
	assert.equal(byName["ICB-DE01-UK01-2025-P7"].pendingNote, null);
	// every pending pair's drafts are noted
	const noted = view.rows.filter((r) => r.pendingNote).map((r) => r.name).sort();
	assert.deepEqual(noted, golden.pending_gap.pairs.flatMap((p) => p.names).sort());
});

// --- konsol#305 R52n (review U7, coordinator ruling S8/U7): the row note
// reads the server's per-row `pending_rule` flag (R52k). The SPA no longer
// re-derives the rule from status, rules and inventory.

test("R52n: on the real payload, the rows with a note are exactly those with pending_rule", () => {
	const golden = goldenBalances();
	assert.ok(golden.balances.some((b) => b.pending_rule === true), "the golden has a pending row");
	assert.ok(golden.balances.some((b) => b.pending_rule === false), "the golden has a row that is not pending");
	const view = icBalancesView(golden);
	const noted = view.rows.filter((r) => r.pendingNote !== null).map((r) => r.name).sort();
	assert.deepEqual(noted, golden.balances.filter((b) => b.pending_rule).map((b) => b.name).sort());
	for (const row of view.rows) {
		assert.ok(row.pendingNote === null || row.pendingNote === PENDING_NOTE, row.name);
	}
});

test("Failure path — R52n: the server says no (pending_rule false) on a draft with a rule and inventory > 0: no note", () => {
	const golden = goldenBalances();
	const ruled = golden.balances.find((b) => b.pending_rule === true);
	assert.equal(ruled.status, "Draft");
	assert.ok(ruled.rules.length > 0 && Number(ruled.ending_inventory_from_ic) > 0, "the flipped row would be re-derived as pending");
	const [view] = icBalancesView({ ...golden, balances: [{ ...ruled, pending_rule: false }] }).rows;
	assert.equal(view.pendingNote, null);
});

test("R52n: the server says yes (pending_rule true): the note shows, whatever the other fields", () => {
	const golden = goldenBalances();
	const plain = golden.balances.find((b) => b.pending_rule === false);
	const [view] = icBalancesView({ ...golden, balances: [{ ...plain, pending_rule: true }] }).rows;
	assert.equal(view.pendingNote, PENDING_NOTE);
});

test("Failure path — R52n: a row without pending_rule, or with a non-boolean one, throws naming it", () => {
	const golden = goldenBalances();
	const ruled = golden.balances.find((b) => b.pending_rule === true);
	const missing = { ...ruled };
	delete missing.pending_rule;
	assert.throws(() => icBalancesView({ ...golden, balances: [missing] }), /pending_rule/);
	for (const bad of [null, 1, 0, "true", "", undefined]) {
		assert.throws(
			() => icBalancesView({ ...golden, balances: [{ ...ruled, pending_rule: bad }] }),
			/pending_rule/,
			String(bad),
		);
	}
});

// --- konsol#305 Y65: Remind each side, and the reminded text (stories 1.5, 5.2; C-R1) ---
//
// Fed the REAL producer's output: konsol/tests/fixtures/close_ic_payload.json
// is `ic_api.get_ic` called on test_close_ic_api.py's stub site (Y60's
// `_reminded_site()`, plus an ic reminder on FR01, caller scoped to UK01 and
// DE01, so FR01's side is masked and its reminder is nulled by the server).

const IC_FIXTURE_PATH = fileURLToPath(new URL("../../konsol/tests/fixtures/close_ic_payload.json", import.meta.url));

function icFixture() {
	return JSON.parse(readFileSync(IC_FIXTURE_PATH, "utf8"));
}

function fixturePair(view, entityA, entityB) {
	for (const group of view.groups) {
		for (const pair of group.pairs) {
			if (pair.entity_a === entityA && pair.entity_b === entityB) return pair;
		}
	}
	throw new Error(`no pair ${entityA} ↔ ${entityB} in the fixture`);
}

test("Y65: panel over a pair with reminders_b gives the reminded text on B only", () => {
	const payload = icFixture();
	const raw = payload.groups.flatMap((g) => g.pairs).find((p) => p.entity_a === "UK01" && p.entity_b === "DE01");
	assert.equal(raw.reminders_a, null, "the fixture's UK01 side has no reminder");
	assert.ok(raw.reminders_b, "the fixture's DE01 side has one");
	const view = intercompanyView(payload, NOW, ZONE);
	const side = panel(fixturePair(view, "UK01", "DE01"));
	const expected = remindedText(raw.reminders_b, NOW, ZONE);
	assert.match(expected, /^Reminded 1× · last .+ by Zz Lead$/);
	assert.deepEqual(side.remindB, { entity: "DE01", text: expected, canRemind: true });
	assert.deepEqual(side.remindA, { entity: "UK01", text: null, canRemind: true });
});

test("Y65: the A side's reminder is on A only (DE01 ↔ FR01)", () => {
	const view = intercompanyView(icFixture(), NOW, ZONE);
	const side = panel(fixturePair(view, "DE01", "FR01"));
	assert.match(side.remindA.text, /^Reminded 1× · last .+ by Zz Lead$/);
	assert.equal(side.remindA.canRemind, true);
	assert.equal(side.remindB.text, null);
});

test("Y65 failure path: a masked side has canRemind false and no entity code in its text", () => {
	const payload = icFixture();
	const view = intercompanyView(payload, NOW, ZONE);
	const pair = fixturePair(view, "UK01", "FR01");
	assert.equal(pair.masked_b, true, "the fixture masks FR01");
	const side = panel(pair);
	assert.equal(side.remindB.canRemind, false);
	assert.equal(side.remindB.text, null);
	assert.equal(side.remindA.canRemind, true, "the visible side can still be reminded");
	// Even if a masked side ever arrived with a reminders entry, nothing of it is shown.
	const leaked = structuredClone(payload);
	const leakedPair = leaked.groups.flatMap((g) => g.pairs).find((p) => p.entity_a === "UK01" && p.entity_b === "FR01");
	leakedPair.reminders_b = { count: 3, last_at: "2025-08-05T09:00:00+01:00", last_by: "zz-x@example.com", last_by_name: "Zz Hidden" };
	const leakedSide = panel(fixturePair(intercompanyView(leaked, NOW, ZONE), "UK01", "FR01"));
	assert.equal(leakedSide.remindB.canRemind, false);
	assert.equal(leakedSide.remindB.text, null);
	assert.ok(!JSON.stringify(leakedSide.remindB).includes("Zz Hidden"));
	assert.ok(!String(leakedSide.remindB.text ?? "").includes("FR01"));
});

test("Y65 failure path: a Viewer payload (can_remind false) gives no Remind on either side, text still shown", () => {
	const payload = { ...icFixture(), can_remind: false };
	const side = panel(fixturePair(intercompanyView(payload, NOW, ZONE), "UK01", "DE01"));
	assert.equal(side.remindA.canRemind, false);
	assert.equal(side.remindB.canRemind, false);
	assert.match(side.remindB.text, /^Reminded 1×/, "the recipient sees the text without the button");
});

test("Y65 failure path: a payload without can_remind throws, naming it", () => {
	const payload = icFixture();
	delete payload.can_remind;
	assert.throws(() => intercompanyView(payload, NOW, ZONE), /can_remind/);
});

test("Y65 failure path: a pair without reminders_b throws, naming it (get_ic always sends it, null when none)", () => {
	const payload = icFixture();
	delete payload.groups[0].pairs[0].reminders_b;
	assert.throws(() => intercompanyView(payload, NOW, ZONE), /reminders_b/);
});

test("Y65 failure path: panel refuses a pair that did not come through intercompanyView", () => {
	assert.throws(() => panel(pairRow({})), /canRemind/);
});

test("Y65: the 'Remind is P2' note is gone from intercompany.js", () => {
	const source = readFileSync(fileURLToPath(new URL("./intercompany.js", import.meta.url)), "utf8");
	assert.doesNotMatch(source, /Remind (is|are) P2|replies and\s+(\*\s*)?Remind are P2/);
});
