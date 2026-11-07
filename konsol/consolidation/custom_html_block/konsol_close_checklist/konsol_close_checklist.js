// Konsol Close Checklist (konsol#407 phase 1): the current period's close
// checklist, one line per check, from konsol.close.overview_api.get_checklist.
// Frappe runs this inside the block's shadow root (root_element). Every server
// string is set as textContent, never parsed as HTML, and a line links only to
// a /desk/ route.
const METHOD = "konsol.close.overview_api.get_checklist";
const GLYPH = { ok: "✓", warn: "!", blocked: "✕", unknown: "?" };
const TONE = { ok: "green", warn: "orange", blocked: "red", unknown: "gray" };
const box = root_element.querySelector(".konsol-checklist");

function el(tag, cls, text) {
	const node = document.createElement(tag);
	if (cls) node.className = cls;
	if (text !== undefined && text !== null) node.textContent = String(text);
	return node;
}

function deskRoute(route) {
	return typeof route === "string" && route.startsWith("/desk/") ? route : null;
}

function render(data) {
	const head = el("div", "kc-head");
	head.append(el("span", "kc-title", "Period close · " + data.title));
	if (data.total) {
		head.append(el("span", "kc-count", `${data.done} of ${data.total} done`));
		// done / total as a thin bar under the title (the fill is --primary).
		const done = Math.min(data.total, Math.max(0, data.done));
		const bar = el("div", "kc-progress");
		bar.setAttribute("role", "progressbar");
		bar.setAttribute("aria-valuemin", "0");
		bar.setAttribute("aria-valuemax", String(data.total));
		bar.setAttribute("aria-valuenow", String(done));
		const fill = el("div", "kc-progress-fill");
		fill.style.width = Math.round((100 * done) / data.total) + "%";
		bar.append(fill);
		head.append(bar);
	}
	const nodes = [head];
	if (data.reason) nodes.push(el("p", "kc-reason", data.reason));
	const list = el("ul", "kc-list");
	for (const line of data.lines || []) {
		if (!Object.prototype.hasOwnProperty.call(TONE, line.state)) {
			throw new Error(`unknown checklist state: ${line.state}`);
		}
		const item = el("li", "kc-line");
		item.append(el("span", `indicator-pill ${TONE[line.state]} kc-glyph`, GLYPH[line.state]));
		const body = el("span", "kc-body");
		const route = deskRoute(line.route);
		const label = el(route ? "a" : "span", "kc-label", line.label);
		if (route) label.href = route;
		body.append(label, el("span", "kc-detail", line.detail));
		item.append(body);
		list.append(item);
	}
	nodes.push(list);
	box.replaceChildren(...nodes);
}

function fail(message) {
	box.replaceChildren(
		el("p", "kc-error", "The period close checklist could not be read: " + (message || "no reason given"))
	);
}

frappe
	.xcall(METHOD, {}, "GET")
	.then((data) => {
		try {
			render(data);
		} catch (e) {
			fail(e.message);
		}
	})
	.catch((e) => fail(typeof e === "string" ? e : e && e.message));
