// konsol#305 C12: intercompany.js
//
// Pure view model for the Intercompany screen (E5; stories 5.1, 5.2, 5.3;
// #305-W3-1, W3-2) and the request body for a send-back. Turns
// `ic_api.get_ic`'s payload (C03, amended by C16/C17 for W3-7) into
// everything Intercompany.vue renders: the state banner, the count chips
// (only when checked), the groups and pair rows with status text and
// masked labels, the partnerless rows, the hidden note, and (through the
// separate `panel` helper) the selected pair's side panel. Takes no
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
//
// An unknown `state` or `match_status` throws, naming it: this view never
// guesses a status for something it has not been told how to read.

import { formatTime, parseZoned } from "./timefmt.js";
import { messageLines } from "./signoff.js";

const AMOUNT_FORMAT = new Intl.NumberFormat("en", { minimumFractionDigits: 2 });

const HIDDEN_LABEL = "Hidden: outside your entities";

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

/** A number -> "1,234.50" / "(1,234.50)"; null/undefined -> "0.00" is never produced from a mask — callers check masked_* first. */
function formatAmount(value) {
	if (value === null || value === undefined) {
		return AMOUNT_FORMAT.format(0);
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
	let text = sentBack ? `Sent to both · ${sentBack.at}` : STATUS_TEXT[matchStatus];
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
function pairView(pair, group, toleranceDeclared, now, timeZone) {
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
	};
}

function groupView(group, now, timeZone) {
	const toleranceDeclared = !!group.tolerance_declared;
	return {
		consolidationGroup: group.consolidation_group,
		reportingCurrency: group.reporting_currency,
		tolerance: group.tolerance,
		toleranceDeclared,
		toleranceText: toleranceText(group),
		icDifferenceAccount: group.ic_difference_account,
		pairs: (group.pairs || []).map((pair) => pairView(pair, group, toleranceDeclared, now, timeZone)),
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
	const banner = bannerFor(payload);
	const chips = payload.state === "checked" ? chipsFor(payload.counts) : null;
	const groups = (payload.groups || []).map((group) => groupView(group, now, timeZone));
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
 * (E5-P12), and the trail (the pair's `sent_back` entry only — replies and
 * Remind are P2). `pair` is one of `intercompanyView`'s pair rows (it
 * carries `ic_difference_account` and a formatted `sent_back`).
 */
export function panel(pair) {
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
	};
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
