// Forecast Accuracy (konsol#348 S6 KC3, FC-4-2): every approved forecast round
// against the actuals of the closed periods after its as-of date, in entity
// currency. Read-only.
// Frappe v16 runs report JS as an IIFE: assign the report explicitly.
frappe.query_reports["Forecast Accuracy"] = {
	filters: [
		{
			fieldname: "scenario_id",
			label: __("Scenario"),
			fieldtype: "Link",
			options: "Scenario",
			get_query: () => ({ filters: { scenario_type: "forecast" } }),
		},
		{
			fieldname: "cycle",
			label: __("Round"),
			fieldtype: "Link",
			options: "Budget Cycle",
			get_query: () => {
				const filters = { scenario_type: "forecast", docstatus: 1 };
				const scenario = frappe.query_report.get_filter_value("scenario_id");
				if (scenario) {
					filters.scenario_id = scenario;
				}
				return { filters };
			},
		},
		{
			fieldname: "entity",
			label: __("Entity"),
			fieldtype: "Link",
			options: "Entity",
		},
		{
			fieldname: "lag",
			label: __("Lag (periods)"),
			fieldtype: "Int",
			description: __("Regular periods after the round's as-of period. Blank shows every lag."),
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && column.fieldname === "forecast_error_pct" && data.forecast_error_pct === "n/a") {
			value = `<span class="text-muted">${value}</span>`;
		}
		return value;
	},
};
