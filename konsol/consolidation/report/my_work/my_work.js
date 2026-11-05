// My Work — the Close app's My work list in Desk. Read-only.
// Frappe v16 runs report JS as an IIFE: assign the report explicitly.
// "Open" is the Desk path mywork_report_model chose; a row with none shows no link.
frappe.query_reports["My Work"] = {
	filters: [],

	formatter(value, row, column, data, default_formatter) {
		if (data && column.fieldname === "open") {
			return data.open ? `<a href="${frappe.utils.escape_html(data.open)}">${__("Open")}</a>` : "";
		}
		value = default_formatter(value, row, column, data);
		if (data && column.fieldname === "kind") {
			const colour = { Blocking: "red", "To do": "blue", Waiting: "gray" }[data.kind];
			if (colour) {
				value = `<span class="indicator-pill ${colour}">${value}</span>`;
			}
		}
		return value;
	},
};
