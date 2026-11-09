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

function group(div, type, list) {
	new frappe.widget.WidgetGroup({
		container: $(div),
		type,
		columns: 3,
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

function fillClose(div) {
	widgets(div, (defs) => {
		group(div, "number_card", items(defs.number_cards));
		const have = items(defs.shortcuts);
		const monthly = MONTHLY_CLOSE.map((l) => have.find((s) => s.label === l)).filter(Boolean);
		group(div, "shortcut", monthly);
	});
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
	frappe.set_route(...m[1].split("/").filter(Boolean).map(decodeURIComponent));
});

const tabs = new frappe.ui.Tabs({
	tabs: [
		{ label: "Close", content: panel("close", fillClose) },
		{ label: "Approvals", content: panel("approvals") },
		{ label: "Trends", content: panel("trends") },
		{ label: "Numbers", content: panel("numbers") },
	],
	active: loadTab(),
	on_change(i) {
		saveTab(i);
	},
});
box.appendChild(tabs.$el);
