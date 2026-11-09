// Konsol Dashboard (trial, Deepak Pai, 8 Nov 2026): the Close home as a standard Page,
// built like Konsol Setup (a Page, not a Custom HTML Block), from
// konsol.close.home_dashboard_api.get_home. Group and Periods are in the page head, then the
// "N items need you" headline, banners that are true now, four KPI tiles with a sparkline, the
// period status card, the entities list and a floating setup pill.
//
// Every server string is set as textContent; the page writes no HTML. Every link is checked
// to be a Desk path (desk()) before it is set. A section the server could not read says
// "Unavailable: <reason>" in place; nothing is drawn as zero for want of a read.
// Styles: konsol_dashboard.css, every rule scoped to .konsol-dashboard.
const KONSOL_DASHBOARD_METHOD = "konsol.close.home_dashboard_api.get_home";
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
	return el("p", "kd-unavailable", "Unavailable: " + (section.reason || "no reason given"));
}

// --- headline and banners ---------------------------------------------------------

function headline(h) {
	const node = el("div", "kd-card kd-headline");
	if (unavailable(h)) {
		node.append(reasonNode(h));
		return node;
	}
	const text = el("div", "kd-headline-text");
	text.append(el("span", "kd-headline-count" + (h.count ? " has-items" : ""), h.text));
	text.append(el("span", "kd-muted", h.detail));
	node.append(text, link("btn btn-primary btn-sm kd-start", h.count ? "Start" : "Open My Work", h.route));
	return node;
}

function banners(list) {
	return (list || []).map((b) => {
		const node = el("div", "kd-banner kd-banner-" + b.kind);
		node.append(el("span", null, b.text), link("kd-banner-action", b.action + " →", b.route));
		return node;
	});
}

// --- tiles -------------------------------------------------------------------------

function sparkline(runs, title) {
	const svg = document.createElementNS(SVG, "svg");
	svg.setAttribute("class", "kd-spark");
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
	const node = el("div", "kd-card kd-tile");
	if (unavailable(t)) {
		node.append(el("div", "kd-tile-title", ["TB submitted", "IC differences", "Days to close", "Consolidated net result"][index]));
		node.append(reasonNode(t));
		return node;
	}
	node.append(link("kd-tile-title", t.title, t.route));
	const body = el("div", "kd-tile-body");
	const left = el("div", "kd-tile-main");
	if (t.state === "ok") {
		left.append(link("kd-tile-value", t.display, t.route));
		if (t.delta_display) left.append(el("span", "kd-delta kd-delta-" + (t.delta_tone || "flat"), t.delta_display));
	} else {
		left.append(el("span", "kd-tile-value kd-muted", "—"));
	}
	body.append(left);
	const spark = sparkline(t.spark, t.title);
	const title = document.createElementNS(SVG, "title");
	title.textContent = pointsTitle(t.points);
	spark.prepend(title);
	body.append(spark);
	node.append(body);
	if (t.breakdown) node.append(el("div", "kd-muted kd-small", t.breakdown));
	if (t.empty) {
		const empty = el("div", "kd-empty kd-small");
		empty.append(el("span", null, t.empty.text));
		if (t.empty.action && desk(t.empty.route)) {
			empty.append(" ", link("kd-empty-action", t.empty.action + " →", t.empty.route));
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
	const node = el("div", "kd-card kd-period");
	node.append(el("div", "kd-card-title", "Period status"));
	if (unavailable(p)) {
		node.append(reasonNode(p));
		return node;
	}
	const head = el("div", "kd-period-head");
	head.append(el("span", "kd-period-name", p.title));
	if (p.status) head.append(pill(p.status, p.status === "Open" ? "warn" : "ok"));
	node.append(head);
	const rows = el("dl", "kd-facts");
	const fact = (label, value) => {
		rows.append(el("dt", null, label));
		const dd = el("dd");
		dd.append(value);
		rows.append(dd);
	};
	fact("Close", pill(p.close.text, p.close.tone));
	fact("Warehouse", link("kd-plain", p.freshness.text, p.freshness.route));
	fact("Waiting for me", link("kd-plain", String(p.waiting), p.waiting_route));
	node.append(rows);
	node.append(link("btn btn-default btn-xs kd-manage", "Manage →", p.route));
	return node;
}

function dot(map, state) {
	const [tone, label] = map[state] || ["gray", state];
	const node = el("span", "kd-dot kd-dot-" + tone);
	node.title = label;
	node.setAttribute("aria-label", label);
	return node;
}

function entities(e, view) {
	const node = el("div", "kd-card kd-entities");
	const head = el("div", "kd-entities-head");
	const title = el("span", "kd-card-title", "Entities" + (e && e.total !== undefined ? " " + e.total : ""));
	head.append(title);
	if (unavailable(e)) {
		node.append(head, reasonNode(e));
		return node;
	}
	const toggle = el("div", "btn-group kd-toggle");
	for (const [open, text] of [[true, "Open " + e.open], [false, "All"]]) {
		const b = el("button", "btn btn-xs " + (view.open_only === open ? "btn-primary" : "btn-default"), text);
		b.type = "button";
		b.addEventListener("click", () => {
			view.open_only = open;
			node.replaceWith(entities(e, view));
		});
		toggle.append(b);
	}
	head.append(toggle);
	node.append(head);
	const legend = el("div", "kd-muted kd-small kd-legend", e.period + " · TB · IC" + (e.ic_state && e.ic_state !== "checked" ? " (" + (e.ic_message || e.ic_state) + ")" : ""));
	node.append(legend);
	const rows = e.rows.filter((r) => !view.open_only || r.open);
	if (!rows.length) {
		node.append(el("p", "kd-muted", view.open_only ? "Nothing open for " + e.period + "." : "No entity you can see."));
	}
	const list = el("ul", "kd-entity-list");
	for (const r of rows) {
		const li = el("li", "kd-entity");
		const a = link("kd-entity-link", null, r.route);
		const name = el("span", "kd-entity-name");
		name.append(el("span", "kd-entity-code", r.entity), el("span", "kd-muted", r.name !== r.entity ? r.name : ""));
		const dots = el("span", "kd-dots");
		dots.append(dot(TB, r.tb), dot(IC, r.ic));
		a.append(name, dots);
		li.append(a);
		list.append(li);
	}
	node.append(list);
	node.append(link("kd-small kd-plain", "All entities →", e.route));
	return node;
}

function setupPill(s) {
	if (!s) return null;
	if (unavailable(s)) return el("div", "kd-setup-pill kd-small", "Setup unavailable: " + s.reason);
	if (s.hidden) return null;
	const node = link("kd-setup-pill", null, s.route);
	node.append(el("span", "kd-small", "Setup " + s.done + " of " + s.total));
	const bar = el("span", "kd-setup-bar");
	bar.setAttribute("role", "progressbar");
	bar.setAttribute("aria-valuemin", "0");
	bar.setAttribute("aria-valuemax", String(s.total));
	bar.setAttribute("aria-valuenow", String(s.done));
	const fill = el("span", "kd-setup-fill");
	fill.style.width = Math.round((100 * Math.min(s.total, Math.max(0, s.done))) / (s.total || 1)) + "%";
	bar.append(fill);
	node.append(bar);
	return node;
}

frappe.pages["konsol-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Konsol Dashboard"),
		single_column: true,
	});
	wrapper.konsol_dashboard = new KonsolDashboard(page);
};

frappe.pages["konsol-dashboard"].refresh = function (wrapper) {
	wrapper.konsol_dashboard && wrapper.konsol_dashboard.refresh();
};

class KonsolDashboard {
	constructor(page) {
		this.page = page;
		this.view = { group: null, periods: 6, open_only: true };
		this.syncing = false;
		// Every rule in konsol_dashboard.css is scoped to this class: Frappe keeps the page's
		// <style> in the document after the page is left.
		this.$root = $("<div></div>").addClass("konsol-dashboard").appendTo(page.main);
		this.box = this.$root[0];
		this.box.setAttribute("aria-live", "polite");
		this.box.append(el("p", "kd-loading", "Loading the close…"));
		// Group and Periods live in the page head. Their options come from the server, so
		// render() fills them in.
		this.group_field = page.add_field({
			fieldtype: "Select",
			fieldname: "group",
			label: __("Group"),
			change: () => this.changed("group"),
		});
		this.periods_field = page.add_field({
			fieldtype: "Select",
			fieldname: "periods",
			label: __("Periods"),
			change: () => this.changed("periods"),
		});
		this.page.set_secondary_action(__("Refresh"), () => this.refresh());
		// No refresh() here: Frappe calls frappe.pages["konsol-dashboard"].refresh when the page
		// is shown, the first time included, so a call here would read the home twice.
	}

	changed(which) {
		if (this.syncing) return;
		// Frappe's set_value fires change after sync_field has cleared `syncing` (it is
		// async), so a refill from render() arrives here too. Reload only when the value
		// differs from the view render() just recorded; otherwise every render would
		// reload the page, forever (8 Nov 2026, found live on 8069).
		if (which === "group") {
			const group = this.group_field.get_value() || null;
			if (group === this.view.group) return;
			this.view.group = group;
		} else {
			const periods = Number(this.periods_field.get_value()) || this.view.periods;
			if (periods === this.view.periods) return;
			this.view.periods = periods;
		}
		this.refresh();
	}

	refresh() {
		const args = { periods: this.view.periods };
		if (this.view.group) args.group = this.view.group;
		return frappe
			.xcall(KONSOL_DASHBOARD_METHOD, args, "GET")
			.then((data) => {
				try {
					this.render(data);
				} catch (e) {
					this.fail(e.message);
				}
			})
			.catch((e) => this.fail(typeof e === "string" ? e : e && e.message));
	}

	fail(message) {
		this.box.replaceChildren(
			el("p", "kd-unavailable", "The close home could not be read: " + (message || "no reason given"))
		);
	}

	// Set a head selector's options and value without firing its change handler.
	sync_field(field, options, value, show) {
		this.syncing = true;
		field.df.options = options;
		field.refresh();
		field.set_value(value);
		field.$wrapper.toggle(show);
		this.syncing = false;
	}

	head(h) {
		const groups = (h.groups || []).map((g) => ({ label: g, value: g }));
		if (!h.group) groups.unshift({ label: __("Choose a group"), value: "" });
		this.sync_field(this.group_field, groups, h.group || "", groups.length > 1 || !!h.group);
		this.sync_field(
			this.periods_field,
			(h.choices || []).map((c) => ({ label: c + " periods", value: String(c) })),
			String(h.periods),
			true
		);
	}

	title(h) {
		const node = el("div", "kd-header");
		const sub = [h.group, h.period].filter(Boolean).join(" · ");
		node.append(el("span", "kd-subtitle", sub || h.period_reason || h.group_message || ""));
		return node;
	}

	render(data) {
		this.view.group = data.header.group || this.view.group;
		this.view.periods = data.header.periods;
		this.head(data.header);
		const nodes = [this.title(data.header), headline(data.headline), ...banners(data.banners)];
		nodes.push(el("div", "kd-section-label", "Close"));
		const grid = el("div", "kd-tiles");
		(data.tiles || []).forEach((t, i) => grid.append(tile(t, i)));
		nodes.push(grid);
		const lower = el("div", "kd-lower");
		lower.append(periodCard(data.period), entities(data.entities, this.view));
		nodes.push(lower);
		const pillNode = setupPill(data.setup);
		if (pillNode) nodes.push(pillNode);
		this.box.replaceChildren(...nodes);
	}
}
