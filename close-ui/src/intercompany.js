// konsol#305 C12: intercompany.js
//
// Pure view model for the Intercompany screen (E5; stories 5.1, 5.2, 5.3;
// #305-W3-1, W3-2) and the request body for a send-back. Turns
// `ic_api.get_ic`'s payload (C03, amended by C16/C17 for W3-7) into
// everything Intercompany.vue renders: the state banner, the count chips
// (only when checked), the groups and pair rows with status text and
// masked labels, the partnerless rows, the hidden note, and (through the
// separate `panel` helper) the selected pair's side panel, with each side's
// reminded text and whether it can be reminded (Y65). Takes no
// vue/frappe/xstate import.
//
// Server text may already contain a literal "<br>" (signoff.js's
// `messageLines` convention); this module reuses that helper so a banner's
// lines are plain text, never v-html.
//
// Amounts: two decimals with thousands separators, negatives in
// parentheses — mirrors tbTable.js's (unexported) `AMOUNT_FORMAT`, defined
// again here since tbTable.js is outside this row's files.
//
// A masked side (W3-2 option B) never renders "0.00" or blank: it shows
// `HIDDEN_LABEL` in place of the amount, a display courtesy, not a
// security control (the difference and the partner's code stay visible).
// An unmasked null amount (not reported) is a different thing again: it
// renders "not reported", never "0.00" (U10) — a missing amount is not a
// zero balance.
//
// An unknown `state`, `match_status` or banner tone throws, naming it: this
// view never guesses a status, or a style, for something it has not been
// told how to read.

import { formatTime, parseZoned } from "./timefmt.js";
import { messageLines } from "./signoff.js";
import { remindedText } from "./remind.js";

const AMOUNT_FORMAT = new Intl.NumberFormat("en", { minimumFractionDigits: 2 });

const HIDDEN_LABEL = "Hidden: outside your entities";
const NOT_REPORTED = "not reported";

const STATUS_TEXT = {
	matched: "Matched",
	within_tolerance: "Within tolerance",
	fx_difference: "FX difference",
	over_tolerance: "Over tolerance",
};

const STATUS_TONE = {
	matched: "ok",
	within_tolerance: "ok",
	fx_difference: "warn",
	over_tolerance: "block",
};

const BLOCKING_STATES = new Set(["not_built", "error"]);

/** The banner's own border/background/text classes for its tone (U12).
 * `block` and `ok` each get a style of their own — `ok` (e.g. the
 * `not_applicable` banner) is a neutral notice, never folded into the
 * amber "warn" styling nothing here produces. Throws on an unknown tone,
 * naming it, the same as an unknown state or match_status. */
const BANNER_TONE_CLASS = {
	block: "border-outline-red-1 bg-surface-red-1 text-ink-gray-8",
	ok: "border-outline-gray-2 bg-surface-gray-1 text-ink-gray-7",
};

export function bannerToneClass(tone) {
	if (!Object.prototype.hasOwnProperty.call(BANNER_TONE_CLASS, tone)) {
		throw new Error(`Unknown intercompany banner tone: ${tone}`);
	}
	return BANNER_TONE_CLASS[tone];
}

/** A number -> "1,234.50" / "(1,234.50)"; null/undefined -> "not reported"
 * (U10) — never "0.00", which would read as an actual zero balance. A
 * masked side never reaches this function with null: callers check
 * masked_* first and use HIDDEN_LABEL instead. */
function formatAmount(value) {
	if (value === null || value === undefined) {
		return NOT_REPORTED;
	}
	const formatted = AMOUNT_FORMAT.format(Math.abs(value));
	return value < 0 ? `(${formatted})` : formatted;
}

function bannerFor(payload) {
	const { state, message, help } = payload;
	if (state === "not_configured") {
		return { tone: "block", lines: [...messageLines(message), ...messageLines(help)] };
	}
	if (state === "not_applicable") {
		return { tone: "ok", lines: messageLines(message) };
	}
	if (BLOCKING_STATES.has(state)) {
		return { tone: "block", lines: messageLines(message) };
	}
	if (state === "checked") {
		return { tone: "ok", lines: messageLines(message) };
	}
	throw new Error(`Unknown intercompany state: ${state}`);
}

function chipsFor(counts) {
	return {
		pairs: counts.pairs,
		within: counts.matched + counts.within_tolerance,
		differences: counts.over_tolerance,
		fx: counts.fx_difference,
		unmatched: counts.unmatched,
	};
}

function toleranceText(group) {
	if (group.tolerance_declared) {
		return `tolerance ${formatAmount(group.tolerance)}`;
	}
	return "Tolerance not declared (0 is undeclared)";
}

function sentBackView(entry, now, timeZone) {
	if (!entry) {
		return null;
	}
	return {
		by: entry.by,
		byName: entry.by_name,
		at: formatTime(parseZoned(entry.at), now, timeZone),
		reason: entry.reason,
	};
}

function statusFor(matchStatus, sentBack, toleranceDeclared) {
	if (!(matchStatus in STATUS_TEXT)) {
		throw new Error(`Unknown intercompany match status: ${matchStatus}`);
	}
	let text = STATUS_TEXT[matchStatus];
	if (sentBack) {
		text += ` · sent back ${sentBack.at}`;
	}
	if (!toleranceDeclared) {
		text += " · tolerance not declared";
	}
	return { text, tone: STATUS_TONE[matchStatus] };
}

/**
 * One pair row from `group_view`, enriched for the screen and for `panel`:
 * carries the group's `ic_difference_account` and `tolerance_declared`
 * alongside its own fields, a formatted `sent_back` (by_name, zoned time),
 * the two balances pre-formatted (or the hidden label), and its status
 * text/tone.
 */
function pairView(pair, group, toleranceDeclared, canRemind, now, timeZone) {
	for (const key of ["reminders_a", "reminders_b"]) {
		if (!(key in pair)) {
			throw new Error(`Intercompany pair ${pair.entity_a} ↔ ${pair.entity_b} has no \`${key}\` (get_ic always sends it, null when none)`);
		}
	}
	const sentBack = sentBackView(pair.sent_back, now, timeZone);
	const status = statusFor(pair.match_status, sentBack, toleranceDeclared);
	return {
		...pair,
		sent_back: sentBack,
		ic_difference_account: group.ic_difference_account,
		tolerance_declared: toleranceDeclared,
		statusText: status.text,
		statusTone: status.tone,
		balanceAText: pair.masked_a ? HIDDEN_LABEL : formatAmount(pair.balance_a),
		balanceBText: pair.masked_b ? HIDDEN_LABEL : formatAmount(pair.balance_b),
		differenceText: formatAmount(pair.difference),
		// Y65: a masked side's reminder never renders (W3-2), even if one arrived.
		remindedAText: pair.masked_a ? null : remindedText(pair.reminders_a, now, timeZone),
		remindedBText: pair.masked_b ? null : remindedText(pair.reminders_b, now, timeZone),
		canRemind,
	};
}

function groupView(group, canRemind, now, timeZone) {
	const toleranceDeclared = !!group.tolerance_declared;
	return {
		consolidationGroup: group.consolidation_group,
		reportingCurrency: group.reporting_currency,
		tolerance: group.tolerance,
		toleranceDeclared,
		toleranceText: toleranceText(group),
		icDifferenceAccount: group.ic_difference_account,
		pairs: (group.pairs || []).map((pair) => pairView(pair, group, toleranceDeclared, canRemind, now, timeZone)),
	};
}

/** One `gold_ic_unmatched` row -> the screen's partnerless-row text (E5-P9). */
function unmatchedView(row) {
	return {
		entity: row.data_area_id,
		account: row.main_account,
		amount: `${formatAmount(row.unmatched_amount)} (group view, after ownership)`,
		local: `${formatAmount(row.unmatched_local_amount)} (entity currency)`,
		note: `No partner · ask ${row.data_area_id}`,
	};
}

function hiddenNoteFor(hidden) {
	const pairs = (hidden && hidden.pairs) || 0;
	const unmatched = (hidden && hidden.unmatched) || 0;
	if (pairs > 0 || unmatched > 0) {
		return `${pairs} pairs and ${unmatched} partnerless rows for entities outside your scope are not shown`;
	}
	return null;
}

/**
 * `get_ic`'s payload -> everything Intercompany.vue renders: the state
 * banner, the count chips (only when `checked`), the groups and pair rows
 * (with status text, masked labels and the fields `panel` needs), the
 * partnerless rows, and the hidden note.
 */
export function intercompanyView(payload, now, timeZone) {
	if (typeof payload.can_remind !== "boolean") {
		throw new Error("Intercompany payload has no `can_remind` flag (get_ic always sends it).");
	}
	const banner = bannerFor(payload);
	const chips = payload.state === "checked" ? chipsFor(payload.counts) : null;
	const groups = (payload.groups || []).map((group) => groupView(group, payload.can_remind, now, timeZone));
	const unmatched = (payload.unmatched || []).map(unmatchedView);
	return {
		banner,
		chips,
		groups,
		unmatched,
		hiddenNote: hiddenNoteFor(payload.hidden),
	};
}

/**
 * The selected pair's side panel: both sides with account and amount (or
 * the hidden label), the difference, the difference-account sentence
 * (E5-P12), the trail (the pair's `sent_back` entry only; replies are not
 * built), and per side `remindA` / `remindB` = `{entity, text, canRemind}`
 * (Y65, C-R1): `text` is remind.js's reminded text or null, and `canRemind`
 * is the payload's `can_remind` AND the side is not masked. A masked side
 * has text null and canRemind false. `pair` is one of `intercompanyView`'s
 * pair rows (it carries `ic_difference_account`, a formatted `sent_back`,
 * `remindedAText` / `remindedBText` and `canRemind`); any other object
 * throws.
 */
export function panel(pair) {
	if (typeof pair.canRemind !== "boolean" || !("remindedAText" in pair) || !("remindedBText" in pair)) {
		throw new Error("panel takes an intercompanyView pair row (canRemind and the reminded texts are missing).");
	}
	const accountSentence = pair.ic_difference_account
		? `Booked to ${pair.ic_difference_account} in the group view while it stays open.`
		: "No difference account is declared: the difference stays on the intercompany accounts.";
	return {
		sideA: {
			entity: pair.entity_a,
			account: pair.account_a,
			amount: pair.masked_a ? HIDDEN_LABEL : formatAmount(pair.balance_a),
		},
		sideB: {
			entity: pair.entity_b,
			account: pair.account_b,
			amount: pair.masked_b ? HIDDEN_LABEL : formatAmount(pair.balance_b),
		},
		difference: formatAmount(pair.difference),
		accountSentence,
		trail: pair.sent_back
			? { by: pair.sent_back.byName, at: pair.sent_back.at, reason: pair.sent_back.reason }
			: null,
		remindA: remindSide(pair.entity_a, pair.masked_a, pair.remindedAText, pair.canRemind),
		remindB: remindSide(pair.entity_b, pair.masked_b, pair.remindedBText, pair.canRemind),
	};
}

function remindSide(entity, masked, text, canRemind) {
	return masked ? { entity, text: null, canRemind: false } : { entity, text, canRemind };
}

/**
 * `send_back`'s POST body (C04's fixed signature): exactly the pair's four
 * keys, the period and the reason. A blank/whitespace reason is refused
 * client-side with the same sentence the server uses, before any request.
 */
export function sendBackBody(period, pair, reason) {
	const trimmed = (reason || "").trim();
	if (!trimmed) {
		return { error: "Say why the difference is sent back: the entities read this reason." };
	}
	return {
		body: {
			fiscal_year: period.fiscal_year,
			fiscal_period: period.fiscal_period,
			entity_a: pair.entity_a,
			account_a: pair.account_a,
			entity_b: pair.entity_b,
			account_b: pair.account_b,
			reason: trimmed,
		},
	};
}

// --- konsol#305 5.4 (#305-W5-4): IC Balances (unrealised profit) ----------
//
// `ic_balance_api.get_ic_balances`'s payload -> the IC Balances section. The
// margin is read-only (the rule is configured in Desk); a balance with no
// matching unrealised-profit rule says that nothing is eliminated, and the
// server's gap names each pair. F51b: a pair two or more rules match is
// eliminated once per rule by dbt; the server's `ambiguous_gap` names each
// pair with its rules and the row is marked. I54 (S8): a draft with a matching
// rule is not eliminated until approved; the server's `pending_gap` names each
// pair with its drafts and each such draft row carries a note. An unknown
// status throws.

function gapView(gap, pairText) {
	return gap ? { lines: messageLines(gap.message), pairs: (gap.pairs || []).map(pairText) } : null;
}

const BALANCE_STATUS_TONE = { Draft: "warn", Approved: "ok" };

function marginText(rules) {
	if (!rules || !rules.length) {
		return "No unrealised-profit rule: nothing is eliminated";
	}
	return rules.map((r) => `${r.margin_pct}% (${r.rule_name || r.rule_id})`).join(", ");
}

function balanceRow(row, canDraft) {
	if (!(row.status in BALANCE_STATUS_TONE)) {
		throw new Error(`Unknown IC Balance status: ${row.status}`);
	}
	return {
		name: row.name,
		sellingEntity: row.selling_entity,
		buyingEntity: row.buying_entity,
		pair: `${row.selling_entity} → ${row.buying_entity}`,
		statusText: row.status,
		statusTone: BALANCE_STATUS_TONE[row.status],
		salesText: formatAmount(row.ic_sales_amount),
		inventoryText: formatAmount(row.ending_inventory_from_ic),
		salesValue: row.ic_sales_amount,
		inventoryValue: row.ending_inventory_from_ic,
		marginText: marginText(row.rules),
		missingRule: !!row.missing_rule,
		ambiguousRule: !!row.ambiguous_rule,
		pendingNote: pendingNote(row),
		editable: canDraft && row.status === "Draft",
	};
}

//: konsol#305 I54 (S8): a draft whose pair has a matching rule and whose
//: ending inventory is above 0 (or unreadable) is in the server's
//: `pending_gap`. R52n (review U7, coordinator ruling S8/U7): the server sends
//: that per row as the boolean `pending_rule` (R52k); the SPA reads it and
//: never re-derives the rule. A missing or non-boolean flag is a bug: throw.
const PENDING_NOTE = "Blocks sign-off: approve, or delete the draft";

function pendingNote(row) {
	if (typeof row.pending_rule !== "boolean") {
		throw new Error(
			`IC Balance ${row.name} has no boolean \`pending_rule\` (get_ic_balances always sends it)`,
		);
	}
	return row.pending_rule ? PENDING_NOTE : null;
}

//: review-w5 U5: `get_ic_balances` always sends these keys, so a missing one
//: is a bug to surface, never "no balances / nothing hidden" (the
//: auditTrail.js `hidden` rule).
const IC_BALANCES_KEYS = ["balances", "hidden", "entities", "can_draft"];
//: F51b: `gap` and `ambiguous_gap` are always sent too, as null when there is
//: no gap; so the key must be present, and null is the declared "no gap".
//: I54: `pending_gap` (a draft with a matching rule, not yet approved) too.
const IC_BALANCES_NULLABLE_KEYS = ["gap", "ambiguous_gap", "pending_gap"];

export function icBalancesView(payload) {
	for (const key of IC_BALANCES_KEYS) {
		if (payload[key] === undefined || payload[key] === null) {
			throw new Error(`IC Balances payload has no \`${key}\` (get_ic_balances always sends it)`);
		}
	}
	for (const key of IC_BALANCES_NULLABLE_KEYS) {
		if (payload[key] === undefined) {
			throw new Error(`IC Balances payload has no \`${key}\` (get_ic_balances always sends it, null when there is none)`);
		}
	}
	const canDraft = payload.can_draft === true;
	const hidden = payload.hidden;
	return {
		rows: payload.balances.map((row) => balanceRow(row, canDraft)),
		gap: gapView(payload.gap, (p) => `${p.selling_entity} → ${p.buying_entity}`),
		ambiguousGap: gapView(
			payload.ambiguous_gap,
			(p) => `${p.selling_entity} → ${p.buying_entity} (${(p.rule_ids || []).join(", ")})`,
		),
		pendingGap: gapView(payload.pending_gap, (p) => `${p.selling_entity} → ${p.buying_entity} (${p.names.join(", ")})`),
		hiddenNote: hidden > 0 ? `${hidden} IC Balances for entities outside your scope are not shown` : null,
		canDraft,
		entities: payload.entities,
		rulesDesk: payload.rules_desk,
	};
}

function amountProblem(value, label) {
	const text = value === null || value === undefined ? "" : String(value).trim();
	if (text === "" || !Number.isFinite(Number(text))) {
		return `The ${label} must be a number.`;
	}
	if (Number(text) < 0) {
		return `The ${label} cannot be negative.`;
	}
	return null;
}

/**
 * `save_ic_balance`'s POST body: exactly the endpoint's keys, plus `name`
 * when editing a draft. Refuses blank or equal entities and a missing,
 * non-numeric or negative amount client-side (the server refuses them too).
 */
export function icBalanceBody(period, form) {
	const problems = [];
	if (!form.selling_entity) problems.push("Name the selling entity.");
	if (!form.buying_entity) problems.push("Name the buying entity.");
	if (form.selling_entity && form.selling_entity === form.buying_entity) {
		problems.push("The selling and buying entity are the same entity: an IC Balance is between two entities.");
	}
	for (const [key, label] of [["ic_sales_amount", "IC sales amount"], ["ending_inventory_from_ic", "ending inventory from IC"]]) {
		const problem = amountProblem(form[key], label);
		if (problem) problems.push(problem);
	}
	if (problems.length) {
		return { error: problems.join("<br>") };
	}
	const body = {
		fiscal_year: period.fiscal_year,
		fiscal_period: period.fiscal_period,
		selling_entity: form.selling_entity,
		buying_entity: form.buying_entity,
		ic_sales_amount: String(form.ic_sales_amount).trim(),
		ending_inventory_from_ic: String(form.ending_inventory_from_ic).trim(),
	};
	if (form.name) body.name = form.name;
	return { body };
}
