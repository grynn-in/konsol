// konsol#305 B31: contextRefresh.js
//
// The header's "Checks: <status>" comes from the period context (A15
// get_context), which AppShell loads once per period. The Checks screen polls
// the run; when it sees the latest run reach a terminal state, or a new run
// appear, it asks the shell to reload the context, once. The shell provides
// the reload under CONTEXT_RELOAD; the screen injects it. No new polling.
//
// Run states are the Assertion Run `status` options. An unknown one is
// refused, never guessed as terminal or in flight.

/** provide/inject key: AppShell provides `() => void`, the Checks screen injects it. */
export const CONTEXT_RELOAD = "konsol.close.reloadContext";

export const IN_FLIGHT = Object.freeze(["Queued", "Running"]);
export const TERMINAL = Object.freeze(["Green", "Amber", "Red", "Error"]);

function checkStatus(latest) {
  if (latest && !IN_FLIGHT.includes(latest.status) && !TERMINAL.includes(latest.status)) {
    throw new Error(`Unknown check run status: ${latest.status}`);
  }
}

/**
 * Should the period context be reloaded after this poll?
 *
 * `prev` is the latest run the screen saw on its previous poll: `undefined`
 * before the first poll of a period (the context was just loaded, so no), or
 * `null` when the period had no run. `next` is get_checks' `latest`.
 */
export function contextReloadNeeded(prev, next) {
  checkStatus(next);
  if (prev === undefined || !next) return false;
  if (!prev || prev.name !== next.name) return true; // a new run
  return IN_FLIGHT.includes(prev.status) && TERMINAL.includes(next.status);
}
