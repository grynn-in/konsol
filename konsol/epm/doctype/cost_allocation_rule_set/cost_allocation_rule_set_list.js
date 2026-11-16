// Colour each Cost Allocation Rule Set row by status.
frappe.listview_settings["Cost Allocation Rule Set"] = {
	get_indicator(doc) {
		const color = { Draft: "orange", Active: "green", Inactive: "gray" }[doc.status];
		return [__(doc.status), color, "status,=," + doc.status];
	},
};
