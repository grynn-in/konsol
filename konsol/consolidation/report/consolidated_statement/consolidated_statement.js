// Consolidated Statement — the Numbers statement (P&L and Balance Sheet) in Desk.
// Frappe v16 runs report JS as an IIFE: assign the report explicitly.
frappe.query_reports["Consolidated Statement"] = {
	filters: [
		{
			fieldname: "fiscal_year",
			label: __("Fiscal Year"),
			fieldtype: "Int",
			reqd: 1,
			default: new Date().getFullYear(),
		},
		{
			fieldname: "fiscal_period",
			label: __("Fiscal Period"),
			fieldtype: "Int",
			reqd: 1,
			default: new Date().getMonth() + 1,
		},
		{
			fieldname: "consolidation_group",
			label: __("Consolidation Group"),
			fieldtype: "Link",
			options: "Consolidation Group",
			description: __("A root group. Leave blank when there is only one."),
			// Root groups only (statement_api._ROOT_FILTER: no data_area_id).
			get_query() {
				return { filters: { data_area_id: ["is", "not set"] } };
			},
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) {
			return value;
		}
		if (column.fieldname === "label" && data.indent) {
			value = `<span style="padding-left: ${data.indent * 2}em">${value}</span>`;
		}
		if (data.bold) {
			value = `<b>${value}</b>`;
		}
		return value;
	},
};
