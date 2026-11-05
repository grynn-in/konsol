// Statement Drill — one statement heading broken into layers (each entity,
// eliminations, CTA, top-side journals, deal rows), its accounts and its
// source documents. Opened from a Consolidated Statement heading.
// Frappe v16 runs report JS as an IIFE: assign the report explicitly.
frappe.query_reports["Statement Drill"] = {
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
			reqd: 1,
			description: __("A root group."),
			// Root groups only (statement_api._ROOT_FILTER: no data_area_id).
			get_query() {
				return { filters: { data_area_id: ["is", "not set"] } };
			},
		},
		{
			fieldname: "heading",
			label: __("Statement Heading"),
			fieldtype: "Link",
			options: "Main Account",
			reqd: 1,
			// A Published group heading of the statement (get_drill refuses
			// anything else, naming it).
			get_query() {
				return {
					filters: { is_group: 1, status: "Published", statement_section: ["is", "set"] },
				};
			},
		},
	],

	// Layers open; accounts and journals one click away.
	initial_depth: 0,

	formatter(value, row, column, data, default_formatter) {
		if (data && column.fieldname === "label") {
			// Labels carry journal descriptions (free text): escaped here.
			value = frappe.utils.escape_html(data.label || "");
		} else {
			value = default_formatter(value, row, column, data);
		}
		if (data && data.bold) {
			value = `<b>${value}</b>`;
		}
		return value;
	},
};
