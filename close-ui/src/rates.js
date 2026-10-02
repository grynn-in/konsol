// konsol#305 E407: rates.js
//
// Pure view and request-building helpers for the Rates screen (stories 4.1,
// 4.3; R5). Turns the three `rates_api.py` GET payloads (`get_rates`,
// `get_pending`, `get_ownership`) into what `Rates.vue` shows, and builds
// the POST bodies for `rates_api.save_rate` and `approval_api.approve`. It
// does not re-decide policy or the move rule: those stay on the server, and
// their sentences are shown exactly as returned (E4-P11: no machine here,
// just pure functions). Imports no vue, frappe-ui or xstate (mirrors
// checks.js).
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

function cellView(cell) {
  return {
    name: cell.name,
    status: cell.status,
    statusLabel: statusLabel(cell.status),
    value: cell.quote,
    quotedPer: cell.quoted_per,
    previousValue: cell.previous ? cell.previous.rate : null,
    previousLabel: previousLabel(cell.previous),
    deltaText: deltaText(cell.delta),
    flag: cell.flag,
    preparer: cell.owner,
    approve: cell.approve ? approveAction(cell.approve) : null,
  };
}

function rowView(row) {
  return {
    fromCurrency: row.from_currency,
    toCurrency: row.to_currency,
    required: row.required,
    closing: cellView(row.closing),
    average: cellView(row.average),
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

function thresholdText(thresholdPct, policyGaps) {
  if (thresholdPct === null || thresholdPct === undefined) {
    const gap = (policyGaps || []).find((g) => g.code === "rate_move_undeclared");
    if (!gap) {
      throw new Error("threshold_pct is undeclared but no rate_move_undeclared policy gap was given.");
    }
    return gap.message;
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
  return {
    rows: (payload.rows || []).map(rowView),
    unrequired: (payload.unrequired || []).map(rowView),
    banner: bannerFor(payload),
    summary: payload.summary,
    canEnter: Boolean(payload.can_enter),
    canApprove: Boolean(payload.can_approve),
    thresholdText: thresholdText(payload.threshold_pct, payload.policy_gaps),
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

/** `get_ownership` payload -> `{blocking, outOfScopeCount, inScopeCount,
 * canRecord, hiddenCount}`. `blocking` entries (`{entity, message, desk}`)
 * are server-authored sentences and pass through unchanged; the
 * out-of-scope list is shown only as a count. */
export function ownershipView(payload) {
  return {
    blocking: payload.blocking || [],
    outOfScopeCount: (payload.out_of_scope || []).length,
    inScopeCount: payload.in_scope_count,
    canRecord: Boolean(payload.can_record),
    hiddenCount: payload.hidden || 0,
  };
}
