// konsol#305 U2: saveFile.test.mjs — the one place a downloaded blob is
// handed to the browser as a file (Numbers' Excel, the Audit trail's CSV).
import { test } from "node:test";
import assert from "node:assert/strict";
import { saveFile } from "./saveFile.js";

function fakes() {
	const log = [];
	const link = {
		click: () => log.push(["click", link.href, link.download]),
		remove: () => log.push(["remove"]),
	};
	const doc = {
		createElement: (tag) => {
			log.push(["create", tag]);
			return link;
		},
		body: { appendChild: (el) => log.push(["append", el === link]) },
	};
	const urls = {
		createObjectURL: (blob) => {
			log.push(["url", blob.size]);
			return "blob:1";
		},
		revokeObjectURL: (href) => log.push(["revoke", href]),
	};
	return { log, doc, urls };
}

test("saveFile clicks a link to the blob named with the server's filename, then cleans up", () => {
	const { log, doc, urls } = fakes();
	const later = [];
	saveFile({ size: 9 }, "trail-FY2025P07.csv", { doc, urls, defer: (fn) => later.push(fn) });
	assert.deepEqual(log, [
		["url", 9],
		["create", "a"],
		["append", true],
		["click", "blob:1", "trail-FY2025P07.csv"],
		["remove"],
	]);
	// U7: the URL is revoked only after the click has handed the file to the
	// browser (a later task), so the save is never aborted.
	assert.equal(later.length, 1);
	later[0]();
	assert.deepEqual(log.at(-1), ["revoke", "blob:1"]);
});

test("U7: saveFile's default defers the revoke to a later task", async () => {
	const { log, doc, urls } = fakes();
	saveFile({ size: 9 }, "x.xlsx", { doc, urls });
	assert.ok(!log.some((e) => e[0] === "revoke"), "not revoked synchronously after click");
	await new Promise((resolve) => setTimeout(resolve, 5));
	assert.deepEqual(log.at(-1), ["revoke", "blob:1"]);
});

test("saveFile refuses a blank filename: the name is the server's, never made up", () => {
	const { log, doc, urls } = fakes();
	assert.throws(() => saveFile({ size: 1 }, "", { doc, urls }), /file name/);
	assert.deepEqual(log, []);
});
