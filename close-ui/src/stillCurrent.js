// konsol#305 review-w5 U7/U8: stillCurrent.js
//
// A call that comes back after the user moved to another period (or group)
// must not write onto the new screen. `whileCurrent(currentKey, task)`
// captures `currentKey()` before the call and compares it after:
// - unchanged: the value is handed back (`{stale: false, value}`), and a
//   refusal is rethrown for the caller to show;
// - moved: `{stale: true}`, success or refusal alike — the caller drops it.

export async function whileCurrent(currentKey, task) {
	const key = currentKey();
	let value;
	try {
		value = await task();
	} catch (error) {
		if (currentKey() !== key) {
			return { stale: true };
		}
		throw error;
	}
	return currentKey() === key ? { stale: false, value } : { stale: true };
}
