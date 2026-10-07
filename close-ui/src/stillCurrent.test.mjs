// konsol#305 review-w5 U7/U8: stillCurrent.test.mjs — a result that comes
// back after the user moved to another period (or group) is ignored, never
// written onto the new screen.
import { test } from "node:test";
import assert from "node:assert/strict";
import { whileCurrent } from "./stillCurrent.js";

function clock(initial) {
	let key = initial;
	return { current: () => key, move: (next) => { key = next; } };
}

test("whileCurrent hands back the value when the key has not moved", async () => {
	const c = clock("2025/7/G1");
	assert.deepEqual(await whileCurrent(c.current, async () => 42), { stale: false, value: 42 });
});

test("whileCurrent rethrows a refusal when the key has not moved", async () => {
	const c = clock("2025/7/G1");
	await assert.rejects(
		whileCurrent(c.current, async () => { throw new Error("refused"); }),
		/refused/,
	);
});

test("whileCurrent marks a late success stale when the period moved during the call", async () => {
	const c = clock("2025/7/G1");
	const out = await whileCurrent(c.current, async () => { c.move("2025/8/G1"); return 42; });
	assert.deepEqual(out, { stale: true });
});

test("whileCurrent swallows a late refusal for another period (the key captured at the click)", async () => {
	const c = clock("2025/7/G1");
	const out = await whileCurrent(c.current, async () => { c.move("2025/8/G1"); throw new Error("refused"); });
	assert.deepEqual(out, { stale: true });
});

test("whileCurrent treats a group change as a move too", async () => {
	const c = clock("2025/7/G1");
	const out = await whileCurrent(c.current, async () => { c.move("2025/7/G2"); return 1; });
	assert.deepEqual(out, { stale: true });
});
