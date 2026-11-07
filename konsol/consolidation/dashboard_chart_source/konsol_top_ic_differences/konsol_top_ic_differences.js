frappe.provide("frappe.dashboards.chart_sources");

// konsol#407 phase 2: a Custom chart; its method checks the caller's roles and
// read permission and reads the intercompany reconciliation of the current period, masked to your entities on every call (never a cached group-by chart).
frappe.dashboards.chart_sources["Konsol Top IC Differences"] = {
	method: "konsol.close.charts_api.top_ic_differences",
	filters: [],
};
