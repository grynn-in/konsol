// konsol#103: the ERP feed proposes, a person accepts. "Pre-fill from ERP"
// creates DRAFT rates from what the ERP quotes for a period; nothing is
// submitted. Submitting a draft (EPM Admin) is the approval.
//
// konsol#584 (Deepak Pai, 22 Nov 2026): "Copy rates from previous period"
// creates DRAFT rates from the previous regular period's approved rates in
// one Rate Set (the set flagged Is Actuals is pre-selected). Same roles, same
// summary: nothing is submitted, and each draft is approved for its period.
const KONSOL_RATE_WRITERS = ["EPM Analyst", "EPM Admin", "System Manager"];

frappe.listview_settings["Group Exchange Rate"] = {
	onload(listview) {
		if (!frappe.user.has_role(KONSOL_RATE_WRITERS)) return;
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
							(out.warned || []).forEach((k) =>
								lines.push(__("Drafted, needs a Reason for Change before approval: {0}", [k]))
							);
							frappe.msgprint(lines.join("<br>"), __("Pre-fill from ERP"));
							listview.refresh();
						},
					});
				},
				__("Propose draft rates from the ERP feed"),
				__("Propose")
			);
		});
		listview.page.add_inner_button(__("Copy rates from previous period"), async () => {
			// Pre-select the actuals set; the user may pick a budget set instead.
			const actuals = await frappe.db.get_value("Rate Set", { is_actuals: 1 }, "name");
			frappe.prompt(
				[
					{ fieldname: "fiscal_year", fieldtype: "Int", label: __("Fiscal Year"), reqd: 1 },
					{ fieldname: "fiscal_period", fieldtype: "Int", label: __("Fiscal Period"), reqd: 1 },
					{
						fieldname: "rate_set", fieldtype: "Link", options: "Rate Set",
						label: __("Rate Set"), reqd: 1,
						default: (actuals && actuals.message && actuals.message.name) || "",
						description: __(
							"Its approved rates in the previous regular period become drafts for this period."
						),
					},
				],
				(values) => {
					frappe.call({
						method: "konsol.group_rates.copy_from_previous_period",
						type: "POST",
						args: values,
						freeze: true,
						callback: (r) => {
							const out = r.message || {};
							const lines = [
								__("Copied from {0}.", [out.from_period || ""]),
								__("{0} draft rate(s) proposed.", [(out.created || []).length]),
								__("{0} skipped.", [(out.skipped || []).length]),
								__("{0} refused.", [(out.refused || []).length]),
							];
							(out.skipped || []).forEach((k) => lines.push(__("Skipped: {0}", [k])));
							(out.refused || []).forEach((k) => lines.push(__("Refused: {0}", [k])));
							(out.warned || []).forEach((k) =>
								lines.push(__("Drafted, needs a Reason for Change before approval: {0}", [k]))
							);
							frappe.msgprint(lines.join("<br>"), __("Copy rates from previous period"));
							listview.refresh();
						},
					});
				},
				__("Propose draft rates from the previous regular period"),
				__("Propose")
			);
		});
	},
};
