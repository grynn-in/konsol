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
import { dueDateText } from "./dueDate.js";

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

// L01f: rates_api.get_pending's `created` now reaches the client zoned
// (L01e: rates_api.py's `_iso` mirrors every sibling close API's and calls
// `timefmt.py`'s `zoned_iso` first). L01d's client-side workaround for the
// then-naive payload -- a private naive-wall-clock re-zoning helper and
// this function's extra zone-name parameter -- is deleted along with it:
// with the server fixed, a naive `created` can only mean a regression, and
// this must not go on silently re-zoning it as if nothing had changed. It
// is read only through
// `parseZoned`, exactly as `tbTable.js`'s `timestampText` reads the TB
// list's own timestamps, and left to throw `parseZoned`'s own "no time
// zone" error on anything zone-less — the same text the TB screen would
// show for the identical defect, not a message invented here, so a server
// regression is visible rather than quietly worked around.

/**
 * A pending item's `created` -> the same formatted text timefmt.js gives
 * the TB list: `formatTime(parseZoned(created), now, timeZone)`, mirroring
 * `tbTable.js`'s `timestampText` exactly. `null`/`undefined` reads "not
 * recorded" (mirrors tbTable.js's missing-creation text). Throws without a
 * valid `timeZone`/`now`, and throws `parseZoned`'s own error for any
 * `created` that carries no time zone — never a guessed display (B09b),
 * and never a silent re-zoning of a value that should already be zoned.
 */
export function pendingCreatedText(created, now, timeZone) {
  if (!timeZone) {
    throw new Error("pendingCreatedText requires a time zone");
  }
  if (!(now instanceof Date) || Number.isNaN(now.getTime())) {
    throw new Error("pendingCreatedText requires a valid `now`");
  }
  if (created === null || created === undefined) {
    return "not recorded";
  }
  return formatTime(parseZoned(created), now, timeZone);
}

/** `get_pending` payload -> `{items, counts, selfApproval, canApprove}`,
 * each item's `approve` run through `approveAction`. */
/** O69's `edit` keys on a pending Ownership Period item (rates_model.op_edit). */
const OWNERSHIP_EDIT_KEYS = ["consolidation_group", "entity", "fiscal_year", "fiscal_period", "ownership_pct", "consolidation_method"];

/**
 * konsol#305 O67: a pending Ownership Period item's server `edit` (O69), or
 * null when the server says the caller cannot edit it here (a Desk draft, a
 * Viewer, a day no Regular period starts on). A missing `edit`, or an edit
 * without one of its keys, throws naming the draft: the client never decides
 * editability itself, and never reads it from the title or detail.
 */
function ownershipEdit(item) {
  if (!("edit" in item)) {
    throw new Error(`pendingView: ${item.name} has no edit`);
  }
  if (item.edit === null) return null;
  for (const key of OWNERSHIP_EDIT_KEYS) {
    if (!item.edit || typeof item.edit !== "object" || !(key in item.edit)) {
      throw new Error(`pendingView: ${item.name}'s edit has no ${key}`);
    }
  }
  return item.edit;
}

/** O67: `pendingView(...).items` -> `{name: edit}` for each Ownership Period
 * draft the server made editable (`edit` not null). */
export function ownershipDraftEdits(items) {
  const out = {};
  for (const item of items) {
    if (item.doctype === "Ownership Period" && item.edit !== null) out[item.name] = item.edit;
  }
  return out;
}

export function pendingView(payload) {
  return {
    items: (payload.items || []).map((item) => {
      const out = { ...item, approve: approveAction(item.approve) };
      if (item.doctype === "Ownership Period") out.edit = ownershipEdit(item);
      return out;
    }),
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
 * canRecord, canChange, hiddenCount, blockingHidden}`. `canRecord` drives
 * only the Desk "Record ownership" link; `canChange` (R53f; #305-R52-4,
 * U10e) is the server's `can_change`, true exactly when
 * `save_ownership_change` would admit the caller, and is what mounts the
 * Change ownership form. A payload without `can_change` throws, and so does
 * `can_change` true without the `change` choices (the server sends them
 * exactly when it is true). Never derived from `can_record` or a role. `blocking` entries
 * (`{entity, message, desk}`) are server-authored sentences and pass
 * through unchanged; the out-of-scope list is shown only as a count.
 * `blockingHidden` (R01h: `blocking_hidden`) is the subset of `hiddenCount`
 * that is a hidden blocking gap, not a merely-hidden out-of-scope entity --
 * `ownershipEmptyMessage` needs that distinction, `hiddenCount` alone
 * cannot tell the two apart. */
export function ownershipView(payload) {
  if (typeof payload.can_change !== "boolean") {
    throw new Error("ownershipView: get_ownership's payload has no can_change");
  }
  if (payload.can_change && (payload.change === null || typeof payload.change !== "object")) {
    throw new Error("ownershipView: get_ownership says can_change but sends no change");
  }
  return {
    blocking: payload.blocking || [],
    outOfScopeCount: (payload.out_of_scope || []).length,
    inScopeCount: payload.in_scope_count,
    canRecord: Boolean(payload.can_record),
    canChange: payload.can_change,
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

// ---------------------------------------------------------------------------
// konsol#305 O59 (story 4.2; #305-4.2-1; wireframe-4.2.md, confirmed as drawn
// by Deepak Pai 7 Oct): the ownership change form's POST body and the effect
// panel. The effect is the server's (`ownership_change_model.effect`, reached
// through `rates_api.preview_ownership_change` and `get_pending`'s OP items):
// this view only lays it out. It never computes a pct, a method, a date or a
// period, and every refusal beyond the two form checks below is the server's.
// Dates are written with dueDate.js's one date wording.

/** The server's own pct sentence (ownership_change_model.PCT_SENTENCE); the
 * test pins it to the REAL preview's refusal. */
const PCT_SENTENCE = "Ownership % must be a number from 0 to 100.";
const NO_ENTITY = "Choose an entity.";
const NO_SIGNED_PERIOD = "No signed period is affected.";
const EFFECT_KEYS = [
  "before",
  "after",
  "current_name",
  "current_ends",
  "first_period",
  "periods",
  "resign",
  "resign_detail",
  "not_shown",
];
const RESIGN_DETAIL_KEYS = ["period", "signed_on", "signed_by_name"];
const SIDE_KEYS = ["pct", "method", "from", "to"];

/** A form value -> a finite number in 0..100, or null. Blank, a bool, a
 * partial number ("80abc"), NaN and infinity are not numbers here (the
 * server's `_pct` rule). */
function ownershipPctValue(value) {
  if (typeof value === "boolean" || value === null || value === undefined) return null;
  if (typeof value === "string" && !value.trim()) return null;
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0 || n > 100) return null;
  return n;
}

/**
 * `(period, form)` -> `{body}` or `{error}` for
 * `rates_api.save_ownership_change`. `period` is `{fiscal_year,
 * fiscal_period}` (the first period affected; the server takes its first
 * day, C-O2). `form` is `{consolidationGroup, entity, ownershipPct,
 * consolidationMethod, name?}`; `name` is set only when editing a draft. The
 * body carries exactly the endpoint's parameters: never `docstatus`,
 * `supersedes`, `end_date` or a deal field. A blank entity and a bad pct are
 * refused here, the pct with the server's own sentence; every other refusal
 * is the server's.
 */
export function ownershipChangeBody(period, form) {
  const entity = typeof form.entity === "string" ? form.entity.trim() : form.entity;
  if (isBlank(entity)) {
    return { error: NO_ENTITY };
  }
  const pct = ownershipPctValue(form.ownershipPct);
  if (pct === null) {
    return { error: PCT_SENTENCE };
  }
  const body = {
    fiscal_year: period.fiscal_year,
    fiscal_period: period.fiscal_period,
    consolidation_group: form.consolidationGroup,
    entity,
    ownership_pct: pct,
    consolidation_method: form.consolidationMethod,
  };
  if (!isBlank(form.name)) {
    body.name = form.name;
  }
  return { body };
}

function requireKeys(obj, keys, where) {
  for (const key of keys) {
    if (!(key in obj)) {
      throw new Error(`ownershipEffectView: the effect has no ${where}${key}`);
    }
  }
}

function effectDate(iso) {
  return dueDateText(iso, "ownershipEffectView");
}

/**
 * `resign_detail` (O64, parallel to `resign`) -> "FY2025 P11 (signed Sat 4
 * Oct 2025 by Jane Doe)" per entry (wireframe-4.2.md §1). An entry without
 * its period, date or signer name, or out of step with `resign`, throws:
 * never a blank signer or a guessed date.
 */
function blankText(value) {
  return typeof value !== "string" || value.trim() === "";
}

function resignLines(effect) {
  const detail = effect.resign_detail;
  if (!Array.isArray(detail)) {
    throw new Error("ownershipEffectView: the effect's resign_detail is not a list");
  }
  if (detail.length !== effect.resign.length) {
    throw new Error("ownershipEffectView: the effect's resign_detail does not match resign");
  }
  return detail.map((entry, i) => {
    requireKeys(entry || {}, RESIGN_DETAIL_KEYS, "resign_detail.");
    if (entry.period !== effect.resign[i]) {
      throw new Error(`ownershipEffectView: the effect's resign_detail ${entry.period} does not match resign ${effect.resign[i]}`);
    }
    if (blankText(entry.signed_by_name)) {
      throw new Error(`ownershipEffectView: the effect's resign_detail.signed_by_name is blank for ${entry.period}`);
    }
    return `${entry.period} (signed ${effectDate(entry.signed_on)} by ${entry.signed_by_name})`;
  });
}

function effectEnd(iso) {
  return iso === null ? "open-ended" : effectDate(iso);
}

/**
 * The server's effect (`ownership_change_model.effect`) -> the wireframe's
 * EFFECT panel: `{rows, currentEnds, firstPeriod, periods, resign,
 * resignNone, notShown}`. `rows` are Ownership, Method and Covers, each
 * `{label, before, after, unchanged}`; Covers' before side runs to
 * `current_ends` and carries the note on the current period's end today.
 * `resignNone` is "No signed period is affected." only when `resign` is
 * empty. `resign` holds one "FY2025 P11 (signed <date> by <name>)" line per
 * signed period and `endsLine` is "<current_name> on <date>" (O65,
 * wireframe-4.2.md §1 and §3). Throws on a missing key, a missing effect, or a date that is not
 * ISO: never a guessed panel.
 */
export function ownershipEffectView(effect) {
  if (!effect || typeof effect !== "object") {
    throw new Error("ownershipEffectView: no effect was given");
  }
  requireKeys(effect, EFFECT_KEYS, "");
  requireKeys(effect.before, SIDE_KEYS, "before.");
  requireKeys(effect.after, SIDE_KEYS, "after.");
  if (!Array.isArray(effect.resign)) {
    throw new Error("ownershipEffectView: the effect's resign is not a list");
  }
  if (blankText(effect.current_name)) {
    throw new Error("ownershipEffectView: the effect's current_name is blank");
  }
  const resign = resignLines(effect);
  const { before, after } = effect;
  const currentEnds = effectDate(effect.current_ends);
  const nowEnds = before.to === null ? "now open-ended" : `now to ${effectDate(before.to)}`;
  return {
    rows: [
      {
        label: "Ownership",
        before: `${formatPct(before.pct)} %`,
        after: `${formatPct(after.pct)} %`,
        unchanged: before.pct === after.pct,
      },
      { label: "Method", before: before.method, after: after.method, unchanged: before.method === after.method },
      {
        label: "Covers",
        before: `${effectDate(before.from)} → ${currentEnds}`,
        after: `${effectDate(after.from)} → ${effectEnd(after.to)}`,
        unchanged: false,
        note: `(${nowEnds}; ends on approval)`,
      },
    ],
    currentEnds,
    endsLine: `${effect.current_name} on ${currentEnds}`,
    firstPeriod: effect.first_period,
    periods: effect.periods,
    resign,
    resignNone: effect.resign.length ? null : NO_SIGNED_PERIOD,
    notShown: effect.not_shown,
  };
}

/** The sentence an Ownership Period drafted in Desk shows in place of the
 * effect panel (O57: such a draft arrives with `effect: null`). */
export const DESK_DRAFT = "Drafted in Desk: effect not previewed.";

/**
 * konsol#305 R52o (review U6, S2): the one EFFECT IF APPROVED view of a
 * pending Ownership Period item, shared by the pending list and the
 * Approvals detail, so neither screen keeps its own copy.
 * - `ownership_effect_error` set (R52i: the server could not read this draft's
 *   effect) -> `{error: <the server's sentence>}`;
 * - `ownership_effect: null` -> `{desk: DESK_DRAFT}`;
 * - otherwise `{view}`: `ownershipEffectView(item.ownership_effect)` without its
 *   Covers row, because the pending panel (wireframe-4.2.md section 3)
 *   shows the current period's end as its own "Ends" line instead.
 * R52q (review S18): the server names the keys for the Ownership Period,
 * so they never collide with a journal item's `effect`. A missing
 * `ownership_effect` or `ownership_effect_error` key (an item carrying the
 * old `effect` instead included) is a server regression and
 * throws, as does an effect `ownershipEffectView` refuses.
 */
export function opEffectView(item) {
  for (const key of ["ownership_effect", "ownership_effect_error"]) {
    if (!item || !(key in item)) {
      throw new Error(`opEffectView: the pending item ${item && item.name} has no ${key}`);
    }
  }
  if (item.ownership_effect_error !== null && item.ownership_effect_error !== undefined) {
    return { error: item.ownership_effect_error };
  }
  if (item.ownership_effect === null) {
    return { desk: DESK_DRAFT };
  }
  const view = ownershipEffectView(item.ownership_effect);
  return { view: { ...view, rows: view.rows.filter((row) => row.label !== "Covers") } };
}
