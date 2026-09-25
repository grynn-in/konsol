// konsol#305 B09: freshness.js
//
// Turns the freshness payload (A05/A16's `{state, as_of, pending,
// changed_since, last_failed}`) into what the freshness bar shows:
// `{tone, text, detail}`.
//
// No policy default: this module never reads the machine's clock or time
// zone. Both `now` and `timeZone` are required parameters, and an unknown
// `state` — one A05 never declared — renders as an explicit red warning,
// never as "fresh".

const TONE_BY_STATE = {
  fresh: "neutral",
  pending: "blue",
  stale: "amber",
  failed: "red",
  never_built: "red",
};

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
// B09b: a server timestamp must carry its zone ("Z" or "+hh:mm"). A zone-less
// string would be read in the browser's zone and show the wrong hour, so it is
// refused rather than guessed.
const ZONED = /(Z|[+-]\d{2}:?\d{2})$/;
function parseZoned(value) {
  if (typeof value !== "string" || !ZONED.test(value)) {
    throw new Error(`Timestamp has no time zone: ${value}`);
  }
  return new Date(value);
}

function formatTime(date, now, timeZone) {
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

/**
 * `(payload, now, timeZone)` → `{tone, text, detail}`.
 *
 * `now` is a `Date`; `timeZone` is an IANA zone name (for example
 * `"Europe/London"`). Neither is defaulted — the caller declares them.
 */
export function freshnessView(payload, now, timeZone) {
  if (!timeZone) {
    throw new Error("freshnessView requires a time zone");
  }
  if (!(now instanceof Date) || Number.isNaN(now.getTime())) {
    throw new Error("freshnessView requires a valid `now`");
  }
  if (!payload || typeof payload.state !== "string") {
    throw new Error("freshnessView requires a payload with a state");
  }

  const { state, as_of, pending, changed_since, last_failed } = payload;

  if (!(state in TONE_BY_STATE)) {
    return {
      tone: "red",
      text: `Unknown freshness state: ${state}`,
      detail: null,
    };
  }

  const tone = TONE_BY_STATE[state];

  switch (state) {
    case "fresh":
      return {
        tone,
        text: `As of ${formatTime(parseZoned(as_of), now, timeZone)}`,
        detail: null,
      };

    case "pending":
      return {
        tone,
        text: `${pending} ${pending === 1 ? "change" : "changes"} pending`,
        detail: null,
      };

    case "stale": {
      const names = (changed_since || []).join(", ");
      return {
        tone,
        text: `Numbers older than changes to ${names}`,
        detail: as_of ? `As of ${formatTime(parseZoned(as_of), now, timeZone)}` : null,
      };
    }

    case "failed": {
      const reason = last_failed && last_failed.reason;
      const at = last_failed && last_failed.at;
      return {
        tone,
        text: `Rebuild failed: ${reason}`,
        detail: at ? `Last attempt ${formatTime(parseZoned(at), now, timeZone)}` : null,
      };
    }

    case "never_built":
      return {
        tone,
        text: "Numbers have never been built",
        detail: null,
      };

    default:
      // Unreachable: every key of TONE_BY_STATE is handled above.
      throw new Error(`freshnessView: unhandled known state ${state}`);
  }
}
