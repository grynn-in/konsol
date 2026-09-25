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
