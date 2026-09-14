import { test } from "node:test";
import assert from "node:assert/strict";
import { closeSteps } from "./domain.js";

const signoffStep = (data) => closeSteps(data).find((s) => s.id === "signoff");

test("signoff is available when the server has declared the period", () => {
	const step = signoffStep({ period: { declared: true, status: "Open" } });
	assert.equal(step.available, true);
});

test("signoff shows a not-declared detail and is unavailable when the server says declared: false", () => {
	const step = signoffStep({ period: { declared: false } });
	assert.equal(step.available, false);
	assert.equal(step.detail, "This period has not been declared on the fiscal calendar.");
});

test("signoff is unavailable, with no not-declared claim, when the server sends no declared field at all", () => {
	const step = signoffStep({ period: { status: "Open" } });
	assert.equal(step.available, false);
	assert.notEqual(step.detail, "This period has not been declared on the fiscal calendar.");
});
