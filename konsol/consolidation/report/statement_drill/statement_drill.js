// Statement Drill — one statement heading broken into layers (each entity,
// eliminations, CTA, top-side journals, deal rows), its accounts and its
// source documents. Opened from a Consolidated Statement heading.
// Frappe v16 runs report JS as an IIFE: assign the report explicitly.
// The Frappe query report checks only `reqd` before a run (conditional-mandatory
// settings are ignored), so `reqd` is set on the filter the basis needs: a blank
// one shows Frappe's inline "Please set filters", never a server error dialog.
// Two entry points: onload (a report opened from a link: Frappe sets the route
// filters, calls onload, then refreshes, so onload must not refresh) and the Basis
// filter's on_change (Frappe calls it INSTEAD of its own refresh, so it refreshes).
function sync_required(report) {
	const entity_basis = report.get_filter_value("basis") === "Entity";
	for (const [fieldname, required] of [["entity", entity_basis], ["consolidation_group", !entity_basis]]) {
		const filter = report.get_filter(fieldname);
		filter.df.reqd = required ? 1 : 0;
		filter.refresh();
	}
}

function on_basis_change() {
	sync_required(frappe.query_report);
	frappe.query_report.refresh();
}

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
			fieldname: "basis",
			label: __("Basis"),
			fieldtype: "Select",
			options: "Group\nEntity",
			default: "Group",
			description: __("Entity: one entity's own trial balance in its functional currency."),
			on_change: on_basis_change,
		},
		{
			fieldname: "entity",
			label: __("Entity"),
			fieldtype: "Link",
			options: "Entity",
			depends_on: 'eval:doc.basis=="Entity"',
			get_query() {
				return { filters: { is_group: 0 } };
			},
		},
		{
			fieldname: "consolidation_group",
			label: __("Consolidation Group"),
			fieldtype: "Link",
			options: "Consolidation Group",
			depends_on: 'eval:doc.basis!="Entity"',
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

	onload: sync_required,

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
