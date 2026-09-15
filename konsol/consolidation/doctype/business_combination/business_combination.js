// Business Combination — Get Balances from Trial Balance (konsol#207)
//
// A Draft deal takes its Acquired Balance Sheet from the acquired entity's
// warehouse trial balance through the acquisition period, on request. The
// server replaces the lines, places the Fair Value Adjustment Total and saves.
frappe.ui.form.on("Business Combination", {
	refresh(frm) {
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.add_custom_button(__("Get Balances from Trial Balance"), () => {
				const n = (frm.doc.acquired_balances || []).length;
				const run = () =>
					frappe.call({
						method: "konsol.consolidation.doctype.business_combination.business_combination.get_balances_from_trial_balance",
						args: { name: frm.doc.name },
						freeze: true,
						callback: () => frm.reload_doc(),
					});
				if (n) {
					frappe.confirm(
						__("This replaces the {0} lines of the Acquired Balance Sheet. Continue?", [n]),
						run
					);
				} else {
					run();
				}
			});
		}
	},
});
