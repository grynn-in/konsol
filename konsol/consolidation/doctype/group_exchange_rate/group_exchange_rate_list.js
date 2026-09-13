// konsol#103: the ERP feed proposes, a person accepts. "Pre-fill from ERP"
// creates DRAFT rates from what the ERP quotes for a period; nothing is
// submitted. Submitting a draft (EPM Admin) is the approval.
frappe.listview_settings["Group Exchange Rate"] = {
	onload(listview) {
		if (!frappe.user.has_role(["EPM Analyst", "EPM Admin", "System Manager"])) return;
		listview.page.add_inner_button(__("Pre-fill from ERP"), () => {
			frappe.prompt(
				[
					{ fieldname: "fiscal_year", fieldtype: "Int", label: __("Fiscal Year"), reqd: 1 },
					{ fieldname: "fiscal_period", fieldtype: "Int", label: __("Fiscal Period"), reqd: 1 },
				],
				(values) => {
					frappe.call({
						method: "konsol.group_rates.prefill_from_erp",
						type: "POST",
						args: values,
						freeze: true,
						callback: (r) => {
							const out = r.message || {};
							const lines = [
								__("{0} draft rate(s) proposed.", [(out.created || []).length]),
								__("{0} already had a draft or approved rate.", [(out.existing || []).length]),
							];
							(out.no_quote || []).forEach((k) => lines.push(__("No ERP quote: {0}", [k])));
							(out.refused || []).forEach((k) => lines.push(__("Refused: {0}", [k])));
							frappe.msgprint(lines.join("<br>"), __("Pre-fill from ERP"));
							listview.refresh();
						},
					});
				},
				__("Propose draft rates from the ERP feed"),
				__("Propose")
			);
		});
	},
};
