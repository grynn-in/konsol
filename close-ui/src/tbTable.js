// konsol#305 B12: tbTable.js
//
// Pure formatting/grouping of the TB screens' server payloads for the table
// views (story 3.1/3.2/3.4/3.7). Takes no frappe/vue import.
//
// - `checkRows(result)` — A11's check-model output, `{ok, rows, file_problems,
//   totals}` (konsol/close/tb_model.py `check_rows`), for the upload screen's
//   check table.
// - `entityRows(myTbs)` — A25's `my_tbs` output, `{period_open, can_upload,
//   can_remind, entities}`, for "my entities" (the trial-balances screen).
// - `compareRows(cmp)` — A12/A28's compare output, `{rows, basis_note,
//   previous_note, previous_code}`, for the compare-by-account view.
//
// Amount convention (every amount here is debit minus credit, or a plain
// debit/credit column): a negative amount renders in brackets — "(1,234.50)"
// for -1234.5 — never with a leading "-". This is how a credit balance is
// shown with a visible sign, since debit-minus-credit puts it below zero.
//
// Unknown is never zero: A12 leaves `current`/`previous`/`change` as `null`
// when there is nothing to show (no previous TB, or the two bases are not
// comparable), and that renders as the dash "—", never "0.00".

import { formatTime, parseZoned } from "./timefmt.js";
import { remindedText } from "./remind.js";
import { ISO_DATE, OVERDUE_TONE, dueDateText } from "./dueDate.js";

const AMOUNT_FORMAT = new Intl.NumberFormat("en", { minimumFractionDigits: 2 });
const DASH = "—";

/** A number -> "1,234.50" / "(1,234.50)" for negative; null/undefined -> the dash. */
function formatAmount(value) {
  if (value === null || value === undefined) {
    return DASH;
  }
  const formatted = AMOUNT_FORMAT.format(Math.abs(value));
  return value < 0 ? `(${formatted})` : formatted;
}

/**
 * A11's `check_rows(...)` result -> `{ok, rows, file_problems, totals}` for
 * the check table: problem rows first (their relative order, and the order
 * of clean rows, is otherwise unchanged — this never re-sorts by line or
 * account), each row's `debit`/`credit` formatted, each `problems[].suggestion`
 * kept as the server sent it, and `totals` formatted.
 */
export function checkRows(result) {
  const rows = (result.rows || [])
    .map((row) => ({
      ...row,
      debit: formatAmount(row.debit),
      credit: formatAmount(row.credit),
      hasProblems: Boolean(row.problems && row.problems.length > 0),
    }))
    .sort((a, b) => Number(b.hasProblems) - Number(a.hasProblems)); // stable: ties keep server order

  const totals = result.totals || {};
  return {
    ok: result.ok,
    rows,
    file_problems: result.file_problems || [],
    totals: {
      debit: formatAmount(totals.debit),
      credit: formatAmount(totals.credit),
      difference: formatAmount(totals.difference),
    },
  };
}

/**
 * E209c: the noun for a count of entities — "entity" only for exactly 1,
 * "entities" for every other count, including 0 and an unknown (null) count.
 */
export function entityWord(n) {
  return n === 1 ? "entity" : "entities";
}

// The seven TB statuses tb_read_api.py's `TB_STATUSES` declares (A25, E209a's
// #289 gap). A status this module does not know is refused, not guessed at:
// it is never shown as if it were one of these.
export const KNOWN_STATUSES = new Set([
  "Received",
  "Exception declared",
  "Not expected this period",
  "Missing",
  "Frequency not declared",
  "Quarter not declared",
  "Not consolidated: no ownership for this period",
]);

// B27: times on the TB list read like the freshness bar (B09): "10:42" today,
// "Sep 20, 10:42" otherwise, in the user's zone, which the caller passes in.
// A zone-less server timestamp is refused (B09b), never read in the browser's
// zone. B29: the formatter is timefmt.js's, shared with the freshness bar.
const NOT_RECORDED = "not recorded";

/** A server timestamp (or none) -> its text on the TB list. */
function timestampText(value, now, timeZone) {
  if (value === null || value === undefined) {
    return NOT_RECORDED;
  }
  return formatTime(parseZoned(value), now, timeZone);
}

/**
 * A25's `my_tbs(...)` result -> one row per entity: `{entity, name, status,
 * tb, tbText, uploaded, exception}`. `tb` stays `null` when there is none
 * (never `{}`); its `on_behalf_label`, when present, is passed through exactly
 * as the server sent it — this never rewrites or re-derives it.
 *
 * B27: `tbText` is the TB's name, or the dash when there is none (never the
 * literal "None"). `uploaded` is the TB's `creation` formatted in `timeZone`
 * relative to `now` (the dash with no TB, "not recorded" when the server sent
 * none), and an exception carries `declaredOnText` the same way. `now` and
 * `timeZone` are required, as in freshnessView (B09); a zone-less timestamp
 * throws.
 *
 * Throws on a status this module does not know, so an entity is never shown
 * with a blank or guessed status.
 *
 * Y62 (story 1.5): `reminded` is remind.js's "Reminded N× · last <time> by
 * <name>" for the entity's `reminders` entry, or null when none was sent.
 * `canRemind` is the payload's `can_remind` AND the status is `Missing`. A
 * payload with no boolean `can_remind`, or an entity with no `reminders` key,
 * throws: neither is read as "no".
 *
 * D60 (story 2.4, decision #305-2.4-1): each row carries the server's
 * `overdue` (D56: `deadline.past` on a Missing row, false otherwise) and
 * `overdueChip`, `{text: "Overdue", tone: OVERDUE_TONE}` when overdue, else
 * null. Show-only: the chip is the warn tone, never the block tone, and
 * nothing is disabled by it. A payload with no `deadline`, or an entity with
 * no boolean `overdue`, throws: neither is read as "not overdue".
 */
export function entityRows(myTbs, now, timeZone) {
  if (!timeZone) {
    throw new Error("entityRows requires a time zone");
  }
  if (!(now instanceof Date) || Number.isNaN(now.getTime())) {
    throw new Error("entityRows requires a valid `now`");
  }
  if (typeof myTbs.can_remind !== "boolean") {
    throw new Error("entityRows: the payload has no can_remind flag (Y56 always sends it).");
  }
  checkDeadline(myTbs, "entityRows");
  return (myTbs.entities || []).map((entity) => {
    if (!KNOWN_STATUSES.has(entity.status)) {
      throw new Error(`entityRows: unknown TB status: ${entity.status}`);
    }
    if (!("reminders" in entity)) {
      throw new Error(`entityRows: ${entity.entity} has no reminders entry (Y56 always sends it, null when none).`);
    }
    if (typeof entity.overdue !== "boolean") {
      throw new Error(`entityRows: ${entity.entity} has no overdue flag (D56 always sends it).`);
    }
    const tb = entity.tb
      ? {
          name: entity.tb.name,
          owner: entity.tb.owner,
          on_behalf_label: entity.tb.on_behalf_label,
          creation: entity.tb.creation,
        }
      : null;
    const exception = entity.exception
      ? { ...entity.exception, declaredOnText: timestampText(entity.exception.declared_on, now, timeZone) }
      : null;
    return {
      entity: entity.entity,
      name: entity.name,
      status: entity.status,
      tb,
      tbText: tb ? tb.name : DASH,
      uploaded: tb ? timestampText(tb.creation, now, timeZone) : DASH,
      exception,
      reminded: remindedText(entity.reminders, now, timeZone),
      canRemind: myTbs.can_remind && entity.status === "Missing",
      overdue: entity.overdue,
      overdueChip: entity.overdue ? { text: OVERDUE_TEXT, tone: OVERDUE_TONE } : null,
    };
  });
}

// --- D60: the TB due header and the overdue chip (stories 2.4, 3.1) ---------
// Decision #305-2.4-1: a deadline is show-only and never blocks. The chip is
// the warn (amber) tone, never the block (red) tone the Missing status uses.
// D62: the tone and the date wording live in dueDate.js, shared with My work.
export { OVERDUE_TONE };
const OVERDUE_TEXT = "Overdue";

/** D56's `deadline` must be present as `{due, past, text}`; anything else throws. */
function checkDeadline(myTbs, who) {
  const deadline = myTbs.deadline;
  if (!deadline || typeof deadline !== "object") {
    throw new Error(`${who}: the payload has no deadline (D56 always sends it).`);
  }
  if (typeof deadline.past !== "boolean") {
    throw new Error(`${who}: the deadline has no past flag (D56 always sends it).`);
  }
  if (deadline.due !== null && !(typeof deadline.due === "string" && ISO_DATE.test(deadline.due))) {
    throw new Error(`${who}: the deadline's due is not an ISO date: ${deadline.due}`);
  }
  if (deadline.due === null && typeof deadline.text !== "string") {
    throw new Error(`${who}: an undeclared deadline has no text (D56 always sends it).`);
  }
  return deadline;
}

/**
 * D56's my_tbs `deadline` -> the screen header's `{text, past}`: "TB due Tue
 * 7 Oct 2025" for a declared date, or the server's own sentence ("No due date
 * declared") when `due` is null — never a guessed date. Throws on a payload
 * without a well-formed `deadline`.
 */
export function tbDue(myTbs) {
  const deadline = checkDeadline(myTbs, "tbDue");
  if (deadline.due === null) {
    return { text: deadline.text, past: false };
  }
  return { text: `TB due ${dueDateText(deadline.due, "tbDue")}`, past: deadline.past };
}

/**
 * A12/A28's compare result -> `{rows, basis_note, previous_note,
 * previous_code}` for the compare-by-account view. Each row's `current`,
 * `previous` and `change` are formatted amounts, with `null` rendered as the
 * dash (never "0.00"). `basis_note` and `previous_note` pass through
 * unchanged — they are the reason a change is not shown, and this never
 * invents its own wording for them.
 */
export function compareRows(cmp) {
  const rows = (cmp.rows || []).map((row) => ({
    account: row.account,
    partner: row.partner,
    is_ic: row.is_ic,
    current: formatAmount(row.current),
    previous: formatAmount(row.previous),
    change: formatAmount(row.change),
  }));
  return {
    rows,
    basis_note: cmp.basis_note ?? null,
    previous_note: cmp.previous_note ?? null,
    previous_code: cmp.previous_code ?? null,
  };
}
