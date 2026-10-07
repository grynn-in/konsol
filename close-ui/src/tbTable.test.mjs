// konsol#305 B12: tbTable.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { checkRows, entityRows, compareRows, entityWord, tbDue, OVERDUE_TONE } from "./tbTable.js";
import { freshnessView } from "./freshness.js";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { remindedText } from "./remind.js";

// B27: entityRows needs the user's zone and `now`, like freshnessView (B09).
const NOW = new Date("2026-09-25T12:00:00Z");
// D60: the server's undeclared TB deadline (deadline_model._undeclared), for
// the hand-built payloads below; the D60 tests feed the golden payload.
const UNDECLARED = { due: null, past: false, text: "No due date declared" };
const TZ = "Europe/London";

// Convention used throughout this file and tbTable.js: an amount is debit
// minus credit. A negative amount (a credit balance, or a check-row credit
// that exceeds its debit) renders in brackets, e.g. -1234.5 -> "(1,234.50)".
// A non-negative amount never carries a "+".

test("checkRows: problem rows come first, with the suggestion text kept", () => {
  const result = {
    ok: false,
    rows: [
      { line: 2, main_account: "1000", partner: "", debit: 100, credit: 0, problems: [] },
      {
        line: 3,
        main_account: "4001",
        partner: "",
        debit: 50,
        credit: 0,
        problems: [
          { code: "UNKNOWN_ACCOUNT", message: "Account 4001 is not in the group chart", suggestion: "Did you mean 4010?" },
        ],
      },
      { line: 4, main_account: "2000", partner: "", debit: 0, credit: 150, problems: [] },
    ],
    file_problems: [],
    totals: { debit: 150, credit: 150, difference: 0 },
  };
  const view = checkRows(result);
  assert.deepEqual(view.rows.map((r) => r.line), [3, 2, 4], "the problem row (line 3) moves to the front; clean rows keep their order");
  assert.equal(view.rows[0].problems[0].suggestion, "Did you mean 4010?");
  assert.equal(view.rows[0].hasProblems, true);
  assert.equal(view.rows[1].hasProblems, false);
});

test("checkRows: the totals line shows the difference", () => {
  const result = {
    ok: false,
    rows: [],
    file_problems: ["Debits (100.00) do not equal credits (150.00); difference -50.00 exceeds the 0.01 tolerance"],
    totals: { debit: 100, credit: 150, difference: -50 },
  };
  const view = checkRows(result);
  assert.equal(view.totals.debit, "100.00");
  assert.equal(view.totals.credit, "150.00");
  assert.equal(view.totals.difference, "(50.00)", "a negative difference (credit-heavy) renders in brackets, not with a minus sign");
});

test("checkRows: a balanced file's difference has no brackets", () => {
  const result = { ok: true, rows: [], file_problems: [], totals: { debit: 100, credit: 100, difference: 0 } };
  const view = checkRows(result);
  assert.equal(view.totals.difference, "0.00");
});

test("entityRows: the on-behalf label is kept verbatim", () => {
  const myTbs = {
    period_open: true,
    can_upload: true,
    can_remind: false,
    deadline: UNDECLARED,
    entities: [
      {
        entity: "ZZE",
        name: "ZZ Entity",
        status: "Received",
        tb: { name: "TBSUB-0001", owner: "admin@example.com", on_behalf_label: "by admin@example.com for ZZE", creation: "2026-09-20T10:00:00Z" },
        exception: null,
        reminders: null,
        overdue: false,
      },
    ],
  };
  const view = entityRows(myTbs, NOW, TZ);
  assert.equal(view[0].tb.on_behalf_label, "by admin@example.com for ZZE");
});

test("entityRows: a TB-less entity keeps tb as null, never an empty object", () => {
  const myTbs = {
    period_open: true,
    can_upload: true,
    can_remind: false,
    deadline: UNDECLARED,
    entities: [{ entity: "ZZM", name: "ZZ Missing", status: "Missing", tb: null, exception: null, reminders: null, overdue: false }],
  };
  const view = entityRows(myTbs, NOW, TZ);
  assert.equal(view[0].tb, null);
  assert.equal(view[0].status, "Missing");
});

test("entityRows: failure path — an unknown status throws, never renders blank", () => {
  const myTbs = {
    period_open: true,
    can_upload: true,
    can_remind: false,
    deadline: UNDECLARED,
    entities: [{ entity: "ZZX", name: "ZZ X", status: "Somehow Pending", tb: null, exception: null, reminders: null, overdue: false }],
  };
  assert.throws(() => entityRows(myTbs, NOW, TZ), /unknown.*status/i);
});

test("compareRows: a change of None renders as —, and basis_note passes through", () => {
  const cmp = {
    rows: [
      { account: "4001", partner: "", is_ic: false, current: 100, previous: null, change: null },
    ],
    basis_note: "This period is Actual and P08 is Budget: the change is not comparable.",
    previous_note: null,
    previous_code: "P08",
  };
  const view = compareRows(cmp);
  assert.equal(view.rows[0].change, "—");
  assert.equal(view.rows[0].previous, "—", "unknown is never zero: previous is blank, not 0.00");
  assert.equal(view.rows[0].current, "100.00");
  assert.equal(view.basis_note, cmp.basis_note);
});

test("compareRows: no previous trial balance — previous_note passes through and every previous/change is the dash", () => {
  const cmp = {
    rows: [{ account: "1000", partner: "", is_ic: false, current: 200, previous: null, change: null }],
    basis_note: null,
    previous_note: "No trial balance for P08",
    previous_code: "P08",
  };
  const view = compareRows(cmp);
  assert.equal(view.previous_note, "No trial balance for P08");
  assert.equal(view.rows[0].previous, "—");
  assert.equal(view.rows[0].change, "—");
});

test("compareRows: a negative change (a swing toward credit) renders in brackets", () => {
  const cmp = {
    rows: [{ account: "4001", partner: "", is_ic: false, current: -50, previous: 100, change: -150 }],
    basis_note: null,
    previous_note: null,
    previous_code: "P08",
  };
  const view = compareRows(cmp);
  assert.equal(view.rows[0].current, "(50.00)");
  assert.equal(view.rows[0].change, "(150.00)");
});

test("compareRows: an intercompany row keeps its partner and is_ic flag", () => {
  const cmp = {
    rows: [{ account: "1500", partner: "ZZP", is_ic: true, current: 10, previous: 10, change: 0 }],
    basis_note: null,
    previous_note: null,
    previous_code: "P08",
  };
  const view = compareRows(cmp);
  assert.equal(view.rows[0].partner, "ZZP");
  assert.equal(view.rows[0].is_ic, true);
  assert.equal(view.rows[0].change, "0.00");
});

// E209b: the drift guard reads tb_read_api.py's own `TB_STATUSES` tuple
// (every status `my_tbs` can emit) instead of guessing by keyword — a status
// like "Not consolidated: no ownership for this period" matches no keyword,
// so the old regex-filter guard would pass while the screen broke on it.
function serverStatuses(src) {
  const tuple = src.match(/TB_STATUSES = \(([\s\S]*?)\)/);
  if (!tuple) {
    throw new Error("tb_read_api.py has no TB_STATUSES tuple");
  }
  const names = [...tuple[1].matchAll(/[A-Z_]+/g)].map((m) => m[0]);
  const values = Object.fromEntries(
    [...src.matchAll(/^([A-Z_]+) = "([^"]+)"$/gm)].map((m) => [m[1], m[2]]),
  );
  return new Set(
    names.map((name) => {
      if (!(name in values)) {
        throw new Error(`TB_STATUSES names ${name}, which has no NAME = "value" line`);
      }
      return values[name];
    }),
  );
}

test("B12b/E209b: the known statuses are exactly tb_read_api.py's TB_STATUSES (A25/E209a)", async () => {
  // Drift guard: a status this module does not know throws, so a server
  // status missing here breaks the TB screen on live.
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../../konsol/close/tb_read_api.py", import.meta.url), "utf8");
  const server = serverStatuses(src);
  const { KNOWN_STATUSES } = await import("./tbTable.js");
  assert.deepEqual([...KNOWN_STATUSES].sort(), [...server].sort());
});

test("the drift guard fails loudly, naming the tuple, when TB_STATUSES is missing", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../../konsol/close/tb_read_api.py", import.meta.url), "utf8");
  const mutated = src.replace(/TB_STATUSES = \([\s\S]*?\)/, "");
  assert.throws(() => serverStatuses(mutated), /TB_STATUSES/);
});

test("entityRows: accepts the #289 'Not consolidated' status (E209a)", () => {
  const myTbs = oneEntity({ entity: "ZZX", status: "Not consolidated: no ownership for this period" });
  const [row] = entityRows(myTbs, NOW, TZ);
  assert.equal(row.status, "Not consolidated: no ownership for this period");
});

// --- B27: no literal "None", and times in the user's zone ------------------

function oneEntity(overrides) {
  return {
    period_open: true,
    can_upload: true,
    can_remind: false,
    deadline: UNDECLARED,
    entities: [{ entity: "ZZE", name: "ZZ Entity", status: "Received", tb: null, exception: null, reminders: null, overdue: false, ...overrides }],
  };
}

test("(B27) a null tb renders the dash for the trial balance and the uploaded time, never 'None'", () => {
  const [row] = entityRows(oneEntity({ status: "Missing" }), NOW, TZ);
  assert.equal(row.tb, null);
  assert.equal(row.tbText, "—");
  assert.equal(row.uploaded, "—");
  assert.notEqual(row.tbText, "None");
});

test("(B27) a TB's name is its tbText", () => {
  const [row] = entityRows(oneEntity({ tb: { name: "TBSUB-0001", owner: "a@example.com", on_behalf_label: "by a@example.com", creation: "2026-09-25T09:30:00+00:00" } }), NOW, TZ);
  assert.equal(row.tbText, "TBSUB-0001");
});

test("(B27) a zoned upload time renders in the user's zone: today as HH:MM", () => {
  // 09:30 UTC is 10:30 in London (BST) and 15:00 in Kolkata.
  const tb = { name: "TBSUB-0001", owner: "a@example.com", on_behalf_label: "by a@example.com", creation: "2026-09-25T09:30:00+00:00" };
  assert.equal(entityRows(oneEntity({ tb }), NOW, "Europe/London")[0].uploaded, "10:30");
  assert.equal(entityRows(oneEntity({ tb }), NOW, "Asia/Kolkata")[0].uploaded, "15:00");
});

test("(B27) an earlier day renders as 'Sep 20, 10:42', the same text as the freshness bar", () => {
  const creation = "2026-09-20T09:42:00Z";
  const tb = { name: "TBSUB-0001", owner: "a@example.com", on_behalf_label: "x", creation };
  const [row] = entityRows(oneEntity({ tb }), NOW, TZ);
  assert.equal(row.uploaded, "Sep 20, 10:42");
  const bar = freshnessView({ state: "fresh", as_of: creation }, NOW, TZ).text;
  assert.equal(`As of ${row.uploaded}`, bar, "one formatting rule with B09");
});

test("(B27) an exception's declared_on (A55) is formatted in the user's zone", () => {
  const exception = { name: "TBX-1", reason: "dormant", declared_by: "a@example.com", declared_on: "2026-09-25T09:30:00+00:00" };
  const [row] = entityRows(oneEntity({ status: "Exception declared", exception }), NOW, TZ);
  assert.equal(row.exception.declaredOnText, "10:30");
  assert.equal(row.exception.reason, "dormant");
  assert.equal(row.uploaded, "—");
});

test("(B27) failure path: a zone-less upload time throws (B09b), never read in the browser's zone", () => {
  const tb = { name: "TBSUB-0001", owner: "a@example.com", on_behalf_label: "x", creation: "2026-09-25T20:22:31.605721" };
  assert.throws(() => entityRows(oneEntity({ tb }), NOW, TZ), /no time zone/i);
});

test("(B27) failure path: a zone-less declared_on throws", () => {
  const exception = { name: "TBX-1", reason: "dormant", declared_by: "a", declared_on: "2026-09-25 20:22:31" };
  assert.throws(() => entityRows(oneEntity({ status: "Exception declared", exception }), NOW, TZ), /no time zone/i);
});

test("(B27) failure path: no time zone or no valid now is refused, never defaulted", () => {
  const myTbs = oneEntity({ status: "Missing" });
  assert.throws(() => entityRows(myTbs, NOW), /time zone/i);
  assert.throws(() => entityRows(myTbs, NOW, ""), /time zone/i);
  assert.throws(() => entityRows(myTbs, undefined, TZ), /now/i);
  assert.throws(() => entityRows(myTbs, new Date("x"), TZ), /now/i);
});

test("(B27) a TB the server sent with no creation reads 'not recorded', never a guessed time", () => {
  const tb = { name: "TBSUB-0001", owner: "a@example.com", on_behalf_label: "x", creation: null };
  assert.equal(entityRows(oneEntity({ tb }), NOW, TZ)[0].uploaded, "not recorded");
});

// E209c: the Trial balances summary word agrees in number with the count.
test("entityWord: 1 is singular, every other count (including 0 and null) is plural", () => {
  assert.equal(entityWord(1), "entity");
  assert.equal(entityWord(2), "entities");
  assert.equal(entityWord(0), "entities");
  assert.equal(entityWord(null), "entities");
});

// --- Y62: the reminded text and the Remind flag on a Missing row (story 1.5) --
// Fed the real producer's output: Y56's golden my_tbs payload (asserted equal
// to the stub-site get_my_tbs call by its own host test).

const GOLDEN = JSON.parse(
  readFileSync(fileURLToPath(new URL("../../konsol/tests/fixtures/close_my_tbs_payload.json", import.meta.url)), "utf8"),
);
const GOLDEN_NOW = new Date("2025-10-07T12:00:00Z");
const byEntity = (rows, code) => {
  const row = rows.find((r) => r.entity === code);
  assert.ok(row, `no row for ${code}`);
  return row;
};

test("(Y62) entityRows(golden): a Missing row with reminders carries remind.js's reminded text", () => {
  const rows = entityRows(GOLDEN, GOLDEN_NOW, TZ);
  const zzc = byEntity(rows, "ZZC");
  assert.equal(zzc.status, "Missing");
  const source = GOLDEN.entities.find((e) => e.entity === "ZZC").reminders;
  assert.equal(zzc.reminded, remindedText(source, GOLDEN_NOW, TZ), "one formatting rule, remind.js's");
  assert.match(zzc.reminded, /^Reminded 2× · last .+ by Zed Lead$/);
  assert.equal(zzc.canRemind, true, "can_remind and Missing");
});

test("(Y62) entityRows(golden): a Missing row with no reminder has null text but can be reminded", () => {
  const zzb = byEntity(entityRows(GOLDEN, GOLDEN_NOW, TZ), "ZZB");
  assert.equal(zzb.status, "Missing");
  assert.equal(zzb.reminded, null);
  assert.equal(zzb.canRemind, true);
});

test("(Y62) entityRows(golden): a Received row has canRemind false, even when can_remind is true", () => {
  assert.equal(GOLDEN.can_remind, true);
  const zza = byEntity(entityRows(GOLDEN, GOLDEN_NOW, TZ), "ZZA");
  assert.equal(zza.status, "Received");
  assert.equal(zza.canRemind, false);
});

test("(Y62) failure path: a Viewer payload (can_remind false) gives no Remind on any row, but keeps the text", () => {
  const viewer = { ...GOLDEN, can_remind: false };
  const rows = entityRows(viewer, GOLDEN_NOW, TZ);
  assert.deepEqual(rows.map((r) => r.canRemind), rows.map(() => false));
  assert.match(byEntity(rows, "ZZC").reminded, /^Reminded 2×/, "the recipient sees the text without the button");
});

test("(Y62) failure path: a payload without a boolean can_remind is refused, never read as false", () => {
  const { can_remind: _drop, ...noFlag } = GOLDEN;
  assert.throws(() => entityRows(noFlag, GOLDEN_NOW, TZ), /can_remind/);
  assert.throws(() => entityRows({ ...GOLDEN, can_remind: "yes" }, GOLDEN_NOW, TZ), /can_remind/);
});

test("(Y62) failure path: an entity without its reminders key is refused (Y56 always sends it)", () => {
  const entities = GOLDEN.entities.map((e) => {
    if (e.entity !== "ZZB") return e;
    const { reminders: _drop, ...rest } = e;
    return rest;
  });
  assert.throws(() => entityRows({ ...GOLDEN, entities }, GOLDEN_NOW, TZ), /reminders/);
});

// --- D60: the TB due header and the overdue chip (stories 2.4, 3.1) ---------
// Decision #305-2.4-1: show-only, never blocks. Fed D56's golden my_tbs
// payload: deadline {due: "2025-10-07", past: true}, ZZB/ZZC Missing and
// overdue, ZZA Received and not overdue.

test("(D60) tbDue(golden): a declared due date reads 'TB due Tue 7 Oct 2025'", () => {
  assert.equal(GOLDEN.deadline.due, "2025-10-07");
  const due = tbDue(GOLDEN);
  assert.equal(due.text, "TB due Tue 7 Oct 2025");
  assert.equal(due.past, true);
});

test("(D60) tbDue: an undeclared deadline shows the server's sentence, never a guessed date", () => {
  const undeclared = { ...GOLDEN, deadline: { due: null, past: false, text: "No due date declared" } };
  const due = tbDue(undeclared);
  assert.equal(due.text, "No due date declared");
  assert.equal(due.past, false);
});

test("(D60) failure path: a payload without deadline throws (tbDue and entityRows)", () => {
  const { deadline: _drop, ...noDeadline } = GOLDEN;
  assert.throws(() => tbDue(noDeadline), /deadline/);
  assert.throws(() => entityRows(noDeadline, GOLDEN_NOW, TZ), /deadline/);
  assert.throws(() => tbDue({ ...GOLDEN, deadline: null }), /deadline/);
});

test("(D60) failure path: a malformed due date throws, never shown as a guess", () => {
  assert.throws(() => tbDue({ ...GOLDEN, deadline: { due: "7 Oct", past: true, text: "Due 7 Oct" } }), /due/);
  assert.throws(() => tbDue({ ...GOLDEN, deadline: { due: "2025-10-07", text: "Due 2025-10-07" } }), /past/);
});

test("(D60) entityRows(golden): Missing rows past due carry the Overdue chip; the Received row does not", () => {
  const rows = entityRows(GOLDEN, GOLDEN_NOW, TZ);
  for (const code of ["ZZB", "ZZC"]) {
    const row = byEntity(rows, code);
    assert.equal(row.status, "Missing");
    assert.equal(row.overdue, true);
    assert.deepEqual(row.overdueChip, { text: "Overdue", tone: OVERDUE_TONE });
  }
  const zza = byEntity(rows, "ZZA");
  assert.equal(zza.overdue, false);
  assert.equal(zza.overdueChip, null);
});

test("(D60) failure path: the overdue chip is warn tone, never the block (red) tone", () => {
  assert.match(OVERDUE_TONE, /amber/);
  assert.doesNotMatch(OVERDUE_TONE, /red/);
});

test("(D60) failure path: an entity without a boolean overdue is refused (D56 always sends it)", () => {
  const entities = GOLDEN.entities.map((e) => {
    if (e.entity !== "ZZB") return e;
    const { overdue: _drop, ...rest } = e;
    return rest;
  });
  assert.throws(() => entityRows({ ...GOLDEN, entities }, GOLDEN_NOW, TZ), /overdue/);
});
