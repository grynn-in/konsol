// Konsol Guide Page list: Published or Draft (konsol#407 phase 3).
frappe.listview_settings["Konsol Guide Page"] = {
	add_fields: ["published"],
	get_indicator(doc) {
		return doc.published
			? [__("Published"), "green", "published,=,1"]
			: [__("Draft"), "gray", "published,=,0"];
	},
};
