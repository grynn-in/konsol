// Trial Balance Upload — pre-fill Amount Basis from the site default (konsolidat#199)
//
// The field itself has no default: a guessed basis double-counts or halves
// every balance downstream. EPM Settings may hold a site-wide Default Amount
// Basis; it is copied onto a NEW upload only, where the user sees it and can
// change it before checking the file. Existing uploads are never touched, and
// an entity-period whose rows carry their own amount_basis column keeps that.
frappe.ui.form.on("Trial Balance Upload", {
	onload(frm) {
		if (!frm.is_new() || frm.doc.amount_basis) return;
		frappe.db.get_single_value("EPM Settings", "default_amount_basis").then((value) => {
			if (value && !frm.doc.amount_basis) {
				frm.set_value("amount_basis", value);
			}
		});
	},
});
