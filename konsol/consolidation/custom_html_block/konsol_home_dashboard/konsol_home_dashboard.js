// Konsol Home Dashboard (trial, Deepak Pai, 8 Nov 2026): the Close home in one
// block, from konsol.close.home_dashboard_api.get_home. Header with Group and Periods
// selectors, the "N items need you" headline, banners that are true now, four
// KPI tiles with a sparkline, the period status card, the entities list and a
// floating setup pill.
//
// Frappe runs this inside the block's shadow root (root_element). Every server
// string is set as textContent; the block writes no HTML. Every link is checked
// to be a Desk path (desk()) before it is set. A section the server could not
// read says "Unavailable: <reason>" in place; nothing is drawn as zero for want
// of a read.
const METHOD = "konsol.close.home_dashboard_api.get_home";
const SVG = "http://www.w3.org/2000/svg";
const TONE = { ok: "green", warn: "orange", blocked: "red", unknown: "gray" };
const TB = {
	submitted: ["green", "TB submitted"],
	exception: ["orange", "TB exception declared"],
	missing: ["red", "TB missing"],
	not_expected: ["gray", "No TB owed"],
};
const IC = {
	ok: ["green", "IC within tolerance"],
	over: ["orange", "IC pair over tolerance"],
	none: ["gray", "No IC pair"],
	unknown: ["gray", "IC not checked"],
};
const box = root_element.querySelector(".konsol-home");
const view = { group: null, periods: 6, open_only: true };

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
	if (!href) return el("span", cls, text);
	const a = el("a", cls, text);
	a.href = href;
	return a;
}

function unavailable(section) {
	return section && section.state === "unavailable";
}

function reasonNode(section) {
	return el("p", "kh-unavailable", "Unavailable: " + (section.reason || "no reason given"));
}

function fail(message) {
	box.replaceChildren(
		el("p", "kh-unavailable", "The close home could not be read: " + (message || "no reason given"))
	);
}

function load() {
	const args = { periods: view.periods };
	if (view.group) args.group = view.group;
	return frappe
		.xcall(METHOD, args, "GET")
		.then((data) => {
			try {
				render(data);
			} catch (e) {
				fail(e.message);
			}
		})
		.catch((e) => fail(typeof e === "string" ? e : e && e.message));
}

// --- header ------------------------------------------------------------------

function select(label, options, value, onChange) {
	const wrap = el("label", "kh-select");
	wrap.append(el("span", "kh-select-label", label));
	const node = el("select", "form-control input-xs");
	for (const [v, text] of options) {
		const option = el("option", null, text);
		option.value = v;
		if (String(v) === String(value)) option.selected = true;
		node.append(option);
	}
	node.addEventListener("change", () => onChange(node.value));
	wrap.append(node);
	return wrap;
}

function header(h) {
	const node = el("div", "kh-header");
	const title = el("div", "kh-heading");
	title.append(el("span", "kh-title", "Close"));
	const sub = [h.group, h.period].filter(Boolean).join(" · ");
	title.append(el("span", "kh-subtitle", sub || h.period_reason || h.group_message || ""));
	node.append(title);
	const tools = el("div", "kh-tools");
	const groups = (h.groups || []).map((g) => [g, g]);
	if (!h.group) groups.unshift(["", "Choose a group"]);
	if (groups.length > 1 || h.group) {
		tools.append(
			select("Group", groups, h.group || "", (v) => {
				view.group = v || null;
				load();
			})
		);
	}
	tools.append(
		select(
			"Periods",
			(h.choices || []).map((c) => [c, c + " periods"]),
			h.periods,
			(v) => {
				view.periods = Number(v);
				load();
			}
		)
	);
	node.append(tools);
	return node;
}

// --- headline and banners ---------------------------------------------------------

function headline(h) {
	const node = el("div", "kh-card kh-headline");
	if (unavailable(h)) {
		node.append(reasonNode(h));
		return node;
	}
	const text = el("div", "kh-headline-text");
	text.append(el("span", "kh-headline-count" + (h.count ? " has-items" : ""), h.text));
	text.append(el("span", "kh-muted", h.detail));
	node.append(text, link("btn btn-primary btn-sm kh-start", h.count ? "Start" : "Open My Work", h.route));
	return node;
}

function banners(list) {
	return (list || []).map((b) => {
		const node = el("div", "kh-banner kh-banner-" + b.kind);
		node.append(el("span", null, b.text), link("kh-banner-action", b.action + " →", b.route));
		return node;
	});
}

// --- tiles -------------------------------------------------------------------------

function sparkline(runs, title) {
	const svg = document.createElementNS(SVG, "svg");
	svg.setAttribute("class", "kh-spark");
	svg.setAttribute("viewBox", "-2 -2 64 22");
	svg.setAttribute("role", "img");
	svg.setAttribute("aria-label", title + " trend");
	for (const run of runs || []) {
		const pts = run.split(" ");
		if (pts.length === 1) {
			const [cx, cy] = pts[0].split(",");
			const dot = document.createElementNS(SVG, "circle");
			dot.setAttribute("cx", cx);
			dot.setAttribute("cy", cy);
			dot.setAttribute("r", "1.6");
			svg.append(dot);
		} else {
			const line = document.createElementNS(SVG, "polyline");
			line.setAttribute("points", run);
			svg.append(line);
		}
	}
	return svg;
}

function pointsTitle(points) {
	return (points || [])
		.map((p) => p.label + ": " + (p.display !== null && p.display !== undefined ? p.display : p.note || "no value"))
		.join("\n");
}

function tile(t, index) {
	const node = el("div", "kh-card kh-tile");
	if (unavailable(t)) {
		node.append(el("div", "kh-tile-title", ["TB submitted", "IC differences", "Days to close", "Consolidated net result"][index]));
		node.append(reasonNode(t));
		return node;
	}
	node.append(link("kh-tile-title", t.title, t.route));
	const body = el("div", "kh-tile-body");
	const left = el("div", "kh-tile-main");
	if (t.state === "ok") {
		left.append(link("kh-tile-value", t.display, t.route));
		if (t.delta_display) left.append(el("span", "kh-delta kh-delta-" + (t.delta_tone || "flat"), t.delta_display));
	} else {
		left.append(el("span", "kh-tile-value kh-muted", "—"));
	}
	body.append(left);
	const spark = sparkline(t.spark, t.title);
	const title = document.createElementNS(SVG, "title");
	title.textContent = pointsTitle(t.points);
	spark.prepend(title);
	body.append(spark);
	node.append(body);
	if (t.breakdown) node.append(el("div", "kh-muted kh-small", t.breakdown));
	if (t.empty) {
		const empty = el("div", "kh-empty kh-small");
		empty.append(el("span", null, t.empty.text));
		if (t.empty.action && desk(t.empty.route)) {
			empty.append(" ", link("kh-empty-action", t.empty.action + " →", t.empty.route));
		}
		node.append(empty);
	}
	return node;
}

// --- period card and entities ---------------------------------------------------------

function pill(text, tone) {
	return el("span", "indicator-pill " + (TONE[tone] || "gray"), text);
}

function periodCard(p) {
	const node = el("div", "kh-card kh-period");
	node.append(el("div", "kh-card-title", "Period status"));
	if (unavailable(p)) {
		node.append(reasonNode(p));
		return node;
	}
	const head = el("div", "kh-period-head");
	head.append(el("span", "kh-period-name", p.title));
	if (p.status) head.append(pill(p.status, p.status === "Open" ? "warn" : "ok"));
	node.append(head);
	const rows = el("dl", "kh-facts");
	const fact = (label, value) => {
		rows.append(el("dt", null, label));
		const dd = el("dd");
		dd.append(value);
		rows.append(dd);
	};
	fact("Close", pill(p.close.text, p.close.tone));
	fact("Warehouse", link("kh-plain", p.freshness.text, p.freshness.route));
	fact("Waiting for me", link("kh-plain", String(p.waiting), p.waiting_route));
	node.append(rows);
	node.append(link("btn btn-default btn-xs kh-manage", "Manage →", p.route));
	return node;
}

function dot(map, state) {
	const [tone, label] = map[state] || ["gray", state];
	const node = el("span", "kh-dot kh-dot-" + tone);
	node.title = label;
	node.setAttribute("aria-label", label);
	return node;
}

function entities(e) {
	const node = el("div", "kh-card kh-entities");
	const head = el("div", "kh-entities-head");
	const title = el("span", "kh-card-title", "Entities" + (e && e.total !== undefined ? " " + e.total : ""));
	head.append(title);
	if (unavailable(e)) {
		node.append(head, reasonNode(e));
		return node;
	}
	const toggle = el("div", "btn-group kh-toggle");
	for (const [open, text] of [[true, "Open " + e.open], [false, "All"]]) {
		const b = el("button", "btn btn-xs " + (view.open_only === open ? "btn-primary" : "btn-default"), text);
		b.type = "button";
		b.addEventListener("click", () => {
			view.open_only = open;
			node.replaceWith(entities(e));
		});
		toggle.append(b);
	}
	head.append(toggle);
	node.append(head);
	const legend = el("div", "kh-muted kh-small kh-legend", e.period + " · TB · IC" + (e.ic_state && e.ic_state !== "checked" ? " (" + (e.ic_message || e.ic_state) + ")" : ""));
	node.append(legend);
	const rows = e.rows.filter((r) => !view.open_only || r.open);
	if (!rows.length) {
		node.append(el("p", "kh-muted", view.open_only ? "Nothing open for " + e.period + "." : "No entity you can see."));
	}
	const list = el("ul", "kh-entity-list");
	for (const r of rows) {
		const li = el("li", "kh-entity");
		const a = link("kh-entity-link", null, r.route);
		const name = el("span", "kh-entity-name");
		name.append(el("span", "kh-entity-code", r.entity), el("span", "kh-muted", r.name !== r.entity ? r.name : ""));
		const dots = el("span", "kh-dots");
		dots.append(dot(TB, r.tb), dot(IC, r.ic));
		a.append(name, dots);
		li.append(a);
		list.append(li);
	}
	node.append(list);
	node.append(link("kh-small kh-plain", "All entities →", e.route));
	return node;
}

function setupPill(s) {
	if (!s) return null;
	if (unavailable(s)) return el("div", "kh-setup-pill kh-small", "Setup unavailable: " + s.reason);
	if (s.hidden) return null;
	const node = link("kh-setup-pill", null, s.route);
	node.append(el("span", "kh-small", "Setup " + s.done + " of " + s.total));
	const bar = el("span", "kh-setup-bar");
	bar.setAttribute("role", "progressbar");
	bar.setAttribute("aria-valuemin", "0");
	bar.setAttribute("aria-valuemax", String(s.total));
	bar.setAttribute("aria-valuenow", String(s.done));
	const fill = el("span", "kh-setup-fill");
	fill.style.width = Math.round((100 * Math.min(s.total, Math.max(0, s.done))) / (s.total || 1)) + "%";
	bar.append(fill);
	node.append(bar);
	return node;
}

function render(data) {
	view.group = data.header.group || view.group;
	view.periods = data.header.periods;
	const nodes = [header(data.header), headline(data.headline), ...banners(data.banners)];
	nodes.push(el("div", "kh-section-label", "Close"));
	const grid = el("div", "kh-tiles");
	(data.tiles || []).forEach((t, i) => grid.append(tile(t, i)));
	nodes.push(grid);
	const lower = el("div", "kh-lower");
	lower.append(periodCard(data.period), entities(data.entities));
	nodes.push(lower);
	const pillNode = setupPill(data.setup);
	if (pillNode) nodes.push(pillNode);
	box.replaceChildren(...nodes);
}

load();
