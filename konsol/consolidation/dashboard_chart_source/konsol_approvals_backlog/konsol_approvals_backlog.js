frappe.provide("frappe.dashboards.chart_sources");

// konsol#407 phase 2: a Custom chart; its method checks the caller's roles and
// read permission and reads the approvals queue on every call (never a cached group-by chart).
frappe.dashboards.chart_sources["Konsol Approvals Backlog"] = {
	method: "konsol.close.charts_api.approvals_backlog",
	filters: [],
};
