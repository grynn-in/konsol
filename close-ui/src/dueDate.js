// konsol#305 D62: dueDate.js
//
// The one due-date wording of the close app (story 2.4), moved here from
// tbTable.js (D60) so the Trial balances header and My work (D62) share it
// instead of each formatting dates their own way. Pure: no frappe/vue import.
//
// Decision #305-2.4-1: a deadline is show-only and never blocks. The overdue
// chip is the warn (amber) tone, never the block (red) tone.

export const OVERDUE_TONE = "bg-surface-amber-1 text-ink-amber-3";
export const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

// A due date is a calendar date, not an instant: it is formatted in UTC so
// the browser's zone can never move it to the day before or after.
const DUE_FORMAT = new Intl.DateTimeFormat("en-GB", {
  weekday: "short",
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

/**
 * "2025-10-07" -> "Tue 7 Oct 2025". `who` names the caller in the error. A
 * string that is not an ISO date, or not a real calendar date, throws.
 */
export function dueDateText(iso, who) {
  const m = typeof iso === "string" ? ISO_DATE.exec(iso) : null;
  if (!m) {
    throw new Error(`${who}: the due date is not an ISO date: ${iso}`);
  }
  const [, y, mo, d] = m;
  const date = new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d)));
  if (date.getUTCDate() !== Number(d) || date.getUTCMonth() !== Number(mo) - 1) {
    throw new Error(`${who}: the deadline's due is not a real date: ${iso}`);
  }
  const parts = Object.fromEntries(DUE_FORMAT.formatToParts(date).map((p) => [p.type, p.value]));
  return `${parts.weekday} ${parts.day} ${parts.month} ${parts.year}`;
}
