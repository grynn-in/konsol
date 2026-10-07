// konsol#305 B15: signoff.test.mjs
//
// Exercises summaryView against A21's real `signoff_model.summary` shape
// (parallel-run override, 25 Sep 2026): `action`, `label`,
// `gates{config_gaps, order, completeness, messages}`, `checks`,
// `acknowledgements{names, total, unlisted}`, `on_behalf{labels, unknown}`,
// `exceptions[]`, `covers`, `previous[]`.
// U47: `commentary[]` (M46's key, story 9.1).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { summaryView, messageLines } from "./signoff.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// U47: M46's golden `commentary` payload — the real producer's output
// (wave-4 lesson), never a hand-built dict.
const GOLDEN_COMMENTARY = JSON.parse(
  fs.readFileSync(
    path.join(__dirname, "..", "..", "konsol", "tests", "fixtures", "close_signoff_commentary.json"),
    "utf8",
  ),
);

// W5-2 (story 8.4): the real statement_api.signoff_commentary output (3
// headings required) and the real get_signoff acknowledgements for a Green
// run with it — both committed by the Python host tests (golden).
const FIXTURES = path.join(__dirname, "..", "..", "konsol", "tests", "fixtures");
const GOLDEN_REQUIRED = JSON.parse(
  fs.readFileSync(path.join(FIXTURES, "close_signoff_commentary_required.json"), "utf8"),
);
const GOLDEN_ACK_COMMENTARY = JSON.parse(
  fs.readFileSync(path.join(FIXTURES, "close_signoff_acknowledgements_commentary.json"), "utf8"),
);

function gates(overrides = {}) {
  return {
    config_gaps: [],
    order: null,
    completeness: null,
    messages: [],
    ...overrides,
  };
}

function checks(overrides = {}) {
  return {
    run: null,
    status: null,
    signoff_status: null,
    failed: null,
    errored: null,
    ...overrides,
  };
}

function acknowledgements(overrides = {}) {
  return { names: [], total: null, unlisted: null, ...overrides };
}

function onBehalf(overrides = {}) {
  return { labels: [], unknown: [], ...overrides };
}

function summary(overrides = {}) {
  return {
    action: "run_checks",
    label: "Run the checks",
    gates: gates(),
    checks: checks(),
    acknowledgements: acknowledgements(),
    on_behalf: onBehalf(),
    exceptions: [],
    covers: [],
    previous: [],
    commentary: [],
    commentary_required: GOLDEN_REQUIRED,
    ...overrides,
  };
}

// --- konsol#305 C11: the sign-off screen's intercompany section -----------

test("C11: no intercompany key reads as 'not reported', visible and never None", () => {
  const view = summaryView(summary());
  assert.deepEqual(view.intercompany.rows, [
    "Intercompany was not reported by the server — nothing was checked.",
  ]);
  assert.equal(view.intercompany.empty, false);
});

test("C11: failure path — intercompany null reads the same 'not reported' line", () => {
  const view = summaryView(summary({ intercompany: null }));
  assert.deepEqual(view.intercompany.rows, [
    "Intercompany was not reported by the server — nothing was checked.",
  ]);
});

test("C11: failure path — not configured never reads as reconciled or matched", () => {
  const view = summaryView(
    summary({
      intercompany: {
        state: "not_configured",
        message: "Intercompany not configured — nothing was checked.",
        counts: null,
      },
    }),
  );
  assert.deepEqual(view.intercompany.rows, ["Intercompany not configured — nothing was checked."]);
  assert.equal(view.intercompany.empty, false);
  for (const row of view.intercompany.rows) {
    assert.doesNotMatch(row, /reconciled|matched|within tolerance|^None$/i);
  }
});

test("C11: failure path — not applicable never reads as reconciled, matched or not configured", () => {
  const view = summaryView(
    summary({
      intercompany: {
        state: "not_applicable",
        message: "Intercompany: none in this group (declared in Close Settings) — not applicable.",
        counts: null,
      },
    }),
  );
  assert.deepEqual(view.intercompany.rows, [
    "Intercompany: none in this group (declared in Close Settings) — not applicable.",
  ]);
  assert.equal(view.intercompany.empty, false);
  for (const row of view.intercompany.rows) {
    assert.doesNotMatch(row, /reconciled|matched|within tolerance|not configured|^None$/i);
  }
});

test("C11: not built and error show their sentence through messageLines", () => {
  const notBuilt = summaryView(
    summary({
      intercompany: {
        state: "not_built",
        message: "The warehouse has not built the intercompany tables yet — nothing was checked.",
        counts: null,
      },
    }),
  );
  assert.deepEqual(notBuilt.intercompany.rows, [
    "The warehouse has not built the intercompany tables yet — nothing was checked.",
  ]);

  const error = summaryView(
    summary({
      intercompany: {
        state: "error",
        message: "Intercompany could not be checked: HTTPError (TIMEOUT_EXCEEDED). Rebuild the consolidation, then open this again.",
        counts: null,
      },
    }),
  );
  assert.deepEqual(error.intercompany.rows, [
    "Intercompany could not be checked: HTTPError (TIMEOUT_EXCEEDED). Rebuild the consolidation, then open this again.",
  ]);
});

test("C11: checked with counts shows the pairs line", () => {
  const view = summaryView(
    summary({
      intercompany: {
        state: "checked",
        message: null,
        counts: { pairs: 10, matched: 5, within_tolerance: 2, fx_difference: 1, over_tolerance: 2, unmatched: 0 },
        sent_back_open: 0,
      },
    }),
  );
  assert.deepEqual(view.intercompany.rows, [
    "10 pairs: 5 matched, 2 within tolerance, 1 FX differences, 2 over tolerance",
  ]);
});

test("C11: checked with sent_back_open adds the open send-backs line", () => {
  const view = summaryView(
    summary({
      intercompany: {
        state: "checked",
        message: null,
        counts: { pairs: 10, matched: 8, within_tolerance: 1, fx_difference: 0, over_tolerance: 1, unmatched: 0 },
        sent_back_open: 2,
      },
    }),
  );
  assert.deepEqual(view.intercompany.rows, [
    "10 pairs: 8 matched, 1 within tolerance, 0 FX differences, 1 over tolerance",
    "2 sent back and still open",
  ]);
});

test("C11: checked with unmatched rows adds the 'without a partner' line", () => {
  const view = summaryView(
    summary({
      intercompany: {
        state: "checked",
        message: null,
        counts: { pairs: 10, matched: 10, within_tolerance: 0, fx_difference: 0, over_tolerance: 0, unmatched: 3 },
        sent_back_open: 0,
      },
    }),
  );
  assert.deepEqual(view.intercompany.rows, [
    "10 pairs: 10 matched, 0 within tolerance, 0 FX differences, 0 over tolerance",
    "3 rows without a partner",
  ]);
});

test("C11: checked with 0 pairs shows the dedicated message line, never a 0-pairs count line", () => {
  const view = summaryView(
    summary({
      intercompany: {
        state: "checked",
        message: null,
        counts: { pairs: 0, matched: 0, within_tolerance: 0, fx_difference: 0, over_tolerance: 0, unmatched: 0 },
        sent_back_open: 0,
      },
    }),
  );
  assert.deepEqual(view.intercompany.rows, [
    "0 intercompany pairs in the last build for this period.",
  ]);
});

test("C11: failure path — an unknown intercompany state throws naming it", () => {
  assert.throws(
    () => summaryView(summary({ intercompany: { state: "fine", message: null, counts: null } })),
    /fine/,
  );
});

test("every section is rendered from a full summary", () => {
  const view = summaryView(
    summary({
      action: "acknowledge",
      label: "Acknowledge the warnings and sign off",
      gates: gates({
        config_gaps: [{ code: "frequency_undeclared", message: "Set the frequency on ZZA." }],
      }),
      checks: checks({ run: "AR-0001", status: "Amber", signoff_status: "Not Signed Off", failed: 0, errored: 0 }),
      acknowledgements: acknowledgements({ names: ["assert_fx_balances"], total: 3, unlisted: 2 }),
      on_behalf: onBehalf({ labels: ["by alice@example.com for ZZA"], unknown: [] }),
      exceptions: [{ entity: "ZZB", reason: "Entity dormant this period", declared_by: "bob@example.com" }],
      covers: ["ZZC: covers P08–P09"],
      previous: [{ code: "P07", status: "Closed", signoff: "Signed Off" }],
    }),
  );

  assert.equal(view.action, "acknowledge");
  assert.equal(view.label, "Acknowledge the warnings and sign off");
  assert.deepEqual(view.gates.rows, ["Set the frequency on ZZA."]);
  assert.deepEqual(view.checks.rows, [
    "Run: AR-0001",
    "Status: Amber",
    "Sign-off status: Not Signed Off",
    "Failed: 0",
    "Errored: 0",
  ]);
  assert.deepEqual(view.acknowledgements.rows, [
    "Acknowledged: assert_fx_balances",
    "Warned in total: 3",
    "Not listed above: 2",
  ]);
  assert.deepEqual(view.onBehalf.rows, ["by alice@example.com for ZZA"]);
  assert.deepEqual(view.exceptions.rows, ["ZZB: Entity dormant this period (declared by bob@example.com)"]);
  assert.deepEqual(view.covers.rows, ["ZZC: covers P08–P09"]);
  assert.deepEqual(view.previous.rows, ["P07: Closed, Signed Off"]);
});

test("every section is empty and says None on a bare summary", () => {
  const view = summaryView(summary());
  assert.deepEqual(view.gates.rows, ["None"]);
  assert.equal(view.gates.empty, true);
  assert.deepEqual(view.checks.rows, ["None"]);
  assert.equal(view.checks.empty, true);
  assert.deepEqual(view.acknowledgements.rows, ["None"]);
  assert.equal(view.acknowledgements.empty, true);
  assert.deepEqual(view.onBehalf.rows, ["None"]);
  assert.equal(view.onBehalf.empty, true);
  assert.deepEqual(view.exceptions.rows, ["None"]);
  assert.equal(view.exceptions.empty, true);
  assert.deepEqual(view.covers.rows, ["None"]);
  assert.equal(view.covers.empty, true);
  assert.deepEqual(view.previous.rows, ["None"]);
  assert.equal(view.previous.empty, true);
  // C11: the intercompany section is never ["None"] — a missing key is "not reported", visible.
  assert.deepEqual(view.intercompany.rows, [
    "Intercompany was not reported by the server — nothing was checked.",
  ]);
  assert.equal(view.intercompany.empty, false);
});

test("configuration gaps are listed first, before order, before completeness", () => {
  const view = summaryView(
    summary({
      gates: gates({
        config_gaps: [{ code: "frequency_undeclared", message: "Declare frequencies first." }],
        order: { blocking: "P07", periods: ["P07", "P08"], message: "Sign off P07 first" },
        completeness: { missing: ["ZZA"], message: "No trial balance from ZZA." },
      }),
    }),
  );
  assert.deepEqual(view.gates.rows, [
    "Declare frequencies first.",
    "Sign off P07 first",
    "No trial balance from ZZA.",
  ]);
});

test("a server message joined with <br> is split into separate escaped lines", () => {
  assert.deepEqual(
    messageLines("First problem<br>Second problem"),
    ["First problem", "Second problem"],
  );
  assert.deepEqual(
    messageLines("A<br/>B<br />C"),
    ["A", "B", "C"],
  );
});

test("a message is left as plain text for a text binding to escape", () => {
  assert.deepEqual(messageLines("A & B <script>"), ["A & B <script>"]);
});

test("<br>-joined gate messages are split and escaped inside the gates section", () => {
  const view = summaryView(
    summary({
      gates: gates({
        order: { blocking: "P07", periods: ["P07"], message: "Sign off P07 first<br>Also fix P06" },
      }),
    }),
  );
  assert.deepEqual(view.gates.rows, ["Sign off P07 first", "Also fix P06"]);
});

test("an unknown failed/errored count is shown as unknown, never 0", () => {
  const view = summaryView(
    summary({
      checks: checks({ run: "AR-0002", status: "Running", signoff_status: "Not Signed Off", failed: null, errored: null }),
    }),
  );
  assert.deepEqual(view.checks.rows, [
    "Run: AR-0002",
    "Status: Running",
    "Sign-off status: Not Signed Off",
    "Failed: unknown",
    "Errored: unknown",
  ]);
});

test("an unknown acknowledgement total is shown as unknown, never 0, even with names listed", () => {
  const view = summaryView(
    summary({
      acknowledgements: acknowledgements({ names: ["assert_fx_balances"], total: null, unlisted: null }),
    }),
  );
  assert.deepEqual(view.acknowledgements.rows, [
    "Acknowledged: assert_fx_balances",
    "Warned in total: unknown",
    "Not listed above: unknown",
  ]);
});

// --- konsol#305 C23: the acknowledgement section shows the IC line -------

test("C23: the server's IC acknowledgement sentence is the first acknowledgements row", () => {
  const view = summaryView(
    summary({
      acknowledgements: acknowledgements({
        intercompany: "Intercompany: 2 pairs over tolerance",
      }),
    }),
  );
  assert.deepEqual(view.acknowledgements.rows, [
    "Intercompany: 2 pairs over tolerance",
  ]);
  assert.equal(view.acknowledgements.empty, false);
});

test("C23: the IC sentence leads, then the named acknowledgements and totals", () => {
  const view = summaryView(
    summary({
      acknowledgements: acknowledgements({
        intercompany: "Intercompany: 1 pair over tolerance",
        names: ["assert_fx_balances"],
        total: 3,
        unlisted: 2,
      }),
    }),
  );
  assert.deepEqual(view.acknowledgements.rows, [
    "Intercompany: 1 pair over tolerance",
    "Acknowledged: assert_fx_balances",
    "Warned in total: 3",
    "Not listed above: 2",
  ]);
});

test("C23: failure path — a null intercompany line (not configured, not applicable, or an older payload) leaves today's rows untouched", () => {
  const view = summaryView(
    summary({
      acknowledgements: acknowledgements({
        intercompany: null,
        names: ["assert_fx_balances"],
        total: 3,
        unlisted: 2,
      }),
    }),
  );
  assert.deepEqual(view.acknowledgements.rows, [
    "Acknowledged: assert_fx_balances",
    "Warned in total: 3",
    "Not listed above: 2",
  ]);
  for (const row of view.acknowledgements.rows) {
    assert.doesNotMatch(row, /null|undefined/i);
  }
});

test("C23: failure path — a missing intercompany key (an older payload) leaves today's bare-summary rows untouched", () => {
  const view = summaryView(summary());
  assert.deepEqual(view.acknowledgements.rows, ["None"]);
  assert.equal(view.acknowledgements.empty, true);
});

test("C23: the acknowledgements section defines no intercompany wording of its own — it only relays the server's sentence", async () => {
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(new URL("./signoff.js", import.meta.url), "utf8");
  const fn = source.slice(
    source.indexOf("function acknowledgementsSection"),
    source.indexOf("function acknowledgementsSection") +
      source.slice(source.indexOf("function acknowledgementsSection")).indexOf("\n}\n") +
      3,
  );
  assert.ok(fn.length > 0, "acknowledgementsSection function not found");
  assert.doesNotMatch(fn, /over tolerance/);
});

test("an on-behalf upload with no tracked flag shows as unknown, not silently dropped", () => {
  const view = summaryView(
    summary({
      on_behalf: onBehalf({
        labels: [],
        unknown: ["ZZA: by carol@example.com; on-behalf not recorded (uploaded before it was tracked)"],
      }),
    }),
  );
  assert.deepEqual(view.onBehalf.rows, [
    "ZZA: by carol@example.com; on-behalf not recorded (uploaded before it was tracked)",
  ]);
  assert.equal(view.onBehalf.empty, false);
});

test("failure path: an unknown action throws", () => {
  assert.throws(
    () => summaryView(summary({ action: "teleport" })),
    /Unknown sign-off action: teleport/,
  );
});

test("B15b: lines stay plain text; the screen's text binding does the escaping", () => {
	// Escaping here AND in Vue's {{ }} would show "&amp;" to the user.
	assert.deepEqual(messageLines("R&D costs<br>P&L <check>"), ["R&D costs", "P&L <check>"]);
});

test("B15b: no screen renders server text as HTML (no v-html anywhere in src)", async () => {
	const { readdir, readFile } = await import("node:fs/promises");
	const { join } = await import("node:path");
	const root = new URL(".", import.meta.url).pathname;
	const bad = [];
	async function walk(dir) {
		for (const e of await readdir(dir, { withFileTypes: true })) {
			const p = join(dir, e.name);
			if (e.isDirectory()) await walk(p);
			else if (p.endsWith(".vue") && (await readFile(p, "utf8")).includes("v-html")) bad.push(p);
		}
	}
	await walk(root);
	assert.deepEqual(bad, []);
});

// konsol#305 B28: a long section is counted, never dropped (C1: 328 TB exceptions).
function exceptionRows(n) {
  return Array.from({ length: n }, (_, i) => ({
    entity: `ZZ${String(i).padStart(3, "0")}`,
    reason: "Entity dormant this period",
    declared_by: "bob@example.com",
  }));
}

test("B28: 328 exceptions show the first 10 and 'and 318 more'; the full list stays available", () => {
  const view = summaryView(summary({ exceptions: exceptionRows(328) }));
  const s = view.exceptions;
  assert.equal(s.rows.length, 328, "rows are never dropped from the data");
  assert.equal(s.shown.length, 10);
  assert.deepEqual(s.shown, s.rows.slice(0, 10));
  assert.equal(s.hidden, 318);
  assert.equal(s.moreText, "and 318 more");
  assert.equal(s.empty, false);
});

test("B28: exactly 10 rows are all shown, with no 'more' line", () => {
  const view = summaryView(summary({ exceptions: exceptionRows(10) }));
  assert.equal(view.exceptions.shown.length, 10);
  assert.equal(view.exceptions.hidden, 0);
  assert.equal(view.exceptions.moreText, null);
});

test("B28: 11 rows show 10 and 'and 1 more'", () => {
  const view = summaryView(summary({ exceptions: exceptionRows(11) }));
  assert.equal(view.exceptions.shown.length, 10);
  assert.equal(view.exceptions.moreText, "and 1 more");
});

test("B28: an empty section shows 'None' and no 'more' line", () => {
  const view = summaryView(summary());
  assert.deepEqual(view.exceptions.shown, ["None"]);
  assert.equal(view.exceptions.hidden, 0);
  assert.equal(view.exceptions.moreText, null);
});

test("B28: every section is counted the same way, not only exceptions", () => {
  const covers = Array.from({ length: 12 }, (_, i) => `ZZ${i}: covers P08`);
  const view = summaryView(summary({ covers }));
  assert.equal(view.covers.rows.length, 12);
  assert.equal(view.covers.shown.length, 10);
  assert.equal(view.covers.moreText, "and 2 more");
});

test("B28: the Sign-off screen renders the shown rows, the count and a Show all toggle", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("./screens/SignOff.vue", import.meta.url), "utf8");
  assert.match(src, /moreText/, "the 'and N more' line is rendered");
  assert.match(src, /\.shown\b/, "the collapsed list renders `shown`");
  assert.match(src, /Show all/, "a Show all toggle is offered");
});

// --- konsol#305 B33: "closed on" is a time in the user's zone -------------

test("B33: a zoned closed_on is shown in the user's zone, like the TB list (B27)", async () => {
  const { closedOnText } = await import("./signoff.js");
  // C1 run 3's raw value; now is the same day in Europe/Berlin.
  const now = new Date("2026-09-25T23:00:00+02:00");
  assert.equal(closedOnText("2026-09-25T22:28:32.554257+02:00", now, "Europe/Berlin"), "22:28");
  // Another day, another zone: the date is added and the hour follows the zone.
  const later = new Date("2026-09-30T12:00:00Z");
  assert.equal(closedOnText("2026-09-25T20:28:32Z", later, "Asia/Kolkata"), "26 Sep, 01:58");
});

test("B33: a missing closed_on reads 'unknown', never blank", async () => {
  const { closedOnText } = await import("./signoff.js");
  const now = new Date("2026-09-25T12:00:00Z");
  for (const missing of [null, undefined, ""]) {
    assert.equal(closedOnText(missing, now, "Europe/Berlin"), "unknown");
  }
});

test("B33: failure path — a zone-less closed_on is refused, never read in the browser's zone (B09b)", async () => {
  const { closedOnText } = await import("./signoff.js");
  const now = new Date("2026-09-25T12:00:00Z");
  assert.throws(() => closedOnText("2026-09-25 22:28:32.554257", now, "Europe/Berlin"), /no time zone/);
});

test("B33: failure path — no user zone is refused, never the machine's zone", async () => {
  const { closedOnText } = await import("./signoff.js");
  const now = new Date("2026-09-25T12:00:00Z");
  for (const zone of [null, undefined, ""]) {
    assert.throws(() => closedOnText("2026-09-25T20:28:32Z", now, zone), /time zone/);
  }
});

// --- konsol#305 U47: the sign-off summary's missing-commentary section ----

test("U47: commentary section reads M46's golden payload, one row per group", () => {
  const view = summaryView(summary({ commentary: GOLDEN_COMMENTARY }));
  assert.deepEqual(view.commentary.rows, [
    "ZZGRP: 0 of 2 headings commented · missing: NET SALES, OPERATING EXPENSES",
  ]);
  assert.equal(view.commentary.empty, false);
});

test("U47: a group with nothing missing reads with no · missing clause", () => {
  const view = summaryView(
    summary({ commentary: [{ consolidation_group: "ZZGRP", headings: 2, with_commentary: 2, missing: [] }] }),
  );
  assert.deepEqual(view.commentary.rows, ["ZZGRP: 2 of 2 headings commented"]);
});

test("U47: no groups reads as the section's own None row", () => {
  const view = summaryView(summary({ commentary: [] }));
  assert.deepEqual(view.commentary.rows, ["None"]);
  assert.equal(view.commentary.empty, true);
});

test("U47: failure path — a missing commentary key throws (an older payload, never defaulted)", () => {
  const s = summary({ commentary: GOLDEN_COMMENTARY });
  delete s.commentary;
  assert.throws(() => summaryView(s), /commentary/);
});

// R41j (U8): `signoff_api.py`'s `_commentary` always returns a list, never
// null, and `result.update({..., "commentary": _commentary(key)})` sets it
// unconditionally on every call — a literal `null` is as much a broken
// contract as the key being absent, so it must throw too, never silently
// read as an empty ("None") section.
test("U47: failure path — a null commentary value throws, never reading as an empty section", () => {
  assert.throws(() => summaryView(summary({ commentary: null })), /commentary/);
});

test("U47: failure path — commentary never changes the summary's action", () => {
  const withGaps = summaryView(summary({ commentary: GOLDEN_COMMENTARY })).action;
  const allCommented = summaryView(
    summary({ commentary: [{ consolidation_group: "ZZGRP", headings: 2, with_commentary: 2, missing: [] }] }),
  ).action;
  const none = summaryView(summary({ commentary: [] })).action;
  assert.equal(withGaps, "run_checks");
  assert.equal(allCommented, "run_checks");
  assert.equal(none, "run_checks");
});

// --- #305-W5-1 (story 9.4, #157): the reject dialog follows the machine --------

test("rejectDialogAfter: a refusal (rejecting → signed) keeps the text and shows the error", async () => {
	const { rejectDialogAfter } = await import("./signoff.js");
	assert.equal(rejectDialogAfter("rejecting", "signed"), "refused");
});

test("rejectDialogAfter: while signed or rejecting, the dialog and its text are kept", async () => {
	const { rejectDialogAfter } = await import("./signoff.js");
	assert.equal(rejectDialogAfter("signed", "rejecting"), "keep");
	assert.equal(rejectDialogAfter("loading", "signed"), "keep");
	assert.equal(rejectDialogAfter(null, "signed"), "keep");
});

test("rejectDialogAfter: any other state (an accepted reject reloads, a close, a refresh) resets it", async () => {
	const { rejectDialogAfter } = await import("./signoff.js");
	for (const [prev, state] of [["rejecting", "loading"], ["signed", "loading"], ["signed", "closing"],
		["loading", "review"], ["loading", "closed"], ["loading", "loadFailed"],
		["closing", "closed"], [null, "review"], ["rejecting", "loadFailed"]]) {
		assert.equal(rejectDialogAfter(prev, state), "reset", `${prev} → ${state}`);
	}
});

// --- konsol#305-W5-2 (story 8.4): commentary required above the threshold ---

test("8.4: the required section names the threshold, then each uncommented heading above it", () => {
  const view = summaryView(summary());
  assert.deepEqual(view.commentaryRequired.rows, [
    "Threshold: a heading needs commentary when its variance against the previous period is above 100.00",
    "G1 · ASSETS: moved 1,735.10 (173.51%) from 1,000.00 — no commentary",
    "G1 · LIABILITIES: moved 803.70 from 0.00 (no percentage on a zero base) — no commentary",
    "G1 · EQUITY: moved 931.40 (118.66%) from 784.90 — no commentary",
  ]);
  assert.equal(view.commentaryRequired.empty, false);
});

test("8.4: the informational missing list stays its own section", () => {
  const view = summaryView(summary({ commentary: GOLDEN_COMMENTARY }));
  assert.deepEqual(view.commentary.rows, [
    "ZZGRP: 0 of 2 headings commented · missing: NET SALES, OPERATING EXPENSES",
  ]);
  assert.ok(!view.commentary.rows.some((r) => r.startsWith("Threshold")));
});

test("8.4: the threshold reads amount, percent, either and both as declared", () => {
  const line = (threshold) => summaryView(summary({
    commentary_required: { ...GOLDEN_REQUIRED, threshold, groups: [], required_missing: 0 },
  })).commentaryRequired.rows[0];
  const lead = "Threshold: a heading needs commentary when its variance against the previous period is above ";
  assert.equal(line({ amount: 5000, percent: null, combine: null }), `${lead}5,000.00`);
  assert.equal(line({ amount: null, percent: 10, combine: null }), `${lead}10%`);
  assert.equal(line({ amount: 5000, percent: 10, combine: "Either is exceeded" }), `${lead}5,000.00 or 10%`);
  assert.equal(line({ amount: 5000, percent: 10, combine: "Both are exceeded" }), `${lead}5,000.00 and 10%`);
});

test("8.4: nothing required says so after the threshold", () => {
  const view = summaryView(summary({
    commentary_required: { ...GOLDEN_REQUIRED, groups: [], required_missing: 0 },
  }));
  assert.equal(view.commentaryRequired.rows[1], "Every heading above the threshold has commentary");
});

test("8.4: a group with no comparison names why it was not compared", () => {
  const view = summaryView(summary({
    commentary_required: {
      ...GOLDEN_REQUIRED,
      groups: [{ consolidation_group: "G1", state: "not_comparable",
                 message: "No rows in the warehouse for FY2025 P06", over_threshold: 0, required: [] }],
      required_missing: 0,
    },
  }));
  assert.ok(view.commentaryRequired.rows.includes(
    "G1: not compared — No rows in the warehouse for FY2025 P06"));
});

test("8.4: undeclared and unknown show the server's message, never a count", () => {
  const undeclared = { state: "undeclared", threshold: null, message: "Declare the commentary threshold in Close Settings.", groups: [], required_missing: null };
  assert.deepEqual(summaryView(summary({ commentary_required: undeclared })).commentaryRequired.rows,
    ["Declare the commentary threshold in Close Settings."]);
  const unknown = { ...GOLDEN_REQUIRED, state: "unknown", message: "The commentary threshold cannot be checked.", required_missing: null };
  assert.deepEqual(summaryView(summary({ commentary_required: unknown })).commentaryRequired.rows,
    ["The commentary threshold cannot be checked."]);
});

test("8.4: failure path — a missing or unknown-state commentary_required throws", () => {
  const s = summary();
  delete s.commentary_required;
  assert.throws(() => summaryView(s), /commentary_required/);
  assert.throws(() => summaryView(summary({ commentary_required: null })), /commentary_required/);
  assert.throws(() => summaryView(summary({ commentary_required: { ...GOLDEN_REQUIRED, state: "bogus" } })), /bogus/);
});

test("8.4: the server's commentary acknowledgement sentence is shown, after intercompany", () => {
  const view = summaryView(summary({ action: "acknowledge", acknowledgements: GOLDEN_ACK_COMMENTARY }));
  assert.deepEqual(view.acknowledgements.rows, [
    "Commentary: 3 headings above the threshold without commentary",
    "Warned in total: 0",
    "Not listed above: 0",
  ]);
  const both = summaryView(summary({
    acknowledgements: { ...GOLDEN_ACK_COMMENTARY, intercompany: "Intercompany: 1 pair over tolerance" },
  }));
  assert.deepEqual(both.acknowledgements.rows.slice(0, 2), [
    "Intercompany: 1 pair over tolerance",
    "Commentary: 3 headings above the threshold without commentary",
  ]);
});

// --- konsol#305 U3/U10: the Reject dialog's state, driven like the screen ----
//
// rejectDialogNext is the whole dialog: the section only applies it. These
// drive it with the real signoffMachine (stale-run refusal) and with the
// events the dialog receives (Esc/overlay/Cancel all close it).

import { createActor, fromPromise } from "xstate";
import { signoffMachine, STALE_RUN_REFUSAL } from "./machines/signoffMachine.js";
import { rejectDialogAfter, rejectDialogNext, REJECT_DIALOG_CLOSED } from "./signoff.js";

const STALE = `${STALE_RUN_REFUSAL} after this summary was loaded. Review the new results and sign again.`;

function signedSummary(run) {
	return { action: "signed", label: "Signed", can_sign: true, can_reject: true, period_status: "Open", checks: { run } };
}

/** A machine whose services are deferred promises the test settles, and a
 * dialog driven by every snapshot change exactly as the section's watch does. */
function harness() {
	const calls = { load: [], reject: [] };
	const deferred = (name) =>
		fromPromise(({ input }) => new Promise((resolve, reject) => calls[name].push({ input, resolve, reject })));
	const actor = createActor(signoffMachine.provide({ actors: { load: deferred("load"), reject: deferred("reject") } }));
	let dialog = REJECT_DIALOG_CLOSED;
	let prev = null;
	actor.subscribe((snap) => {
		if (snap.value !== prev) {
			dialog = rejectDialogNext(dialog, { type: "MACHINE", prev, state: snap.value, error: snap.context.error });
			prev = snap.value;
		}
	});
	actor.start();
	const flush = () => new Promise((r) => setTimeout(r, 0));
	return {
		actor,
		calls,
		flush,
		get dialog() {
			return dialog;
		},
		ui(event) {
			dialog = rejectDialogNext(dialog, event);
		},
	};
}

test("U3: rejecting → loading with a message is a stale refusal; with none it is an accepted reject", () => {
	assert.equal(rejectDialogAfter("rejecting", "loading", STALE), "refused");
	assert.equal(rejectDialogAfter("rejecting", "loading", null), "reset");
});

test("U3: a stale-run refusal of Reject keeps the typed reason and shows the server's message while the summary reloads", async () => {
	const h = harness();
	h.calls.load[0].resolve(signedSummary("RUN-1"));
	await h.flush();
	assert.equal(h.actor.getSnapshot().value, "signed");
	h.ui({ type: "OPEN" });
	h.ui({ type: "TYPE", text: "TB for ZZA is the March file" });
	h.ui({ type: "SENT" });
	h.actor.send({ type: "REJECT", reason: h.dialog.reason });
	assert.equal(h.calls.reject[0].input.run, "RUN-1");
	h.calls.reject[0].reject(new Error(STALE));
	await h.flush();
	assert.equal(h.actor.getSnapshot().value, "loading", "the summary reloads");
	assert.deepEqual(h.dialog, { open: true, reason: "TB for ZZA is the March file", refused: [STALE] });

	h.calls.load[1].resolve(signedSummary("RUN-2"));
	await h.flush();
	assert.equal(h.actor.getSnapshot().value, "signed");
	assert.deepEqual(h.dialog, { open: true, reason: "TB for ZZA is the March file", refused: [STALE] },
		"still open with the text and the message: a fresh click is needed");
	assert.equal(h.calls.reject.length, 1, "nothing is re-sent on its own");

	h.ui({ type: "SENT" });
	h.actor.send({ type: "REJECT", reason: h.dialog.reason });
	assert.equal(h.calls.reject[1].input.run, "RUN-2", "the fresh click goes against the reloaded summary");
	assert.equal(h.calls.reject[1].input.reason, "TB for ZZA is the March file");
	h.actor.stop();
});

test("U3: a stale refusal whose reload no longer offers Reject closes the dialog", async () => {
	const h = harness();
	h.calls.load[0].resolve(signedSummary("RUN-1"));
	await h.flush();
	h.ui({ type: "OPEN" });
	h.ui({ type: "TYPE", text: "why" });
	h.actor.send({ type: "REJECT", reason: "why" });
	h.calls.reject[0].reject(new Error(STALE));
	await h.flush();
	assert.equal(h.dialog.open, true);
	h.calls.load[1].resolve({ action: "rerun", label: "Re-run", can_sign: true, checks: { run: "RUN-2" } });
	await h.flush();
	assert.equal(h.actor.getSnapshot().value, "review");
	assert.deepEqual(h.dialog, REJECT_DIALOG_CLOSED);
	h.actor.stop();
});

test("U3: an accepted reject closes the dialog and drops the text", async () => {
	const h = harness();
	h.calls.load[0].resolve(signedSummary("RUN-1"));
	await h.flush();
	h.ui({ type: "OPEN" });
	h.ui({ type: "TYPE", text: "why" });
	h.actor.send({ type: "REJECT", reason: "why" });
	h.calls.reject[0].resolve({ ok: true });
	await h.flush();
	assert.deepEqual(h.dialog, REJECT_DIALOG_CLOSED);
	h.actor.stop();
});

test("U3: a non-stale refusal keeps the text and shows the message (rejecting → signed)", async () => {
	const h = harness();
	h.calls.load[0].resolve(signedSummary("RUN-1"));
	await h.flush();
	h.ui({ type: "OPEN" });
	h.ui({ type: "TYPE", text: "why" });
	h.actor.send({ type: "REJECT", reason: "why" });
	h.calls.reject[0].reject(new Error("Only the Close Lead may reject."));
	await h.flush();
	assert.equal(h.actor.getSnapshot().value, "signed");
	assert.deepEqual(h.dialog, { open: true, reason: "why", refused: ["Only the Close Lead may reject."] });
	h.actor.stop();
});

test("U10: closing the dialog any way (Esc, overlay, Cancel) resets the text and the message", () => {
	let d = rejectDialogNext(REJECT_DIALOG_CLOSED, { type: "OPEN" });
	d = rejectDialogNext(d, { type: "TYPE", text: "old reason" });
	d = rejectDialogNext(d, { type: "MACHINE", prev: "rejecting", state: "signed", error: "Refused." });
	assert.deepEqual(d, { open: true, reason: "old reason", refused: ["Refused."] });
	d = rejectDialogNext(d, { type: "CLOSE" });
	assert.deepEqual(d, REJECT_DIALOG_CLOSED);
	d = rejectDialogNext(d, { type: "OPEN" });
	assert.deepEqual(d, { open: true, reason: "", refused: [] }, "reopening shows neither the old text nor the old refusal");
});

test("U10: the dialog state is never shared: every step returns a new object", () => {
	const d = rejectDialogNext(REJECT_DIALOG_CLOSED, { type: "OPEN" });
	assert.notEqual(d, REJECT_DIALOG_CLOSED);
	assert.equal(REJECT_DIALOG_CLOSED.open, false);
	assert.throws(() => rejectDialogNext(d, { type: "NOPE" }), /NOPE/);
});

// --- konsol#305 review-w5 U9: every group's message shows; no guessed state ---

test("U9: a checked group from no_chart shows its message (commentary_model.requirement)", () => {
  const view = summaryView(summary({
    commentary_required: {
      ...GOLDEN_REQUIRED,
      groups: [{ consolidation_group: "G2", state: "checked",
                 message: "No chart of accounts is mapped for G2.", over_threshold: 0, required: [] }],
      required_missing: 0,
    },
  }));
  assert.ok(view.commentaryRequired.rows.includes("G2: No chart of accounts is mapped for G2."),
    view.commentaryRequired.rows.join(" | "));
});

test("U9: a checked group with no message adds no line of its own", () => {
  const view = summaryView(summary());
  assert.ok(!view.commentaryRequired.rows.some((r) => r.startsWith("G1: ")));
});

test("U9: failure path — an unknown group state throws, naming it", () => {
  assert.throws(() => summaryView(summary({
    commentary_required: {
      ...GOLDEN_REQUIRED,
      groups: [{ consolidation_group: "G1", state: "maybe", message: null, over_threshold: 0, required: [] }],
      required_missing: 0,
    },
  })), /maybe/);
});
