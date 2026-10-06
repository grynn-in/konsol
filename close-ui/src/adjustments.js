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
 *
 * `dimensions` passes through unchanged (konsolidat#245 option D;
 * `journal_api.get_journals`'s `dimensions: [{key, label, suggestions}]`):
 * the declared journal dimensions, generated from the Published Dimension
 * rows, never hard-coded (konsol#287). `[]` when none are declared — the
 * screen then looks exactly as it did before option D.
 *
 * `reversingIn` (#305 story 6.5) is `get_journals`'s `reversing_in`: the
 * Approved journals of earlier periods that reverse into this one, each
 * with `approvedAtText` and `totalsText` added. Missing `reversing_in`
 * throws, like `dimensions`.
 *
 * D04 correction to D03: `get_journals` (D02) always sends `dimensions`,
 * even as `[]`, so a missing key is a bug in the caller, not "zero
 * dimensions" — this throws rather than silently defaulting to `[]`.
 */
export function journalsView(payload, now, timeZone) {
	if (!timeZone) {
		throw new Error("journalsView requires a time zone");
	}
	if (!(now instanceof Date) || Number.isNaN(now.getTime())) {
		throw new Error("journalsView requires a valid `now`");
	}
	if (payload.dimensions === undefined) {
		throw new Error("journalsView requires `dimensions` (journal_api.get_journals always sends it, even as [])");
	}
	if (payload.reversing_in === undefined) {
		throw new Error("journalsView requires `reversing_in` (journal_api.get_journals always sends it, even as [])");
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
			//: U6: the list shows this, never the raw `total_debit`/`total_credit`.
			totalsText: `${formatAmount(journal.total_debit)} / ${formatAmount(journal.total_credit)}`,
			lastRejection: last_rejection
				? {
						reason: last_rejection.reason,
						actor: last_rejection.actor,
						at: timeText(last_rejection.at, now, timeZone),
					}
				: null,
		};
	});
	//: #305 story 6.5: Approved journals of earlier periods whose reversal
	//: posts into this one — read-only; `label`, `origin`, `lines` (the
	//: reversal posting) and `effect` pass through from the server.
	const reversingIn = payload.reversing_in.map((item) => ({
		...item,
		approvedAtText: timeText(item.approved_at, now, timeZone),
		totalsText: `${formatAmount(item.total_debit)} / ${formatAmount(item.total_credit)}`,
	}));
	return {
		period: payload.period,
		journals,
		reversingIn,
		groups: payload.groups,
		accounts: payload.accounts,
		dimensions: payload.dimensions,
		reversalChoices: payload.reversal_choices || [],
		workflowInstalled: Boolean(payload.workflow_installed),
		firstState: payload.first_state,
		canDraft: Boolean(payload.can_draft),
		canSend: Boolean(payload.can_send),
		canEditPeriod: Boolean(payload.can_edit_period),
	};
}

/**
 * `AMOUNT_FORMAT`, exported (U6): the list's totals column must show the
 * same 2dp-with-grouping text the effect panel already uses, not the raw
 * float `journal_api.get_journals` sends (`total_debit`/`total_credit`,
 * journal_model.py's `_totals` — a float rounded to 2dp, no grouping, no
 * guaranteed trailing zero).
 */
export function formatAmount(value) {
	return AMOUNT_FORMAT.format(value);
}

/**
 * The draft editor's duration choices (W3-3 option A): "This period only,
 * no reversal", then one "Reverses in <code>" per `view.reversalChoices`
 * (journalsView's carry-through of journal_model.reversal_choices), in the
 * order the server sent them. Never a third, "stays until reversed" option
 * (W3-3 rejected it). With no reversal choices, only "none" is offered;
 * the caller shows `NO_REVERSAL_NOTE` in that case.
 *
 * Takes `journalsView`'s output, not the raw payload (one key convention
 * across this module: `durationOptions`, `editable` and `durationIndex` all
 * read the view's camelCase keys).
 */
export function durationOptions(view) {
	const choices = view.reversalChoices || [];
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

/**
 * Which of `durationOptions`'s entries matches `duration` (`draft.duration`:
 * `{kind: "none"}` or `{kind: "reverses", fiscal_year, fiscal_period}`).
 * Throws when none matches — never falls back to index 0, which would show
 * "This period only, no reversal" in the select while the draft still holds
 * a real reversal pair (U1): a duration that is not among the offered
 * options is a bug to surface, not a UI state to paper over.
 */
export function durationIndex(options, duration) {
	const idx = options.findIndex(
		(o) =>
			o.kind === duration.kind &&
			(o.kind !== "reverses" || (o.fiscal_year === duration.fiscal_year && o.fiscal_period === duration.fiscal_period)),
	);
	if (idx < 0) {
		throw new Error(`durationIndex: no option matches duration ${JSON.stringify(duration)}`);
	}
	return idx;
}

//: `Number("0x10")` parses as 16 and `Number("Infinity")` as Infinity;
//: neither is a decimal amount, so parseCents must reject both itself
//: rather than rely on `Number.isNaN` (U12).
const HEX_LIKE = /^\s*[+-]?0x/i;

//: A blank amount is 0 cents; anything that does not parse as a decimal
//: amount (including the "0x10"/"Infinity" cases above) is `null`, never a
//: guessed number. Shared by `toCents` (draftTotals' invalid-line tracking)
//: and `amountsEqual` (draftDirty's in-cents comparison, U3) — one parse
//: rule, not two.
function parseCents(value) {
	if (value === null || value === undefined || value === "") {
		return 0;
	}
	const num = Number(value);
	const invalidValue = !Number.isFinite(num) || (typeof value === "string" && HEX_LIKE.test(value));
	if (invalidValue) {
		return null;
	}
	return Math.round(num * 100);
}

function toCents(value, pos, invalid) {
	const cents = parseCents(value);
	if (cents === null) {
		if (!invalid.includes(pos)) {
			invalid.push(pos);
		}
		return 0;
	}
	return cents;
}

//: Two amounts are equal when they parse to the same cents (U3: "100"
//: typed on screen and 100 last saved are the same amount, never a
//: string/number mismatch). When either side does not parse as a decimal
//: amount, fall back to a literal compare — two copies of the same invalid
//: text ("abc") are still equal; a parsed amount against unparseable text
//: never is.
function amountsEqual(a, b) {
	const ca = parseCents(a);
	const cb = parseCents(b);
	if (ca !== null && cb !== null) {
		return ca === cb;
	}
	return a === b;
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
 *
 * `dimKeys` (konsolidat#245 option D; default `[]`, the allow-list the
 * server declared via `journal_api.get_journals`'s `dimensions`, never
 * hard-coded, konsol#287): each line carries exactly these dim keys too,
 * default `''` when missing. Any other dim-looking key on a draft line —
 * undeclared, or belonging to a dimension no longer gated for journals —
 * is dropped like every other key `LINE_KEYS` does not name; a typed value
 * for a declared key is sent verbatim, never refused (konsol#247).
 */
export function saveJournalBody(period, draft, dimKeys = []) {
	const duration = draft.duration || { kind: "none" };
	const reversing = duration.kind === "reverses";
	const lines = (draft.lines || []).map((line) => {
		const row = {};
		for (const key of LINE_KEYS) {
			row[key] = line[key];
		}
		for (const key of dimKeys) {
			row[key] = line[key] ?? "";
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
 * Whether `journal` may be edited on the Adjustments screen (A18): only a
 * Draft (`docstatus` 0, `status` the workflow's first state — "already
 * sent" otherwise), only in an editable (Open) period, and only when the
 * caller may draft at all (`view.canDraft`, which already folds in
 * `journal_api`'s `DRAFT_ROLES` check) — A06's own save path still decides
 * for real; this only decides whether the screen offers the control.
 *
 * No "journal is their own" branch: E6-P1 option (c) draws `save_journal`'s
 * line at `DRAFT_ROLES`, not at ownership, so a preparer who has lost (or
 * never had) a draft role gets no edit control here either (U2). Takes
 * `journalsView`'s output (camelCase `firstState`/`canEditPeriod`/
 * `canDraft`), not the raw payload.
 */
export function editable(journal, view) {
	if (journal.docstatus !== 0) {
		return false;
	}
	if (journal.status !== view.firstState) {
		return false;
	}
	if (!view.canEditPeriod) {
		return false;
	}
	return Boolean(view.canDraft);
}

/**
 * A saved journal's or a draft's `lines` -> the editor's line shape: the
 * exact fields the inputs bind to, with a blank amount/description never
 * left `undefined`. Used both to seed the editor from a saved journal
 * (`openEdit`) and, inside `snapshotDraft`/`draftDirty`, to compare the
 * editor's current lines against what was last saved — one shape, so the
 * two never drift apart (previously duplicated inline in Adjustments.vue).
 *
 * `dimKeys` (konsolidat#245 option D; default `[]`, so a caller that never
 * declares any dimension gets exactly today's five-field line shape): each
 * declared key is carried, default `''` when the line has no value for it
 * — never omitted, and never refused for carrying a value that is not
 * among the dimension's suggestions (konsol#247, free text).
 */
export function snapshotLines(lines, dimKeys = []) {
	return (lines || []).map((line) => {
		const out = {
			data_area_id: line.data_area_id,
			main_account: line.main_account,
			debit_amount: line.debit_amount ?? "",
			credit_amount: line.credit_amount ?? "",
			description: line.description || "",
		};
		for (const key of dimKeys) {
			out[key] = line[key] ?? "";
		}
		return out;
	});
}

/**
 * D04 (konsolidat#245 option D): the declared dimension keys, in the
 * server's order (`view.dimensions`'s own order, `journalsView`'s output,
 * never the raw payload) — the one place Adjustments.vue reads `dimKeys`
 * from, so every `snapshotLines`/`snapshotDraft`/`draftDirty`/
 * `saveJournalBody` call, and every new blank line, uses the same list.
 * `null`/no view yet (the list hasn't loaded) gives `[]`, matching every
 * other function in this module's zero-dimensions default.
 */
export function dimKeysOf(view) {
	return view ? view.dimensions.map((d) => d.key) : [];
}

/**
 * D04: a line's declared dimension value for display (the journal detail
 * panel) — blank (missing, `null`, or `''`) reads as the explicit "—",
 * never silently rendered as empty text indistinguishable from "no column
 * here at all". A dimension value is free text (konsol#247), so any other
 * typed value, including one that collides with other screens' blank
 * marker, is shown verbatim.
 */
export function dimValueText(line, key) {
	const value = line[key];
	return value === undefined || value === null || value === "" ? "—" : value;
}

function durationsEqual(a, b) {
	a = a || { kind: "none" };
	b = b || { kind: "none" };
	if (a.kind !== b.kind) {
		return false;
	}
	if (a.kind !== "reverses") {
		return true;
	}
	return a.fiscal_year === b.fiscal_year && a.fiscal_period === b.fiscal_period;
}

/**
 * The editor's state right after it was last saved (a successful
 * `save_journal`, or a journal just loaded into the editor by `openEdit` —
 * equally "last saved") -> a plain snapshot of every field Send gates on
 * (U3): `consolidation_group`, `adjustment_type`, `description`,
 * `duration` and `lines`. `draftDirty` compares a later editor state
 * against this snapshot; previously only `lines` was ever compared, so a
 * changed group, type, description or duration never disabled Send.
 *
 * `dimKeys` (konsolidat#245 option D; default `[]`) is passed straight to
 * `snapshotLines`, so the declared dimension values are part of what
 * "last saved" means too.
 */
export function snapshotDraft(draft, dimKeys = []) {
	return {
		consolidation_group: draft.consolidation_group,
		adjustment_type: draft.adjustment_type,
		description: draft.description || "",
		duration: durationsEqual(draft.duration, { kind: "none" })
			? { kind: "none" }
			: { kind: "reverses", fiscal_year: draft.duration.fiscal_year, fiscal_period: draft.duration.fiscal_period },
		lines: snapshotLines(draft.lines, dimKeys),
	};
}

/**
 * Whether the editor's current state differs from `snapshot` (U3: Send
 * must be disabled, and the effect panel must show "save the draft to see
 * its effect" rather than a stale saved effect, while anything on screen
 * differs from what was last saved). `snapshot` is `null` before any save
 * (a fresh "New" draft never saved) — nothing to be dirty against, so this
 * returns `false`.
 *
 * Amounts compare in cents through `amountsEqual`, never as raw strings: a
 * line typed as `"100"` against a saved `100` is not dirty.
 *
 * `dimKeys` (konsolidat#245 option D; default `[]`): a changed declared
 * dimension value on any line is dirty too, compared as plain text (a
 * dimension value is free text, konsol#247, never a number).
 */
export function draftDirty(snapshot, draft, dimKeys = []) {
	if (!snapshot) {
		return false;
	}
	if (snapshot.consolidation_group !== draft.consolidation_group) {
		return true;
	}
	if (snapshot.adjustment_type !== draft.adjustment_type) {
		return true;
	}
	if ((snapshot.description || "") !== (draft.description || "")) {
		return true;
	}
	if (!durationsEqual(snapshot.duration, draft.duration)) {
		return true;
	}
	const current = snapshotLines(draft.lines, dimKeys);
	if (snapshot.lines.length !== current.length) {
		return true;
	}
	for (let i = 0; i < snapshot.lines.length; i++) {
		const a = snapshot.lines[i];
		const b = current[i];
		if (a.data_area_id !== b.data_area_id) return true;
		if (a.main_account !== b.main_account) return true;
		if (a.description !== b.description) return true;
		if (!amountsEqual(a.debit_amount, b.debit_amount)) return true;
		if (!amountsEqual(a.credit_amount, b.credit_amount)) return true;
		for (const key of dimKeys) {
			if ((a[key] ?? "") !== (b[key] ?? "")) return true;
		}
	}
	return false;
}

/**
 * Whether the "New" control may be shown (U4). `journal_api.can_draft`
 * (the server's `view.canDraft`) does not look at period status, so a
 * locked period would otherwise let the whole journal be typed before
 * `save_journal` refuses it. The screen gates on both itself.
 */
export function canOpenNew(view) {
	return Boolean(view && view.canDraft && view.canEditPeriod);
}

/**
 * Whether "Save draft" may run (U4): the period still accepts edits
 * (`view.canEditPeriod`), and every line's amount parses (`draftTotals`'s
 * `invalid` is empty — a line that does not parse never reaches the
 * server as 0).
 */
export function canSaveDraft(view, totals) {
	return Boolean(view && view.canEditPeriod && totals && totals.invalid.length === 0);
}

/**
 * Whether "Send for approval" may run (U3, U4): the draft is saved
 * (`draft.name` set), the caller may send (`view.canSend`), the period
 * still accepts edits, and nothing on screen differs from what was last
 * saved (`dirty`, from `draftDirty` against the save-time snapshot) —
 * otherwise Send would send the last saved lines, not what is on screen.
 */
export function canSendDraft(view, draft, dirty) {
	return Boolean(view && view.canSend && view.canEditPeriod && draft && draft.name && !dirty);
}
