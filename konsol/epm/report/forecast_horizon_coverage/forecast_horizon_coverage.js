// Forecast Horizon Coverage (konsol#348 S6 KA9): one row per forecast round, with
// whether every period its horizon reaches has an approved Budget Cycle. Read-only.
// Frappe v16 runs report JS as an IIFE: assign the report explicitly.
frappe.query_reports["Forecast Horizon Coverage"] = {
	filters: [
		{
			fieldname: "scenario_id",
			label: __("Scenario"),
			fieldtype: "Link",
			options: "Scenario",
			get_query: () => ({ filters: { scenario_type: "forecast" } }),
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && column.fieldname === "state_label") {
			const colour = { ok: "green", uncovered: "orange", undeclared_as_of: "red" }[data.state];
			if (colour) {
				value = `<span class="indicator-pill ${colour}">${value}</span>`;
			}
		}
		return value;
	},
};
