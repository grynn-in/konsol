// Business Disposal — the disposer pre-fill (konsol#117 pair 1).
//
// Disposer Node is required. When it is blank and the disposed node is typed,
// the form offers the disposed node's tree parent, the one default of the
// design: the server does the same in before_validate, but a required Link
// left blank never reaches the server, so the form fills it where the user
// can see and change it.
frappe.ui.form.on("Business Disposal", {
	consolidation_group: prefill_disposer,
	disposed_entity: prefill_disposer,
});

function prefill_disposer(frm) {
	if (frm.doc.disposer_node || !frm.doc.consolidation_group || !frm.doc.disposed_entity
		|| frm.doc.docstatus !== 0) {
		return;
	}
	const filters = {
		consolidation_group: frm.doc.consolidation_group,
		data_area_id: frm.doc.disposed_entity,
	};
	frappe.db.get_value("Consolidation Group", filters, "parent_consolidation_group").then((r) => {
		const parent = r && r.message && r.message.parent_consolidation_group;
		if (parent && !frm.doc.disposer_node) {
			frm.set_value("disposer_node", parent);
		}
	});
}
