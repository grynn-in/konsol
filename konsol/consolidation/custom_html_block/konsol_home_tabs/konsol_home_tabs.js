// Konsol Home Tabs (konsol#497, row 2: skeleton). Four tabs on the Konsol
// home: Close, Approvals, Trends, Numbers. Close is the default. Each panel is
// built lazily by frappe.ui.Tabs on first activation and starts empty; later
// rows fill them. Nothing here calls the server at construction.
// Frappe runs this inside the block's shadow root (root_element).
const box = root_element.querySelector(".konsol-home-tabs");

// Last tab, per user and browser (konsol#497 spike Q7: localStorage costs no
// request on load or per click). Swap these two functions to move it to
// user_settings later.
const TAB_COUNT = 4;
function tabKey() {
	return "konsol-home-tab:" + frappe.session.user;
}
function loadTab() {
	try {
		const raw = localStorage.getItem(tabKey());
		if (typeof raw !== "string" || !/^[0-9]+$/.test(raw)) return 0;
		const i = parseInt(raw, 10);
		return i < TAB_COUNT ? i : 0;
	} catch (e) {
		return 0;
	}
}
function saveTab(i) {
	try {
		localStorage.setItem(tabKey(), String(i));
	} catch (e) {
		// private mode: the choice is simply not remembered
	}
}

// One definitions call for the whole block, shared by every panel (konsol#497
// spike Q5). It starts on the first panel activation, never at construction.
// A failed call is forgotten so the next activation can try again.
let defsPromise = null;
function getDefs() {
	if (!defsPromise) {
		defsPromise = frappe
			.xcall(
				"frappe.desk.desktop.get_desktop_page",
				{ page: JSON.stringify({ name: "Konsol", title: "Konsol", public: 1 }) },
				"GET"
			)
			.catch((e) => {
				defsPromise = null;
				throw e;
			});
	}
	return defsPromise;
}
const items = (x) => (Array.isArray(x) ? x : (x && x.items) || []);

// The Monthly close shortcuts, in the order the Close panel shows them. A
// shortcut the user may not open is absent from get_desktop_page and is skipped.
const MONTHLY_CLOSE = [
	"My Work",
	"Period Close",
	"Consolidated Statement",
	"Trial Balance Uploads",
	"Trial Balance Submissions",
	"Close Assertions",
];

// The waiting shortcuts, in the workspace's order (konsol.json, "{} waiting").
const WAITING = [
	"Consolidation Journals",
	"Business Combinations",
	"Business Disposals",
	"Group Exchange Rates",
	"Ownership Periods",
	"Historical Equity Rates",
	"IC Balances",
	"Build Approvals",
];

// The Trends charts, in the workspace's order (konsol.json). TB Submission
// Progress is full width; every chart in one WidgetGroup has the same width
// (spike Q3), so it gets its own one-column group and the rest share a two-column one.
const FULL_WIDTH_CHART = "TB Submission Progress";
const CHARTS = [
	"TB Submission Progress",
	"Net Sales and Net Result",
	"Top IC Differences",
	"Approvals Backlog",
	"Closing vs Average Rate",
	"Entities by Status",
	"Days to Close",
];

function errorLine(div, e) {
	const line = document.createElement("div");
	line.className = "khp-error text-muted";
	const why = (e && (e.message || e.statusText)) || String(e);
	line.textContent = "Cannot read: " + why;
	div.appendChild(line);
}

// Widgets are created after the panel is attached: the content fn returns a
// detached div (spike Q3), so wait one frame.
function widgets(div, build) {
	getDefs()
		.then((defs) => {
			requestAnimationFrame(() => {
				try {
					build(defs);
				} catch (e) {
					errorLine(div, e);
				}
			});
		})
		.catch((e) => errorLine(div, e));
}

function group(div, type, list, columns) {
	new frappe.widget.WidgetGroup({
		container: $(div),
		type,
		columns: columns || 3,
		widgets: list,
		options: {},
	});
}

function panel(name, fill) {
	return () => {
		const div = document.createElement("div");
		div.className = "khp-panel khp-" + name;
		if (fill) fill(div);
		return div;
	};
}

// The period close checklist (copied from konsol_close_checklist.js: a block
// cannot be nested inside this shadow root, spike Q6). Every server string is
// set as textContent; a line links only to a /desk/ route, and in-app clicks
// go through the delegated handler below.
const CHECKLIST_METHOD = "konsol.close.overview_api.get_checklist";
const GLYPH = { ok: "\u2713", warn: "!", blocked: "\u2715", unknown: "?" };
const TONE = { ok: "green", warn: "orange", blocked: "red", unknown: "gray" };

function el(tag, cls, text) {
	const node = document.createElement(tag);
	if (cls) node.className = cls;
	if (text !== undefined && text !== null) node.textContent = String(text);
	return node;
}

function deskRoute(route) {
	return typeof route === "string" && route.startsWith("/desk/") ? route : null;
}

function renderChecklist(target, data) {
	const head = el("div", "kc-head");
	head.append(el("span", "kc-title", "Period close \u00b7 " + data.title));
	if (data.total) {
		// One clamped value drives the text, the bar and its aria state.
		const done = Math.min(data.total, Math.max(0, data.done));
		const count = el("span", "kc-count", `${done} of ${data.total} done`);
		count.id = "kc-count";
		head.append(count);
		const bar = el("div", "kc-progress");
		bar.setAttribute("role", "progressbar");
		bar.setAttribute("aria-labelledby", count.id);
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
	target.replaceChildren(...nodes);
}

function checklistFailed(target, message) {
	target.replaceChildren(
		el("p", "kc-error", "The period close checklist could not be read: " + (message || "no reason given"))
	);
}

// One GET per Close activation (the content fn runs once), never at construction.
function fillChecklist(target) {
	target.appendChild(el("p", "kc-loading", "Loading the period close checklist\u2026"));
	frappe
		.xcall(CHECKLIST_METHOD, {}, "GET")
		.then((data) => {
			try {
				renderChecklist(target, data);
			} catch (e) {
				checklistFailed(target, e.message);
			}
		})
		.catch((e) => checklistFailed(target, typeof e === "string" ? e : e && e.message));
}

// The #484 headline: "N items need you", from get_my_work counts. A missing
// count is an error line, never "0" or "Nothing needs you". Start opens My Work
// through the delegated click handler below. One GET per Close activation.
const MY_WORK_METHOD = "konsol.close.mywork_api.get_my_work";
const MY_WORK_ROUTE = "/desk/query-report/My%20Work";

function headlineText(n) {
	if (n === 0) return "Nothing needs you";
	return n === 1 ? "1 item needs you" : n + " items need you";
}

function renderHeadline(target, work) {
	const counts = work && work.counts;
	const isCount = (v) => typeof v === "number" && Number.isFinite(v) && v >= 0;
	if (!counts || !isCount(counts.blocking)) throw new Error("My Work gave no blocking count");
	const todo = isCount(counts.todo) ? counts.todo : 0;
	const waiting = isCount(counts.waiting) ? counts.waiting : 0;
	const start = el("a", "khp-start", "Start");
	start.href = MY_WORK_ROUTE;
	target.replaceChildren(
		el("span", "khp-headline-text", headlineText(counts.blocking)),
		el("span", "khp-headline-detail", `${todo} to do \u00b7 ${waiting} waiting`),
		start
	);
}

function fillHeadline(target) {
	frappe
		.xcall(MY_WORK_METHOD, {}, "GET")
		.then((work) => {
			try {
				renderHeadline(target, work);
			} catch (e) {
				target.replaceChildren();
				errorLine(target, e);
			}
		})
		.catch((e) => {
			target.replaceChildren();
			errorLine(target, e);
		});
}

// Close panel order, as the old workspace had it: headline, cards, checklist, Monthly close.
function fillClose(div) {
	const headline = el("div", "khp-headline");
	const cards = el("div", "khp-cards");
	const checklist = el("section", "khp-checklist konsol-checklist");
	const monthly = el("div", "khp-monthly");
	div.append(headline, cards, checklist, monthly);
	fillHeadline(headline);
	fillChecklist(checklist);
	widgets(div, (defs) => {
		group(cards, "number_card", items(defs.number_cards));
		const have = items(defs.shortcuts);
		const list = MONTHLY_CLOSE.map((l) => have.find((s) => s.label === l)).filter(Boolean);
		group(monthly, "shortcut", list);
	});
}

// Approvals panel: the waiting shortcuts, with their counts. A shortcut the
// user may not open is absent from get_desktop_page and is skipped.
function fillApprovals(div) {
	widgets(div, (defs) => {
		const have = items(defs.shortcuts);
		group(div, "shortcut", WAITING.map((l) => have.find((s) => s.label === l)).filter(Boolean));
	});
}

// Trends panel: the charts the user may see, created after the panel is
// attached (rAF in widgets). The chart "..." menu is hidden by CSS already.
function fillTrends(div) {
	widgets(div, (defs) => {
		const have = items(defs.charts);
		const nameOf = (c) => c.chart_name || c.label;
		const pick = (n) => have.find((c) => nameOf(c) === n);
		const full = pick(FULL_WIDTH_CHART);
		if (full) {
			const one = el("div", "khp-chart-full");
			div.appendChild(one);
			group(one, "chart", [full], 1);
		}
		const rest = CHARTS.filter((n) => n !== FULL_WIDTH_CHART).map(pick).filter(Boolean);
		if (rest.length) {
			const two = el("div", "khp-chart-grid");
			div.appendChild(two);
			group(two, "chart", rest, 2);
		}
	});
}

// Numbers panel: four report links, no server call. All go through the
// delegated click handler below. The entity statement carries route options in
// data-route-options (a plain href cannot), which the handler sets on
// frappe.route_options just before set_route (konsol#495 Basis filter).
const NUMBERS = [
	["Consolidated Statement", "Consolidated Statement", "The group statement of financial position and performance.", null],
	["Entity statement", "Consolidated Statement", "The same statement for one entity, before consolidation.", { basis: "Entity" }],
	["Statement Drill", "Statement Drill", "Trace a statement line back to its accounts and entries.", null],
	["Consolidation Worksheet", "Consolidation Worksheet", "How entity balances become the group figures.", null],
];

function fillNumbers(div) {
	for (const [label, report, description, routeOptions] of NUMBERS) {
		const row = el("div", "khp-number");
		const link = el("a", "khp-number-link", label);
		link.href = "/desk/query-report/" + encodeURIComponent(report);
		if (routeOptions) link.setAttribute("data-route-options", JSON.stringify(routeOptions));
		row.append(link, el("span", "khp-number-detail", description));
		div.appendChild(row);
	}
}

// Links inside the shadow root are invisible to Frappe's router, so an <a href>
// does a full page load (#484 failure 3). Route in-app links ourselves.
root_element.addEventListener("click", (e) => {
	if (e.defaultPrevented || e.button || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
	const a = e.target && e.target.closest && e.target.closest("a[href]");
	if (!a) return;
	const href = a.getAttribute("href");
	if (href.charAt(0) === "#") return;
	let url;
	try {
		url = new URL(href, location.href);
	} catch (err) {
		return;
	}
	if (url.origin !== location.origin) return;
	const m = url.pathname.match(/^\/(?:desk|app)\/(.+)$/);
	if (!m) return;
	e.preventDefault();
	// Options ride on the link (data-route-options) and are set just before routing.
	let options = null;
	try {
		options = a.dataset && a.dataset.routeOptions ? JSON.parse(a.dataset.routeOptions) : null;
	} catch (err) {
		options = null;
	}
	if (options && typeof options === "object") frappe.route_options = options;
	frappe.set_route(...m[1].split("/").filter(Boolean).map(decodeURIComponent));
});

const tabs = new frappe.ui.Tabs({
	tabs: [
		{ label: "Close", content: panel("close", fillClose) },
		{ label: "Approvals", content: panel("approvals", fillApprovals) },
		{ label: "Trends", content: panel("trends", fillTrends) },
		{ label: "Numbers", content: panel("numbers", fillNumbers) },
	],
	active: loadTab(),
	on_change(i) {
		saveTab(i);
	},
});
box.appendChild(tabs.$el);
