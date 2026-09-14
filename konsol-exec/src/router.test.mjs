import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

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
