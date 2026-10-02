// konsol#305 E207: periodGrid.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { COLUMNS, TONES, toneClass, gridView, readinessView } from "./periodGrid.js";

function ownership(tone, label) {
	return { tone, label: label ?? `ownership ${tone}` };
}
function tb(tone, label) {
	return { tone, label: label ?? `tb ${tone}` };
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
	const view = gridView(payload(), false);
	assert.deepEqual(
		view.rows.map((r) => r.entity),
		["ZZAA", "ZZBB"],
	);
});

test("gridView with problemsOnly true keeps only problem rows, server order preserved", () => {
	const p = payload({
		rows: [row("ZZAA", false), row("ZZBB", true), row("ZZCC", true)],
	});
	const view = gridView(p, true);
	assert.deepEqual(
		view.rows.map((r) => r.entity),
		["ZZBB", "ZZCC"],
	);
});

test("all and problems come from payload.counts, never recomputed from rows", () => {
	const p = payload({ counts: { rows: 9, problems: 4, hidden: 0 } });
	const view = gridView(p, false);
	assert.equal(view.all, 9);
	assert.equal(view.problems, 4);
});

test("hiddenNote is null when counts.hidden is 0", () => {
	const view = gridView(payload({ counts: { rows: 2, problems: 1, hidden: 0 } }), false);
	assert.equal(view.hiddenNote, null);
});

test("hiddenNote names the count when counts.hidden is 3", () => {
	const view = gridView(payload({ counts: { rows: 2, problems: 1, hidden: 3 } }), false);
	assert.equal(view.hiddenNote, "3 entities outside your scope are not shown");
});

test("ratesNote is null when rates_error is not set", () => {
	const view = gridView(payload({ rates_error: null }), false);
	assert.equal(view.ratesNote, null);
});

test("ratesNote names the error when rates_error is set", () => {
	const view = gridView(payload({ rates_error: "No closing rate build for the period" }), false);
	assert.equal(view.ratesNote, "Rates cannot be checked: No closing rate build for the period");
});

test("empty reads 'No problems in <code>' under problemsOnly", () => {
	const view = gridView(payload(), true);
	assert.equal(view.empty, "No problems in FY2026 P07");
});

test("empty reads 'No entities in scope for <code>' when not problemsOnly", () => {
	const view = gridView(payload(), false);
	assert.equal(view.empty, "No entities in scope for FY2026 P07");
});

test("gridView throws on a row with an unknown cell tone", () => {
	const p = payload({ rows: [row("ZZAA", true, ["ok", "warn", "ok"])] });
	assert.throws(() => gridView(p, false), /unknown tone: warn/);
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

test("readinessView's entities text is only the hidden suffix when nothing is visible", () => {
	const p = readinessPayload({
		items: [item("ownership", "blocked", { entities: [], hidden: 2 })],
	});
	const view = readinessView(p);
	assert.equal(view.items[0].entities, "and 2 outside your scope");
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
