// Konsol Handbook (konsol#407 phase 3; Handbook option C, Deepak Pai, 7 Nov 2026):
// the product guide that ships with Konsol and this site's own published
// pages, from konsol.handbook.get_handbook. Frappe runs this inside the
// block's shadow root (root_element).
//
// Every server string is set as textContent except a page's body, which the
// server rendered from markdown and sanitised (handbook_model.sanitize); that
// is the one HTML the block writes. Search reads the body's text through an
// inert DOMParser document, which runs no script and loads nothing.
const METHOD = "konsol.handbook.get_handbook";
const box = root_element.querySelector(".konsol-handbook");
const state = { data: null, audience: null, query: "", open: null };
const textOf = new Map();

function el(tag, cls, text) {
	const node = document.createElement(tag);
	if (cls) node.className = cls;
	if (text !== undefined && text !== null) node.textContent = String(text);
	return node;
}

function plain(page) {
	if (!textOf.has(page.id)) {
		const body = new DOMParser().parseFromString(page.html || "", "text/html").body;
		textOf.set(page.id, [page.title, page.section, body.textContent].join(" ").toLowerCase());
	}
	return textOf.get(page.id);
}

function matches(page) {
	const words = state.query.toLowerCase().split(/\s+/).filter(Boolean);
	return words.every((w) => plain(page).includes(w));
}

function fail(message) {
	box.replaceChildren(
		el("p", "kh-error", "The Konsol Handbook could not be read: " + (message || "no reason given"))
	);
}

function load(audience) {
	return frappe
		.xcall(METHOD, audience ? { audience } : {}, "GET")
		.then((data) => {
			state.data = data;
			state.audience = audience || null;
			render();
		})
		.catch((e) => fail(typeof e === "string" ? e : e && e.message));
}

function openPage(id) {
	state.open = id;
	const shown = (state.data.pages || []).some((p) => p.id === id);
	if (!shown || state.query) {
		state.query = "";
		return load(null).then(() => scrollTo(id));
	}
	renderList();
	scrollTo(id);
}

function scrollTo(id) {
	const node = root_element.querySelector(`[data-page="${CSS.escape(id)}"]`);
	if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
}

function chips() {
	const row = el("div", "kh-chips");
	const mine = new Set(state.data.my_audiences || []);
	for (const audience of [null, ...(state.data.audiences || [])]) {
		const label = audience === null ? "All" : audience;
		const chip = el("button", "kh-chip" + (audience === state.audience ? " active" : ""), label);
		chip.type = "button";
		if (mine.has(audience)) {
			chip.classList.add("mine");
			chip.title = "Your role";
		}
		chip.addEventListener("click", () => load(audience));
		row.append(chip);
	}
	return row;
}

function tiles() {
	const list = state.data.start_here || [];
	if (!list.length) return null;
	const wrap = el("div", "kh-start");
	wrap.append(el("div", "kh-subhead", "Start here"));
	const grid = el("div", "kh-tiles");
	for (const tile of list) {
		const button = el("button", "kh-tile" + (tile.mine ? " mine" : ""));
		button.type = "button";
		button.append(el("span", "kh-tile-role", tile.audience + (tile.mine ? " · you" : "")),
			el("span", "kh-tile-title", tile.title));
		button.addEventListener("click", () => openPage(tile.id));
		grid.append(button);
	}
	wrap.append(grid);
	return wrap;
}

function pageNode(page) {
	const item = el("details", "kh-page");
	item.dataset.page = page.id;
	if (state.open === page.id) item.open = true;
	const summary = el("summary", "kh-summary");
	summary.append(
		el("span", "kh-page-title", page.title),
		el("span", "kh-badge " + (page.origin === "Konsol" ? "konsol" : "site"), page.origin),
		el("span", "kh-audience", page.audience)
	);
	item.append(summary);
	const body = el("div", "kh-body");
	body.innerHTML = page.html;
	item.append(body);
	if (typeof page.edit_route === "string" && page.edit_route.startsWith("/desk/")) {
		const edit = el("a", "kh-edit", "Edit this page");
		edit.href = page.edit_route;
		item.append(edit);
	}
	item.addEventListener("toggle", () => {
		if (item.open) state.open = page.id;
	});
	return item;
}

function renderList() {
	const list = root_element.querySelector(".kh-list");
	const pages = (state.data.pages || []).filter(matches);
	const nodes = [];
	if (!pages.length) {
		nodes.push(el("p", "kh-empty", state.query ? "No page matches “" + state.query + "”." : "No pages."));
	}
	const sections = [];
	const bySection = new Map();
	for (const page of pages) {
		if (!bySection.has(page.section)) {
			bySection.set(page.section, []);
			sections.push(page.section);
		}
		bySection.get(page.section).push(page);
	}
	for (const section of sections) {
		nodes.push(el("div", "kh-subhead", section));
		for (const page of bySection.get(section)) nodes.push(pageNode(page));
	}
	list.replaceChildren(...nodes);
}

function render() {
	const head = el("div", "kh-head");
	head.append(el("span", "kh-title", "Konsol Handbook"));
	const search = el("input", "kh-search form-control");
	search.type = "search";
	search.placeholder = "Search the handbook";
	search.value = state.query;
	search.addEventListener("input", () => {
		state.query = search.value;
		renderList();
	});
	head.append(search);
	const nodes = [head, chips()];
	const start = tiles();
	if (start) nodes.push(start);
	if ((state.data.problems || []).length) {
		const problems = el("ul", "kh-problems");
		for (const text of state.data.problems) problems.append(el("li", null, text));
		nodes.push(problems);
	}
	nodes.push(el("div", "kh-list"));
	box.replaceChildren(...nodes);
	renderList();
}

load(null);
