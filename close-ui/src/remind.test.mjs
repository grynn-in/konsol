// konsol#305 Y61: remind.js — the Remind POST body and the "Reminded" text.
//
// Fed the real producer's output: the rows of Y56's golden payload
// konsol/tests/fixtures/close_my_tbs_payload.json (asserted equal to the
// stub-site get_my_tbs call by its own host test). The topic labels and the
// endpoint's parameter names are read from the Python source, never copied.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { REMIND, TOPICS, TOPIC_LABEL, remindBody, remindedText } from "./remind.js";

function repoFile(rel) {
	return readFileSync(fileURLToPath(new URL("../../" + rel, import.meta.url)), "utf8");
}

const golden = JSON.parse(repoFile("konsol/tests/fixtures/close_my_tbs_payload.json"));
const rowOf = (entity) => {
	const row = golden.entities.find((r) => r.entity === entity);
	assert.ok(row, `golden payload has no row for ${entity}`);
	return row;
};
const PERIOD = { fiscal_year: 2025, fiscal_period: 7 };

// --- remindBody ---------------------------------------------------------------

test("REMIND names the Y54 endpoint", () => {
	assert.equal(REMIND, "konsol.close.remind_api.remind");
});

test("remindBody returns exactly {fiscal_year, fiscal_period, entity, topic}", () => {
	const row = rowOf("ZZC");
	for (const topic of ["tb", "ic"]) {
		const body = remindBody(PERIOD, row.entity, topic);
		assert.deepEqual(body, { fiscal_year: 2025, fiscal_period: 7, entity: "ZZC", topic });
		assert.deepEqual(Object.keys(body).sort(), ["entity", "fiscal_period", "fiscal_year", "topic"]);
	}
});

test("remindBody carries no extra key even when the period object has more", () => {
	const body = remindBody({ ...PERIOD, code: "FY2025 P07", recipients: ["x@y"] }, "ZZB", "tb");
	assert.deepEqual(Object.keys(body).sort(), ["entity", "fiscal_period", "fiscal_year", "topic"]);
});

test("remindBody's keys are exactly remind_api.remind's parameters (forge test)", () => {
	const source = repoFile("konsol/close/remind_api.py");
	const match = source.match(/\ndef remind\(([^)]*)\):/);
	assert.ok(match, "remind_api.py's def remind(...) was not found");
	const params = match[1].split(",").map((p) => p.trim()).filter(Boolean);
	assert.ok(!params.some((p) => p.startsWith("*")), "remind takes no *args/**kwargs");
	assert.deepEqual(Object.keys(remindBody(PERIOD, "ZZB", "tb")), params);
});

test("remindBody throws on an unknown topic", () => {
	for (const topic of ["x", "", null, undefined, "TB"]) {
		assert.throws(() => remindBody(PERIOD, "ZZB", topic), /topic/i);
	}
});

test("remindBody throws on a missing period or entity", () => {
	assert.throws(() => remindBody(null, "ZZB", "tb"), /period/i);
	assert.throws(() => remindBody({ fiscal_year: 2025 }, "ZZB", "tb"), /period/i);
	assert.throws(() => remindBody({ fiscal_period: 7 }, "ZZB", "tb"), /period/i);
	assert.throws(() => remindBody(PERIOD, "", "tb"), /entity/i);
	assert.throws(() => remindBody(PERIOD, null, "tb"), /entity/i);
});

// --- topic parity with remind_model.py -----------------------------------------

function pythonTopicLabels() {
	const source = repoFile("konsol/close/remind_model.py");
	const tuple = source.match(/\nTOPICS = \(([^)]*)\)/);
	assert.ok(tuple, "remind_model.py's TOPICS tuple was not found");
	const labels = source.match(/\nTOPIC_LABEL = \{([^}]*)\}/);
	assert.ok(labels, "remind_model.py's TOPIC_LABEL dict was not found");
	return {
		topics: [...tuple[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]),
		labels: Object.fromEntries([...labels[1].matchAll(/"([a-z_]+)"\s*:\s*"([^"]+)"/g)].map((m) => [m[1], m[2]])),
	};
}

test("TOPICS and TOPIC_LABEL match remind_model.py exactly", () => {
	const py = pythonTopicLabels();
	assert.deepEqual([...TOPICS], py.topics);
	assert.deepEqual({ ...TOPIC_LABEL }, py.labels);
});

// --- remindedText ---------------------------------------------------------------

const LONDON = "Europe/London";

test("remindedText on the golden ZZC row: count, zoned time and full name", () => {
	const entry = rowOf("ZZC").reminders;
	// last_at 2025-10-06T10:00:00+01:00 is 10:00 in London; "now" is the next day.
	assert.equal(
		remindedText(entry, new Date("2025-10-07T09:00:00Z"), LONDON),
		"Reminded 2× · last Oct 6, 10:00 by Zed Lead"
	);
});

test("remindedText zones the time with the same rule as the rest of the app", () => {
	const entry = rowOf("ZZC").reminders;
	assert.equal(
		remindedText(entry, new Date("2025-10-07T09:00:00Z"), "Asia/Kolkata"),
		"Reminded 2× · last Oct 6, 14:30 by Zed Lead"
	);
	// Same calendar day: the time alone, as formatTime shows today.
	assert.equal(
		remindedText(entry, new Date("2025-10-06T15:00:00Z"), LONDON),
		"Reminded 2× · last 10:00 by Zed Lead"
	);
});

test("remindedText returns null for a row with no reminders (golden ZZB, ZZA)", () => {
	for (const entity of ["ZZB", "ZZA"]) {
		assert.equal(rowOf(entity).reminders, null);
		assert.equal(remindedText(rowOf(entity).reminders, new Date("2025-10-07T09:00:00Z"), LONDON), null);
	}
});

test("remindedText throws on a missing entry (undefined is a missing key, not 'none')", () => {
	assert.throws(() => remindedText(undefined, new Date(), LONDON), /reminders/i);
});

test("remindedText throws when the entry lacks a key", () => {
	const entry = rowOf("ZZC").reminders;
	for (const key of ["count", "last_at", "last_by_name"]) {
		const broken = { ...entry };
		delete broken[key];
		assert.throws(() => remindedText(broken, new Date(), LONDON), new RegExp(key));
	}
});

test("remindedText throws on a count that cannot be read", () => {
	const entry = rowOf("ZZC").reminders;
	for (const count of [0, -1, 1.5, "2", null]) {
		assert.throws(() => remindedText({ ...entry, count }, new Date(), LONDON), /count/i);
	}
});

test("remindedText throws on a zone-less last_at (never guessed)", () => {
	const entry = rowOf("ZZC").reminders;
	assert.throws(
		() => remindedText({ ...entry, last_at: "2025-10-06 10:00:00" }, new Date(), LONDON),
		/time zone/i
	);
});

// --- R52s (U8): a Remind lands on its own period --------------------------------
// Periods are route.js's real parse() output: the screen captures one when the
// Remind is pressed and compares it with the route's period after the POST.

import * as remindModule from "./remind.js";
import { parse } from "./route.js";

const P07 = parse("/close/2025/7/trial-balances");
const P08 = parse("/close/2025/8/trial-balances");

function afterPost(sent, current) {
	assert.equal(typeof remindModule.afterPost, "function", "remind.js exports afterPost");
	return remindModule.afterPost(sent, current);
}

test("(R52s) afterPost: the route is still on the period the Remind was sent for → reload", () => {
	assert.equal(afterPost(P07, parse("/close/2025/7/trial-balances")), "reload");
});

test("(R52s) failure path: sent for P07, the route is now P08 → skip the reload and drop the error", () => {
	assert.equal(afterPost(P07, P08), "skip");
});

test("(R52s) afterPost: another year with the same period number is another period → skip", () => {
	assert.equal(afterPost(P07, parse("/close/2026/7/trial-balances")), "skip");
});

test("(R52s) afterPost: the same period on another screen is still that period → reload", () => {
	assert.equal(afterPost(P07, parse("/close/2025/7/period")), "reload");
});

test("(R52s) afterPost: a route with no period (bad address, bare /close) → skip", () => {
	for (const path of ["/close/x/7/trial-balances", "/close", "/elsewhere"]) {
		assert.equal(afterPost(P07, parse(path)), "skip", path);
	}
});

test("(R52s) afterPost throws when the sent period is missing (never guessed)", () => {
	for (const sent of [null, undefined, {}, parse("/close"), parse("/close/x/7/trial-balances")]) {
		assert.throws(() => afterPost(sent, P07), /period/i);
	}
});
