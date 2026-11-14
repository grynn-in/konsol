// Cost Allocation Statistic: Load from File (konsol#269 K5b).
//
// A draft with a Values File attached can replace every value row from the
// CSV. The server parses the whole file first and refuses with every bad row
// listed, so a failed load changes nothing. Unsaved edits are saved first.
frappe.ui.form.on("Cost Allocation Statistic", {
	refresh(frm) {
		if (frm.doc.docstatus === 0 && !frm.is_new() && frm.doc.values_file) {
			frm.add_custom_button(__("Load from File"), () => {
				const run = () =>
					frappe.call({
						method: "konsol.epm.doctype.cost_allocation_statistic.cost_allocation_statistic.load_values_from_file",
						args: { name: frm.doc.name },
						freeze: true,
						callback: () => frm.reload_doc(),
					});
				const go = () =>
					frm.is_dirty() ? frm.save().then(() => { if (!frm.is_dirty()) run(); }) : run();
				const n = (frm.doc.values || []).length;
				if (n) {
					frappe.confirm(
						__("Replace the {0} rows below with the rows in the file?", [n]), go);
				} else {
					go();
				}
			});
		}
	},
});
