frappe.ui.form.on("Pipeline Build Request", {
	refresh(frm) {
		// Run after the default dashboard refresh so we can replace static links.
		setTimeout(() => refresh_pipeline_build_request_connections(frm), 0);
	},
});

function refresh_pipeline_build_request_connections(frm) {
	if (frm.is_new() || !frm.dashboard) {
		return;
	}

	const { trigger_doctype, trigger_docname } = frm.doc;

	frm.dashboard.data = null;
	frm.dashboard.data_rendered = false;

	if (trigger_doctype && trigger_docname) {
		frm.dashboard.init_data();
		frm.dashboard.add_transactions({
			label: __("Trigger"),
			items: [trigger_doctype],
		});
		frm.dashboard.refresh();
		return;
	}

	frm.dashboard.hide();
}