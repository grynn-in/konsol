// Konsol Home Tabs (konsol#497, row 2: skeleton). Four tabs on the Konsol
// home: Close, Approvals, Trends, Numbers. Close is the default. Each panel is
// built lazily by frappe.ui.Tabs on first activation and starts empty; later
// rows fill them. Nothing here calls the server at construction.
// Frappe runs this inside the block's shadow root (root_element).
const box = root_element.querySelector(".konsol-home-tabs");

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
	active: 0,
	on_change() {},
});
box.appendChild(tabs.$el);
