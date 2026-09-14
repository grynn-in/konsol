/**
 * The upload page lets the user declare the Amount Basis (konsolidat#199).
 *
 * A .vue file is not host-runnable under node --test, so these read
 * UploadView.vue as text and assert the pieces the machine relies on: the
 * select that sends SET_BASIS, the blank "not given" option, the one-time
 * default from the server, and the per-entity-period basis in the report.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const vue = readFileSync(path.join(here, "components", "UploadView.vue"), "utf8");
const template = vue.slice(vue.indexOf("<template>"));

test("the page has a labelled Amount Basis select bound to the machine's amountBasis", () => {
	assert.match(template, /<select[^>]*\bid="amount-basis"/, "a <select id=\"amount-basis\">");
	assert.match(template, /<label[^>]*\bfor="amount-basis"[^>]*>\s*Amount Basis/, "labelled \"Amount Basis\"");
	assert.ok(template.includes("ctx.amountBasis"), "its value is the machine's amountBasis");
});

test("changing the select sends SET_BASIS with the chosen basis", () => {
	assert.match(template, /@change="[^"]*SET_BASIS[^"]*amountBasis/, "@change sends { type: \"SET_BASIS\", amountBasis }");
});

test("a blank option says that each entity-period must then carry its own column", () => {
	assert.ok(template.includes('<option value="">Not given: each entity-period must carry its own amount_basis column</option>'));
	assert.ok(vue.includes("amount_bases"), "the three bases come from the server, not from the page");
	assert.ok(!vue.includes('"Period-end balance"'), "the page never carries its own copy of the basis strings");
});

test("the site default is sent as SET_BASIS once, only while the context is blank", () => {
	const script = vue.slice(0, vue.indexOf("<template>"));
	assert.ok(script.includes("default_amount_basis"), "reads the default from the server");
	assert.match(script, /if \([^)]*default_amount_basis[^)]*&&\s*!ctx\.value\.amountBasis\)/, "only when nothing is set yet");
	assert.match(script, /send\(\{ type: "SET_BASIS", amountBasis: [^}]*default_amount_basis \}\)/);
});

test("the report shows each entity-period's basis and the help text says what a wrong choice does", () => {
	assert.ok(template.includes(">Basis</th>"), "a Basis column");
	assert.ok(template.includes("r.amount_basis"), "each report row's amount_basis");
	assert.match(template, /wrong (basis|choice)[^<]*build names/i, "one sentence: a wrong choice and that the build names it");
});
