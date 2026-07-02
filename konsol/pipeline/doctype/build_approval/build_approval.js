frappe.ui.form.on("Build Approval", {
	refresh(frm) {
		// Run after the default dashboard refresh so we can add the trigger link.
		setTimeout(() => refresh_build_approval_connections(frm), 0);
	},
});

function refresh_build_approval_connections(frm) {
	if (frm.is_new() || !frm.dashboard) {
		return;
	}

	const { trigger_doctype, trigger_docname } = frm.doc;

	frm.dashboard.data = null;
	frm.dashboard.data_rendered = false;
	frm.dashboard.init_data();

	if (trigger_doctype && trigger_docname) {
		frm.dashboard.add_transactions({
			label: __("Trigger"),
			items: [trigger_doctype],
		});
	}

	frm.dashboard.refresh();
}