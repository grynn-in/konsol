// Konsol Close Band (konsol#610, option B): the current period's six close
// items (close status, trial balances in, waiting for my approval, IC pairs
// over tolerance, TB exceptions declared, warehouse freshness), from
// konsol.close.overview_api.get_close_band. They were Custom Number Cards
// until Frappe 16.60 drew a card's value as text and printed their HTML.
// Frappe runs this inside the block's shadow root (root_element). Every server
// string is set as textContent, never parsed as HTML; the colour is checked
// against the state; an item links only to a /desk/ route.
const METHOD = "konsol.close.overview_api.get_close_band";
const TONE = { ok: "green", warn: "orange", blocked: "red", unknown: "gray" };
const box = root_element.querySelector(".konsol-close-band");

function el(tag, cls, text) {
	const node = document.createElement(tag);
	if (cls) node.className = cls;
	if (text !== undefined && text !== null) node.textContent = String(text);
	return node;
}

function deskRoute(route) {
	return typeof route === "string" && route.startsWith("/desk/") ? route : null;
}

function render(items) {
	if (!Array.isArray(items)) throw new Error("the close band came back empty");
	const list = el("div", "kb-list");
	for (const item of items) {
		if (!Object.prototype.hasOwnProperty.call(TONE, item.state)) {
			throw new Error(`unknown close band state: ${item.state}`);
		}
		if (item.colour !== TONE[item.state]) {
			throw new Error(`close band colour ${item.colour} does not match state ${item.state}`);
		}
		const route = deskRoute(item.route);
		const tile = el(route ? "a" : "div", "kb-item");
		if (route) tile.href = route;
		tile.append(
			el("span", "kb-label", item.label),
			el("span", `indicator-pill ${TONE[item.state]} kb-value`, item.text)
		);
		list.append(tile);
	}
	box.replaceChildren(list);
}

function fail(message) {
	box.replaceChildren(el("p", "kb-error", "The close band could not be read: " + (message || "no reason given")));
}

frappe
	.xcall(METHOD, {}, "GET")
	.then((items) => {
		try {
			render(items);
		} catch (e) {
			fail(e.message);
		}
	})
	.catch((e) => fail(typeof e === "string" ? e : e && e.message));
