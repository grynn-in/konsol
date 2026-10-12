// Trial Balance Upload — pre-fill Amount Basis from the site default (konsolidat#199)
//
// The field itself has no default: a guessed basis double-counts or halves
// every balance downstream. EPM Settings may hold a site-wide Default Amount
// Basis; it is copied onto a NEW upload only, where the user sees it and can
// change it before checking the file. Existing uploads are never touched, and
// an entity-period whose rows carry their own amount_basis column keeps that.
//
// konsol#332-2-D1 (Deepak Pai 12 Oct): the indicator is the upload as it
// stands now, derived by the server from the submissions that are still
// submitted (`status_now`, sent by the controller's onload). Frappe's own
// indicator is the stored "Job status", which still reads Loaded after every
// submission was cancelled. The stored field is never written here.
frappe.ui.form.on("Trial Balance Upload", {
	refresh(frm) {
		const status = (frm.doc.__onload || {}).status_now;
		if (status && !frm.is_dirty()) {
			frm.page.set_indicator(__(status), frappe.utils.guess_colour(status));
		}
	},
	onload(frm) {
		if (!frm.is_new() || frm.doc.amount_basis) return;
		frappe.db.get_single_value("EPM Settings", "default_amount_basis").then((value) => {
			if (value && !frm.doc.amount_basis) {
				frm.set_value("amount_basis", value);
			}
		});
	},
});
