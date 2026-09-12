import { test } from "node:test";
import assert from "node:assert/strict";
import {
	periodCode, periodLabel, parsePeriodRoute, monthPath, currentMonthPath, isMine,
	stageTarget, crumbsFor, defaultExpanded, openCount, firstOpenItem, shortTime, actionHint,
} from "./home.js";

test("period codes and labels match the server's vocabulary", () => {
	assert.deepEqual([0, 1, 9, 12, 13].map(periodCode), ["OPN", "P01", "P09", "P12", "CLS"]);
	assert.equal(periodLabel(2026, 9), "Sep 2026");
	assert.equal(periodLabel(2026, 0), "Opening balances");
	assert.equal(periodLabel(2026, 13), "Year-end close");
});

test("route params parse to a real period or nothing", () => {
	assert.deepEqual(parsePeriodRoute({ year: "2026", period: "9" }), { year: 2026, period: 9 });
	assert.deepEqual(parsePeriodRoute({ year: "2026", period: "0" }), { year: 2026, period: 0 });
	assert.equal(parsePeriodRoute({ year: "2026", period: "14" }), null);
	assert.equal(parsePeriodRoute({ year: "abc", period: "1" }), null);
	assert.equal(parsePeriodRoute({}), null);
});

test("paths", () => {
	assert.equal(monthPath(2026, 9), "/2026/9");
	assert.equal(currentMonthPath(new Date(2026, 8, 12)), "/2026/9");
});

test("a stage is yours when you hold one of its roles", () => {
	const stage = { owners: ["EPM Analyst", "EPM Admin"] };
	assert.equal(isMine(stage, ["EPM Admin"]), true);
	assert.equal(isMine(stage, ["EPM User"]), false);
	assert.equal(isMine(stage, undefined), false);
});

test("lane stages open a step or the filtered desk list", () => {
	assert.deepEqual(stageTarget({ id: "signoff" }, 2026, 9), { step: "signoff" });
	assert.deepEqual(stageTarget({ id: "consolidate" }, 2026, 9), { step: "consolidation" });
	assert.equal(stageTarget({ id: "trial_balances" }, 2026, 9).href,
		"/app/trial-balance-submission?fiscal_year=2026&fiscal_period=9");
	assert.equal(stageTarget({ id: "unknown" }, 2026, 9).href, "/app");
});

test("the path always says year, month, then view", () => {
	assert.deepEqual(crumbsFor({ name: "month", year: 2026, period: 9 }).map((c) => c.label),
		["FY2026", "P09 · Sep 2026", "Close"]);
	const step = crumbsFor({ name: "step", year: 2026, period: 9, stepLabel: "Sign off period" });
	assert.equal(step[1].to, "/2026/9");
	assert.equal(step[0].to, undefined);
	assert.equal(step[2].label, "Sign off period");
	assert.deepEqual(crumbsFor({ name: "month" }), [{ label: "Konsol" }]);
	assert.deepEqual(crumbsFor({ name: "uploads" }).map((c) => c.label), ["Group", "Upload trial balances"]);
});

test("navigator opens the current and the selected year", () => {
	const s = defaultExpanded({ current: { fiscal_year: 2026 } }, { year: 2024, period: 3 });
	assert.deepEqual([...s].sort(), [2024, 2026]);
	assert.equal(defaultExpanded(null, null).size, 0);
});

test("counts and the default selection skip done rows", () => {
	const month = { mine: [{ id: "a", state: "done" }, { id: "b", state: "incomplete" }], waiting: [{ id: "c", state: "waiting" }] };
	assert.equal(openCount(month.mine), 1);
	assert.equal(firstOpenItem(month).id, "b");
	assert.equal(firstOpenItem({ mine: [{ id: "a", state: "done" }], waiting: [] }).id, "a");
	assert.equal(firstOpenItem(null), null);
});

test("an action's hint, shown beside the row button: the reason when disabled, the note when allowed", () => {
	const closed = "Dec 2099 is closed.";
	assert.equal(actionHint({ allowed: false, reason: closed, note: null }), closed);
	assert.equal(actionHint({ allowed: false, reason: "You don't have permission for this.", note: null }),
		"You don't have permission for this.");
	assert.equal(actionHint({ allowed: true, reason: null, note: `Can't submit: ${closed} Delete the draft if it isn't needed.` }),
		"Can't submit: Dec 2099 is closed. Delete the draft if it isn't needed.");
	assert.equal(actionHint({ allowed: true, reason: null, note: `Can't approve: ${closed}` }), `Can't approve: ${closed}`);
	assert.equal(actionHint({ allowed: true, reason: null }), "");
	assert.equal(actionHint({ allowed: false, reason: null, note: "ignored when disabled" }), "");
	assert.equal(actionHint(null), "");
});

test("short times", () => {
	assert.equal(shortTime("2026-06-21 20:23:35.837441"), "21 Jun 20:23");
	assert.equal(shortTime(null), "");
	assert.equal(shortTime("soon"), "soon");
});
