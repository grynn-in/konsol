// konsol#305 B12: tbTable.js
//
// Pure formatting/grouping of the TB screens' server payloads for the table
// views (story 3.1/3.2/3.4/3.7). Takes no frappe/vue import.
//
// - `checkRows(result)` — A11's check-model output, `{ok, rows, file_problems,
//   totals}` (konsol/close/tb_model.py `check_rows`), for the upload screen's
//   check table.
// - `entityRows(myTbs)` — A25's `my_tbs` output, `{period_open, can_upload,
//   entities}`, for "my entities" (the trial-balances screen).
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

// The TB statuses A25 declares (signoff_model.expected_entities). A status
// this module does not know is refused, not guessed at: it is never shown
// as if it were one of these.
export const KNOWN_STATUSES = new Set([
  "Received",
  "Exception declared",
  "Not expected this period",
  "Missing",
  "Frequency not declared",
  "Quarter not declared",
]);

/**
 * A25's `my_tbs(...)` result -> one row per entity: `{entity, name, status,
 * tb, exception}`. `tb` stays `null` when there is none (never `{}`); its
 * `on_behalf_label`, when present, is passed through exactly as the server
 * sent it — this never rewrites or re-derives it.
 *
 * Throws on a status this module does not know, so an entity is never shown
 * with a blank or guessed status.
 */
export function entityRows(myTbs) {
  return (myTbs.entities || []).map((entity) => {
    if (!KNOWN_STATUSES.has(entity.status)) {
      throw new Error(`entityRows: unknown TB status: ${entity.status}`);
    }
    return {
      entity: entity.entity,
      name: entity.name,
      status: entity.status,
      tb: entity.tb
        ? {
            name: entity.tb.name,
            owner: entity.tb.owner,
            on_behalf_label: entity.tb.on_behalf_label,
            creation: entity.tb.creation,
          }
        : null,
      exception: entity.exception || null,
    };
  });
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
