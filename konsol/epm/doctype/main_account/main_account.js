// konsol#182: the group chart of accounts. Publish and Unpublish are the
// governed path (GovernedReferenceDocument.publish / unpublish): only the Close
// Lead (EPM Admin) may do either, and each requests one `chart` rebuild.
frappe.ui.form.on("Main Account", {
	refresh(frm) {
		// Frappe hides a saved document's naming field (form.js, autoname
		// "field:main_account"). The code is the warehouse join key and cannot
		// be renamed, so show it, read-only (Deepak Pai, 5 Nov 2026).
		if (!frm.is_new()) {
			frm.toggle_display("main_account", true);
			frm.set_df_property("main_account", "read_only", 1);
		}
		if (frm.is_new() || !frappe.user.has_role(["EPM Admin", "System Manager"])) return;
		if (frm.doc.status !== "Published") {
			frm.add_custom_button(__("Publish"), () =>
				frm.call("publish").then(() => frm.reload_doc())
			);
		} else {
			frm.add_custom_button(__("Unpublish"), () =>
				frappe.confirm(
					__(
						"Withdraw this account from the group chart? New trial balances posting to it are refused."
					),
					() => frm.call("unpublish").then(() => frm.reload_doc())
				)
			);
		}
	},
	is_group(frm) {
		// A heading is never posted to and carries no intercompany rows.
		if (frm.doc.is_group) {
			frm.set_value("is_posting", 0);
			frm.set_value("allow_ic", 0);
		} else {
			frm.set_value("is_posting", 1);
		}
	},
});
