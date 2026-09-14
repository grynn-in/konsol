import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { parsePeriodRoute } from "./home.js";

// router.js imports Vue single-file components, which plain `node --test`
// can't load (no .vue transform outside the Vite build), so this checks the
// route table as source text rather than importing it — same approach as
// home.js's source-shape assertions.
function routeLines() {
	const source = readFileSync(fileURLToPath(new URL("./router.js", import.meta.url)), "utf8");
	const body = source.match(/const routes = \[([\s\S]*?)\n\];/)[1];
	return body
		.split("\n")
		.map((l) => l.trim())
		.filter((l) => l.startsWith("{"));
}

// PR #192 review finding 4a-adjacent (row 70c2): the no-period home route "/"
// had no component, so once the redirect guard (see router.js's beforeEach)
// left the user there — nothing declared for today, or the server
// unreachable — the content pane rendered nothing. Every route that isn't a
// plain redirect must have a component to render, so this can't regress.
test("every non-redirect route has a component", () => {
	const lines = routeLines();
	assert.ok(lines.length > 0, "could not find the route table in router.js");
	for (const line of lines) {
		if (line.includes("redirect:")) continue;
		assert.ok(line.includes("component:"), `route has no component: ${line}`);
	}
});

test("the no-period home route is named home", () => {
	const lines = routeLines();
	const home = lines.find((l) => l.includes('path: "/"'));
	assert.ok(home, "no route for path \"/\"");
	assert.ok(home.includes('name: "home"'), `path "/" is not named home: ${home}`);
});

// PR #192 review finding 6: parsePeriodRoute (home.js) accepts any period
// 0..255, but the route pattern only matched one or two digits, so a
// three-digit period like 140 never reached the parser at all — the
// catch-all would redirect it to the no-period home instead.
test("the month route's period pattern matches a three-digit period like 140", () => {
	const lines = routeLines();
	const month = lines.find((l) => l.includes('name: "month"'));
	assert.ok(month, "no route named month");
	const rawPattern = month.match(/:period\(([^)]+)\)/)?.[1];
	assert.ok(rawPattern, `month route has no :period(...) pattern: ${month}`);
	// The source is a JS string literal, so a regex backslash is doubled
	// (`\\d`) in the raw text; collapse it back to build a real RegExp.
	const paramPattern = rawPattern.replace(/\\\\/g, "\\");
	const periodRegex = new RegExp(`^(?:${paramPattern})$`);
	assert.ok(
		periodRegex.test("140"),
		`month route's period pattern ${paramPattern} does not match "140"`
	);
});

// Re-review nit 3 (from 70g): the route pattern matches /2026/256..999 (up to
// three digits), but parsePeriodRoute rejects anything above 255 — with no
// guard, the params reach MonthView unparsable and it shows "Loading…"
// forever (App.vue's routePeriod stays null, so home is never told to load
// anything). A per-route beforeEnter caught this for a fresh navigation, but
// not an in-app push that only changes the params of an already-matched
// route (e.g. /2026/9 -> /2026/300): beforeEnter doesn't re-run for that.
// Re-review 2 nit 4 (from 70l): the check must live in the global
// router.beforeEach guard instead, which runs on every navigation.
test("the month route has no per-route beforeEnter guard", () => {
	const lines = routeLines();
	const month = lines.find((l) => l.includes('name: "month"'));
	assert.ok(month, "no route named month");
	assert.ok(!month.includes("beforeEnter"), `month route should not have a beforeEnter guard: ${month}`);
});

test("the global beforeEach guard redirects an unparsable month route to /", async () => {
	const source = readFileSync(fileURLToPath(new URL("./router.js", import.meta.url)), "utf8");
	const body = source.match(/router\.beforeEach\(async \(to\) => \{([\s\S]*?)\n\}\);/)?.[1];
	assert.ok(body, "could not find router.beforeEach's guard body in router.js");

	// router.js can't be imported directly (it pulls in Vue SFCs), so rebuild
	// the guard as a real async function from its source text, with the real
	// parsePeriodRoute injected and periodTree/currentMonthPath stubbed —
	// same source-based approach as the tests above.
	const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
	const guard = new AsyncFunction("parsePeriodRoute", "periodTree", "currentMonthPath", "to", body);

	const unreachablePeriodTree = () =>
		Promise.reject(new Error("periodTree should not be called for a month route"));

	assert.equal(
		await guard(parsePeriodRoute, unreachablePeriodTree, () => "/", {
			name: "month",
			path: "/2026/256",
			params: { year: "2026", period: "256" },
		}),
		"/"
	);
	assert.equal(
		await guard(parsePeriodRoute, unreachablePeriodTree, () => "/", {
			name: "month",
			path: "/2026/9",
			params: { year: "2026", period: "9" },
		}),
		true
	);
});
