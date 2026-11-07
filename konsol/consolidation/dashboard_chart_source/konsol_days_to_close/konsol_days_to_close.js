frappe.provide("frappe.dashboards.chart_sources");

// konsol#407 phase 2: a Custom chart; its method checks the caller's roles and
// read permission and reads the signed close runs against each period's end date on every call (never a cached group-by chart).
frappe.dashboards.chart_sources["Konsol Days to Close"] = {
	method: "konsol.close.charts_api.days_to_close",
	filters: [],
};
