// Business Combination — Get Balances from Trial Balance (konsol#207)
//
// A Draft deal takes its Acquired Balance Sheet from the acquired entity's
// warehouse trial balance through the acquisition period, on request. The
// server replaces the lines, places the Fair Value Adjustment Total and saves.
// Only a real Draft (Pending Approval is docstatus 0 too); unsaved edits are
// saved first so the server uses what was typed, not what was stored. Frappe
// v15's frm.save() resolves even when the save fails, so the method runs only
// once the form is no longer dirty (a failed save keeps the typed values).
frappe.ui.form.on("Business Combination", {
	// Acquirer Node (konsol#117 pair 1): required; when blank and the acquired
	// node is typed, the form offers the acquired node's tree parent, the one
	// default of the design. The server does the same in before_validate, but
	// a required Link left blank never reaches the server.
	consolidation_group: prefill_acquirer,
	acquired_entity: prefill_acquirer,
	refresh(frm) {
		if (frm.doc.docstatus === 0 && !frm.is_new() && frm.doc.status === "Draft") {
			frm.add_custom_button(__("Get Balances from Trial Balance"), () => {
				const n = (frm.doc.acquired_balances || []).length;
				const run = () =>
					frappe.call({
						method: "konsol.consolidation.doctype.business_combination.business_combination.get_balances_from_trial_balance",
						args: { name: frm.doc.name },
						freeze: true,
						callback: () => frm.reload_doc(),
					});
				const go = () =>
					frm.is_dirty() ? frm.save().then(() => { if (!frm.is_dirty()) run(); }) : run();
				if (n) {
					frappe.confirm(
						__("This replaces the {0} lines of the Acquired Balance Sheet. Continue?", [n]),
						go
					);
				} else {
					go();
				}
			});
		}
	},
});

function prefill_acquirer(frm) {
	if (frm.doc.acquirer_node || !frm.doc.consolidation_group || !frm.doc.acquired_entity
		|| frm.doc.docstatus !== 0) {
		return;
	}
	const filters = {
		consolidation_group: frm.doc.consolidation_group,
		data_area_id: frm.doc.acquired_entity,
	};
	frappe.db.get_value("Consolidation Group", filters, "parent_consolidation_group").then((r) => {
		const parent = r && r.message && r.message.parent_consolidation_group;
		if (parent && !frm.doc.acquirer_node) {
			frm.set_value("acquirer_node", parent);
		}
	});
}
