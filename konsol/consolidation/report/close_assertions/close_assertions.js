// Close Assertions — reconciliation board filters
frappe.query_reports["Close Assertions"] = {
	filters: [
		{
			fieldname: "close_run",
			label: __("Close Run"),
			fieldtype: "Link",
			options: "Close Run",
			reqd: 0,
			description: __("Leave blank for the latest completed run"),
		},
		{ fieldname: "fiscal_year", label: __("Fiscal Year"), fieldtype: "Int" },
		{ fieldname: "fiscal_period", label: __("Fiscal Period"), fieldtype: "Int" },
	],

	// red/green the Result cell
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data) {
			const color = data.status === "Pass" ? "green" : "red";
			value = `<span class="indicator ${color}">${data.status}</span>`;
		}
		return value;
	},
};
