frappe.listview_settings["Close Run"] = {
	get_indicator(doc) {
		const map = {
			Green: ["Green", "green", "status,=,Green"],
			Red: ["Red", "red", "status,=,Red"],
			Running: ["Running", "orange", "status,=,Running"],
			Error: ["Error", "red", "status,=,Error"],
			Queued: ["Queued", "gray", "status,=,Queued"],
		};
		return map[doc.status] || ["Unknown", "gray", ""];
	},
};
