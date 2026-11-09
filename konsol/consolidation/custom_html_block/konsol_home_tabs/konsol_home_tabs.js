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

function panel(name) {
	return () => {
		const div = document.createElement("div");
		div.className = "khp-panel khp-" + name;
		return div;
	};
}

const tabs = new frappe.ui.Tabs({
	tabs: [
		{ label: "Close", content: panel("close") },
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
