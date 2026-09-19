frappe.listview_settings["Assertion Run"] = {
	get_indicator(doc) {
		const map = {
			Green: ["Green", "green", "status,=,Green"],
			// konsol#265: warnings, nothing failed — signable, with a note.
			Amber: ["Amber", "yellow", "status,=,Amber"],
			Red: ["Red", "red", "status,=,Red"],
			Running: ["Running", "orange", "status,=,Running"],
			Error: ["Error", "red", "status,=,Error"],
			Queued: ["Queued", "gray", "status,=,Queued"],
		};
		return map[doc.status] || ["Unknown", "gray", ""];
	},
};
