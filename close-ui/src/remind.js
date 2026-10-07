// konsol#305 Y61: remind.js (story 1.5; C-R1..C-R6)
//
// The one pure module behind the three Remind surfaces (the Trial balances
// list, the grid's TB cell, the IC panel):
// - `remindBody(period, entity, topic)`: the exact POST body for Y54's
//   `konsol.close.remind_api.remind(fiscal_year, fiscal_period, entity,
//   topic)`. Nothing else is sent: the server picks the recipients.
// - `remindedText(entry, now, timeZone)`: "Reminded 2× · last <time> by
//   <full name>" from a payload's `reminders` entry ({count, last_at,
//   last_by, last_by_name}, close_event.reminders via Y53), or null when the
//   entry is null (no reminder sent). The time is zoned by timefmt.js's
//   formatTime, the app's one time formatter.
//
// No silent fallbacks: an unknown topic, a missing key or an unreadable
// count throws; a zone-less timestamp is refused by parseZoned.

import { formatTime, parseZoned } from "./timefmt.js";

export const REMIND = "konsol.close.remind_api.remind";

/** remind_model.TOPICS / TOPIC_LABEL (a test parses the Python and checks parity). */
export const TOPICS = Object.freeze(["tb", "ic"]);
export const TOPIC_LABEL = Object.freeze({ tb: "Trial balance", ic: "Intercompany" });

/** The Remind POST body: exactly {fiscal_year, fiscal_period, entity, topic}. */
export function remindBody(period, entity, topic) {
	if (!TOPICS.includes(topic)) {
		throw new Error(`Unknown reminder topic ${JSON.stringify(topic)}: use one of ${TOPICS.join(", ")}.`);
	}
	if (!period || period.fiscal_year == null || period.fiscal_period == null) {
		throw new Error("Remind: no period (fiscal_year and fiscal_period) to remind about.");
	}
	if (typeof entity !== "string" || !entity) {
		throw new Error("Remind: no entity to remind.");
	}
	return {
		fiscal_year: period.fiscal_year,
		fiscal_period: period.fiscal_period,
		entity,
		topic,
	};
}

/** "Reminded N× · last <time> by <full name>", or null for no reminder. */
export function remindedText(entry, now, timeZone) {
	if (entry === null) return null;
	if (entry === undefined || typeof entry !== "object") {
		throw new Error("Payload is missing its reminders entry.");
	}
	for (const key of ["count", "last_at", "last_by_name"]) {
		if (!(key in entry)) throw new Error(`Reminders entry is missing ${key}.`);
	}
	const { count, last_at: lastAt, last_by_name: byName } = entry;
	if (!Number.isInteger(count) || count < 1) {
		throw new Error(`Reminders entry has an unreadable count: ${JSON.stringify(count)}.`);
	}
	if (typeof byName !== "string" || !byName) {
		throw new Error("Reminders entry has no last_by_name.");
	}
	const at = formatTime(parseZoned(lastAt), now, timeZone);
	return `Reminded ${count}× · last ${at} by ${byName}`;
}
