// konsol#305 B08: nav.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { navFor } from "./nav.js";

const NO_COUNTS = { by_screen: {} };

test("close_lead sees all four screens", () => {
	const nav = navFor("close_lead", ["EPM Admin"], NO_COUNTS);
	assert.deepEqual(
		nav.map((n) => n.screen),
		["my-work", "trial-balances", "checks", "sign-off"],
	);
});

test("group_accountant (Analyst) sees all four screens", () => {
	const nav = navFor("group_accountant", ["EPM Analyst"], NO_COUNTS);
	assert.deepEqual(
		nav.map((n) => n.screen),
		["my-work", "trial-balances", "checks", "sign-off"],
	);
});

test("entity_accountant sees My work and Trial balances only, never Checks or Sign-off", () => {
	const nav = navFor("entity_accountant", ["Entity Accountant"], NO_COUNTS);
	assert.deepEqual(
		nav.map((n) => n.screen),
		["my-work", "trial-balances"],
	);
});

test("viewer sees Trial balances, Checks and Sign-off, never My work", () => {
	const nav = navFor("viewer", ["EPM User"], NO_COUNTS);
	assert.deepEqual(
		nav.map((n) => n.screen),
		["trial-balances", "checks", "sign-off"],
	);
});

test("each item carries its label", () => {
	const nav = navFor("close_lead", ["EPM Admin"], NO_COUNTS);
	const byScreen = Object.fromEntries(nav.map((n) => [n.screen, n.label]));
	assert.equal(byScreen["my-work"], "My work");
	assert.equal(byScreen["trial-balances"], "Trial balances");
	assert.equal(byScreen["checks"], "Checks");
	assert.equal(byScreen["sign-off"], "Sign-off");
});

test("counts attach to their screens, and a blocking count is flagged", () => {
	const counts = {
		by_screen: {
			"my-work": { count: 3, blocking: true },
			"trial-balances": { count: 1, blocking: false },
			checks: { count: 0, blocking: false },
			"sign-off": { count: 2, blocking: true },
		},
	};
	const nav = navFor("close_lead", ["EPM Admin"], counts);
	const byScreen = Object.fromEntries(nav.map((n) => [n.screen, n]));
	assert.equal(byScreen["my-work"].count, 3);
	assert.equal(byScreen["my-work"].blocking, true);
	assert.equal(byScreen["trial-balances"].count, 1);
	assert.equal(byScreen["trial-balances"].blocking, false);
	assert.equal(byScreen["sign-off"].blocking, true);
});

test("a screen missing from by_screen defaults to a zero, non-blocking count", () => {
	const nav = navFor("close_lead", ["EPM Admin"], { by_screen: {} });
	for (const item of nav) {
		assert.equal(item.count, 0);
		assert.equal(item.blocking, false);
	}
});

test("failure path: no close role gets an explicit result, never an empty nav", () => {
	const nav = navFor(null, [], NO_COUNTS);
	assert.deepEqual(nav, [{ screen: null, label: "No close role", count: null, blocking: false }]);
	assert.notDeepEqual(nav, []);
});

test("failure path: an unrecognized persona string also gets the explicit no-role result", () => {
	const nav = navFor("some_other_role", ["Some Other Role"], NO_COUNTS);
	assert.deepEqual(nav, [{ screen: null, label: "No close role", count: null, blocking: false }]);
});
