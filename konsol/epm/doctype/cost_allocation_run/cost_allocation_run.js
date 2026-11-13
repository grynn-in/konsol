frappe.ui.form.on("Cost Allocation Run", {
	refresh(frm) {
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.add_custom_button(__("Compute Weights"), () => {
				frappe.call({
					method: "konsol.epm.doctype.cost_allocation_run.cost_allocation_run.compute_weights",
					type: "POST",
					args: { name: frm.doc.name },
					freeze: true,
					callback: (r) => {
						const n = (r.message || {}).carried_forward_count || 0;
						if (n) {
							frappe.show_alert({
								message: __("{0} driver values were carried forward.", [n]),
								indicator: "orange",
							});
						}
						frm.reload_doc();
					},
				});
			});
		}
	},
});
