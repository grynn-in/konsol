// konsol#305 B29: timefmt.js
//
// The one time formatter and the one zone lookup for the whole app
// (B09 freshness bar, B27 TB list). Nothing here reads the machine's clock:
// `now` and `timeZone` are always passed in.

// B09b: a server timestamp must carry its zone ("Z" or "+hh:mm"). A zone-less
// string would be read in the browser's zone and show the wrong hour, so it is
// refused rather than guessed.
const ZONED = /(Z|[+-]\d{2}:?\d{2})$/;

/** A zoned ISO string -> Date; anything else throws. */
export function parseZoned(value) {
  if (typeof value !== "string" || !ZONED.test(value)) {
    throw new Error(`Timestamp has no time zone: ${value}`);
  }
  return new Date(value);
}

function sameCalendarDay(a, b, timeZone) {
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return fmt.format(a) === fmt.format(b);
}

/** A Date, shown in `timeZone`, relative to `now` — "10:42" today, "Sep 20, 10:42" otherwise. */
export function formatTime(date, now, timeZone) {
  const time = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);

  if (sameCalendarDay(date, now, timeZone)) {
    return time;
  }

  const day = new Intl.DateTimeFormat("en-US", {
    timeZone,
    month: "short",
    day: "numeric",
  }).format(date);

  return `${day}, ${time}`;
}

const MS_PER_DAY = 24 * 60 * 60 * 1000;

function calendarParts(date, timeZone) {
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return fmt.format(date).split("-").map(Number);
}

function daysBetween(fromParts, toParts) {
  const from = Date.UTC(fromParts[0], fromParts[1] - 1, fromParts[2]);
  const to = Date.UTC(toParts[0], toParts[1] - 1, toParts[2]);
  return Math.round((to - from) / MS_PER_DAY);
}

/**
 * konsol#305 F03: the one age rule for every screen that shows "how old is
 * this item" (Approvals' "oldest waiting" header, My work's `since`). They
 * used to disagree on the same item (live: 16 vs 17 days) because
 * approvals.js floored raw elapsed milliseconds while myWork.js diffed
 * calendar dates — a result that drifts by a day depending on what time of
 * day `now` lands on. This is always a **whole-calendar-day** difference,
 * judged in `timeZone`, never elapsed-time division.
 *
 * `value` is either a zoned ISO timestamp (an instant: read by its
 * calendar date in `timeZone`) or a bare `YYYY-MM-DD` date (already a
 * calendar date, with no time of day to misread, so no zone conversion
 * applies to it). `null`/`undefined` -> `null` (nothing to show). A
 * `value` whose calendar date is after `now`'s (clock skew on a
 * timestamp, or a not-yet-ended period on a bare date) also renders
 * nothing — a negative age would be a lie. `0` days -> "today"; otherwise
 * "1 day" / "N days".
 */
export function ageText(value, now, timeZone) {
  if (!value) return null;
  const valueParts = ZONED.test(value)
    ? calendarParts(parseZoned(value), timeZone)
    : value.slice(0, 10).split("-").map(Number);
  const days = daysBetween(valueParts, calendarParts(now, timeZone));
  if (days < 0) return null;
  if (days === 0) return "today";
  return days === 1 ? "1 day" : `${days} days`;
}

/** The user's IANA zone: Frappe's boot, else the browser's; null if neither says. */
export function userTimeZone() {
  const boot = typeof window !== "undefined" && window && window.frappe && window.frappe.boot;
  const fromBoot = boot && boot.time_zone && boot.time_zone.user;
  if (fromBoot) return fromBoot;
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}
