frappe.provide("frappe.dashboards.chart_sources");

// konsol#407 phase 2: a Custom chart; its method checks the caller's roles and
// read permission and reads the consolidated statement of the default consolidation group on every call (never a cached group-by chart).
frappe.dashboards.chart_sources["Konsol Net Sales and Net Result"] = {
	method: "konsol.close.charts_api.net_sales_and_result",
	filters: [],
};
