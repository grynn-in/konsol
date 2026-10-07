// konsol#305 E207: periodGrid.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { COLUMNS, TONES, toneClass, gridView, readinessView, deadlineStrip, DEADLINE_STEPS } from "./periodGrid.js";
import { remindedText } from "./remind.js";

const NOW = new Date("2025-10-06T15:00:00Z");
const TZ = "Europe/London";

// D61: the hand-built payloads below carry the golden file's deadlines (the
// real producer's output), so every gridView call sees the keys Y57/D57 send.
const GOLDEN_DEADLINES = JSON.parse(
	readFileSync(fileURLToPath(new URL("../../konsol/tests/fixtures/close_period_grid_payload.json", import.meta.url)), "utf8"),
).deadlines;

function ownership(tone, label) {
	return { tone, label: label ?? `ownership ${tone}` };
}
function tb(tone, label) {
	return { tone, label: label ?? `tb ${tone}`, reminders: null, overdue: false };
}
function rate(tone, label) {
	return { tone, label: label ?? `rate ${tone}` };
}

function row(entity, problem, tones = ["ok", "ok", "ok"]) {
	return {
		entity,
		name: entity,
		currency: "USD",
		in_scope: true,
		problem,
		ownership: ownership(tones[0]),
		tb: tb(tones[1]),
		rate: rate(tones[2]),
	};
}

function payload(overrides = {}) {
	return {
		period: { fiscal_year: 2026, fiscal_period: 7, code: "FY2026 P07", status: "Open" },
		rows: [row("ZZAA", true), row("ZZBB", false)],
		counts: { rows: 2, problems: 1, hidden: 0 },
		rates_error: null,
		deadlines: JSON.parse(JSON.stringify(GOLDEN_DEADLINES)),
		signoff_overdue: false,
		...overrides,
	};
}

test("COLUMNS is exactly the three grid columns, never Intercompany or Checks", () => {
	assert.deepEqual(COLUMNS, ["Ownership", "Trial balance", "Closing rate"]);
	assert.ok(!COLUMNS.includes("Intercompany"));
	assert.ok(!COLUMNS.includes("Checks"));
});

test("TONES lists the three tones", () => {
	assert.deepEqual(TONES, ["ok", "blocking", "none"]);
});

test("toneClass maps each tone to its class", () => {
	assert.equal(toneClass("ok"), "bg-surface-green-1 text-ink-green-3");
	assert.equal(toneClass("blocking"), "bg-surface-red-1 text-ink-red-3");
	assert.equal(toneClass("none"), "bg-surface-gray-1 text-ink-gray-6");
});

test("toneClass throws on an unknown tone", () => {
	assert.throws(() => toneClass("warn"), /unknown tone: warn/);
});

test("gridView with problemsOnly false keeps every row in server order", () => {
	const view = gridView(payload(), false, NOW, TZ);
	assert.deepEqual(
		view.rows.map((r) => r.entity),
		["ZZAA", "ZZBB"],
	);
});

test("gridView with problemsOnly true keeps only problem rows, server order preserved", () => {
	const p = payload({
		rows: [row("ZZAA", false), row("ZZBB", true), row("ZZCC", true)],
	});
	const view = gridView(p, true, NOW, TZ);
	assert.deepEqual(
		view.rows.map((r) => r.entity),
		["ZZBB", "ZZCC"],
	);
});

test("all and problems come from payload.counts, never recomputed from rows", () => {
	const p = payload({ counts: { rows: 9, problems: 4, hidden: 0 } });
	const view = gridView(p, false, NOW, TZ);
	assert.equal(view.all, 9);
	assert.equal(view.problems, 4);
});

test("hiddenNote is null when counts.hidden is 0", () => {
	const view = gridView(payload({ counts: { rows: 2, problems: 1, hidden: 0 } }), false, NOW, TZ);
	assert.equal(view.hiddenNote, null);
});

test("hiddenNote names the count when counts.hidden is 3", () => {
	const view = gridView(payload({ counts: { rows: 2, problems: 1, hidden: 3 } }), false, NOW, TZ);
	assert.equal(view.hiddenNote, "3 entities outside your scope are not shown");
});

test("ratesNote is null when rates_error is not set", () => {
	const view = gridView(payload({ rates_error: null }), false, NOW, TZ);
	assert.equal(view.ratesNote, null);
});

test("ratesNote names the error when rates_error is set", () => {
	const view = gridView(payload({ rates_error: "No closing rate build for the period" }), false, NOW, TZ);
	assert.equal(view.ratesNote, "Rates cannot be checked: No closing rate build for the period");
});

test("empty reads 'No problems in <code>' under problemsOnly", () => {
	const view = gridView(payload(), true, NOW, TZ);
	assert.equal(view.empty, "No problems in FY2026 P07");
});

test("empty reads 'No entities in scope for <code>' when not problemsOnly", () => {
	const view = gridView(payload(), false, NOW, TZ);
	assert.equal(view.empty, "No entities in scope for FY2026 P07");
});

test("gridView throws on a row with an unknown cell tone", () => {
	const p = payload({ rows: [row("ZZAA", true, ["ok", "warn", "ok"])] });
	assert.throws(() => gridView(p, false, NOW, TZ), /unknown tone: warn/);
});

function item(code, state, overrides = {}) {
	return {
		code,
		state,
		label: code,
		detail: `${code} detail`,
		entities: [],
		hidden: 0,
		...overrides,
	};
}

function readinessPayload(overrides = {}) {
	return {
		ready: 6,
		total: 9,
		items: [
			item("period_open", "ok"),
			item("first_close", "ok"),
			item("previous_signed", "ok"),
			item("policies", "ok"),
			item("configuration", "ok"),
			item("ownership", "blocked"),
			item("trial_balances", "blocked"),
			item("rates", "blocked"),
			item("checks", "unknown"),
		],
		...overrides,
	};
}

test("readinessView's title names ready and total", () => {
	const view = readinessView(readinessPayload({ ready: 6, total: 9 }));
	assert.equal(view.title, "Ready to close · 6 of 9");
});

test("readinessView glyphs: ok, blocked, unknown", () => {
	const view = readinessView(readinessPayload());
	const byCode = Object.fromEntries(view.items.map((it, i) => [readinessPayload().items[i].code, it]));
	assert.equal(byCode.period_open.glyph, "✓");
	assert.equal(byCode.ownership.glyph, "✕");
	assert.equal(byCode.checks.glyph, "?");
});

test("readinessView throws on an unknown item state", () => {
	const p = readinessPayload({ items: [item("period_open", "amber")] });
	assert.throws(() => readinessView(p), /unknown state: amber/);
});

test("readinessView's entities text appends the hidden suffix", () => {
	const p = readinessPayload({
		items: [item("ownership", "blocked", { entities: ["ZZAA", "ZZBB"], hidden: 2 })],
	});
	const view = readinessView(p);
	assert.equal(view.items[0].entities, "ZZAA, ZZBB and 2 outside your scope");
});

test("readinessView's entities text is null when nothing is visible: the detail already counts hidden (R01m)", () => {
	// Real server shape (readiness_model._hidden_detail): a fully-hidden item's
	// detail already says "N entities you cannot see", so the entities text
	// must not repeat the count as a separate "(and N outside your scope)".
	const p = readinessPayload({
		items: [
			item("ownership", "blocked", {
				label: "Every trial balance has ownership",
				detail: "2 entities you cannot see",
				entities: [],
				hidden: 2,
			}),
		],
	});
	const view = readinessView(p);
	assert.equal(view.items[0].entities, null);
	assert.equal(view.items[0].text, "Every trial balance has ownership: 2 entities you cannot see");
});

test("readinessView prints the item's label with its detail (R01m)", () => {
	const p = readinessPayload({
		items: [item("checks", "ok", { label: "Checks", detail: "Green" })],
	});
	const view = readinessView(p);
	assert.equal(view.items[0].text, "Checks: Green");
});

test("readinessView's entities text is null with no entities and nothing hidden", () => {
	const p = readinessPayload({ items: [item("period_open", "ok")] });
	const view = readinessView(p);
	assert.equal(view.items[0].entities, null);
});

test("readinessView's entities text caps at 5 codes, with 'and N more' for the rest", () => {
	const codes = Array.from({ length: 306 }, (_, i) => `ZZ${String(i + 1).padStart(3, "0")}`);
	const p = readinessPayload({
		items: [item("configuration", "blocked", { entities: codes, hidden: 0 })],
	});
	const view = readinessView(p);
	assert.equal(view.items[0].entities, "ZZ001, ZZ002, ZZ003, ZZ004, ZZ005 and 301 more");
});

test("readinessView's entities text shows all 5 codes with no 'more' when there are exactly 5", () => {
	const codes = ["ZZ001", "ZZ002", "ZZ003", "ZZ004", "ZZ005"];
	const p = readinessPayload({
		items: [item("configuration", "blocked", { entities: codes, hidden: 0 })],
	});
	const view = readinessView(p);
	assert.equal(view.items[0].entities, "ZZ001, ZZ002, ZZ003, ZZ004, ZZ005");
	assert.ok(!view.items[0].entities.includes("more"));
});

test("readinessView's entities text combines the 'and N more' cap with the hidden suffix", () => {
	const codes = Array.from({ length: 7 }, (_, i) => `ZZ${String(i + 1).padStart(3, "0")}`);
	const p = readinessPayload({
		items: [item("configuration", "blocked", { entities: codes, hidden: 2 })],
	});
	const view = readinessView(p);
	assert.equal(
		view.items[0].entities,
		"ZZ001, ZZ002, ZZ003, ZZ004, ZZ005 and 2 more and 2 outside your scope"
	);
});

// The terms are built at run time (rather than written literally here) so
// this file itself never contains the substrings route.test.mjs's
// whole-tree scanner looks for.
test("the source contains no local-storage or session-storage calls", () => {
	const path = fileURLToPath(new URL("./periodGrid.js", import.meta.url));
	const source = readFileSync(path, "utf8");
	const forbidden = ["local" + "Storage", "session" + "Storage"];
	for (const term of forbidden) {
		assert.ok(!source.includes(term), `unexpected ${term}`);
	}
});

// --- konsol#305 review-w5: the period is named with its year ---------------

test("gridView: the header and the empty text name FY + period, not the live code 'P07' alone", () => {
	const p = payload({ period: { fiscal_year: 2025, fiscal_period: 7, code: "P07", status: "Open" }, rows: [] });
	const view = gridView(p, false, NOW, TZ);
	assert.equal(view.title, "Period FY2025 P07");
	assert.equal(view.empty, "No entities in scope for FY2025 P07");
	assert.equal(gridView(p, true, NOW, TZ).empty, "No problems in FY2025 P07");
});

// --- konsol#305 Y63: the reminded text in the Trial balance cell -----------
//
// Fed the real producer's output: Y57's golden payload
// konsol/tests/fixtures/close_period_grid_payload.json (asserted equal to the
// stub-site get_period_grid call by its own host test). The expected text is
// remind.js's remindedText over the same entry, never a copied string.

const goldenGrid = JSON.parse(
	readFileSync(fileURLToPath(new URL("../../konsol/tests/fixtures/close_period_grid_payload.json", import.meta.url)), "utf8"),
);
const clone = (o) => JSON.parse(JSON.stringify(o));
const goldenRow = (view, entity) => {
	const r = view.rows.find((x) => x.entity === entity);
	assert.ok(r, `no row for ${entity}`);
	return r;
};

test("Y63: a Missing TB cell carries remindedText(cell.reminders) as tbReminded", () => {
	const src = goldenGrid.rows.find((r) => r.entity === "ZZA");
	assert.equal(src.tb.label, "Missing");
	assert.ok(src.tb.reminders, "the golden Missing cell has a reminders entry");
	const view = gridView(goldenGrid, false, NOW, TZ);
	const zza = goldenRow(view, "ZZA");
	assert.equal(zza.tbReminded, remindedText(src.tb.reminders, NOW, TZ));
	assert.match(zza.tbReminded, /^Reminded 2× · last .+ by Zed Lead$/);
});

test("Y63: a cell with reminders null has no reminded text", () => {
	const view = gridView(goldenGrid, false, NOW, TZ);
	for (const entity of ["ZZB", "ZZC", "ZZX"]) {
		assert.equal(goldenRow(view, entity).tbReminded, null, entity);
	}
});

test("Y63: only a Missing cell shows the text (a Received cell with an entry shows none)", () => {
	const p = clone(goldenGrid);
	const zzb = p.rows.find((r) => r.entity === "ZZB");
	zzb.tb.reminders = clone(p.rows.find((r) => r.entity === "ZZA").tb.reminders);
	assert.equal(goldenRow(gridView(p, false, NOW, TZ), "ZZB").tbReminded, null);
});

test("Y63: the problems filter keeps the reminded text on the rows it keeps", () => {
	const view = gridView(goldenGrid, true, NOW, TZ);
	assert.match(goldenRow(view, "ZZA").tbReminded, /^Reminded 2×/);
});

test("Y63 failure path: a TB cell missing the reminders key throws (Y57 always sends it)", () => {
	const p = clone(goldenGrid);
	delete p.rows.find((r) => r.entity === "ZZB").tb.reminders;
	assert.throws(() => gridView(p, false, NOW, TZ), /ZZB.*reminders/);
});

test("Y63 failure path: an unreadable reminders entry on a Missing cell throws, never shows 0", () => {
	const p = clone(goldenGrid);
	p.rows.find((r) => r.entity === "ZZA").tb.reminders.count = null;
	assert.throws(() => gridView(p, false, NOW, TZ), /unreadable count/);
});

test("Y63 failure path: gridView requires a valid now and a time zone", () => {
	assert.throws(() => gridView(goldenGrid, false, NOW, null), /time zone/);
	assert.throws(() => gridView(goldenGrid, false, new Date("x"), TZ), /now/);
	assert.throws(() => gridView(goldenGrid, false, undefined, TZ), /now/);
});

// --- konsol#305 D61: the deadlines strip and overdue cells -----------------
//
// Fed the real producer's output: D57/D57b's golden payload (tb, ic and
// signoff past; journals undeclared; signoff_overdue true; ZZA's Missing TB
// cell overdue). The SPA never derives overdue: it shows the server's flags.

test("D61: DEADLINE_STEPS is the server's four steps in strip order", () => {
	assert.deepEqual(DEADLINE_STEPS, ["tb", "ic", "journals", "signoff"]);
	assert.deepEqual(Object.keys(goldenGrid.deadlines).sort(), [...DEADLINE_STEPS].sort());
});

test("D61: golden -> the strip text, in order, undeclared step reads 'No due date declared'", () => {
	const strip = deadlineStrip(goldenGrid);
	assert.deepEqual(
		strip.map((i) => i.text),
		["TB due 2025-10-03", "IC due 2025-10-07", "Journals: No due date declared", "Sign-off due 2025-10-01"],
	);
	assert.deepEqual(strip.map((i) => i.step), DEADLINE_STEPS);
});

test("D61: Sign-off is marked Overdue from signoff_overdue; TB is not marked in the strip", () => {
	const strip = deadlineStrip(goldenGrid);
	const by = Object.fromEntries(strip.map((i) => [i.step, i]));
	assert.equal(goldenGrid.signoff_overdue, true);
	assert.equal(by.signoff.overdue, true);
	assert.equal(by.tb.overdue, false);
	const p = clone(goldenGrid);
	p.signoff_overdue = false;
	assert.equal(deadlineStrip(p).find((i) => i.step === "signoff").overdue, false);
});

test("D61: the strip never derives overdue from a past date (one source of truth)", () => {
	const p = clone(goldenGrid);
	assert.equal(p.deadlines.signoff.past, true);
	p.signoff_overdue = false; // e.g. the period is signed
	assert.equal(deadlineStrip(p).find((i) => i.step === "signoff").overdue, false);
});

test("D61: gridView carries the strip as deadlines", () => {
	assert.deepEqual(gridView(goldenGrid, false, NOW, TZ).deadlines, deadlineStrip(goldenGrid));
});

test("D61: an overdue Missing TB cell carries tbOverdue; other cells do not", () => {
	const view = gridView(goldenGrid, false, NOW, TZ);
	assert.equal(goldenGrid.rows.find((r) => r.entity === "ZZA").tb.overdue, true);
	assert.equal(goldenRow(view, "ZZA").tbOverdue, true);
	for (const entity of ["ZZB", "ZZC"]) {
		assert.equal(goldenRow(view, entity).tbOverdue, false, entity);
	}
});

test("D61 failure path: a payload missing the deadlines key throws", () => {
	const p = clone(goldenGrid);
	delete p.deadlines;
	assert.throws(() => deadlineStrip(p), /deadlines/);
	assert.throws(() => gridView(p, false, NOW, TZ), /deadlines/);
});

test("D61 failure path: an unknown step key throws", () => {
	const p = clone(goldenGrid);
	p.deadlines.close = { due: null, past: false, text: "No due date declared" };
	assert.throws(() => deadlineStrip(p), /unknown deadline step: close/);
});

test("D61 failure path: a missing step throws", () => {
	const p = clone(goldenGrid);
	delete p.deadlines.journals;
	assert.throws(() => deadlineStrip(p), /journals/);
});

test("D61 failure path: a step missing due or text throws", () => {
	for (const key of ["due", "text"]) {
		const p = clone(goldenGrid);
		delete p.deadlines.tb[key];
		assert.throws(() => deadlineStrip(p), new RegExp(`tb.*${key}`), key);
	}
});

test("D61 failure path: a payload missing signoff_overdue throws", () => {
	const p = clone(goldenGrid);
	delete p.signoff_overdue;
	assert.throws(() => deadlineStrip(p), /signoff_overdue/);
});

test("D61 failure path: a TB cell missing the overdue key throws", () => {
	const p = clone(goldenGrid);
	delete p.rows.find((r) => r.entity === "ZZB").tb.overdue;
	assert.throws(() => gridView(p, false, NOW, TZ), /ZZB.*overdue/);
});
