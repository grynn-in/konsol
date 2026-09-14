// Colour each EPM Fiscal Year row by status. Colours match konsol-exec's
// PERIOD_THEME (src/home.js) so the desk list view and the konsol-exec home
// page agree on what Open/Closed/Locked look like.
frappe.listview_settings["EPM Fiscal Year"] = {
	get_indicator(doc) {
		const color = { Open: "blue", Closed: "green", Locked: "gray" }[doc.status];
		return [__(doc.status), color, "status,=," + doc.status];
	},
};
