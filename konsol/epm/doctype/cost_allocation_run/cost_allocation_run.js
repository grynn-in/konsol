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
			if ((frm.doc.weights || []).length) {
				frm.add_custom_button(__("Preview Sender Pools"), () => {
					frappe.call({
						method: "konsol.epm.doctype.cost_allocation_run.cost_allocation_run.preview_sender_pools",
						type: "GET",
						args: { name: frm.doc.name },
						freeze: true,
						callback: (r) => {
							const m = r.message || {};
							const rows = (m.pools || []).map((p) =>
								`<tr><td>${frappe.utils.escape_html(p.sender_member)}</td>` +
								`<td style="text-align:right">${format_currency(p.amount)}</td></tr>`
							).join("");
							const note = m.has_rows ? "" :
								`<p class="text-muted">${__("The warehouse has no rows for these accounts, scenario and period, so every pool is zero.")}</p>`;
							const d = new frappe.ui.Dialog({
								title: __("Sender Pools"),
								fields: [{
									fieldtype: "HTML", fieldname: "pools",
									options: note +
										`<table class="table table-bordered"><thead><tr><th>${__("Sender")}</th>` +
										`<th style="text-align:right">${__("Pool")}</th></tr></thead><tbody>${rows}` +
										`<tr><td><b>${__("Total")}</b></td><td style="text-align:right"><b>${format_currency(m.total || 0)}</b></td></tr>` +
										"</tbody></table>",
								}],
							});
							d.show();
						},
					});
				}, __("View"));
			}
		}
	},
});
