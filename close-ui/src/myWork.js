// konsol#305 B10: myWork.js
//
// Pure grouping of A20's my-work items for the My work screen (B18). Takes
// no frappe/vue import; the caller supplies `items` already ranked by the
// server (`mywork_model.rank`, A20) and never re-sorts them here — sorting
// again on the client would silently override the server's ranking.
//
// Coordinator override (parallel run, 25 Sep 2026): a kind with no items is
// never dropped from the result. Dropping an empty kind is indistinguishable
// from "the server sent nothing for this kind today" and from "this screen
// has no waiting section at all" — both are lies by omission. Every kind
// section is always present, and an empty one says so explicitly.
//
// Item shape (A20, mywork_model.py): {id, kind: "blocking"|"todo"|"waiting",
// title, period: {fiscal_year, fiscal_period, code} | absent for a setup gap,
// owner, action}. `action` is either {screen, entity?} (an in-app route) or
// {desk: "/app/..."} (a configuration gap; story 0.4 — the only Desk link).

import { format } from "./route.js";
import { ageText as sharedAgeText, userTimeZone } from "./timefmt.js";
import { periodName } from "./periodName.js";
import { remindedText } from "./remind.js";
import { OVERDUE_TONE, dueDateText } from "./dueDate.js";

const KIND_ORDER = ["blocking", "todo", "waiting"];

const TITLES = {
	blocking: "Blocking",
	todo: "To do",
	waiting: "Waiting on others",
};

const EMPTY_TITLES = {
	blocking: "Nothing blocking",
	todo: "Nothing to do",
	waiting: "Nothing waiting on others",
};

/**
 * `items` (A20's shape) → `[{kind, title, items, empty}]`, one entry per
 * kind, always in blocking/todo/waiting order, never omitting an empty one.
 * Items keep the order they arrived in; only grouped, never re-sorted.
 *
 * Throws "unknown item kind: <kind>" on an item whose kind is not one of
 * the three — that item is surfaced as an error, not silently dropped.
 */
export function sections(items) {
	const byKind = { blocking: [], todo: [], waiting: [] };
	for (const item of items || []) {
		if (!Object.prototype.hasOwnProperty.call(byKind, item.kind)) {
			throw new Error(`unknown item kind: ${item.kind}`);
		}
		byKind[item.kind].push(item);
	}
	return KIND_ORDER.map((kind) => {
		const kindItems = byKind[kind];
		const empty = kindItems.length === 0;
		return {
			kind,
			title: empty ? EMPTY_TITLES[kind] : TITLES[kind],
			items: kindItems,
			empty,
		};
	});
}

/**
 * `item` (A20's shape) and `current` (`{year, period}`, the URL's period,
 * or null/absent) → the in-app route path for a period item, or
 * `{external: "/app/..."}` for a setup-gap item (story 0.4). A period
 * item's route never depends on client-side "last viewed" state (D5): the
 * period comes from the item itself, never from `current`.
 *
 * E6-P10 (A20): a screen item with no `period` (an approval or adjustment
 * item that belongs to no single period) routes to `current` instead. With
 * no `current` (nothing in the URL) it returns null — a period is never
 * invented — and the caller (MyWork.vue) renders the item's title with no
 * link.
 *
 * B18b: when the item's action names an entity (an Entity Accountant's
 * "Upload TB for <entity>" item), the route carries it as `?entity=<code>`,
 * so TrialBalances.vue can open that entity's detail area directly. An
 * action with no entity carries no query string — never an invented one.
 */
export function itemRoute(item, current) {
	const action = item.action || {};
	if (action.desk) {
		return { external: action.desk };
	}
	const period = item.period;
	const year = period ? period.fiscal_year : current ? current.year : null;
	const fiscalPeriod = period ? period.fiscal_period : current ? current.period : null;
	if (year == null || fiscalPeriod == null) return null;
	const path = format({ year, period: fiscalPeriod, screen: action.screen });
	return action.entity ? `${path}?entity=${encodeURIComponent(action.entity)}` : path;
}

/**
 * `since` (an ISO date, e.g. "2026-09-13", a zoned timestamp for the
 * Close Lead's approvals item, or null) and `today` (a `Date`, always
 * supplied by the caller) → an age string such as "12 days", "1 day" or
 * "today" for B18's My work screen (A53, story 1.1).
 *
 * konsol#305 F03: the day-math itself is timefmt.js's `ageText`, the one
 * shared rule Approvals (approvals.js) also uses — this file used to diff
 * calendar dates while approvals.js floored raw elapsed milliseconds, so
 * the same waiting item aged differently on the two screens (live: 16 vs
 * 17 days). The age is read in the site's own time zone (`userTimeZone`,
 * B29), the same zone Approvals.vue already passes in explicitly; this
 * screen has no such parameter to thread through, so it resolves the zone
 * here instead of reading the browser's raw local calendar.
 *
 * This module still never reads the clock: `today` is always an injected
 * parameter, the same rule the pure Python models follow.
 *
 * `since === null` (a setup-gap item, A53's `since_reason:
 * "configuration gap"`) renders nothing: `null`. A `since` that is still in
 * the future (the period has not ended yet) also renders nothing — there
 * is no elapsed age to show, and a negative day count would be a lie.
 */
export function ageText(since, today) {
	return sharedAgeText(since, today, userTimeZone());
}

// --- U7 (review-w3.md): the item's badge ------------------------------------
//
// MyWork.vue:248 badged every period-less item orange "Setup", on the
// (wrong) assumption that "no period" means "configuration gap". It does
// not: mywork_model.py returns period-less items from three different
// builders —
// - `setup_gap_items`: a real configuration gap. Carries
//   `since_reason: "configuration gap"`.
// - `approvals_item`: the Close Lead's one-item approvals queue. Carries
//   `since_reason: "oldest waiting"`.
// - `sent_back_items`, for a doctype outside `_SENT_BACK_PERIOD_KEYED`
//   (Historical Equity Rate, Ownership Period, Business Combination,
//   Business Disposal): a sent-back draft. Carries
//   `since_reason: "sent back"`.
// Badging the last two "Setup" makes routine approval/rework look like a
// setup defect. `since_reason` is the one field every period-less builder
// sets, and only `setup_gap_items` sets it to "configuration gap" — so it,
// not "no period", decides.
//
// A period item (it carries `period`, from `_period_item` or
// `ic_fix_items`) always gets its period's name (FY + period), themed by kind,
// regardless of `since_reason`.
//
// An unknown kind throws (mirrors `sections`): a badge is never guessed,
// and never defaults to "Setup".

const BADGE_THEME = { blocking: "red", todo: "blue", waiting: "gray" };
const BADGE_LABEL = { blocking: "Blocking", todo: "To do", waiting: "Waiting" };

/**
 * `item` (A20's shape) -> `{theme, label}` for the My work screen's badge.
 */
export function badgeFor(item) {
	if (!Object.prototype.hasOwnProperty.call(BADGE_THEME, item.kind)) {
		throw new Error(`unknown item kind: ${item.kind}`);
	}
	if (item.period) {
		//: review-w5: the live period code is "P08" alone; the badge names the
		//: year (periodName), never the bare code.
		return { theme: BADGE_THEME[item.kind], label: periodName(item.period.fiscal_year, item.period.fiscal_period) };
	}
	if (item.since_reason === "configuration gap") {
		return { theme: "orange", label: "Setup" };
	}
	return { theme: BADGE_THEME[item.kind], label: BADGE_LABEL[item.kind] };
}

// --- Y64 (stories 1.5, 1.2): the reminded line on TB items -----------------
//
// mywork_model (Y58) puts `reminded` on TB items only:
// - the Entity Accountant's "Upload TB for X": `{count, last_at,
//   last_by_name}` → remind.js's one rule, "Reminded 2× · last … by …";
// - the "Waiting on N trial balances" items: `{reminded, of}` →
//   "R of N reminded".
// Nobody reminded is `reminded: null` (Y58 never sends a 0), so null gives no
// line: never "0×" or "0 of N". An item that is not a TB item carries no
// `reminded` key at all and gets no line. Any other value throws.

/** R52t (U13): the counted reminder's line when the browser gave no zone. */
export const NO_ZONE_REMINDED = "Your browser reported no time zone, so the reminder time cannot be shown.";

/**
 * `item` (A20's shape), `now` (a `Date`, injected) and `timeZone` → the
 * reminded line, or null. A falsy `timeZone` gives NO_ZONE_REMINDED for a
 * counted reminder (no Intl call); `{reminded, of}` needs no zone.
 */
export function remindedLine(item, now, timeZone) {
	if (!Object.prototype.hasOwnProperty.call(item, "reminded")) return null;
	const reminded = item.reminded;
	if (reminded === null) return null;
	if (typeof reminded !== "object") {
		throw new Error(`${item.id}: unreadable reminded value ${JSON.stringify(reminded)}.`);
	}
	if ("count" in reminded) {
		if (timeZone) return remindedText(reminded, now, timeZone);
		// R52t (U13): no zone is said on this item, never thrown by Intl over
		// the whole screen. An unreadable entry still throws as remindedText
		// would (same checks, same order); only the time needs the zone.
		for (const key of ["count", "last_at", "last_by_name"]) {
			if (!(key in reminded)) throw new Error(`Reminders entry is missing ${key}.`);
		}
		if (!Number.isInteger(reminded.count) || reminded.count < 1) {
			throw new Error(`Reminders entry has an unreadable count: ${JSON.stringify(reminded.count)}.`);
		}
		if (typeof reminded.last_by_name !== "string" || !reminded.last_by_name) {
			throw new Error("Reminders entry has no last_by_name.");
		}
		return NO_ZONE_REMINDED;
	}
	const { reminded: r, of } = reminded;
	if (!Number.isInteger(r) || !Number.isInteger(of) || r < 1 || r > of) {
		throw new Error(`${item.id}: unreadable reminded value ${JSON.stringify(reminded)}.`);
	}
	return `${r} of ${of} reminded`;
}

// --- D62 (stories 1.1, 2.4): the due line and the overdue badge -------------
//
// mywork_model (D58) puts `due: {date, text, overdue}` on a period item whose
// step is known, and `due: null` on a period item with no step. The items no
// `_period_item` builds (setup gaps, the approvals item, sent-back items,
// mywork_model.sent_back_items) carry no `due` key at all. Engineering call
// (D62, coordinator note 7 Oct): an absent `due` reads like null, no line;
// the server is not changed to add due:null. Any other value throws.
//
// - overdue: "Overdue since Thu 7 Aug 2025", the warn (amber) tone, never the
//   block tone; it disables nothing (#305-2.4-1);
// - not yet due: "Due Tue 7 Oct 2025";
// - undeclared (`date` null): the server's own sentence, "No due date
//   declared", in the mute tone; never a guessed date.
// The date wording is dueDate.js's, the one D60's TB header uses. The items
// keep the server's rank (D58 ranks by due); nothing here re-sorts them.

export const DUE_TONE = "bg-surface-gray-2 text-ink-gray-7";
export const MUTE_TONE = "bg-surface-gray-1 text-ink-gray-5";

/** `item` (A20's shape) -> `{text, tone, overdue}`, or null. */
export function dueLine(item) {
	if (!Object.prototype.hasOwnProperty.call(item, "due")) return null;
	const due = item.due;
	if (due === null) return null;
	const who = `${item.id}: due`;
	if (typeof due !== "object") {
		throw new Error(`${who} is unreadable: ${JSON.stringify(due)}.`);
	}
	if (typeof due.overdue !== "boolean") {
		throw new Error(`${who} has no overdue flag (D58 always sends it).`);
	}
	if (!("date" in due)) {
		throw new Error(`${who} has no date (D58 sends it, null when undeclared).`);
	}
	if (due.date === null) {
		if (due.overdue) {
			throw new Error(`${who} is overdue with no date declared.`);
		}
		if (typeof due.text !== "string" || !due.text) {
			throw new Error(`${who} is undeclared with no text (D58 always sends it).`);
		}
		return { text: due.text, tone: MUTE_TONE, overdue: false };
	}
	const date = dueDateText(due.date, who);
	return due.overdue
		? { text: `Overdue since ${date}`, tone: OVERDUE_TONE, overdue: true }
		: { text: `Due ${date}`, tone: DUE_TONE, overdue: false };
}
