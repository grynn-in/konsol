// konsol#305 B10: myWork.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { sections, itemRoute } from "./myWork.js";

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

test("a route is built for each period item, using route.js's format", () => {
	const item = { id: "tb:2026-08:ZZE", kind: "todo", title: "Upload TB for ZZE", period: period(2026, 8, "P08"), owner: "Entity Accountant", action: { screen: "trial-balances", entity: "ZZE" } };
	assert.equal(itemRoute(item), "/close/2026/8/trial-balances");
});

test("a gap item with action.desk gets an external Desk link, the only Desk link", () => {
	const item = { id: "gap:first_close", kind: "blocking", title: "First close period not declared", owner: "EPM Admin", action: { desk: "/app/close-settings" } };
	assert.deepEqual(itemRoute(item), { external: "/app/close-settings" });
});

test("failure path: an item with an unknown kind throws, and is not silently dropped", () => {
	const items = [
		{ id: "ok", kind: "blocking", title: "Rates missing (2)", period: period(2026, 8, "P08"), owner: "EPM Admin", action: { screen: "sign-off" } },
		{ id: "bad", kind: "sometime", title: "?", period: period(2026, 8, "P08"), owner: "EPM Admin", action: { screen: "sign-off" } },
	];
	assert.throws(() => sections(items), /unknown item kind/);
});
