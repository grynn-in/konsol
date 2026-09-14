import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
	parsePeriodRoute, monthPath, currentMonthPath, isMine,
	stageTarget, crumbsFor, periodCrumb, selectedFor, defaultExpanded, openCount, firstOpenItem, shortTime, actionHint,
} from "./home.js";

test("no client-side 0/13 period vocabulary: the server's code and label are used as-is", () => {
	const source = readFileSync(fileURLToPath(new URL("./home.js", import.meta.url)), "utf8");
	assert.equal(/\bOPN\b/.test(source), false, "home.js must not hard-code the OPN code");
	assert.equal(/\bCLS\b/.test(source), false, "home.js must not hard-code the CLS code");
	assert.equal(source.includes("periodCode"), false, "home.js must not compute a period code");
	assert.equal(source.includes("periodLabel"), false, "home.js must not compute a period label");
});

test("route params parse to a real period or nothing; the server decides what exists, not this parser", () => {
	assert.deepEqual(parsePeriodRoute({ year: "2026", period: "9" }), { year: 2026, period: 9 });
	assert.deepEqual(parsePeriodRoute({ year: "2026", period: "0" }), { year: 2026, period: 0 });
	assert.deepEqual(parsePeriodRoute({ year: "2099", period: "14" }), { year: 2099, period: 14 });
	assert.deepEqual(parsePeriodRoute({ year: "2026", period: "255" }), { year: 2026, period: 255 });
	assert.equal(parsePeriodRoute({ year: "2026", period: "256" }), null);
	assert.equal(parsePeriodRoute({ year: "2026", period: "-1" }), null);
	assert.equal(parsePeriodRoute({ year: "abc", period: "1" }), null);
	assert.equal(parsePeriodRoute({}), null);
});

test("paths", () => {
	assert.equal(monthPath(2026, 9), "/2026/9");
});

test("currentMonthPath opens the server's declared current period, never a guessed calendar month", () => {
	// The server says today falls in FY2026 P09: go straight there.
	assert.equal(currentMonthPath({ current: { fiscal_year: 2026, fiscal_period: 9 } }), "/2026/9");
	// Nothing declared covers today: the no-period home, not a guess.
	assert.equal(currentMonthPath({ current: null }), "/");
	// The tree hasn't loaded yet: still the no-period home, never a crash.
	assert.equal(currentMonthPath(null), "/");
	assert.equal(currentMonthPath(undefined), "/");
});

test("home.js no longer guesses a period from today's calendar month/year", () => {
	const source = readFileSync(fileURLToPath(new URL("./home.js", import.meta.url)), "utf8");
	assert.equal(/getMonth\s*\(/.test(source), false, "home.js must not read the calendar month");
	assert.equal(/getFullYear\s*\(/.test(source), false, "home.js must not read the calendar year");
	assert.equal(/new Date\s*\(/.test(source), false, "home.js must not default to today's date");
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

test("the path always says year, month, then view; code and label are exactly the server's", () => {
	assert.deepEqual(crumbsFor({ name: "month", year: 2026, period: 9, code: "P09", label: "Sep 2026" }).map((c) => c.label),
		["FY2026", "P09 · Sep 2026", "Close"]);
	const step = crumbsFor({ name: "step", year: 2026, period: 9, code: "P09", label: "Sep 2026", stepLabel: "Sign off period" });
	assert.equal(step[1].to, "/2026/9");
	assert.equal(step[0].to, undefined);
	assert.equal(step[2].label, "Sign off period");
	assert.deepEqual(crumbsFor({ name: "month" }), [{ label: "Konsol" }]);
	assert.deepEqual(crumbsFor({ name: "uploads" }).map((c) => c.label), ["Group", "Upload trial balances"]);
	// A 13-period year's close (fiscal_period 14) is just whatever the server calls it.
	assert.deepEqual(crumbsFor({ name: "month", year: 2099, period: 14, code: "P14", label: "Second close" }).map((c) => c.label),
		["FY2099", "P14 · Second close", "Close"]);
});

test("periodCrumb reads the server's code/label from the tree; an undeclared period gets a 'not declared' crumb, never undefined", () => {
	const tree = { years: [{ fiscal_year: 2026, periods: [{ fiscal_period: 9, code: "P09", label: "Sep 2026" }] }] };
	assert.deepEqual(periodCrumb(tree, 2026, 9), { code: "P09", label: "Sep 2026" });
	assert.deepEqual(periodCrumb(tree, 2026, 14), { code: "Period 14", label: "not declared" });
	assert.deepEqual(periodCrumb(tree, 2099, 1), { code: "Period 1", label: "not declared" });
});

test("periodCrumb makes no 'not declared' claim while the home tree hasn't loaded yet", () => {
	// No tree at all (still loading): no claim, just the bare period number.
	assert.deepEqual(periodCrumb(null, 2026, 9), { code: "Period 9", label: "" });
	assert.deepEqual(periodCrumb(undefined, 2026, 9), { code: "Period 9", label: "" });
	assert.deepEqual(periodCrumb({}, 2026, 9), { code: "Period 9", label: "" });

	// A loaded tree that simply doesn't have the period is still "not declared".
	const tree = { years: [{ fiscal_year: 2026, periods: [{ fiscal_period: 9, code: "P09", label: "Sep 2026" }] }] };
	assert.deepEqual(periodCrumb(tree, 2026, 14), { code: "Period 14", label: "not declared" });

	// crumbsFor must not render a dangling " · " when the label is empty.
	const crumbs = crumbsFor({ name: "month", year: 2026, period: 9, ...periodCrumb(null, 2026, 9) });
	assert.deepEqual(crumbs.map((c) => c.label), ["FY2026", "Period 9", "Close"]);
});

test("App.vue's exact crumb inputs (name, year, period, stepLabel) plus the tree give the server's code/label; nothing ever says undefined", () => {
	const tree = { years: [{ fiscal_year: 2026, periods: [{ fiscal_period: 9, code: "P09", label: "Sep 2026" }] }] };
	const build = (name, year, period, stepLabel) =>
		crumbsFor({ name, year, period, ...periodCrumb(tree, year, period), stepLabel });

	const month = build("month", 2026, 9, null).map((c) => c.label);
	assert.deepEqual(month, ["FY2026", "P09 · Sep 2026", "Close"]);
	assert.equal(month.some((l) => /undefined/.test(l)), false);

	// FY2099 period 14 isn't in the tree: a "not declared" crumb, never an
	// invented month name and never "undefined".
	const step = build("step", 2099, 14, "Sign off period").map((c) => c.label);
	assert.deepEqual(step, ["FY2099", "Period 14 · not declared", "Sign off period"]);
	assert.equal(step.some((l) => /undefined/.test(l)), false);
});

test("selectedFor: the no-period home never borrows the plane's period (PR #192 re-review finding 2)", () => {
	const planePeriod = { year: "2026", period: "9" };
	// The plane has a period, but "/" says no declared period covers today:
	// the navigator must not contradict that by highlighting one anyway.
	assert.equal(selectedFor("home", null, planePeriod), null);
	assert.equal(selectedFor("home", null, null), null);

	// The month route always uses the URL's own period, plane or not.
	const routeSelection = { year: 2026, period: 9 };
	assert.deepEqual(selectedFor("month", routeSelection, null), routeSelection);
	assert.deepEqual(selectedFor("month", routeSelection, planePeriod), routeSelection);
	assert.equal(selectedFor("month", null, planePeriod), null);

	// Close/step routes keep today's behaviour: the plane's period, if any.
	assert.deepEqual(selectedFor("close", null, planePeriod), { year: 2026, period: 9 });
	assert.deepEqual(selectedFor("step", null, planePeriod), { year: 2026, period: 9 });
	assert.deepEqual(selectedFor("step-tab", null, planePeriod), { year: 2026, period: 9 });
	assert.equal(selectedFor("close", null, null), null);
	assert.equal(selectedFor("close", null, { year: "", period: "" }), null);
});

test("navigator opens the current and the selected year", () => {
	const s = defaultExpanded({ current: { fiscal_year: 2026 } }, { year: 2024, period: 3 });
	assert.deepEqual([...s].sort(), [2024, 2026]);
	assert.equal(defaultExpanded(null, null).size, 0);
	// Nothing declared for today (konsol#189 review finding 2): current is
	// null, not a guessed year. Must not throw, and expands nothing extra.
	assert.equal(defaultExpanded({ current: null }, null).size, 0);
	assert.deepEqual([...defaultExpanded({ current: null }, { year: 2024, period: 3 })], [2024]);
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
