frappe.provide("frappe.dashboards.chart_sources");

// konsol#407 phase 2: a Custom chart; its method checks the caller's roles and
// read permission and reads the sign-off gate's expected and missing trial balances, cut to your entities on every call (never a cached group-by chart).
frappe.dashboards.chart_sources["Konsol TB Submission Progress"] = {
	method: "konsol.close.charts_api.tb_submission_progress",
	filters: [],
};
