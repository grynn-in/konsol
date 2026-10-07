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
	// F03: same-day now reads "today", the one wording both screens share
	// (konsol#305 F03; was "0 days" here and "today" on Approvals).
	assert.equal(ageText("2026-09-25", today), "today");
});

test("a null since renders nothing", () => {
	assert.equal(ageText(null, new Date(2026, 8, 25)), null);
});

test("failure path: a since that has not happened yet renders nothing, never a negative age", () => {
	assert.equal(ageText("2026-10-01", new Date(2026, 8, 25)), null);
});

// --- F03: the same waiting item ages identically on Approvals and My work --
//
// Live showed the Close Lead's one item 16 days old on My work and 17 on
// Approvals (konsol#305 F03). Both read mywork_model.approvals_item /
// approvals_model.waiting_for_me's own "oldest" moment: Approvals gets it
// as the full zoned timestamp (A08), My work gets it truncated to its
// first 10 characters (mywork_model.py:373, `oldest[:10]`) — already the
// right calendar date in the site's zone (the timestamp itself is zoned
// there before truncation), so feeding each screen its own real shape of
// the same moment must still land on the same age.
function withBootZone(timeZone, fn) {
	const had = Object.prototype.hasOwnProperty.call(globalThis, "window");
	const prev = globalThis.window;
	globalThis.window = { frappe: { boot: { time_zone: { user: timeZone } } } };
	try {
		return fn();
	} finally {
		if (had) globalThis.window = prev;
		else delete globalThis.window;
	}
}

test("F03: Approvals' oldest and My work's since agree on the same item's age", async () => {
	const { queueView } = await import("./approvals.js");
	const timeZone = "Europe/London";
	const now = new Date("2026-09-29T07:00:00Z");
	// 23:50 local on the 13th: the case that drifted under elapsed-hours math.
	const oldest = "2026-09-13T23:50:00+01:00";
	const payload = {
		items: [], sent_back: [], hidden: 0, self_approval: "Blocked", can_approve: true,
		waiting: { count: 2, oldest },
	};
	const approvalsAge = queueView(payload, now, timeZone).header.match(/oldest (.+)$/)[1];
	const myWorkAge = withBootZone(timeZone, () => ageText(oldest.slice(0, 10), now));
	assert.equal(approvalsAge, "16 days");
	assert.equal(myWorkAge, "16 days");
	assert.equal(myWorkAge, approvalsAge);
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

test("U7: a period item (mywork_model._period_item, e.g. Rates missing) gets the kind's color and the period as FY + period (live code is \"P08\" alone)", () => {
	const item = {
		id: "rates:2026-08", kind: "blocking", title: "Rates missing (2)",
		period: { fiscal_year: 2026, fiscal_period: 8, code: "P08", since: "2026-08-31" },
		owner: "EPM Admin", action: { screen: "rates" },
	};
	assert.deepEqual(badgeFor(item), { theme: "red", label: "FY2026 P08" });
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
	assert.deepEqual(badgeFor(item), { theme: "red", label: "FY2026 P08" });
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

// --- Y64 (stories 1.5, 1.2): the reminded line on TB items ------------------
// Fed the real producer's output: Y59's golden My work items (asserted equal
// to the stub-site get_my_work call by its own host test). Two personas: the
// Entity Accountant's "Upload TB for X" carries {count, last_at,
// last_by_name}; the waiting items carry {reminded, of}. D62 owns `due`.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { remindedLine } from "./myWork.js";
import { remindedText } from "./remind.js";

const MYWORK_GOLDEN = JSON.parse(
	readFileSync(fileURLToPath(new URL("../../konsol/tests/fixtures/close_mywork_items.json", import.meta.url)), "utf8"),
);
const Y64_NOW = new Date("2025-10-07T12:00:00Z");
const Y64_TZ = "Europe/London";
const goldenItem = (persona, id) => {
	const item = MYWORK_GOLDEN[persona].find((i) => i.id === id);
	assert.ok(item, `no golden ${persona} item ${id}`);
	return item;
};

test("(Y64) golden Entity Accountant item: 'Reminded 2× · last … by Jane Doe', remind.js's one rule", () => {
	const item = goldenItem("entity_accountant", "tb:2025-07:ZZA");
	const line = remindedLine(item, Y64_NOW, Y64_TZ);
	assert.equal(line, remindedText(item.reminded, Y64_NOW, Y64_TZ));
	assert.equal(line, "Reminded 2× · last 6 Aug, 14:05 by Jane Doe");
	assert.equal(
		remindedLine(goldenItem("entity_accountant", "tb:2025-09:ZZA"), Y64_NOW, Y64_TZ),
		"Reminded 1× · last 10 Sep, 08:00 by Raj Patel",
	);
});

test("(Y64) golden waiting items: 'R of N reminded'", () => {
	assert.equal(remindedLine(goldenItem("group_accountant", "tbs-waiting:2025-07"), Y64_NOW, Y64_TZ), "2 of 2 reminded");
	assert.equal(remindedLine(goldenItem("group_accountant", "tbs-waiting:2025-09"), Y64_NOW, Y64_TZ), "1 of 1 reminded");
});

test("(Y64) golden: every item that is not a TB item has no line, and every TB item has one", () => {
	for (const persona of Object.keys(MYWORK_GOLDEN)) {
		for (const item of MYWORK_GOLDEN[persona]) {
			const line = remindedLine(item, Y64_NOW, Y64_TZ);
			if ("reminded" in item) assert.ok(line, `${persona} ${item.id} has a line`);
			else assert.equal(line, null, `${persona} ${item.id} has no line`);
		}
	}
});

test("(Y64) failure path: reminded null gives no line, never '0×' or '0 of N'", () => {
	for (const persona of Object.keys(MYWORK_GOLDEN)) {
		for (const item of MYWORK_GOLDEN[persona]) {
			if (!("reminded" in item)) continue;
			assert.equal(remindedLine({ ...item, reminded: null }, Y64_NOW, Y64_TZ), null, `${item.id} with reminded null`);
		}
	}
});

test("(Y64) failure path: an unreadable reminded value throws, never a guessed line", () => {
	const waiting = goldenItem("group_accountant", "tbs-waiting:2025-07");
	const bad = [
		{ reminded: 0, of: 5 },
		{ reminded: 3, of: 2 },
		{ reminded: 1 },
		{ of: 2 },
		{ reminded: "1", of: 2 },
		{},
		"2 of 2",
	];
	for (const reminded of bad) {
		assert.throws(() => remindedLine({ ...waiting, reminded }, Y64_NOW, Y64_TZ), Error, JSON.stringify(reminded));
	}
	const ea = goldenItem("entity_accountant", "tb:2025-07:ZZA");
	const { last_by_name, ...noName } = ea.reminded;
	assert.throws(() => remindedLine({ ...ea, reminded: noName }, Y64_NOW, Y64_TZ), /last_by_name/);
	assert.throws(() => remindedLine({ ...ea, reminded: { ...ea.reminded, count: 0 } }, Y64_NOW, Y64_TZ), /count/);
});

// --- R52t (U13, stories 1.5, 1.2): no time zone says so on the item ----------
// MyWork.vue runs remindedLine for every item inside `grouped`; a throw there
// replaces the whole screen with "Invalid time zone specified: null". With no
// zone, the counted entry gives the item a sentence and never calls Intl; the
// {reminded, of} form needs no zone and is unchanged.

const R52T_NO_ZONE = "Your browser reported no time zone, so the reminder time cannot be shown.";

function withoutIntl(fn) {
	const saved = globalThis.Intl;
	globalThis.Intl = new Proxy(
		{},
		{
			get(_target, key) {
				throw new Error(`Intl.${String(key)} was called`);
			},
		},
	);
	try {
		return fn();
	} finally {
		globalThis.Intl = saved;
	}
}

test("(R52t) failure path: a golden counted reminder with timeZone null gives the no-zone sentence, never a throw", () => {
	const item = goldenItem("entity_accountant", "tb:2025-07:ZZA");
	assert.equal(remindedLine(item, Y64_NOW, null), "Your browser reported no time zone, so the reminder time cannot be shown.");
	assert.equal(
		remindedLine(goldenItem("entity_accountant", "tb:2025-09:ZZA"), Y64_NOW, null),
		"Your browser reported no time zone, so the reminder time cannot be shown.",
	);
});

test("(R52t) every falsy time zone gives the sentence and calls no Intl", () => {
	const item = goldenItem("entity_accountant", "tb:2025-07:ZZA");
	for (const zone of [null, undefined, ""]) {
		assert.equal(withoutIntl(() => remindedLine(item, Y64_NOW, zone)), R52T_NO_ZONE, `zone ${JSON.stringify(zone)}`);
	}
});

test("(R52t) with no zone, every golden item still gets its line: waiting items unchanged, null stays no line", () => {
	assert.equal(remindedLine(goldenItem("group_accountant", "tbs-waiting:2025-07"), Y64_NOW, null), "2 of 2 reminded");
	assert.equal(remindedLine(goldenItem("group_accountant", "tbs-waiting:2025-09"), Y64_NOW, null), "1 of 1 reminded");
	for (const persona of Object.keys(MYWORK_GOLDEN)) {
		for (const item of MYWORK_GOLDEN[persona]) {
			const line = withoutIntl(() => remindedLine(item, Y64_NOW, null));
			if (!("reminded" in item)) assert.equal(line, null, `${persona} ${item.id} has no line`);
			else if ("count" in item.reminded) assert.equal(line, R52T_NO_ZONE, `${persona} ${item.id}`);
			else assert.match(line, /^\d+ of \d+ reminded$/, `${persona} ${item.id}`);
			if ("reminded" in item) assert.equal(remindedLine({ ...item, reminded: null }, Y64_NOW, null), null, `${item.id} null`);
		}
	}
});

test("(R52t) no zone does not hide an unreadable counted entry: it still throws", () => {
	const ea = goldenItem("entity_accountant", "tb:2025-07:ZZA");
	assert.throws(() => remindedLine({ ...ea, reminded: { ...ea.reminded, count: 0 } }, Y64_NOW, null), /count/);
	const { last_by_name, ...noName } = ea.reminded;
	assert.throws(() => remindedLine({ ...ea, reminded: noName }, Y64_NOW, null), /last_by_name/);
});

// --- D62 (stories 1.1, 2.4): the due line and the overdue badge -------------
// Fed the same golden My work items (D59 regenerated them from the real
// stub-site get_my_work call). D58's `due` is {date, text, overdue} on a
// period item with a step, null on a period item without one, and ABSENT on
// the items no `_period_item` builds: setup gaps, the approvals item and
// sent-back items (mywork_model.sent_back_items, coordinator note 7 Oct).
// Engineering call (D62): the SPA reads an absent `due` like null, no line;
// the server is not changed to add due:null. The date wording is D60's one
// formatter (dueDate.js), never a second one.

import { dueLine, MUTE_TONE } from "./myWork.js";
import { OVERDUE_TONE, tbDue } from "./tbTable.js";

test("(D62) golden: an overdue item reads 'Overdue since Thu 7 Aug 2025' in the amber warn tone", () => {
	for (const [persona, id] of [["entity_accountant", "tb:2025-07:ZZA"], ["group_accountant", "tbs-waiting:2025-07"]]) {
		const line = dueLine(goldenItem(persona, id));
		assert.deepEqual(line, { text: "Overdue since Thu 7 Aug 2025", tone: OVERDUE_TONE, overdue: true }, `${persona} ${id}`);
	}
});

test("(D62) golden: a not-yet-due item reads 'Due Tue 7 Oct 2025', not the warn tone", () => {
	const line = dueLine(goldenItem("entity_accountant", "tb:2025-09:ZZA"));
	assert.equal(line.text, "Due Tue 7 Oct 2025");
	assert.equal(line.overdue, false);
	assert.notEqual(line.tone, OVERDUE_TONE);
	assert.doesNotMatch(line.tone, /amber|red/);
});

test("(D62) golden: an undeclared step shows the server's 'No due date declared' in the mute tone", () => {
	const line = dueLine(goldenItem("group_accountant", "tbs-waiting:2025-09"));
	assert.deepEqual(line, { text: "No due date declared", tone: MUTE_TONE, overdue: false });
	assert.doesNotMatch(MUTE_TONE, /amber|red/);
});

test("(D62) the date wording is D60's one formatter (tbTable's tbDue), not a second one", () => {
	for (const persona of Object.keys(MYWORK_GOLDEN)) {
		for (const item of MYWORK_GOLDEN[persona]) {
			if (!item.due || item.due.date === null) continue;
			const d60 = tbDue({ deadline: { due: item.due.date, past: item.due.overdue, text: item.due.text } }).text;
			const date = d60.replace(/^TB due /, "");
			assert.ok(dueLine(item).text.endsWith(` ${date}`), `${item.id}: ${dueLine(item).text} vs ${d60}`);
		}
	}
});

test("(D62) failure path: the overdue badge is warn tone, never the block (red) tone", () => {
	assert.match(OVERDUE_TONE, /amber/);
	assert.doesNotMatch(OVERDUE_TONE, /red/);
});

test("(D62) failure path: due null renders nothing", () => {
	for (const persona of Object.keys(MYWORK_GOLDEN)) {
		for (const item of MYWORK_GOLDEN[persona]) {
			assert.equal(dueLine({ ...item, due: null }), null, `${persona} ${item.id} with due null`);
		}
	}
	// the golden's own null-due period items (checks-failing, rates, checks-run)
	const nulls = MYWORK_GOLDEN.group_accountant.filter((i) => "due" in i && i.due === null);
	assert.ok(nulls.length >= 3);
	for (const item of nulls) assert.equal(dueLine(item), null, item.id);
});

test("(D62) an item with no due key (setup gap, approvals, sent back) renders nothing, never throws", () => {
	const gaps = MYWORK_GOLDEN.group_accountant.filter((i) => !("due" in i));
	assert.ok(gaps.length >= 3, "the golden has period-less items without a due key");
	for (const item of gaps) assert.equal(dueLine(item), null, item.id);
	// mywork_model.sent_back_items (mywork_model.py:666-686) builds a
	// period-keyed sent-back item with `period` and no `due`.
	const tb = goldenItem("entity_accountant", "tb:2025-07:ZZA");
	const { due, reminded, ...rest } = tb;
	const sentBack = { ...rest, id: "sent-back:Trial Balance Upload:TBU-1", kind: "todo", title: "Sent back: TB · ZZA" };
	assert.equal(dueLine(sentBack), null);
});

test("(D62) failure path: an unreadable due throws, never a guessed date", () => {
	const item = goldenItem("entity_accountant", "tb:2025-07:ZZA");
	const bad = [
		"Due 7 Aug",
		{ date: "7 Aug", text: "Due 7 Aug", overdue: true },
		{ date: "2025-02-30", text: "Due 2025-02-30", overdue: false },
		{ date: "2025-08-07", text: "Due 2025-08-07" },
		{ date: "2025-08-07", text: "Due 2025-08-07", overdue: "yes" },
		{ date: null, text: "No due date declared", overdue: true },
		{ date: null, overdue: false },
		{ text: "Due 2025-08-07", overdue: true },
	];
	for (const due of bad) {
		assert.throws(() => dueLine({ ...item, due }), /due/, JSON.stringify(due));
	}
});

test("(D62) failure path: sections keeps the server's order, no client re-sort", () => {
	const src = readFileSync(fileURLToPath(new URL("./myWork.js", import.meta.url)), "utf8");
	const start = src.indexOf("export function sections(");
	const end = src.indexOf("\n}\n", start);
	assert.ok(start >= 0 && end > start);
	assert.doesNotMatch(src.slice(start, end), /\.sort\(|\.reverse\(/);
	for (const persona of Object.keys(MYWORK_GOLDEN)) {
		const items = MYWORK_GOLDEN[persona];
		for (const section of sections(items)) {
			assert.deepEqual(
				section.items.map((i) => i.id),
				items.filter((i) => i.kind === section.kind).map((i) => i.id),
				`${persona} ${section.kind}`,
			);
		}
	}
});
