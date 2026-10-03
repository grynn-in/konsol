// konsol#305 A14: approvals.js
//
// Pure queue view and request-building helpers for the Approvals screen
// (E6; stories 6.2, 6.3, 6.4; R2, R5). Turns approvals_api.get_queue's
// payload (A10) into what Approvals.vue (A19) renders, and builds the
// POST body for approval_api.reject. Takes no vue/frappe import.
//
// The approve request reuses rates.js's `approveAction` and `approveBody`
// (rates.js:143-149, :345-363) rather than a copy: they are imported here
// and re-exported, so Approvals.vue reaches both "through approvals.js"
// (A19's own wording) without a second import of rates.js.
//
// `effectView` (adjustments.js, A13) is reused the same way for a journal
// item's `effect`: every other doctype in the queue has no `effect` key
// (approvals_model.py's `_shape`), so it is only ever applied when the key
// is present, never guessed into existence for a non-journal item.
//
// `queueView`'s "oldest <age>" is computed from `waiting.oldest`, a zoned
// datetime (approvals_model.waiting_for_me's `created`, A08/A10's `_iso`),
// not a bare date — so this does not reuse myWork.js's `ageText`, which
// takes a date-only `since` string (My work's own convention) and would
// misparse a full zoned timestamp. The age here is computed the same way
// rates.js's `pendingCreatedText` reads a zoned value: through
// `parseZoned`, never a guessed re-zoning.

import { effectView } from "./adjustments.js";
import { approveAction, approveBody } from "./rates.js";
import { formatTime, parseZoned } from "./timefmt.js";

export { approveAction, approveBody };

//: approval_api.reject's own refusal sentence (approval_api.py:76-77),
//: shown here before the request is ever sent.
export const REJECT_REASON_ERROR = "A rejection needs a reason.";

const MS_PER_DAY = 24 * 60 * 60 * 1000;

function timeText(value, now, timeZone) {
	if (value === null || value === undefined) {
		return "not recorded";
	}
	return formatTime(parseZoned(value), now, timeZone);
}

/** A zoned `oldest` timestamp, elapsed against `now`, as "today", "1 day" or
 * "N days". `null`/`undefined` (nothing waiting) gives `null`: no age to
 * show. A timestamp that reads later than `now` (clock skew, not expected
 * from the server) floors at "today" rather than showing a negative count. */
function ageText(oldest, now) {
	if (!oldest) {
		return null;
	}
	const days = Math.floor((now.getTime() - parseZoned(oldest).getTime()) / MS_PER_DAY);
	if (days <= 0) {
		return "today";
	}
	return days === 1 ? "1 day" : `${days} days`;
}

/** `payload.waiting` (`{count, oldest}`, approvals_model.waiting_for_me,
 * always present — `approvals_api.queue_for` sets it unconditionally) ->
 * "N waiting for you · oldest <age>", or "Nothing waiting for you" when the
 * count is 0. A missing `waiting`, or one with no numeric `count`, is a
 * contract break with the server and throws rather than reading as "nothing
 * waiting" (U12). */
function headerText(waiting, now) {
	if (!waiting || typeof waiting.count !== "number") {
		throw new Error("queueView requires payload.waiting ({count, oldest}).");
	}
	const count = waiting.count;
	if (!count) {
		return "Nothing waiting for you";
	}
	const age = ageText(waiting.oldest, now);
	return age ? `${count} waiting for you · oldest ${age}` : `${count} waiting for you`;
}

/** `payload.hidden` (an entity-scoped count, A10, always present — the same
 * guarantee as `waiting`) -> "N awaiting outside your scope" when it is
 * positive (mirrors rates.js's `pendingEmptyMessage` / periodGrid.js's
 * hiddenNote), else `null`: no note when nothing is hidden. A missing or
 * non-numeric `hidden` throws rather than reading as zero (U12). */
function hiddenNoteText(hidden) {
	if (typeof hidden !== "number") {
		throw new Error("queueView requires payload.hidden (a number).");
	}
	return hidden > 0 ? `${hidden} awaiting outside your scope` : null;
}

/** The server's item (approvals_model.queue_items' shape, every field
 * passed through unchanged except the ones below), formatted for display.
 * A journal's `effect` runs through `effectView` when the key is present;
 * no other doctype carries one. */
function baseView(item, now, timeZone) {
	const view = { ...item, createdText: timeText(item.created, now, timeZone) };
	if ("effect" in item) {
		view.effect = effectView(item.effect);
	}
	return view;
}

/** One of `payload.items` (not sent back) -> the Approvals screen's row.
 * An `inline` item (every doctype except Business Combination/Disposal)
 * gets `approve: approveAction(item.approve)` (R2's `not_approver` and
 * R5's `refused` pass straight through as `approveAction`'s "none"/
 * "refused" kinds, carrying the server's sentence) and no `deskLink`. A
 * `desk` item (BC/BD, the W3-1..4 engineering call: approved in the Desk
 * only) gets `deskLink: item.desk` and no approve action at all — the
 * server's computed mode is never shown as a button there. */
function itemView(item, now, timeZone) {
	const inline = Boolean(item.inline);
	const view = baseView(item, now, timeZone);
	view.inline = inline;
	if (!inline) {
		view.approve = null;
		view.deskLink = item.desk;
		return view;
	}
	view.approve = approveAction(item.approve);
	// A "reason" item (Allowed with reason) shows `approve.message` as the
	// label on the reason input (Approvals.vue). `rates_model.approve_mode`
	// only ever returns "reason" with the no-reason refusal as `message`
	// (rates_model.py:178-204: the "direct" branch returns before this one,
	// so `problem` is never falsy here) — a reason item with no message is
	// a contract break with the server, never papered over with an invented
	// label (U12).
	if (view.approve.kind === "reason" && !view.approve.message) {
		throw new Error("A \"reason\" approve item needs the server's own message.");
	}
	view.deskLink = null;
	return view;
}

/** One of `payload.sent_back` -> the Approvals screen's "sent back" row:
 * `rejection.reason`, the actor and the formatted `at`, and no approve
 * action at all (E6-P11: a sent-back item is never counted as waiting, and
 * is never shown with a control to act on it here — the preparer, not the
 * approver, acts next). */
function sentBackView(item, now, timeZone) {
	const inline = Boolean(item.inline);
	const rejection = item.rejection || {};
	const view = baseView(item, now, timeZone);
	delete view.approve;
	view.inline = inline;
	view.deskLink = inline ? null : item.desk;
	view.rejection = {
		reason: rejection.reason,
		actor: rejection.actor,
		at: timeText(rejection.at, now, timeZone),
	};
	return view;
}

/**
 * `approvals_api.get_queue`'s payload (A10) -> everything the Approvals
 * screen reads: `{header, items, sentBack, hiddenNote, canApprove,
 * selfApproval}`.
 *
 * Requires a `timeZone` and a valid `now` (B09b, mirrors adjustments.js's
 * `journalsView` / rates.js's `pendingCreatedText`): there is no guessed
 * display for a timestamp with no zone, or with no clock to compare it to.
 */
export function queueView(payload, now, timeZone) {
	if (!timeZone) {
		throw new Error("queueView requires a time zone");
	}
	if (!(now instanceof Date) || Number.isNaN(now.getTime())) {
		throw new Error("queueView requires a valid `now`");
	}
	const items = (payload.items || []).map((item) => itemView(item, now, timeZone));
	const sentBack = (payload.sent_back || []).map((item) => sentBackView(item, now, timeZone));
	return {
		header: headerText(payload.waiting, now),
		items,
		sentBack,
		hiddenNote: hiddenNoteText(payload.hidden),
		canApprove: Boolean(payload.can_approve),
		selfApproval: payload.self_approval || null,
	};
}

/**
 * `(doctype, name, reason)` -> `{body}` or `{error}` for
 * `approval_api.reject`. A blank or whitespace-only reason is refused on
 * the client with the server's own sentence (`REJECT_REASON_ERROR`,
 * approval_api.py:76-77) before any request goes out; a real reason is
 * trimmed before it goes in the body.
 */
export function rejectBody(doctype, name, reason) {
	const trimmed = (reason || "").trim();
	if (!trimmed) {
		return { error: REJECT_REASON_ERROR };
	}
	return { body: { doctype, name, reason: trimmed } };
}
