// konsol#305 E407: rates.js
//
// Pure view and request-building helpers for the Rates screen (stories 4.1,
// 4.3; R5). Turns the three `rates_api.py` GET payloads (`get_rates`,
// `get_pending`, `get_ownership`) into what `Rates.vue` shows, and builds
// the POST bodies for `rates_api.save_rate` and `approval_api.approve`. It
// does not re-decide policy or the move rule: those stay on the server, and
// their sentences are shown exactly as returned (E4-P11: no machine here,
// just pure functions). Imports no vue, frappe-ui or xstate (mirrors
// checks.js). It does import timefmt.js (B29's one formatter and zone
// lookup), the same as tbTable.js does for the TB list.
//
// Where this differs from the row's own sketch (written 30 Sep against the
// plan, before E403/E405/E406 were built and amended by #305-W2-10/W2-14):
// - `previousLabel` is the server's `previous.label` verbatim, e.g.
//   "FY2026 P07 (GER-123)" (group_rates.py's own `previous_approved` format,
//   mirrored by rates_model._previous_cell). The sketch showed a bare
//   "FY2026 P08": re-deriving that from fiscal_year/fiscal_period here would
//   duplicate the server's label format. We pass it through instead.
// - Each cell view carries a raw `status` alongside `statusLabel` (not in
//   the sketch's field list), the same pattern checks.js's causeView uses
//   (status + label together): `Rates.vue` needs the raw value to decide
//   editability without string-matching a label.
// - `ownershipView` also returns `hiddenCount` (payload `hidden`): W2-10/
//   W2-14 (2 Oct) added entity-permission cuts with a hidden count to
//   get_ownership after this row's sketch was written; dropping it here
//   would silently hide from a scoped Viewer that entities exist beyond
//   what they can see.
// - `pendingView` and `ownershipView` have no dedicated cases in the row's
//   "test first" list (only gridView/saveBody/approveAction/approveBody do);
//   this file adds one test each so neither exported function ships with
//   zero coverage.
// - R01k (SPA must-fix 2): `previousValue` was the bare true rate
//   (`previous.rate`, always "per 1"), shown with no unit next to a quote
//   that may be "per 100" or more — e.g. JPY 0.6673 per 100 beside a
//   previous of 0.0066070000000000005. `previous` carries its own
//   `quoted_per` (rates_model.py's `_previous_cell`), so it is re-expressed
//   as a quote in that same unit, matching the wireframe (Rates.dc.html),
//   which shows both sides as quotes, never a bare true rate.
// - R01o (SPA should-fix 6): the cell view dropped `change_reason`,
//   `edited_by` (#305-W2-14), `extra_drafts` and `source`, all already on
//   every `rates_model` cell (and, for `edited_by`, set by `rates_api.py`
//   alongside `approve`): an approver could not see why a rate moved, who
//   else touched the draft besides its owner, that more than one draft
//   exists for the same grain, or that a quote came from the ERP pre-fill
//   rather than being typed. `extraDraftsText` turns the `extra_drafts`
//   array into the count the approver needs ("and N more draft(s)"), not
//   the names: those are internal doc IDs, not something to show.
// - R01n (SPA should-fix 5): `Rates.vue`'s `resetDrafts` kept a cell's draft
//   across a reload only when a save had just refused it (a key in
//   `cellErrors`); any other unsaved, un-refused edit was silently
//   overwritten by the server's current value on the next reload (the one
//   `approve()` triggers for every grid cell, not only the one just
//   approved). `mergeDrafts` is the pure decision this needs: keep the
//   existing edit buffer whenever it still differs from its own baseline, or
//   the cell currently carries a refusal, and only then fall back to a fresh
//   baseline built from the server's cell. It is exported (not inlined in
//   `resetDrafts`) because it has two independent reasons to keep an old
//   draft (dirty, refused) and three input shapes (no old draft, a clean
//   old draft, a dirty/refused one) worth covering with real inputs rather
//   than asserting the source text names a variable.
// - R01v: a cell's raw `flag` (facts: live, 28/28 Approved cells flagged
//   "+0.0% · flagged" because `group_rates.move_problem` returns the same
//   "declare the threshold" sentence for every cell with a reference,
//   whenever the threshold is undeclared, Approved cells included) is
//   replaced by `flagTag`/`flagMessage`, the pure decision `moveFlagView`
//   makes over where it shows: never on an Approved cell (a move is judged
//   only where a rate can still be entered), and as the short "no threshold
//   declared" cue rather than "flagged" (and with no per-cell warning box)
//   while the threshold itself is undeclared — that sentence already shows
//   once, in the banner. `thresholdText` likewise returns `null`, not the
//   gap message, while undeclared: the summary line adds nothing the banner
//   does not already say. `ownershipGapsCount` is the same kind of fix
//   folded in from R01q's gate: the Ownership tab's count was visible gaps
//   only (`blocking.length`), so a scoped user with none of their own could
//   read a plain "Ownership" tab (no count, read as none) while gaps
//   existed outside their scope; it is now `blocking.length + blockingHidden`.

import { formatTime, parseZoned } from "./timefmt.js";

export const STATUS_LABELS = {
  missing: "Missing",
  awaiting_approval: "Awaiting approval",
  approved: "Approved",
};

const APPROVE_KIND = {
  direct: "button",
  reason: "reason",
  refused: "refused",
  not_approver: "none",
};

const NO_REASON_ERROR = "Give a reason: Close Settings allows self-approval only with a reason.";

function statusLabel(status) {
  const label = STATUS_LABELS[status];
  if (!label) {
    throw new Error(`Unknown rate status: ${status}`);
  }
  return label;
}

function deltaText(delta) {
  if (delta === null || delta === undefined) {
    return "—";
  }
  const pct = delta * 100;
  const sign = pct < 0 ? "−" : "+";
  return `${sign}${Math.abs(pct).toFixed(1)}%`;
}

function previousLabel(previous) {
  return previous ? previous.label : "No previous approved rate";
}

/** Re-multiplying a division in plain float arithmetic can land one bit off
 * the clean decimal (0.6607 / 100, then back, shows 0.6606999999999999).
 * MariaDB keeps a quote to 9 decimal places (group_rates.py's own
 * MIN_SIGNIFICANT_DIGITS comment), so rounding there removes exactly that
 * noise without guessing a per-cell precision. */
function roundToQuotePrecision(value) {
  return Math.round(value * 1e9) / 1e9;
}

/** `previous` (server shape: `{rate, quoted_per, ...}`, `rate` always "per
 * 1") -> `{value, quotedPer}` for display as a quote in its own unit, e.g.
 * previous.rate 0.006607 with quoted_per "100" -> {value: 0.6607, quotedPer:
 * 100}. `null` previous (no earlier approved rate) -> both null. */
function previousQuote(previous) {
  if (!previous) {
    return { value: null, quotedPer: null };
  }
  const per = Number(previous.quoted_per) || 1;
  return { value: roundToQuotePrecision(previous.rate * per), quotedPer: per };
}

/**
 * {kind, message}: kind is one of "button" (direct), "reason", "refused",
 * "none" (not_approver). Throws on an unknown server mode rather than
 * guessing what the caller may do.
 */
export function approveAction(approve) {
  const kind = APPROVE_KIND[approve.mode];
  if (!kind) {
    throw new Error(`Unknown approve mode: ${approve.mode}`);
  }
  return { kind, message: approve.message };
}

/** `["GER-10"]` (one extra draft besides the one shown) -> "and 1 more
 * draft"; two or more -> "and N more drafts". `[]`/undefined -> null: no
 * line to show. The names themselves are internal doc IDs and never shown. */
function extraDraftsText(extraDrafts) {
  const count = (extraDrafts || []).length;
  if (!count) {
    return null;
  }
  return count === 1 ? "and 1 more draft" : `and ${count} more drafts`;
}

/**
 * Where a cell's move problem (`cell.flag`, `group_rates.move_problem`'s
 * sentence) should show (R01v, SPA must-fix follow-up): a move is judged
 * only where a rate can still be entered, so an Approved cell never shows
 * it, however the server set it (goal 1) — the server is right to report
 * it, the view decides where to place it.
 *
 * A flag only means a judged move once a threshold is declared
 * (`thresholdDeclared`). While it is not, `group_rates.move_problem`
 * returns the identical "declare the threshold" sentence
 * (`close_policy_model.RATE_MOVE_MESSAGE`) for every cell that has
 * something to compare against, regardless of how far the rate has
 * actually moved — that sentence already reaches the screen once, in the
 * banner (`bannerFor`'s `policy_gaps`), so a cell shows only the short
 * "no threshold declared" cue beside its delta (goal 3), never the
 * sentence a second and third time (goal 2), and never the amber "flagged"
 * word, which implies a judged, real move.
 *
 * Returns `{tag, message}`: `tag` is `"flagged"` (threshold declared, this
 * rate exceeds it or the ERP quote), `"no threshold declared"` (threshold
 * undeclared, a reference exists) or `null` (no flag, or Approved).
 * `message` — the full sentence for the per-cell warning box — is non-null
 * only for `"flagged"`.
 */
export function moveFlagView(cell, thresholdDeclared) {
  if (cell.status === "approved" || !cell.flag) {
    return { tag: null, message: null };
  }
  if (thresholdDeclared) {
    return { tag: "flagged", message: cell.flag };
  }
  return { tag: "no threshold declared", message: null };
}

function cellView(cell, thresholdDeclared) {
  const prevQuote = previousQuote(cell.previous);
  const moveFlag = moveFlagView(cell, thresholdDeclared);
  return {
    name: cell.name,
    status: cell.status,
    statusLabel: statusLabel(cell.status),
    value: cell.quote,
    quotedPer: cell.quoted_per,
    previousValue: prevQuote.value,
    previousQuotedPer: prevQuote.quotedPer,
    previousLabel: previousLabel(cell.previous),
    deltaText: deltaText(cell.delta),
    flagTag: moveFlag.tag,
    flagMessage: moveFlag.message,
    preparer: cell.owner,
    approve: cell.approve ? approveAction(cell.approve) : null,
    changeReason: cell.change_reason ?? null,
    editedBy: cell.edited_by ?? null,
    extraDraftsText: extraDraftsText(cell.extra_drafts),
    source: cell.source ?? null,
  };
}

function rowView(row, thresholdDeclared) {
  return {
    fromCurrency: row.from_currency,
    toCurrency: row.to_currency,
    required: row.required,
    closing: cellView(row.closing, thresholdDeclared),
    average: cellView(row.average, thresholdDeclared),
  };
}

function bannerFor({ pairs_error, blockers, policy_gaps }) {
  const lines = [];
  if (pairs_error) {
    lines.push(`The warehouse could not say which currencies this period translates: ${pairs_error}`);
  }
  for (const blocker of blockers || []) {
    lines.push(blocker);
  }
  for (const gap of policy_gaps || []) {
    lines.push(gap.message);
  }
  return lines;
}

/** `%g`-ish: 30 -> "30", 12.5 -> "12.5" — no guessed decimals, no float noise. */
function formatPct(n) {
  return String(Math.round(n * 10000) / 10000);
}

/**
 * R01v goal 2: while the threshold is undeclared, its gap sentence belongs
 * in the banner only — `bannerFor` already lists it once from
 * `policy_gaps`. The summary line that `thresholdText` feeds adds nothing
 * in that case (`null`); still validated against `policyGaps` so a payload
 * that forgets the gap is a loud error here, not a silently blank screen.
 */
function thresholdText(thresholdPct, policyGaps) {
  if (thresholdPct === null || thresholdPct === undefined) {
    const gap = (policyGaps || []).find((g) => g.code === "rate_move_undeclared");
    if (!gap) {
      throw new Error("threshold_pct is undeclared but no rate_move_undeclared policy gap was given.");
    }
    return null;
  }
  return `Moves over ${formatPct(thresholdPct)}% need a Reason for Change`;
}

/**
 * `get_rates` payload -> `{rows, unrequired, banner, summary, canEnter,
 * canApprove, thresholdText}`. `banner` is an array of sentences, in order:
 * the pairs_error sentence (if any), each blockers sentence, each
 * policy_gaps[].message.
 */
export function gridView(payload) {
  const thresholdDeclared = payload.threshold_pct !== null && payload.threshold_pct !== undefined;
  return {
    rows: (payload.rows || []).map((row) => rowView(row, thresholdDeclared)),
    unrequired: (payload.unrequired || []).map((row) => rowView(row, thresholdDeclared)),
    banner: bannerFor(payload),
    summary: payload.summary,
    canEnter: Boolean(payload.can_enter),
    canApprove: Boolean(payload.can_approve),
    thresholdText: thresholdText(payload.threshold_pct, payload.policy_gaps),
    // E409c: the Group Exchange Rate `quoted_per` field's own Select options
    // (E409b), passed through verbatim. No hand-copied list here: an empty
    // default is a visible gap, never a guessed list of units.
    quotedPerOptions: payload.quoted_per_options || [],
  };
}

function isBlank(value) {
  return value === null || value === undefined || value === "";
}

/**
 * `(period, row, rateType, input)` -> `{body}` or `{error}` for
 * `rates_api.save_rate`. `row` is a grid row as `gridView`'s `rows`/
 * `unrequired` hand it back out (`fromCurrency`/`toCurrency`, plus
 * `closing`/`average` cell views carrying `name`), `rateType` is
 * `"Closing"` or `"Average"`, and `input` is `{quote, quotedPer,
 * changeReason}` from the form. The body carries exactly the grain, the
 * quote, the unit and the reason — never `docstatus`, `source`, `owner`,
 * `erp_quote` or `source_note` (forged on the client too, same as the
 * server refuses them): the magnitude, digits and move rules stay on the
 * server.
 */
export function saveBody(period, row, rateType, input) {
  const key = rateType === "Closing" ? "closing" : rateType === "Average" ? "average" : null;
  if (!key) {
    throw new Error(`Unknown rate type: ${rateType}`);
  }
  const cell = row[key];

  const quote = input.quote;
  if (isBlank(quote) || Number.isNaN(Number(quote))) {
    return { error: "Enter a quote." };
  }
  if (isBlank(input.quotedPer)) {
    return { error: "Choose Quoted Per." };
  }

  const body = {
    fiscal_year: period.fiscal_year,
    fiscal_period: period.fiscal_period,
    from_currency: row.fromCurrency,
    to_currency: row.toCurrency,
    rate_type: rateType,
    quote: Number(quote),
    quoted_per: input.quotedPer,
    change_reason: isBlank(input.changeReason) ? null : input.changeReason,
  };
  if (cell && cell.name) {
    body.name = cell.name;
  }
  return { body };
}

/**
 * `(doctype, name, action, reason)` -> `{body}` or `{error}` for
 * `approval_api.approve`. `action` is an `approveAction` result. `"refused"`
 * and `"none"` never build a body: they return the server's own sentence as
 * the error. `"reason"` with a blank or whitespace-only reason is refused
 * client-side with the same sentence Close Settings gives; a real reason is
 * trimmed before it goes in the body.
 */
export function approveBody(doctype, name, action, reason) {
  switch (action.kind) {
    case "button":
      return { body: { doctype, name } };
    case "reason": {
      const trimmed = (reason || "").trim();
      if (!trimmed) {
        return { error: NO_REASON_ERROR };
      }
      return { body: { doctype, name, reason: trimmed } };
    }
    case "refused":
    case "none":
      return { error: action.message };
    default:
      throw new Error(`Unknown approve action kind: ${action.kind}`);
  }
}

/**
 * Decide the edit-buffer entry for one cell after a reload (R01n).
 * `old` is the draft currently in the buffer (`{quote, quotedPer, orig}`) or
 * `undefined` when there is none yet; `fresh` is the cell's current view
 * (`cellView`'s shape: `{value, quotedPer, ...}`); `hasError` is whether
 * this cell currently holds a save refusal (a key in `cellErrors`).
 *
 * Keeps `old` unchanged whenever it still differs from its own `orig`
 * baseline (the user has not saved it) or the cell is refused — a refusal
 * can stand even when the typed value now equals `orig` again, because the
 * server already said no to that attempt and the Reason for Change box it
 * opened should stay. Otherwise returns a fresh, clean draft built from
 * `fresh`, so an approved or otherwise-updated cell starts from the new
 * server value rather than a stale one.
 */
export function mergeDrafts(old, fresh, hasError) {
  const freshOrig = {
    quote: fresh.value == null ? "" : String(fresh.value),
    quotedPer: fresh.quotedPer == null ? "" : String(fresh.quotedPer),
  };
  const dirty = Boolean(old) && (old.quote !== old.orig.quote || old.quotedPer !== old.orig.quotedPer);
  if (old && (hasError || dirty)) {
    return old;
  }
  return { ...freshOrig, orig: freshOrig };
}

// L01d: rates_api.get_pending's `created` (HER/OP drafts) is the one close
// API timestamp that still reaches the client naive (rates_api.py `_iso`
// returns a bare `isoformat()`; every sibling endpoint's `_iso` calls
// konsol/close/timefmt.py's `zoned_iso(value, get_system_timezone())`
// first — logged as a backend gap, L01e, out of this row's files: only
// close-ui/src/rates.js and sections/RatesPending.vue). This bridges that
// gap on the client: a naive value is read as `systemZone` wall time
// (Frappe's own storage convention — the same zone the server would have
// attached, surfaced to the client as `frappe.boot.time_zone.system`, not
// a guess at the browser's, which is exactly what parseZoned/B09b refuses
// to do), then shown in `timeZone` (the viewer's own zone) exactly like
// the TB list (tbTable.js's `timestampText`). Once L01e lands, `created`
// arrives zoned and this falls straight through `parseZoned` unchanged.
// Whether `created` is zoned is decided by trying `parseZoned` itself
// (timefmt.js's own test, B09b) rather than a second zone-detecting regex
// here: a source scan (timefmt.test.mjs) holds that regex to timefmt.js
// alone, same as it holds parseZoned/formatTime/userTimeZone there.
const NAIVE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?$/;

/** A naive wall-clock ISO string (no zone) -> the Date instant it names in
 * `zoneName`. Deterministic: computed from `zoneName`'s own UTC offset at
 * that moment (via Intl), never the host machine's own zone. Mirrors, in
 * JS, what `konsol/close/timefmt.py`'s `zoned_iso` does in Python
 * (`naive_dt.replace(tzinfo=ZoneInfo(tz_name))`). */
function naiveInZone(value, zoneName) {
  const m = NAIVE.exec(value);
  if (!m) {
    throw new Error(`pendingCreatedText cannot read this timestamp: ${value}`);
  }
  const [, y, mo, d, h, mi, s, frac] = m;
  const ms = frac ? Number(frac.slice(0, 3).padEnd(3, "0")) : 0;
  const guessUtc = Date.UTC(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s), ms);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: zoneName,
    hourCycle: "h23",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(new Date(guessUtc)).reduce((acc, p) => {
    acc[p.type] = p.value;
    return acc;
  }, {});
  const hour = parts.hour === "24" ? 0 : Number(parts.hour);
  const sameDigitsAsUtc = Date.UTC(
    Number(parts.year), Number(parts.month) - 1, Number(parts.day),
    hour, Number(parts.minute), Number(parts.second), ms,
  );
  const offsetMs = sameDigitsAsUtc - guessUtc; // zoneName's own UTC offset at that moment
  return new Date(guessUtc - offsetMs);
}

/**
 * A pending item's `created` -> the same formatted text timefmt.js gives
 * the TB list: `formatTime` in `timeZone` (the viewer's own zone, e.g.
 * `userTimeZone()`), relative to `now`. `null`/`undefined` reads "not
 * recorded" (mirrors tbTable.js's missing-creation text). A zoned value is
 * read with `parseZoned`, unchanged; a naive one (the live defect, L01e)
 * is read as `systemZone` wall time first (see `naiveInZone` above).
 * Throws without a valid `timeZone`/`now`/`systemZone` or an unreadable
 * `created` — never a guessed display (B09b).
 */
export function pendingCreatedText(created, now, timeZone, systemZone) {
  if (!timeZone) {
    throw new Error("pendingCreatedText requires a time zone");
  }
  if (!(now instanceof Date) || Number.isNaN(now.getTime())) {
    throw new Error("pendingCreatedText requires a valid `now`");
  }
  if (created === null || created === undefined) {
    return "not recorded";
  }
  let zoned;
  try {
    zoned = parseZoned(created);
  } catch {
    zoned = null; // not a zoned string (B09b) — fall through to the naive case below
  }
  if (zoned) {
    return formatTime(zoned, now, timeZone);
  }
  if (!systemZone) {
    throw new Error("pendingCreatedText requires the system time zone to read a naive timestamp");
  }
  return formatTime(naiveInZone(created, systemZone), now, timeZone);
}

/** `get_pending` payload -> `{items, counts, selfApproval, canApprove}`,
 * each item's `approve` run through `approveAction`. */
export function pendingView(payload) {
  return {
    items: (payload.items || []).map((item) => ({ ...item, approve: approveAction(item.approve) })),
    counts: payload.counts,
    selfApproval: payload.self_approval,
    canApprove: Boolean(payload.can_approve),
  };
}

/** #305-R01p: the "Historical equity rates" tab's total pending count --
 * HER and OP together (`counts["Historical Equity Rate"] +
 * counts["Ownership Period"]`). The two doctypes share one tab, so the
 * wireframe's "N pending" counts both; counting HER alone (the live
 * defect) understates the tab when OP drafts are also waiting. */
export function pendingCount(counts) {
  return (counts["Historical Equity Rate"] || 0) + (counts["Ownership Period"] || 0);
}

/** #305-R01p: the pending list's empty-state text, from `pendingView`'s
 * result (`{items, counts}`). Null when there are visible items (the
 * caller renders the list instead). With no visible items and
 * `counts.hidden > 0`, entities outside the viewer's scope have drafts the
 * viewer cannot see, so "none awaiting" would be a lie -- this names the
 * hidden count instead (mirrors periodGrid.js's hiddenNote / auditTrail.js's
 * hiddenNote). Only when nothing is hidden either does it say none awaiting. */
export function pendingEmptyMessage(view) {
  if (view.items.length) return null;
  const hidden = (view.counts && view.counts.hidden) || 0;
  if (hidden > 0) {
    return `${hidden} awaiting outside your scope`;
  }
  return "No historical equity rates or ownership periods are awaiting approval.";
}

/** `get_ownership` payload -> `{blocking, outOfScopeCount, inScopeCount,
 * canRecord, hiddenCount, blockingHidden}`. `blocking` entries
 * (`{entity, message, desk}`) are server-authored sentences and pass
 * through unchanged; the out-of-scope list is shown only as a count.
 * `blockingHidden` (R01h: `blocking_hidden`) is the subset of `hiddenCount`
 * that is a hidden blocking gap, not a merely-hidden out-of-scope entity --
 * `ownershipEmptyMessage` needs that distinction, `hiddenCount` alone
 * cannot tell the two apart. */
export function ownershipView(payload) {
  return {
    blocking: payload.blocking || [],
    outOfScopeCount: (payload.out_of_scope || []).length,
    inScopeCount: payload.in_scope_count,
    canRecord: Boolean(payload.can_record),
    hiddenCount: payload.hidden || 0,
    blockingHidden: payload.blocking_hidden || 0,
  };
}

/** #305-R01q: the Ownership tab's empty-state sentence, from
 * `ownershipView`'s result (or `null`/`undefined` while loading, mirroring
 * the section's own `!view` branch). Null return when there are visible
 * blocking entries (the caller renders the list instead). With no visible
 * entries and `blockingHidden > 0`, entities outside the viewer's scope
 * have ownership gaps the viewer cannot see, so "No ownership gaps" would
 * be a lie -- this names the hidden count instead (mirrors
 * pendingEmptyMessage / periodGrid.js's hiddenNote). Only when nothing is
 * hidden either does it say there are none. */
export function ownershipEmptyMessage(view) {
  if (view && view.blocking.length) return null;
  const hidden = (view && view.blockingHidden) || 0;
  if (hidden > 0) {
    return `${hidden} ${hidden === 1 ? "gap" : "gaps"} outside your scope`;
  }
  return "No ownership gaps for this period.";
}

/** #305-R01v (folded in from R01q's gate): the Ownership tab label's gap
 * count — every blocking gap, visible or hidden (`blocking.length +
 * blockingHidden`), not the visible count alone. `ownershipView`'s result
 * (or `null`/`undefined` while loading, mirroring `ownershipEmptyMessage`'s
 * own guard) -> a number, or `null`. Counting the visible list alone let a
 * scoped user whose own entities carry no gap read a plain "Ownership" tab
 * label (0 gaps) while gaps still existed outside their scope. */
export function ownershipGapsCount(view) {
  if (!view) return null;
  return view.blocking.length + (view.blockingHidden || 0);
}
