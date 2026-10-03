// konsol#305 B10: myWork.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { sections, itemRoute, ageText, badgeFor } from "./myWork.js";

function period(fiscal_year, fiscal_period, code) {
	return { fiscal_year, fiscal_period, code };
}

test("blocking items are kept apart from todo items, and the order within each is preserved", () => {
	const items = [
		{ id: "b1", kind: "blocking", title: "Rates missing (2)", period: period(2026, 8, "P08"), owner: "EPM Admin", action: { screen: "sign-off" } },
		{ id: "t1", kind: "todo", title: "Run checks", period: period(2026, 8, "P08"), owner: "EPM Analyst", action: { screen: "checks" } },
		{ id: "b2", kind: "blocking", title: "Re-sign needed", period: period(2026, 8, "P08"), owner: "EPM Admin", action: { screen: "sign-off" } },
		{ id: "t2", kind: "todo", title: "Upload TB for ZZE", period: period(2026, 8, "P08"), owner: "Entity Accountant", action: { screen: "trial-balances", entity: "ZZE" } },
	];
	const grouped = sections(items);
	const blocking = grouped.find((s) => s.kind === "blocking");
	const todo = grouped.find((s) => s.kind === "todo");
	assert.deepEqual(blocking.items.map((i) => i.id), ["b1", "b2"]);
	assert.deepEqual(todo.items.map((i) => i.id), ["t1", "t2"]);
});

test("the server's ranking is kept: items are never re-sorted within a kind", () => {
	// Server already ranked these older-period-first; a later period appears
	// before an earlier one here on purpose, to prove the client does not
	// re-sort by period.
	const items = [
		{ id: "later", kind: "waiting", title: "Waiting on 1 trial balance", period: period(2026, 9, "P09"), owner: "EPM Analyst", action: { screen: "trial-balances" } },
		{ id: "earlier", kind: "waiting", title: "Waiting on 2 trial balances", period: period(2026, 8, "P08"), owner: "EPM Analyst", action: { screen: "trial-balances" } },
	];
	const grouped = sections(items);
	const waiting = grouped.find((s) => s.kind === "waiting");
	assert.deepEqual(waiting.items.map((i) => i.id), ["later", "earlier"]);
});

test("every kind section is always present, in blocking, todo, waiting order", () => {
	const grouped = sections([]);
	assert.deepEqual(grouped.map((s) => s.kind), ["blocking", "todo", "waiting"]);
});

test("an empty group says so explicitly, and is never omitted", () => {
	const items = [
		{ id: "b1", kind: "blocking", title: "Rates missing (2)", period: period(2026, 8, "P08"), owner: "EPM Admin", action: { screen: "sign-off" } },
	];
	const grouped = sections(items);
	assert.equal(grouped.length, 3, "waiting and todo must not disappear just because they are empty");

	const todo = grouped.find((s) => s.kind === "todo");
	assert.deepEqual(todo.items, []);
	assert.equal(todo.empty, true);
	assert.equal(todo.title, "Nothing to do");

	const waiting = grouped.find((s) => s.kind === "waiting");
	assert.deepEqual(waiting.items, []);
	assert.equal(waiting.empty, true);
	assert.equal(waiting.title, "Nothing waiting on others");

	const blocking = grouped.find((s) => s.kind === "blocking");
	assert.equal(blocking.empty, false);
	assert.equal(blocking.title, "Blocking");
});

test("a route is built for each period item, using route.js's format, and (B18b) carries action.entity as ?entity=", () => {
	const item = { id: "tb:2026-08:ZZE", kind: "todo", title: "Upload TB for ZZE", period: period(2026, 8, "P08"), owner: "Entity Accountant", action: { screen: "trial-balances", entity: "ZZE" } };
	assert.equal(itemRoute(item), "/close/2026/8/trial-balances?entity=ZZE");
});

test("an action with no entity carries no query string (failure path: never invent one)", () => {
	const item = { id: "checks", kind: "todo", title: "Run checks", period: period(2026, 8, "P08"), owner: "EPM Analyst", action: { screen: "checks" } };
	assert.equal(itemRoute(item), "/close/2026/8/checks");
});

test("a gap item with action.desk gets an external Desk link, the only Desk link", () => {
	const item = { id: "gap:first_close", kind: "blocking", title: "First close period not declared", owner: "EPM Admin", action: { desk: "/app/close-settings" } };
	assert.deepEqual(itemRoute(item), { external: "/app/close-settings" });
});

// --- A20: a period-less screen item routes to the URL's current period ----

test("A20: a period-less screen item routes to current (the URL's period)", () => {
	const item = { id: "gap:approvals", kind: "todo", title: "Approve the pending journal", owner: "EPM Admin", action: { screen: "approvals" } };
	assert.equal(itemRoute(item, { year: 2025, period: 9 }), "/close/2025/9/approvals");
});

test("A20 failure path: a period-less item with no current returns null, and does not throw", () => {
	const item = { id: "gap:approvals", kind: "todo", title: "Approve the pending journal", owner: "EPM Admin", action: { screen: "approvals" } };
	assert.equal(itemRoute(item, null), null);
	assert.equal(itemRoute(item), null);
});

test("A20 failure path: a period item still routes to its own period, never current's", () => {
	const item = { id: "checks", kind: "todo", title: "Run checks", period: period(2026, 8, "P08"), owner: "EPM Analyst", action: { screen: "checks" } };
	assert.equal(itemRoute(item, { year: 2025, period: 9 }), "/close/2026/8/checks");
});

test("A20: a Desk item still gives {external}, with a current supplied", () => {
	const item = { id: "gap:first_close", kind: "blocking", title: "First close period not declared", owner: "EPM Admin", action: { desk: "/app/close-settings" } };
	assert.deepEqual(itemRoute(item, { year: 2025, period: 9 }), { external: "/app/close-settings" });
});

test("failure path: an item with an unknown kind throws, and is not silently dropped", () => {
	const items = [
		{ id: "ok", kind: "blocking", title: "Rates missing (2)", period: period(2026, 8, "P08"), owner: "EPM Admin", action: { screen: "sign-off" } },
		{ id: "bad", kind: "sometime", title: "?", period: period(2026, 8, "P08"), owner: "EPM Admin", action: { screen: "sign-off" } },
	];
	assert.throws(() => sections(items), /unknown item kind/);
});

// --- B18b: ageText ------------------------------------------------------
//
// A53's `since` is the period's end date (an ISO date, no time). `ageText`
// turns `since` plus an injected `today` into the age text B18 shows next
// to a period item. `today` is always a parameter here: this module never
// reads the clock itself (the coordinator's rule, mirrored from the pure
// Python models: age is computed from `since` and a `today` injected as a
// parameter, never read inside a pure module).

test("ageText renders an age from since and an injected today", () => {
	const today = new Date(2026, 8, 25); // 25 Sep 2026
	assert.equal(ageText("2026-09-13", today), "12 days");
	assert.equal(ageText("2026-09-24", today), "1 day");
	assert.equal(ageText("2026-09-25", today), "0 days");
});

test("a null since renders nothing", () => {
	assert.equal(ageText(null, new Date(2026, 8, 25)), null);
});

test("failure path: a since that has not happened yet renders nothing, never a negative age", () => {
	assert.equal(ageText("2026-10-01", new Date(2026, 8, 25)), null);
});

// --- U7 (review-w3.md): badgeFor ---------------------------------------
//
// MyWork.vue:248 badged every period-less item "Setup" (orange), including
// the Close Lead's approvals queue and a sent-back draft — both real,
// routine work, not a configuration gap. Only mywork_model.setup_gap_items'
// output (since_reason "configuration gap") is a gap. The other item
// builders that return a period-less item — mywork_model.approvals_item
// (since_reason "oldest waiting") and mywork_model.sent_back_items for a
// doctype outside _SENT_BACK_PERIOD_KEYED (since_reason "sent back") — get
// the kind's own badge instead. A period item (it carries `period`) always
// gets its period's code, themed by kind, regardless of since_reason.
//
// Fixtures below mirror the real shapes konsol/close/mywork_model.py
// returns (read 3 Oct 2026), not invented ones.

test("U7: a period item (mywork_model._period_item, e.g. Rates missing) gets the kind's color and the period code", () => {
	const item = {
		id: "rates:2026-08", kind: "blocking", title: "Rates missing (2)",
		period: { fiscal_year: 2026, fiscal_period: 8, code: "P08", since: "2026-08-31" },
		owner: "EPM Admin", action: { screen: "rates" },
	};
	assert.deepEqual(badgeFor(item), { theme: "red", label: "P08" });
});

test("U7: a configuration-gap item (mywork_model.setup_gap_items) gets Setup", () => {
	const item = {
		id: "gap:first_close", kind: "blocking", title: "First close period not declared",
		detail: "Set the first close period in Close Settings.", owner: "EPM Admin",
		entities: [], users: [], action: { desk: "/app/close-settings" },
		since: null, since_reason: "configuration gap",
	};
	assert.deepEqual(badgeFor(item), { theme: "orange", label: "Setup" });
});

test("U7: the Close Lead's approvals queue item (mywork_model.approvals_item) is period-less but is not a gap", () => {
	const item = {
		id: "approvals", kind: "todo", title: "Approve 3 items", owner: "EPM Admin",
		action: { screen: "approvals" }, since: "2026-09-20", since_reason: "oldest waiting",
	};
	assert.deepEqual(badgeFor(item), { theme: "blue", label: "To do" });
});

test("U7: a sent-back Historical Equity Rate (mywork_model.sent_back_items, no period for this doctype) is not a gap", () => {
	const item = {
		id: "sent-back:Historical Equity Rate:ZZHER-001", kind: "todo",
		title: "Sent back: Historical rate · ZZE FY2025",
		detail: "jane on 2026-09-18: wrong basis", owner: "EPM Admin",
		action: { screen: "rates" }, since: "2026-09-18", since_reason: "sent back",
	};
	assert.deepEqual(badgeFor(item), { theme: "blue", label: "To do" });
});

test("U7: a sent-back Business Disposal (mywork_model.sent_back_items, Desk action) is not a gap", () => {
	const item = {
		id: "sent-back:Business Disposal:ZZBD-001", kind: "todo",
		title: "Sent back: Disposal · ZZE disposes ZZSub",
		detail: "jane on 2026-09-18: wrong date", owner: "EPM Admin",
		action: { desk: "/app/business-disposal/ZZBD-001" }, since: "2026-09-18",
		since_reason: "sent back",
	};
	assert.deepEqual(badgeFor(item), { theme: "blue", label: "To do" });
});

test("U7: an IC fix item (mywork_model.ic_fix_items) always carries a period, so it gets the period code, not Setup", () => {
	const item = {
		id: "ic:2026-08:ZZE:ZZE|1000|ZZF|2000", kind: "blocking",
		title: "Intercompany difference with ZZF (1000 ↔ 2000)",
		detail: "Sent back by jane on 2026-09-10: please fix. Difference 150.00 in Group A (tolerance 50.00). Your side 1200.00.",
		period: { fiscal_year: 2026, fiscal_period: 8, code: "P08", since: "2026-08-31" },
		owner: "Entity Accountant", action: { screen: "trial-balances", entity: "ZZE" },
	};
	assert.deepEqual(badgeFor(item), { theme: "red", label: "P08" });
});

test("U7: a waiting-kind period-less item gets the gray waiting badge, not Setup", () => {
	const item = {
		id: "approvals", kind: "waiting", title: "Waiting on something period-less",
		owner: "EPM Admin", action: { screen: "approvals" },
	};
	assert.deepEqual(badgeFor(item), { theme: "gray", label: "Waiting" });
});

test("failure path: an unknown kind throws, and is never defaulted to Setup", () => {
	const item = { id: "x", kind: "mystery", owner: "EPM Admin", action: {} };
	assert.throws(() => badgeFor(item), /unknown item kind/);
});
