// Ownership Period — the owner pre-fill (konsol#117 pair 1).
//
// Owner Node is required. When it is blank and the owned node is typed, the
// form offers the owned node's tree parent, the one default of the design: the
// server does the same in before_insert / before_validate, but a required
// Link blank on the form never reaches the server, so the form fills it where
// the user can see and change it.
frappe.ui.form.on("Ownership Period", {
	consolidation_group: prefill_owner,
	data_area_id: prefill_owner,
});

function prefill_owner(frm) {
	if (frm.doc.owner_node || !frm.doc.consolidation_group || frm.doc.docstatus !== 0) {
		return;
	}
	// a blank Link is stored as NULL: ask for "not set", not ""
	const filters = {
		consolidation_group: frm.doc.consolidation_group,
		data_area_id: frm.doc.data_area_id || ["is", "not set"],
	};
	frappe.db.get_value("Consolidation Group", filters, "parent_consolidation_group").then((r) => {
		const parent = r && r.message && r.message.parent_consolidation_group;
		if (parent && !frm.doc.owner_node) {
			frm.set_value("owner_node", parent);
		}
	});
}
