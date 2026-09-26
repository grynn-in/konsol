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
 * `item` (A20's shape) → the in-app route path for a period item, or
 * `{external: "/app/..."}` for a setup-gap item (story 0.4). A period
 * item's route never depends on client-side "last viewed" state (D5): the
 * period comes from the item itself.
 *
 * B18b: when the item's action names an entity (an Entity Accountant's
 * "Upload TB for <entity>" item), the route carries it as `?entity=<code>`,
 * so TrialBalances.vue can open that entity's detail area directly. An
 * action with no entity carries no query string — never an invented one.
 */
export function itemRoute(item) {
	const action = item.action || {};
	if (action.desk) {
		return { external: action.desk };
	}
	const period = item.period;
	const path = format({ year: period.fiscal_year, period: period.fiscal_period, screen: action.screen });
	return action.entity ? `${path}?entity=${encodeURIComponent(action.entity)}` : path;
}

const MS_PER_DAY = 24 * 60 * 60 * 1000;

/**
 * `since` (an ISO date, e.g. "2026-09-13", or null) and `today` (a `Date`,
 * always supplied by the caller) → an age string such as "12 days" or
 * "1 day" for B18's My work screen (A53, story 1.1).
 *
 * This module never reads the clock: `today` is always an injected
 * parameter, the same rule the pure Python models follow — the age is
 * computed from `since` and a `today` that is injected, never read inside
 * a pure module.
 *
 * `since === null` (a setup-gap item, A53's `since_reason:
 * "configuration gap"`) renders nothing: `null`. A `since` that is still in
 * the future (the period has not ended yet) also renders nothing — there
 * is no elapsed age to show, and a negative day count would be a lie.
 */
export function ageText(since, today) {
	if (!since) return null;
	const [year, month, day] = since.split("-").map(Number);
	const sinceUTC = Date.UTC(year, month - 1, day);
	const todayUTC = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
	const days = Math.floor((todayUTC - sinceUTC) / MS_PER_DAY);
	if (days < 0) return null;
	return days === 1 ? "1 day" : `${days} days`;
}
