// konsol#305 B07: route.js
//
// D5: the period lives in the URL, /close/<year>/<period>/<screen>, and
// nothing is silently remembered — no browser storage, no "last viewed"
// default. `parse` never coerces a malformed year or period into a valid
// one; it names the problem instead. Whether a given (year, period) is a
// period the server has declared — including P0 and P13 — is the server's
// job, not this module's: a well-formed but undeclared period parses fine
// and is refused later, by the server.
//
// `/close` with nothing after it parses to `{year: null}`. There is no
// client default for that case; the caller asks the server (A15) for the
// landing period.

export const SCREENS = ["my-work", "trial-balances", "checks", "sign-off"];

const INTEGER_RE = /^\d+$/;

/**
 * path (string) → {year, period, screen} | {year: null} | {error: string}.
 *
 * `year` and `period` are required to be plain non-negative integers written
 * in decimal (`INTEGER_RE`); anything else — "abc", "9.5", "P13", a sign, a
 * leading "FY" — is an explicit `{error: ...}`, never `Number()`-coerced.
 */
export function parse(path) {
  const parts = String(path)
    .split("/")
    .filter((part) => part.length > 0);

  if (parts[0] !== "close") {
    return { error: "not a close path" };
  }
  if (parts.length === 1) {
    return { year: null };
  }

  const [, yearStr, periodStr, screen] = parts;

  if (!INTEGER_RE.test(yearStr || "")) {
    return { error: "invalid year" };
  }
  if (!INTEGER_RE.test(periodStr || "")) {
    return { error: "invalid period" };
  }
  if (!SCREENS.includes(screen)) {
    return { error: "unknown screen" };
  }

  return { year: Number(yearStr), period: Number(periodStr), screen };
}

/** {year, period, screen} → "/close/<year>/<period>/<screen>". */
export function format({ year, period, screen }) {
  return `/close/${year}/${period}/${screen}`;
}
