// Consolidation Worksheet — the trial balance side by side in Desk.
// Rows are accounts; columns are entities, eliminations, adjustments and
// Consolidated. All numbers come from tb_worksheet_api.get_worksheet via the
// report's execute; this script holds filters and formatting only.
// Frappe v16 runs report JS as an IIFE: assign the report explicitly.
frappe.query_reports["Consolidation Worksheet"] = {
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
		{
			fieldname: "entities",
			label: __("Entities"),
			fieldtype: "MultiSelectList",
			description: __("Leave blank to show every entity. Others are summed into one column."),
			get_data(txt) {
				return frappe.db.get_link_options("Entity", txt);
			},
		},
		{
			fieldname: "dimension",
			label: __("Dimension"),
			fieldtype: "Link",
			options: "Dimension",
			description: __("Optional: split each account by this dimension's values."),
			get_query() {
				return { filters: { status: "Published", in_trial_balance: 1 } };
			},
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && data.bold) {
			value = `<b>${value}</b>`;
		}
		return value;
	},
};
