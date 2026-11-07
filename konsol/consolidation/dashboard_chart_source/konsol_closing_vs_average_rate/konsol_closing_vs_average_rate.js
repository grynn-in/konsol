frappe.provide("frappe.dashboards.chart_sources");

// konsol#407 phase 2: a Custom chart; its method checks the caller's roles and
// read permission and reads approved group exchange rates into the group reporting currency on every call (never a cached group-by chart).
frappe.dashboards.chart_sources["Konsol Closing vs Average Rate"] = {
	method: "konsol.close.charts_api.closing_vs_average_rate",
	filters: [],
};
