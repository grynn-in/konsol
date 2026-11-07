frappe.provide("frappe.dashboards.chart_sources");

// konsol#407 phase 2: a Custom chart; its method checks the caller's roles and
// read permission and reads Entity on every call (never a cached group-by chart).
frappe.dashboards.chart_sources["Konsol Entities by Status"] = {
	method: "konsol.close.charts_api.entities_by_status",
	filters: [],
};
