import { test } from "node:test";
import assert from "node:assert/strict";
import { errorFrom, isNoAccess } from "./homeApi.js";

const serverMessages = (text) => JSON.stringify([JSON.stringify({ message: text })]);

test("the thrown message comes from _server_messages, as Frappe sends it", () => {
	const err = errorFrom(403, {
		exc_type: "PermissionError",
		_server_messages: serverMessages("Konsol needs an EPM or budget role. Ask your administrator for access."),
	});
	assert.equal(err.message, "Konsol needs an EPM or budget role. Ask your administrator for access.");
	assert.equal(isNoAccess(err), true);
});

test("with tracebacks allowed, the class prefix is stripped", () => {
	const err = errorFrom(417, { exception: "frappe.exceptions.ValidationError: No such period: FY2026 period 14" });
	assert.equal(err.message, "No such period: FY2026 period 14");
	assert.equal(isNoAccess(err), false);
});

test("no access is decided by status or type, never by wording", () => {
	assert.equal(isNoAccess(errorFrom(403, {})), true);
	assert.equal(isNoAccess(errorFrom(500, { exc_type: "PermissionError" })), true);
	assert.equal(isNoAccess(errorFrom(502, {})), false);
	assert.equal(errorFrom(502, {}).message, "API error (502)");
	assert.equal(isNoAccess(null), false);
});
