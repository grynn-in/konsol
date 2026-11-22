// konsol#592 T14: the Rate Sheet list. The status indicator, and "New from
// previous", which drafts a sheet from the previous approved one. It writes,
// so it POSTs.
const KONSOL_RATE_SHEET_WRITERS = ["EPM Analyst", "EPM Admin", "System Manager"];

frappe.listview_settings["Rate Sheet"] = {
	add_fields: ["status", "docstatus"],
	get_indicator(doc) {
		const map = {
			Draft: ["Draft", "red", "status,=,Draft"],
			"Pending Approval": ["Pending Approval", "orange", "status,=,Pending Approval"],
			Approved: ["Approved", "green", "status,=,Approved"],
			Cancelled: ["Cancelled", "gray", "status,=,Cancelled"],
		};
		return map[doc.status] || ["Draft", "red", "status,=,Draft"];
	},
	onload(listview) {
		if (!frappe.user.has_role(KONSOL_RATE_SHEET_WRITERS)) return;
		listview.page.add_inner_button(__("New from previous"), () => {
			frappe.prompt(
				[
					{ fieldname: "rate_basis", fieldtype: "Select", label: __("Rate Basis"), reqd: 1,
						options: "Statutory\nConstant", default: "Statutory" },
					{ fieldname: "fiscal_year", fieldtype: "Int", label: __("Fiscal Year"), reqd: 1 },
					{ fieldname: "fiscal_period", fieldtype: "Int", label: __("Fiscal Period"),
						description: __("Statutory only."), depends_on: "eval:doc.rate_basis == 'Statutory'" },
				],
				(values) => {
					frappe.call({
						method: "konsol.group_rates.duplicate_previous",
						type: "POST",
						args: values,
						freeze: true,
						callback: (r) => {
							const out = r.message || {};
							if (out.name) {
								frappe.show_alert({
									message: __("Draft {0} created from {1}.", [out.name, out.copied_from]),
									indicator: "green",
								});
								frappe.set_route("Form", "Rate Sheet", out.name);
							}
						},
					});
				},
				__("Draft a sheet from the previous approved one"),
				__("Create draft")
			);
		});
	},
};
