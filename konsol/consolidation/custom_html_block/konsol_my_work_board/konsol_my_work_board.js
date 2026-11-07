// My Work board (konsol#419; option C, Deepak Pai, 7 Nov 2026): the items of
// konsol.close.mywork_api.get_my_work as cards in Blocking, To do and
// Waiting, read live from konsol.close.mywork_board_api.get_board. Nothing is
// stored and nothing is dragged: a card moves when the work behind it is done.
// Frappe runs this inside the block's shadow root (root_element).
//
// Every server string is set as textContent; the block writes no HTML. Every
// link is checked to be a Desk path (desk()) before it is set.
//
// Each card shows only its heading until opened (Deepak Pai, 7 Nov 2026: "just
// show the headings with a collapsible link and collapse all /expand all
// toggle button on the top bar"): a <details> whose <summary> is the title
// and period, closed on load; the top-bar button opens or closes them all.
const METHOD = "konsol.close.mywork_board_api.get_board";
const box = root_element.querySelector(".konsol-mywork");

function el(tag, cls, text) {
	const node = document.createElement(tag);
	if (cls) node.className = cls;
	if (text !== undefined && text !== null) node.textContent = String(text);
	return node;
}

function desk(path) {
	return typeof path === "string" && path.startsWith("/desk/") ? path : null;
}

function link(cls, text, path) {
	const href = desk(path);
	if (!href) return null;
	const a = el("a", cls, text);
	a.href = href;
	return a;
}

function fail(message) {
	box.replaceChildren(
		el("p", "kmw-error", "My Work could not be read: " + (message || "no reason given"))
	);
}

function load() {
	return frappe
		.xcall(METHOD, {}, "GET")
		.then(render)
		.catch((e) => fail(typeof e === "string" ? e : e && e.message));
}

function cardNode(card) {
	const node = el("details", "kmw-card kmw-" + card.kind);
	const head = el("summary", "kmw-card-head");
	head.append(el("span", "kmw-card-title", card.title));
	if (card.period) head.append(el("span", "kmw-period", card.period));
	node.append(head);
	if (card.detail) node.append(el("p", "kmw-detail", card.detail));
	const fix = el("div", "kmw-fix");
	fix.append(el("div", "kmw-fix-label", "How to fix"));
	const fixes = card.fix || [];
	const steps = el(fixes.length > 1 ? "ol" : "ul", "kmw-steps");
	for (const step of fixes) steps.append(el("li", null, step));
	fix.append(steps);
	node.append(fix);
	if (card.no_go) node.append(el("p", "kmw-no-go", card.no_go));
	const meta = el("div", "kmw-meta");
	if (card.due && card.due.text) {
		meta.append(
			el(
				"span",
				"kmw-due" + (card.due.overdue ? " overdue" : ""),
				"Due " + card.due.text + (card.due.overdue ? " (overdue)" : "")
			)
		);
	}
	if (card.owner) meta.append(el("span", "kmw-owner", card.owner));
	const handbook = link("kmw-handbook", "Handbook", card.handbook);
	if (handbook) meta.append(handbook);
	const go = link("btn btn-primary btn-xs kmw-go", "Go", card.go);
	if (go) meta.append(go);
	node.append(meta);
	return node;
}

function columnNode(column) {
	const node = el("div", "kmw-column kmw-col-" + column.kind);
	const head = el("div", "kmw-column-head");
	head.append(
		el("span", "kmw-column-label", column.label),
		el("span", "kmw-count", column.cards.length)
	);
	node.append(head);
	if (!column.cards.length) node.append(el("p", "kmw-empty", "Nothing here."));
	for (const card of column.cards) node.append(cardNode(card));
	return node;
}

function cards() {
	return [...box.querySelectorAll("details.kmw-card")];
}

function toggleButton() {
	const button = el("button", "btn btn-default btn-xs kmw-toggle", "Expand all");
	button.type = "button";
	const label = () => {
		button.textContent = cards().some((c) => !c.open) ? "Expand all" : "Collapse all";
	};
	button.addEventListener("click", () => {
		const open = cards().some((c) => !c.open);
		for (const c of cards()) c.open = open;
		label();
	});
	// A card opened or closed by hand changes what the button will do.
	box.addEventListener("toggle", label, true);
	return button;
}

function render(board) {
	const head = el("div", "kmw-head");
	head.append(el("span", "kmw-title", "My Work"));
	const tools = el("div", "kmw-tools");
	const refresh = el("button", "btn btn-default btn-xs", "Refresh");
	refresh.type = "button";
	refresh.addEventListener("click", load);
	tools.append(toggleButton(), refresh);
	const list = link("btn btn-default btn-xs kmw-list", "List view", board.list_view);
	if (list) tools.append(list);
	head.append(tools);
	const nodes = [head];
	if (board.message) nodes.push(el("p", "kmw-message", board.message));
	const grid = el("div", "kmw-board");
	for (const column of board.columns || []) grid.append(columnNode(column));
	nodes.push(grid);
	box.replaceChildren(...nodes);
}

load();
