// Close Assertions — reconciliation board filters
frappe.query_reports["Close Assertions"] = {
	filters: [
		{
			fieldname: "close_run",
			label: __("Assertion Run"),
			fieldtype: "Link",
			options: "Assertion Run",
			reqd: 0,
			description: __("Leave blank for the latest completed run"),
		},
		{ fieldname: "fiscal_year", label: __("Fiscal Year"), fieldtype: "Int" },
		{ fieldname: "fiscal_period", label: __("Fiscal Period"), fieldtype: "Int" },
	],

	// Run checks: the close app's own endpoint (checks_api.run_checks, POST),
	// for the period chosen here (the filters' period by default). The server
	// refuses a period that is not Open or a second run in flight; its message
	// is shown as written.
	onload(report) {
		report.page.add_inner_button(__("Run checks"), () => {
			const fy = report.get_filter_value("fiscal_year");
			const fp = report.get_filter_value("fiscal_period");
			frappe.prompt(
				[
					{
						fieldname: "what",
						fieldtype: "HTML",
						options: `<p>${__(
							"The close checks for the period start now and run in the background. When they finish, the new run is the one the sign-off reads, and it starts Not signed. Refresh this report to see the result.",
						)}</p>`,
					},
					{ fieldname: "fiscal_year", label: __("Fiscal Year"), fieldtype: "Int", reqd: 1, default: fy || new Date().getFullYear() },
					{ fieldname: "fiscal_period", label: __("Fiscal Period"), fieldtype: "Int", reqd: 1, default: fp || null },
				],
				(v) =>
					frappe.call({
						method: "konsol.close.checks_api.run_checks",
						type: "POST",
						args: { fiscal_year: v.fiscal_year, fiscal_period: v.fiscal_period },
						freeze: true,
						freeze_message: __("Starting the checks…"),
						callback(r) {
							frappe.show_alert(
								{ message: __("Checks started: {0}", [frappe.utils.escape_html(r.message)]), indicator: "green" },
								7,
							);
							report.refresh();
						},
					}),
				__("Run checks"),
				__("Run checks"),
			);
		});
	},

	// red/green the Result cell
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data) {
			const color = data.status === "Pass" ? "green" : "red";
			value = `<span class="indicator ${color}">${__(data.status)}</span>`;
		}
		return value;
	},
};
