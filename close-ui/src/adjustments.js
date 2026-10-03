// konsol#305 A13: adjustments.js
//
// Pure view model and request-body helpers for the Adjustments screen
// (E6; stories 6.1, 6.2; #305-W3-3, W3-4). Turns `journal_api.get_journals`'s
// payload (A05) into what Adjustments.vue (A17, A18) renders, and turns the
// draft editor's state into `journal_api.save_journal`'s (A06) request body.
// Takes no vue/frappe import; the period comes from the URL (route.js), not
// from here or from browser storage.
//
// Amounts: two decimals with thousands separators (mirrors tbTable.js's and
// intercompany.js's unexported `AMOUNT_FORMAT`, defined again here since
// those files are outside this row's files).
//
// `draftTotals` works in integer cents throughout (`Math.round(Number(x) *
// 100)`), never a float sum: 0.10 + 0.20 must equal 0.30 exactly.

import { formatTime, parseZoned } from "./timefmt.js";

const AMOUNT_FORMAT = new Intl.NumberFormat("en", { minimumFractionDigits: 2 });

//: W3-3 rejected a "stays until reversed" duration; a journal either posts
//: this period only, or reverses in a named declared, Regular, Open, later
//: period (journal_model.reversal_choices). Shown only when `durationOptions`
//: offers nothing beyond "none".
export const NO_REVERSAL_NOTE = "No open Regular period after this one to reverse into";

//: `saveJournalBody`'s line keys, in the order a Consolidation Journal Line
//: accepts them (journal_model.LINE_KEYS) — never more, never fewer.
const LINE_KEYS = ["data_area_id", "main_account", "debit_amount", "credit_amount", "description"];

function timeText(value, now, timeZone) {
	if (value === null || value === undefined) {
		return "not recorded";
	}
	return formatTime(parseZoned(value), now, timeZone);
}

/**
 * `journal_api.get_journals`'s payload -> everything the Adjustments screen
 * reads for the list: the period, the drafting choices (`groups`,
 * `accounts`) and what the caller may do, passed through; each journal with
 * its timestamps and its last rejection's time formatted (the
 * `pendingCreatedText` pattern, rates.js:414-425) rather than left as a raw
 * zoned ISO string. `effect` and `lines` pass through unchanged: the screen
 * runs the selected journal's `effect` through `effectView` itself, and
 * `editable` is computed per journal by the caller (it needs the signed-in
 * user, which this view does not take).
 *
 * Requires a `timeZone` and a valid `now` (B09b): there is no guessed
 * display for a timestamp with no zone, or with no clock to compare it to.
 */
export function journalsView(payload, now, timeZone) {
	if (!timeZone) {
		throw new Error("journalsView requires a time zone");
	}
	if (!(now instanceof Date) || Number.isNaN(now.getTime())) {
		throw new Error("journalsView requires a valid `now`");
	}
	const journals = (payload.journals || []).map((journal) => {
		const { last_rejection, created, modified, approved_at, ...rest } = journal;
		return {
			...rest,
			created,
			modified,
			approved_at,
			createdText: timeText(created, now, timeZone),
			modifiedText: timeText(modified, now, timeZone),
			approvedAtText: timeText(approved_at, now, timeZone),
			lastRejection: last_rejection
				? {
						reason: last_rejection.reason,
						actor: last_rejection.actor,
						at: timeText(last_rejection.at, now, timeZone),
					}
				: null,
		};
	});
	return {
		period: payload.period,
		journals,
		groups: payload.groups,
		accounts: payload.accounts,
		workflowInstalled: Boolean(payload.workflow_installed),
		firstState: payload.first_state,
		canDraft: Boolean(payload.can_draft),
		canSend: Boolean(payload.can_send),
		canEditPeriod: Boolean(payload.can_edit_period),
	};
}

/**
 * The draft editor's duration choices (W3-3 option A): "This period only,
 * no reversal", then one "Reverses in <code>" per
 * `payload.reversal_choices` (journal_model.reversal_choices), in the order
 * the server sent them. Never a third, "stays until reversed" option
 * (W3-3 rejected it). With no reversal choices, only "none" is offered;
 * the caller shows `NO_REVERSAL_NOTE` in that case.
 */
export function durationOptions(payload) {
	const choices = payload.reversal_choices || [];
	const options = [{ kind: "none", label: "This period only, no reversal" }];
	for (const choice of choices) {
		options.push({
			kind: "reverses",
			fiscal_year: choice.fiscal_year,
			fiscal_period: choice.fiscal_period,
			label: "Reverses in " + choice.code,
		});
	}
	return options;
}

function toCents(value, pos, invalid) {
	if (value === null || value === undefined || value === "") {
		return 0;
	}
	const cents = Math.round(Number(value) * 100);
	if (Number.isNaN(cents)) {
		if (!invalid.includes(pos)) {
			invalid.push(pos);
		}
		return 0;
	}
	return cents;
}

/**
 * The draft editor's running totals (E6-P8, display only — the server's
 * `journal_model.balance_problem` is the rule that actually refuses an
 * unbalanced save). Works in integer cents throughout, never a float sum,
 * so 0.10 + 0.20 equals 0.30 exactly. A line whose amount does not parse as
 * a number counts as 0 and the line's 1-based position is recorded in
 * `invalid` (mirrors journal_model.clean_lines's 1-based line numbering).
 *
 * Returns `{debit, credit, difference, balanced, invalid}`.
 */
export function draftTotals(lines) {
	let debitCents = 0;
	let creditCents = 0;
	const invalid = [];
	(lines || []).forEach((line, idx) => {
		debitCents += toCents(line.debit_amount, idx + 1, invalid);
		creditCents += toCents(line.credit_amount, idx + 1, invalid);
	});
	const differenceCents = Math.abs(debitCents - creditCents);
	return {
		debit: debitCents / 100,
		credit: creditCents / 100,
		difference: differenceCents / 100,
		balanced: differenceCents === 0,
		invalid,
	};
}

/**
 * The draft editor's state -> `journal_api.save_journal`'s (A06) request
 * body: exactly the nine parameter names it declares (no `**kwargs` there,
 * so an unknown key is dropped silently; this still never sends one),
 * carrying only `LINE_KEYS` inside each line. A forged `status`,
 * `docstatus`, `approved_by` or similar on `draft`, or `docstatus` /
 * `name` / `parent` on a line, never reaches the body: it is built fresh
 * from named fields only, never spread from `draft`.
 *
 * `period` is `{fiscal_year, fiscal_period}` (the URL's period, route.js).
 * `draft.duration` is one of `durationOptions`'s entries (or `{kind:
 * "none"}`); "none" always sends 0/0 (a blank Int reads as 0,
 * journal_model.reversal_pair_problem). `name` is sent only when editing
 * an existing draft.
 */
export function saveJournalBody(period, draft) {
	const duration = draft.duration || { kind: "none" };
	const reversing = duration.kind === "reverses";
	const lines = (draft.lines || []).map((line) => {
		const row = {};
		for (const key of LINE_KEYS) {
			row[key] = line[key];
		}
		return row;
	});
	const body = {
		fiscal_year: period.fiscal_year,
		fiscal_period: period.fiscal_period,
		consolidation_group: draft.consolidation_group,
		adjustment_type: draft.adjustment_type,
		description: draft.description,
		lines: JSON.stringify(lines),
		reverse_fiscal_year: reversing ? duration.fiscal_year : 0,
		reverse_fiscal_period: reversing ? duration.fiscal_period : 0,
	};
	if (draft.name) {
		body.name = draft.name;
	}
	return body;
}

function amountText(netDebit) {
	if (!netDebit) {
		return "no change";
	}
	const formatted = AMOUNT_FORMAT.format(Math.abs(netDebit));
	return netDebit > 0 ? `Dr ${formatted}` : `Cr ${formatted}`;
}

/**
 * A journal's `effect` (journal_model.statement_effect, carried on each
 * A05 journal) -> the Adjustments screen's effect panel: each heading's
 * label (`heading_name`, or "no heading" when the account has none — never
 * dropped) and amount text (`Dr`/`Cr`/"no change", 2dp with grouping), in
 * the server's order (already Profit and Loss, then Balance Sheet, then
 * None, journal_model._SECTION_ORDER). `sections` (the per-section
 * subtotal) passes through the same way.
 *
 * `effectView(null)` — an unsaved edit, which has no saved `effect` yet —
 * gives `{note: "Save the draft to see its effect"}` (E6-P8), never a
 * guessed or stale amount.
 */
export function effectView(effect) {
	if (effect === null || effect === undefined) {
		return { note: "Save the draft to see its effect" };
	}
	return {
		headings: (effect.headings || []).map((heading) => ({
			section: heading.section,
			heading: heading.heading,
			label: heading.heading_name || "no heading",
			amountText: amountText(heading.net_debit),
		})),
		sections: (effect.sections || []).map((section) => ({
			section: section.section,
			amountText: amountText(section.net_debit),
		})),
		noHeading: effect.no_heading || 0,
	};
}

/**
 * Whether `user` may edit `journal` on the Adjustments screen (A18): only a
 * Draft (`docstatus` 0, `status` the workflow's first state — "already
 * sent" otherwise), only in an editable (Open) period, and only when the
 * caller may draft at all (`payload.can_draft`, which already folds in
 * `journal_api`'s `DRAFT_ROLES` check) or the journal is their own
 * (`journal.preparer === user`) — A06's own save path still decides for
 * real; this only decides whether the screen offers the control.
 */
export function editable(journal, payload, user) {
	if (journal.docstatus !== 0) {
		return false;
	}
	if (journal.status !== payload.first_state) {
		return false;
	}
	if (!payload.can_edit_period) {
		return false;
	}
	return Boolean(payload.can_draft) || journal.preparer === user;
}
