// Trial Balance Submission — pre-fill Amount Basis from the site default (konsolidat#199)
//
// The field itself has no default: a guessed basis double-counts or halves
// every balance downstream. EPM Settings may hold a site-wide Default Amount
// Basis; it is copied onto a NEW document only, where the user sees it and can
// change it before saving. Existing documents are never touched.
frappe.ui.form.on("Trial Balance Submission", {
	onload(frm) {
		if (!frm.is_new() || frm.doc.amount_basis) return;
		frappe.db.get_single_value("EPM Settings", "default_amount_basis").then((value) => {
			if (value && !frm.doc.amount_basis) {
				frm.set_value("amount_basis", value);
			}
		});
	},
});
